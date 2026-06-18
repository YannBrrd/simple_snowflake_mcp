import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import mcp.server.stdio
import mcp.types as types
import snowflake.connector
import yaml
from dotenv import load_dotenv
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from pydantic import AnyUrl

# Load environment variables from the .env file
load_dotenv()

# Repository root, used to constrain where configuration files may be loaded from.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Built-in defaults. Extracted from load_config() so the baseline configuration
# lives in one named place rather than buried in the loader (CQ-L4).
DEFAULT_CONFIG: dict[str, Any] = {
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "file_logging": {
            "enabled": False,
            "filename": "logs/server.log",
            "max_bytes": 10485760,
            "backup_count": 5,
        },
    },
    "server": {
        "name": "simple_snowflake_mcp",
        "version": "0.2.0",
        "description": "Enhanced Snowflake MCP Server with full protocol compliance",
        "connection": {
            "test_on_startup": True,
            "timeout": 30,
        },
    },
    "snowflake": {
        "read_only": True,
        "default_query_limit": 1000,
        "max_query_limit": 50000,
        # Server-side statement timeout, applied as a Snowflake session parameter.
        "statement_timeout_seconds": 300,
        # Reuse a single Snowflake connection across queries instead of opening a
        # new authenticated connection per query (mitigates connection-exhaustion DoS).
        "connection_reuse": True,
    },
    "security": {
        # Simple in-process sliding-window rate limit across all tool calls.
        "rate_limit": {
            "enabled": True,
            "max_calls": 60,
            "window_seconds": 60,
        },
        # Bounds on the in-memory notes store.
        "notes": {
            "max_count": 100,
            "max_content_length": 10000,
        },
    },
    "mcp": {
        "experimental_features": {
            "resource_subscriptions": True,
            "completion_support": False,
        },
        "notifications": {
            "resources_changed": True,
            "tools_changed": True,
            "prompts_changed": True,
        },
    },
}


def _deep_merge(default: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``loaded`` into a copy of ``default``."""
    for key, value in loaded.items():
        if key in default and isinstance(default[key], dict) and isinstance(value, dict):
            _deep_merge(default[key], value)
        else:
            default[key] = value
    return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, result))


def _resolve_config_path() -> Path | None:
    """
    Resolve the configuration file path from CONFIG_FILE, constrained to REPO_ROOT.

    Prevents path traversal / absolute-path injection via CONFIG_FILE (SEC-M4):
    a value that resolves outside the repository is rejected and the default
    config.yaml is used instead.
    """
    config_file = os.getenv("CONFIG_FILE", "config.yaml")
    candidate = (REPO_ROOT / config_file).resolve()
    if not candidate.is_relative_to(REPO_ROOT):
        print(f"CONFIG_FILE '{config_file}' resolves outside the repository; ignoring it.")
        candidate = (REPO_ROOT / "config.yaml").resolve()
    return candidate


def _ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return parent[key] as a dict, replacing a missing/non-dict value with {}.

    A user/attacker config that overrides a subtree with a scalar or null (e.g.
    ``snowflake: null`` or ``server: {connection: null}``) must not crash the
    server when that subtree is later indexed.
    """
    value = parent.get(key)
    if not isinstance(value, dict):
        value = parent[key] = {}
    return value


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Coerce security-sensitive merged values to safe types/bounds (SEC-M4).

    A malicious or mistaken config must not be able to silently disable a control
    by supplying, e.g., read_only as the string "false" or an absurd query limit,
    nor crash the server by replacing a config subtree with a non-mapping value.
    """
    # Fallbacks reference DEFAULT_CONFIG so the baseline lives in exactly one
    # place and the two cannot drift.
    d_sf = DEFAULT_CONFIG["snowflake"]
    sf = _ensure_dict(config, "snowflake")
    sf["read_only"] = _as_bool(sf.get("read_only", d_sf["read_only"]), default=True)
    sf["max_query_limit"] = _as_int(
        sf.get("max_query_limit", d_sf["max_query_limit"]), d_sf["max_query_limit"], 1, 10_000_000
    )
    sf["default_query_limit"] = _as_int(
        sf.get("default_query_limit", d_sf["default_query_limit"]),
        d_sf["default_query_limit"],
        1,
        sf["max_query_limit"],
    )
    sf["statement_timeout_seconds"] = _as_int(
        sf.get("statement_timeout_seconds", d_sf["statement_timeout_seconds"]),
        d_sf["statement_timeout_seconds"],
        1,
        86_400,
    )
    sf["connection_reuse"] = _as_bool(
        sf.get("connection_reuse", d_sf["connection_reuse"]), default=True
    )

    d_rl = DEFAULT_CONFIG["security"]["rate_limit"]
    d_nt = DEFAULT_CONFIG["security"]["notes"]
    sec = _ensure_dict(config, "security")
    rl = _ensure_dict(sec, "rate_limit")
    rl["enabled"] = _as_bool(rl.get("enabled", d_rl["enabled"]), default=True)
    rl["max_calls"] = _as_int(
        rl.get("max_calls", d_rl["max_calls"]), d_rl["max_calls"], 1, 1_000_000
    )
    rl["window_seconds"] = _as_int(
        rl.get("window_seconds", d_rl["window_seconds"]), d_rl["window_seconds"], 1, 86_400
    )
    nt = _ensure_dict(sec, "notes")
    nt["max_count"] = _as_int(
        nt.get("max_count", d_nt["max_count"]), d_nt["max_count"], 0, 1_000_000
    )
    nt["max_content_length"] = _as_int(
        nt.get("max_content_length", d_nt["max_content_length"]),
        d_nt["max_content_length"],
        0,
        10_000_000,
    )

    # server.connection is read at import (CONNECTION_TIMEOUT) and at startup
    # (test_on_startup); normalize it so a malformed override can't crash import.
    srv = _ensure_dict(config, "server")
    srv.setdefault("name", DEFAULT_CONFIG["server"]["name"])
    srv.setdefault("version", DEFAULT_CONFIG["server"]["version"])
    srv.setdefault("description", DEFAULT_CONFIG["server"]["description"])
    d_conn = DEFAULT_CONFIG["server"]["connection"]
    conn = _ensure_dict(srv, "connection")
    conn["timeout"] = _as_int(conn.get("timeout", d_conn["timeout"]), d_conn["timeout"], 1, 86_400)
    conn["test_on_startup"] = _as_bool(
        conn.get("test_on_startup", d_conn["test_on_startup"]), default=True
    )
    return config


def load_config() -> dict[str, Any]:
    """
    Load configuration from a YAML file, deep-merged onto DEFAULT_CONFIG and
    validated. Falls back to defaults if the file is missing or unreadable.
    """
    import copy

    default_config = copy.deepcopy(DEFAULT_CONFIG)
    config_path = _resolve_config_path()
    try:
        if config_path and config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                loaded_config = yaml.safe_load(f) or {}
            if not isinstance(loaded_config, dict):
                print(f"Config file {config_path} is not a mapping; using defaults")
                return _validate_config(default_config)
            return _validate_config(_deep_merge(default_config, loaded_config))
        print(f"Config file not found at {config_path}, using defaults")
        return _validate_config(default_config)
    except Exception as e:
        print(f"Error loading config file: {e}, using defaults")
        return _validate_config(default_config)


# Load configuration
CONFIG = load_config()


def setup_logging():
    """Setup logging based on configuration."""
    log_config = CONFIG.get("logging", {})

    # Convert string log level to logging constant. The LOG_LEVEL environment
    # variable overrides the config file (documented in README).
    log_level_str = os.getenv("LOG_LEVEL", log_config.get("level", "INFO")).upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Setup basic configuration
    log_format = log_config.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Configure root logger
    logging.basicConfig(level=log_level, format=log_format, force=True)

    # Setup file logging if enabled
    file_config = log_config.get("file_logging", {})
    if file_config.get("enabled", False):
        try:
            from logging.handlers import RotatingFileHandler

            log_file = Path(file_config.get("filename", "logs/server.log"))
            log_file.parent.mkdir(exist_ok=True)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=file_config.get("max_bytes", 10485760),
                backupCount=file_config.get("backup_count", 5),
            )
            file_handler.setFormatter(logging.Formatter(log_format))
            logging.getLogger().addHandler(file_handler)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")


# Setup logging
setup_logging()
logger = logging.getLogger(__name__)
logger.info(f"Configuration loaded: {CONFIG['server']['name']} v{CONFIG['server']['version']}")

# Store notes as a simple key-value dict to demonstrate state management
notes: dict[str, str] = {}

# Track resource subscriptions for notifications
resource_subscriptions: dict[str, set[str]] = {}

# Server metadata from configuration
SERVER_INFO = {
    "name": CONFIG["server"]["name"],
    "version": CONFIG["server"]["version"],
    "description": CONFIG["server"]["description"],
    "author": "Yann Barraud",
    "license": "MIT",
}

server = Server("simple_snowflake_mcp")

# Read-only mode is derived solely from server configuration; the environment
# variable overrides the config file. It is NEVER client-controllable (SEC-C2/M1).
READ_ONLY = _as_bool(
    os.getenv("MCP_READ_ONLY", str(CONFIG["snowflake"]["read_only"])), default=True
)
DEFAULT_QUERY_LIMIT = CONFIG["snowflake"]["default_query_limit"]
MAX_QUERY_LIMIT = CONFIG["snowflake"]["max_query_limit"]
STATEMENT_TIMEOUT_SECONDS = CONFIG["snowflake"]["statement_timeout_seconds"]
CONNECTION_REUSE = CONFIG["snowflake"]["connection_reuse"]
CONNECTION_TIMEOUT = CONFIG["server"]["connection"].get("timeout", 30)

# Required Snowflake credential keys.
_REQUIRED_SNOWFLAKE_KEYS = ("user", "password", "account")
# Connection config keys that are safe to echo back to a client (never the password).
_SAFE_CONFIG_KEYS = ("user", "account", "warehouse", "database", "schema")


class SnowflakeConfigError(Exception):
    """Raised when required Snowflake connection settings are missing."""


def get_snowflake_config() -> dict[str, Any]:
    """
    Build Snowflake connection kwargs from the environment at call time (CQ-C3).

    Reading env lazily (rather than at import) means credentials injected by a
    container entrypoint or a test are honored. Raises SnowflakeConfigError if a
    required setting is missing.
    """
    config: dict[str, Any] = {
        "user": os.getenv("SNOWFLAKE_USER"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    }
    missing = [k for k in _REQUIRED_SNOWFLAKE_KEYS if not config[k]]
    if missing:
        raise SnowflakeConfigError(
            "Missing required Snowflake settings: "
            + ", ".join(f"SNOWFLAKE_{k.upper()}" for k in missing)
        )

    for key, env_name in (
        ("warehouse", "SNOWFLAKE_WAREHOUSE"),
        ("database", "SNOWFLAKE_DATABASE"),
        ("schema", "SNOWFLAKE_SCHEMA"),
    ):
        value = os.getenv(env_name)
        if value:
            config[key] = value

    # Bound how long connection and queries may run (SEC-M3, CQ-H2).
    config["login_timeout"] = CONNECTION_TIMEOUT
    config["network_timeout"] = CONNECTION_TIMEOUT
    config["session_parameters"] = {
        "STATEMENT_TIMEOUT_IN_SECONDS": STATEMENT_TIMEOUT_SECONDS,
    }
    return config


def safe_config_echo() -> dict[str, Any]:
    """Return non-secret connection info suitable for returning to a client."""
    try:
        cfg = get_snowflake_config()
    except SnowflakeConfigError as e:
        return {"error": str(e)}
    return {k: cfg[k] for k in _SAFE_CONFIG_KEYS if cfg.get(k)}


# ---------------------------------------------------------------------------
# Read-only enforcement (SEC-C1, SEC-C2, CQ-H1)
# ---------------------------------------------------------------------------
# Keyword filtering is DEFENSE-IN-DEPTH ONLY. The real security boundary must be
# a least-privilege, SELECT-only Snowflake role (see README). This guard exists
# so a misconfigured deployment fails closed rather than open.

_READ_ONLY_PREFIXES = frozenset({"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH"})
# Tokens that perform writes / DDL / side effects. Used to reject CTE-fronted DML
# (e.g. "WITH x AS (...) DELETE ...") and anything similar.
_WRITE_TOKENS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "UPSERT",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "CALL",
        "COPY",
        "PUT",
        "GET",
        "REMOVE",
        "UNLOAD",
        "USE",
        "SET",
        "UNSET",
        "COMMENT",
        "RENAME",
        "REPLACE",
        "EXECUTE",
    }
)

_WORD_RE = re.compile(r"[A-Za-z_]+")
# Opening of a dollar-quoted string: $$ or $tag$ (tag is an identifier).
_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
# A write/DDL token used as a *statement* keyword (followed by whitespace or
# end-of-input), not as a function call such as REPLACE(...) or GET(...).
_WRITE_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_WRITE_TOKENS)) + r")\b(?!\s*\()",
    re.IGNORECASE,
)


def _strip_sql_noise(sql: str) -> str:
    """
    Replace comments and quoted spans with a space so keyword/separator scanning
    operates only on SQL structure, not on data.

    This is a single left-to-right pass (not independent regex substitutions) so
    that a ``--`` or ``/*`` inside a string literal is treated as data, and a
    semicolon or keyword inside a literal/identifier/dollar-quote cannot be
    mistaken for a statement boundary or DML. Recognizes line comments (``--``),
    block comments (``/* */``), single-quoted strings, double-quoted identifiers
    (both with doubled-quote escapes), and dollar-quoted strings (``$$``/``$tag$``).
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        pair = sql[i : i + 2]

        if pair == "--":  # line comment, to end of line (keep the newline)
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl
            out.append(" ")
        elif pair == "/*":  # block comment
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        elif ch in "'\"":  # quoted string ('') or identifier ("")
            i += 1
            while i < n:
                # Snowflake honors backslash escapes inside string literals
                # (e.g. 'O\'Brien'), so a backslash skips the next char. Quoted
                # identifiers ("...") use only doubled-quote escaping.
                if ch == "'" and sql[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if sql[i] == ch:
                    if i + 1 < n and sql[i + 1] == ch:  # doubled-quote escape
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
        elif ch == "$" and (m := _DOLLAR_TAG_RE.match(sql, i)):  # dollar-quote
            tag = m.group(0)
            end = sql.find(tag, i + len(tag))
            i = n if end == -1 else end + len(tag)
            out.append(" ")
        else:
            out.append(ch)
            i += 1

    return "".join(out).strip()


def is_read_only_sql(sql: str) -> bool:
    """
    Conservative check that ``sql`` is a single read-only statement.

    Strips comments and quoted spans, rejects multiple statements, requires an
    allow-listed leading keyword, and (for WITH) rejects CTE-fronted DML. Quoted
    data is removed first so semicolons or keywords inside literals do not cause
    false rejections, and write tokens used as functions (e.g. REPLACE(), GET())
    are distinguished from statement keywords (e.g. DELETE, INSERT).
    """
    cleaned = _strip_sql_noise(sql)
    if not cleaned:
        return False

    # Reject multiple statements (ignore a single trailing semicolon).
    statements = [s for s in cleaned.split(";") if s.strip()]
    if len(statements) != 1:
        return False
    cleaned = statements[0].strip()

    first_match = _WORD_RE.match(cleaned)
    if not first_match:
        return False
    first_word = first_match.group(0).upper()
    if first_word not in _READ_ONLY_PREFIXES:
        return False

    # A CTE may legally front DML in Snowflake; reject if a write statement
    # keyword appears anywhere (a same-named function call is allowed).
    if first_word == "WITH" and _WRITE_TOKEN_RE.search(cleaned):
        return False
    return True


# ---------------------------------------------------------------------------
# Identifier validation (SEC-H1, CQ-C2)
# ---------------------------------------------------------------------------
# Allow the characters valid in Snowflake unquoted identifiers (letters, digits,
# underscore, dollar) plus the LIKE wildcards % and _. Quotes, whitespace, and
# statement separators remain rejected, so the value is injection-safe.
_IDENTIFIER_PATTERN_RE = re.compile(r"^[A-Za-z0-9_$%]+$")
# Word-boundary match for a LIMIT clause (not a substring of an identifier).
_LIMIT_KEYWORD_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def validate_like_pattern(value: str, field: str) -> str:
    """
    Validate a value destined for a ``LIKE '...'`` clause. SHOW ... LIKE does not
    accept bind parameters, so we restrict the value to a safe character class
    (letters, digits, underscore, dollar, and the % wildcard) to prevent injection.
    """
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN_RE.match(value):
        raise ValueError(f"Invalid {field}: only letters, digits, '_', '$' and '%' are permitted")
    return value


# ---------------------------------------------------------------------------
# Connection management (CQ-C1, SEC-M2)
# ---------------------------------------------------------------------------
_connection = None
_connection_lock = threading.Lock()
_error_counter = 0
# Errors that indicate the connection itself is unusable (network drop, expired
# session) rather than a problem with the SQL. A reused connection that raises
# one of these is retried once on a fresh connection.
_CONNECTION_ERRORS = (
    snowflake.connector.errors.OperationalError,
    snowflake.connector.errors.InterfaceError,
)


def _get_connection():
    """Return a (possibly cached) live Snowflake connection."""
    global _connection
    if _connection is None:
        _connection = snowflake.connector.connect(**get_snowflake_config())
    return _connection


def _run_sql_once(query: str, row_limit: int | None) -> tuple[Any, bool]:
    """Execute the query on the (possibly cached) connection.

    Returns ``(data, truncated)``. When ``row_limit`` is set we fetch one extra
    row so we can tell the caller whether the result was capped, then trim back
    to ``row_limit`` — the truncation is reported rather than hidden (SEC-H2).
    """
    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute(query)
        if cur.description:
            columns = [desc[0] for desc in cur.description]
            if row_limit:
                rows = list(cur.fetchmany(row_limit + 1))
                truncated = len(rows) > row_limit
                rows = rows[:row_limit]
            else:
                rows = cur.fetchall()
                truncated = False
            return [dict(zip(columns, row)) for row in rows], truncated
        return {"status": "success", "rowcount": cur.rowcount}, False


def _reset_connection() -> None:
    """Close and discard the cached connection so the next call reconnects."""
    global _connection
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None


def _sql_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8", "replace")).hexdigest()[:12]


def _log_exception(description: str, exc: Exception) -> str:
    """Log full exception detail server-side with a reference id; return the id."""
    global _error_counter
    _error_counter += 1
    ref = f"E{_error_counter:06d}"
    logger.error("%s failed [ref %s]: %s", description, ref, exc)
    return ref


def _safe_snowflake_execute(
    query: str,
    description: str = "Query",
    *,
    is_user_sql: bool = False,
    row_limit: int | None = None,
) -> dict[str, Any]:
    """
    Execute a Snowflake query with read-only enforcement, resource-safe cleanup,
    and client-safe error handling.

    This is the single chokepoint for SQL execution. When ``is_user_sql`` is True
    and the server is in read-only mode, the read-only guard is applied here so no
    caller can bypass it (SEC-C1/SEC-C2). ``row_limit`` bounds rows at the driver
    via fetchmany rather than editing SQL text (SEC-H2, CQ-M2).
    """
    if is_user_sql and READ_ONLY and not is_read_only_sql(query):
        logger.warning(
            "Rejected non-read-only query in read-only mode (%d chars, hash=%s)",
            len(query),
            _sql_hash(query),
        )
        return {
            "success": False,
            "error": (
                "Only read-only queries (SELECT/SHOW/DESCRIBE/EXPLAIN/WITH without "
                "DML) are permitted while the server is in read-only mode."
            ),
            "data": None,
        }

    # Enforce the default row cap here, at the single chokepoint, so it applies
    # to every user-SQL tool and cannot be skipped by a caller that forgets to
    # request it (SEC-H2). An explicit row_limit (client limit) takes precedence.
    if is_user_sql and row_limit is None:
        row_limit = _default_row_cap(query)

    # Do not log raw SQL (may contain PII/secrets) at INFO; emit a hash instead
    # and reserve the full text for DEBUG (SEC-M2, CQ-M4).
    logger.info("Executing %s (%d chars, hash=%s)", description, len(query), _sql_hash(query))
    logger.debug("SQL for %s: %s", description, query)

    try:
        with _connection_lock:
            # Capture whether we're about to reuse a connection from a previous
            # call (vs. opening a fresh one). A reused connection may have gone
            # stale (idle session timeout, proxy/VPN drop); if it fails at the
            # connection level we reconnect and retry once so the caller doesn't
            # see a spurious error on the first query after an idle period.
            reusing = _connection is not None
            try:
                result, truncated = _run_sql_once(query, row_limit)
            except _CONNECTION_ERRORS:
                _reset_connection()
                if not reusing:
                    raise
                logger.info("Snowflake connection appears stale; reconnecting and retrying once")
                result, truncated = _run_sql_once(query, row_limit)
            finally:
                if not CONNECTION_REUSE:
                    _reset_connection()

        logger.info("%s completed successfully", description)
        return {"success": True, "data": result, "truncated": truncated, "row_limit": row_limit}

    except SnowflakeConfigError as e:
        # Safe to surface: contains only which env vars are missing, no secrets.
        logger.error("%s failed: %s", description, e)
        return {"success": False, "error": str(e), "data": None}
    except Exception as e:
        ref = _log_exception(description, e)
        return {
            "success": False,
            "error": f"An internal error occurred while executing the query (ref {ref}).",
            "data": None,
        }


async def _execute(
    query: str,
    description: str = "Query",
    *,
    is_user_sql: bool = False,
    row_limit: int | None = None,
) -> dict[str, Any]:
    """
    Await the synchronous chokepoint on a worker thread.

    Snowflake I/O (connect/execute/fetch) is blocking and can run for up to the
    statement timeout; running it inline would freeze the asyncio event loop (and
    the rate limiter). Offloading to a thread keeps the server responsive while
    the per-connection ``_connection_lock`` still serializes access to the single
    shared connection.
    """
    return await asyncio.to_thread(
        _safe_snowflake_execute,
        query,
        description,
        is_user_sql=is_user_sql,
        row_limit=row_limit,
    )


def _format_markdown_table(data: list[dict[str, Any]]) -> str:
    """Format query results as a markdown table, escaping cell contents."""
    if not data:
        return "No results found."

    def _cell(value: Any) -> str:
        if value is None:
            return ""
        # Escape pipes and collapse newlines so they don't break the table (CQ-L2).
        text = str(value).replace("\\", "\\\\").replace("|", "\\|")
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        if len(text) > 500:
            text = text[:497] + "..."
        return text

    columns = list(data[0].keys())
    header = "| " + " | ".join(_cell(c) for c in columns) + " |"
    separator = "|" + "---|" * len(columns)
    rows = ["| " + " | ".join(_cell(row.get(col)) for col in columns) + " |" for row in data]
    return header + "\n" + separator + "\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# Rate limiting (SEC-M3)
# ---------------------------------------------------------------------------
class _RateLimiter:
    """Simple in-process sliding-window limiter over all tool invocations."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > self.window:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                return False
            self._calls.append(now)
            return True


_rate_limit_cfg = CONFIG["security"]["rate_limit"]
_rate_limiter = _RateLimiter(_rate_limit_cfg["max_calls"], _rate_limit_cfg["window_seconds"])
_RATE_LIMIT_ENABLED = _rate_limit_cfg["enabled"]
_NOTES_MAX_COUNT = CONFIG["security"]["notes"]["max_count"]
_NOTES_MAX_LENGTH = CONFIG["security"]["notes"]["max_content_length"]


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """
    List available resources including notes and Snowflake schema information.
    Each resource is exposed with appropriate URI schemes.
    """
    resources = []

    # Add note resources
    for name in notes:
        resources.append(
            types.Resource(
                uri=AnyUrl(f"note://internal/{name}"),
                name=f"Note: {name}",
                description=f"A simple note named {name}",
                mimeType="text/plain",
            )
        )

    # Add Snowflake schema resource
    resources.append(
        types.Resource(
            uri=AnyUrl("snowflake://schema/metadata"),
            name="Snowflake Schema Metadata",
            description="Comprehensive Snowflake database schema information",
            mimeType="application/json",
        )
    )

    # Add connection status resource
    resources.append(
        types.Resource(
            uri=AnyUrl("snowflake://status/connection"),
            name="Snowflake Connection Status",
            description="Current Snowflake connection status and configuration",
            mimeType="application/json",
        )
    )

    logger.info(f"Listed {len(resources)} resources")
    return resources


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    """
    Read a specific resource's content by its URI.
    Supports multiple URI schemes: note://, snowflake://
    """
    logger.info(f"Reading resource: {uri}")

    if uri.scheme == "note":
        name = uri.path
        if name is not None:
            name = name.lstrip("/")
            if name in notes:
                return notes[name]
        raise ValueError(f"Note not found: {name}")

    elif uri.scheme == "snowflake":
        if str(uri) == "snowflake://schema/metadata":
            # Return comprehensive schema metadata
            result = await _execute(
                "SHOW DATABASES", "Schema metadata query", row_limit=MAX_QUERY_LIMIT
            )
            if result["success"]:
                return json.dumps(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "server_info": SERVER_INFO,
                        "databases": result["data"],
                        "connection_config": safe_config_echo(),
                    },
                    indent=2,
                )
            else:
                return json.dumps({"error": result["error"]}, indent=2)

        elif str(uri) == "snowflake://status/connection":
            # Return connection status
            result = await _execute(
                "SELECT CURRENT_VERSION(), CURRENT_TIMESTAMP()", "Connection status"
            )
            if result["success"]:
                status_data = {
                    "status": "connected",
                    "timestamp": datetime.now().isoformat(),
                    "snowflake_info": result["data"][0] if result["data"] else {},
                    "server_config": SERVER_INFO,
                    "read_only_mode": READ_ONLY,
                }
            else:
                status_data = {
                    "status": "error",
                    "timestamp": datetime.now().isoformat(),
                    "error": result["error"],
                    "read_only_mode": READ_ONLY,
                }
            return json.dumps(status_data, indent=2)

    raise ValueError(f"Unsupported URI scheme or path: {uri}")


@server.subscribe_resource()
async def handle_subscribe_resource(uri: AnyUrl) -> None:
    """
    Subscribe to resource updates.
    Clients will be notified when the resource changes.
    """
    uri_str = str(uri)
    logger.info(f"Subscribing to resource updates: {uri_str}")

    if uri_str not in resource_subscriptions:
        resource_subscriptions[uri_str] = set()

    # Add client to subscription (in a real implementation, you'd track client IDs)
    resource_subscriptions[uri_str].add("default_client")


@server.unsubscribe_resource()
async def handle_unsubscribe_resource(uri: AnyUrl) -> None:
    """
    Unsubscribe from resource updates.
    """
    uri_str = str(uri)
    logger.info(f"Unsubscribing from resource updates: {uri_str}")

    if uri_str in resource_subscriptions:
        resource_subscriptions[uri_str].discard("default_client")
        if not resource_subscriptions[uri_str]:
            del resource_subscriptions[uri_str]


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """
    List available prompts with comprehensive argument definitions.
    Each prompt can have optional arguments to customize behavior.
    """
    return [
        types.Prompt(
            name="summarize-notes",
            description="Creates a summary of all notes",
            arguments=[
                types.PromptArgument(
                    name="style",
                    description="Style of the summary (brief/detailed/executive)",
                    required=False,
                ),
                types.PromptArgument(
                    name="format",
                    description="Output format (text/markdown/json)",
                    required=False,
                ),
            ],
        ),
        types.Prompt(
            name="analyze-snowflake-schema",
            description="Analyze and summarize Snowflake database schema",
            arguments=[
                types.PromptArgument(
                    name="database",
                    description="Specific database to analyze (optional)",
                    required=False,
                ),
                types.PromptArgument(
                    name="focus",
                    description="Analysis focus (tables/views/functions/all)",
                    required=False,
                ),
            ],
        ),
        types.Prompt(
            name="generate-sql-query",
            description="Generate SQL query suggestions based on schema",
            arguments=[
                types.PromptArgument(
                    name="intent",
                    description="What you want to accomplish with the query",
                    required=True,
                ),
                types.PromptArgument(
                    name="complexity",
                    description="Query complexity level (simple/intermediate/advanced)",
                    required=False,
                ),
            ],
        ),
        types.Prompt(
            name="troubleshoot-connection",
            description="Help troubleshoot Snowflake connection issues",
            arguments=[
                types.PromptArgument(
                    name="error_message",
                    description="Error message or symptoms you're experiencing",
                    required=False,
                )
            ],
        ),
    ]


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    """
    Generate a prompt by combining arguments with server state.
    Supports multiple prompt types with dynamic content generation.
    """
    logger.info(f"Generating prompt: {name} with arguments: {arguments}")
    args = arguments or {}

    if name == "summarize-notes":
        style = args.get("style", "brief")
        format_type = args.get("format", "text")
        detail_prompt = " Give extensive details." if style == "detailed" else ""
        if style == "executive":
            detail_prompt = " Provide executive summary with key insights and actionable items."

        notes_content = "\n".join(f"- {name}: {content}" for name, content in notes.items())
        if not notes_content:
            notes_content = "No notes available."

        base_text = f"Here are the current notes to summarize:{detail_prompt}\n\n{notes_content}"
        if format_type == "json":
            base_text += "\n\nPlease format the response as JSON."
        elif format_type == "markdown":
            base_text += "\n\nPlease format the response as markdown."

        return types.GetPromptResult(
            description="Summarize the current notes",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=base_text),
                )
            ],
        )

    elif name == "analyze-snowflake-schema":
        database = args.get("database", "all databases")
        focus = args.get("focus", "all")

        # Get schema information
        result = await _execute("SHOW DATABASES", "Schema analysis", row_limit=MAX_QUERY_LIMIT)
        schema_info = result["data"] if result["success"] else [{"error": result["error"]}]

        prompt_text = f"""Analyze the following Snowflake database schema information:

Target: {database}
Focus: {focus}

Schema Information:
{json.dumps(schema_info, indent=2)}

Please provide insights about:
- Database structure and organization
- Table/view relationships (if focus includes tables/views)
- Data patterns and potential optimizations
- Recommended queries or analysis approaches
"""

        return types.GetPromptResult(
            description="Analyze Snowflake database schema",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    elif name == "generate-sql-query":
        intent = args.get("intent", "general analysis")
        complexity = args.get("complexity", "simple")

        # Get current schema context
        result = await _execute(
            "SHOW DATABASES", "Query generation context", row_limit=MAX_QUERY_LIMIT
        )
        schema_context = result["data"] if result["success"] else []

        prompt_text = f"""Generate SQL queries for Snowflake based on the following requirements:

Intent: {intent}
Complexity Level: {complexity}

Available Schema Context:
{json.dumps(schema_context, indent=2)}

Please provide:
1. One or more SQL queries that accomplish the intent
2. Explanation of what each query does
3. Any assumptions made about the data structure
4. Performance considerations (if complexity is intermediate/advanced)

Ensure queries are compatible with Snowflake SQL dialect.
"""

        return types.GetPromptResult(
            description="Generate SQL query suggestions",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    elif name == "troubleshoot-connection":
        error_msg = args.get("error_message", "general connection issues")

        # Get connection status
        status_result = await _execute("SELECT CURRENT_VERSION()", "Connection test")
        connection_status = (
            "Connected successfully"
            if status_result["success"]
            else f"Connection failed: {status_result['error']}"
        )

        prompt_text = f"""Help troubleshoot Snowflake connection issues:

Error/Symptoms: {error_msg}
Current Connection Status: {connection_status}
Server Configuration: {SERVER_INFO}
Read-Only Mode: {READ_ONLY}

Configuration (sensitive data removed):
{json.dumps(safe_config_echo(), indent=2)}

Please provide:
1. Likely causes of the issue
2. Step-by-step troubleshooting guide
3. Common solutions
4. How to verify the fix
5. Prevention tips for the future
"""

        return types.GetPromptResult(
            description="Troubleshoot Snowflake connection issues",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    List available tools with comprehensive JSON Schema validation.
    Each tool specifies detailed arguments and validation rules.
    """
    return [
        types.Tool(
            name="get-connection-info",
            description="Get current Snowflake connection information and server status",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        types.Tool(
            name="add-note",
            description="Add or update a note for future reference",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Note name/identifier",
                    },
                    "content": {"type": "string", "minLength": 1, "description": "Note content"},
                },
                "required": ["name", "content"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="delete-note",
            description="Delete an existing note",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "description": "Note name to delete"}
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="execute-snowflake-sql",
            description="Execute a SQL query on Snowflake and return the result as JSON",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL query to execute",
                        "minLength": 1,
                        "examples": ["SELECT CURRENT_TIMESTAMP()", "SHOW DATABASES"],
                    },
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "csv"],
                        "default": "json",
                        "description": "Output format for results",
                    },
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list-snowflake-warehouses",
            description="List available Snowflake Data Warehouses (DWH) with details",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include detailed warehouse information",
                    }
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list-databases",
            description="List all accessible Snowflake databases with optional filtering",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Filter databases by name pattern (supports wildcards)",
                        "examples": ["PROD_%", "%_DEV"],
                    },
                    "include_details": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include database details and metadata",
                    },
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="execute-query",
            description=(
                "Execute a SQL query with server-enforced read-only protection and "
                "flexible output format. Read-only mode is governed by server "
                "configuration and cannot be overridden by the caller."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL query to execute",
                        "minLength": 1,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["markdown", "json", "csv"],
                        "default": "markdown",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50000,
                        "description": "Maximum rows to return",
                    },
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="export-schema",
            description="Export database schema information in various formats",
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Database to export (optional; exports all if omitted)",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["json", "yaml", "sql"],
                        "default": "json",
                    },
                    "include_data_samples": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include sample data",
                    },
                },
                "additionalProperties": False,
            },
        ),
    ]


def _text(content: str) -> list[types.TextContent]:
    """Wrap a string as the standard single-item TextContent response (CQ-H3)."""
    return [types.TextContent(type="text", text=content)]


def _render_output(data: Any, format_type: str) -> str:
    """Render query result data as json (default), markdown, or csv."""
    if format_type in ("markdown", "csv"):
        # Non-SELECT statements return a status dict; render it as a one-row
        # table/CSV rather than silently falling back to JSON.
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else None
        if rows is not None:
            if format_type == "markdown":
                return _format_markdown_table(rows)
            if not rows:
                return "No data returned"
            import csv
            import io

            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
            return buffer.getvalue()
    return json.dumps(data, indent=2, default=str)


def _coerce_limit(raw_limit: Any) -> int | None:
    """
    Coerce an explicit client-supplied row limit to a bounded integer (SEC-H2).

    Never trusts the schema-declared type: a non-conforming client could send a
    string or an out-of-range value. Returns None when no explicit limit is
    given, in which case the chokepoint applies the default cap. The limit is
    applied at the driver via fetchmany, never concatenated into SQL text.
    """
    if raw_limit is None:
        return None
    # bool is a subclass of int; reject it so limit:true doesn't become 1.
    if isinstance(raw_limit, bool):
        raise ValueError("'limit' must be an integer")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("'limit' must be an integer") from exc
    return max(1, min(limit, MAX_QUERY_LIMIT))


def _default_row_cap(sql: str) -> int | None:
    """
    Default row cap for row-producing reads (SELECT and WITH ... SELECT) that do
    not already constrain themselves with a LIMIT. Uses a tokenized check so an
    identifier containing the substring "LIMIT" (e.g. delimiter_col) does not
    suppress the cap, and so CTE-fronted reads are also bounded.
    """
    cleaned = _strip_sql_noise(sql)
    first_match = _WORD_RE.match(cleaned)
    first_word = first_match.group(0).upper() if first_match else ""
    if first_word in ("SELECT", "WITH") and not _LIMIT_KEYWORD_RE.search(cleaned):
        return DEFAULT_QUERY_LIMIT
    return None


async def _run_user_query(
    sql: str, format_type: str, description: str, row_limit: int | None
) -> list[types.TextContent]:
    """
    Shared execution path for the user-facing SQL tools (CQ-M1). Read-only
    enforcement and row limiting happen inside _safe_snowflake_execute, the single
    SQL chokepoint, so neither tool can bypass them.
    """
    result = await _execute(sql, description, is_user_sql=True, row_limit=row_limit)
    if not result["success"]:
        return _text(f"Snowflake error: {result['error']}")

    contents = _text(_render_output(result["data"], format_type))
    if result.get("truncated"):
        # Never silently drop rows: tell the caller the result was capped and how
        # to retrieve more. Emitted as a separate content block so it does not
        # corrupt JSON/CSV output.
        contents.append(
            types.TextContent(
                type="text",
                text=(
                    f"Note: results were truncated to {result['row_limit']} rows. "
                    f"Pass a higher `limit` (up to {MAX_QUERY_LIMIT}) or add an explicit "
                    "LIMIT clause to retrieve more."
                ),
            )
        )
    return contents


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """
    Handle tool execution requests with comprehensive error handling and validation.
    Tools can modify server state and notify clients of changes.
    """
    # Log the tool name and argument keys only; argument values may contain SQL
    # with embedded PII/secrets, so the full payload is reserved for DEBUG (CQ-M4).
    args = arguments or {}
    logger.info("Executing tool: %s (args: %s)", name, sorted(args.keys()))
    logger.debug("Tool %s full arguments: %s", name, args)

    # In-process rate limiting across all tools (SEC-M3).
    if _RATE_LIMIT_ENABLED and not _rate_limiter.allow():
        logger.warning("Rate limit exceeded; rejecting tool call: %s", name)
        return _text("Rate limit exceeded. Please slow down and retry shortly.")

    try:
        # Connection and metadata tools
        if name == "get-connection-info":
            result = await _execute(
                "SELECT CURRENT_VERSION(), CURRENT_USER(), CURRENT_DATABASE(), "
                "CURRENT_SCHEMA(), CURRENT_WAREHOUSE()",
                "Connection info",
            )
            if result["success"]:
                info = {
                    "server_info": SERVER_INFO,
                    "connection_status": "connected",
                    "snowflake_info": result["data"][0] if result["data"] else {},
                    "config": safe_config_echo(),
                    "read_only_mode": READ_ONLY,
                    "timestamp": datetime.now().isoformat(),
                }
                return _text(json.dumps(info, indent=2))
            return _text(f"Connection error: {result['error']}")

        # Note management tools
        elif name == "add-note":
            note_name = args.get("name")
            content = args.get("content")
            if not note_name or not content:
                raise ValueError("Both 'name' and 'content' are required")
            if len(content) > _NOTES_MAX_LENGTH:
                raise ValueError(
                    f"Note content exceeds maximum length of {_NOTES_MAX_LENGTH} characters"
                )
            # Bound the in-memory store (SEC-L2): allow updates to existing notes,
            # but cap the number of distinct notes.
            if note_name not in notes and len(notes) >= _NOTES_MAX_COUNT:
                raise ValueError(
                    f"Note limit reached ({_NOTES_MAX_COUNT}); delete a note before adding more"
                )

            old_content = notes.get(note_name)
            notes[note_name] = content
            return _text(
                f"Note '{note_name}' {'updated' if old_content else 'created'} successfully"
            )

        elif name == "delete-note":
            note_name = args.get("name")
            if not note_name:
                raise ValueError("'name' parameter is required")
            if note_name in notes:
                del notes[note_name]
                return _text(f"Note '{note_name}' deleted successfully")
            return _text(f"Note '{note_name}' not found")

        # Snowflake SQL tools — both route through the same governed path.
        elif name == "execute-snowflake-sql":
            sql = args.get("sql")
            if not sql:
                raise ValueError("'sql' parameter is required")
            format_type = args.get("format", "json")
            # No explicit limit; the chokepoint applies the default row cap so
            # this tool can't be used for an unbounded fetch.
            return await _run_user_query(sql, format_type, "SQL execution", None)

        elif name == "execute-query":
            sql = args.get("sql")
            if not sql:
                raise ValueError("'sql' parameter is required")
            # NOTE: read-only is governed solely by server config; there is
            # deliberately no client-supplied read_only argument (SEC-C2, SEC-M1).
            format_type = args.get("format", "markdown")
            row_limit = _coerce_limit(args.get("limit"))
            return await _run_user_query(sql, format_type, "Execute query", row_limit)

        elif name == "list-snowflake-warehouses":
            include_details = args.get("include_details", True)
            result = await _execute("SHOW WAREHOUSES", "List warehouses", row_limit=MAX_QUERY_LIMIT)
            if result["success"]:
                if include_details:
                    output = json.dumps(result["data"], indent=2, default=str)
                else:
                    output = "\n".join(row.get("name", "") for row in result["data"])
                return _text(output)
            return _text(f"Snowflake error: {result['error']}")

        elif name == "list-databases":
            pattern = args.get("pattern")
            include_details = args.get("include_details", False)

            query = "SHOW DATABASES"
            if pattern:
                # Validate before interpolation: SHOW ... LIKE takes no binds (SEC-H1).
                query += f" LIKE '{validate_like_pattern(pattern, 'pattern')}'"

            result = await _execute(query, "List databases", row_limit=MAX_QUERY_LIMIT)
            if result["success"]:
                if include_details:
                    output = json.dumps(result["data"], indent=2, default=str)
                else:
                    output = "\n".join(row.get("name", "") for row in result["data"])
                return _text(output)
            return _text(f"Snowflake error: {result['error']}")

        elif name == "export-schema":
            database = args.get("database")
            format_type = args.get("format", "json")

            schema_data = {
                "exported_at": datetime.now().isoformat(),
                "server_info": SERVER_INFO,
            }

            if database:
                db_query = f"SHOW DATABASES LIKE '{validate_like_pattern(database, 'database')}'"
            else:
                db_query = "SHOW DATABASES"

            db_result = await _execute(
                db_query, "Export schema - databases", row_limit=MAX_QUERY_LIMIT
            )
            if not db_result["success"]:
                return _text(f"Error getting database info: {db_result['error']}")

            schema_data["databases"] = db_result["data"]

            if format_type == "yaml":
                output = yaml.dump(schema_data, default_flow_style=False)
            elif format_type == "sql":
                output = f"-- Schema export generated at {schema_data['exported_at']}\n"
                output += f"-- Server: {SERVER_INFO['name']} v{SERVER_INFO['version']}\n\n"
                for db in schema_data["databases"]:
                    output += f"-- Database: {db.get('name', 'Unknown')}\n"
            else:  # json
                output = json.dumps(schema_data, indent=2, default=str)

            return _text(output)

        else:
            raise ValueError(f"Unknown tool: {name}")

    except ValueError as e:
        # Our own validation messages are safe to surface verbatim.
        logger.info("Tool %s rejected input: %s", name, e)
        return _text(f"Invalid request: {e}")
    except Exception as e:
        ref = _log_exception(f"Tool execution ({name})", e)
        return _text(f"Tool execution error (ref {ref}). See server logs for details.")


async def test_snowflake_connection():
    """Test Snowflake connection for debugging purposes."""
    result = await _execute("SELECT CURRENT_TIMESTAMP()", "Connection test")
    if result["success"]:
        timestamp = result["data"][0] if result["data"] else "No data"
        logger.info("Snowflake connection OK, CURRENT_TIMESTAMP: %s", timestamp)
    else:
        logger.error(f"Snowflake connection error: {result['error']}")


async def main():
    """Main entry point for the MCP server."""
    logger.info(f"Starting {SERVER_INFO['name']} v{SERVER_INFO['version']}")
    logger.info(
        f"Configuration: Read-only mode: {READ_ONLY}, Log level: {CONFIG['logging']['level']}"
    )

    # Test connection on startup if configured
    if CONFIG["server"]["connection"]["test_on_startup"]:
        await test_snowflake_connection()

    # Get notification and experimental capabilities from config
    mcp_config = CONFIG["mcp"]
    notifications = mcp_config["notifications"]
    experimental = mcp_config["experimental_features"]

    # Build experimental capabilities dict
    experimental_caps = {}
    if experimental.get("resource_subscriptions", False):
        experimental_caps["resourceSubscriptions"] = True
    if experimental.get("completion_support", False):
        experimental_caps["completionSupport"] = True

    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_INFO["name"],
                server_version=SERVER_INFO["version"],
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(
                        resources_changed=notifications.get("resources_changed", True),
                        tools_changed=notifications.get("tools_changed", True),
                        prompts_changed=notifications.get("prompts_changed", True),
                    ),
                    experimental_capabilities=experimental_caps,
                ),
            ),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
