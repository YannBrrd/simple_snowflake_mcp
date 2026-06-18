"""Security regression tests for the remediation of the audit findings.

Each test references the audit finding it guards against. The Snowflake connector
is mocked throughout; no live account is contacted.
"""

from unittest.mock import MagicMock

import pytest

from simple_snowflake_mcp import server


# ---------------------------------------------------------------------------
# Read-only keyword guard (SEC-C1, SEC-C2, CQ-H1)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  select * from t",
        "SHOW DATABASES",
        "DESCRIBE TABLE t",
        "EXPLAIN SELECT 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "/* comment */ SELECT 1",
        "-- lead comment\nSELECT 1",
        # Comment characters inside string literals are data, not comments.
        "SELECT '/*' AS a, b FROM t WHERE c = '*/ ; x'",
        "SELECT 'a -- b' AS note FROM t",
        # Semicolon inside a (tagged) dollar-quoted body is data, not a separator.
        "SELECT $body$a;b$body$ AS x",
        "SELECT $$a;b$$ AS x",
    ],
)
def test_read_only_accepts_reads(sql):
    assert server.is_read_only_sql(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM t",
        "DROP TABLE t",
        "UPDATE t SET a = 1",
        "INSERT INTO t VALUES (1)",
        "GRANT ROLE ACCOUNTADMIN TO USER x",
        "CREATE TABLE t (a int)",
        "TRUNCATE TABLE t",
        # CTE-fronted DML must be rejected even though it starts with WITH.
        "WITH x AS (SELECT 1) DELETE FROM t WHERE id IN (SELECT id FROM x)",
        # Multi-statement input must be rejected.
        "SELECT 1; DROP TABLE t",
        # Comment cannot smuggle a write past the first-keyword check.
        "/* SELECT */ DELETE FROM t",
        # A real semicolon outside any literal makes this multi-statement; the
        # leading string literal must not hide the trailing DROP via the '--'.
        "SELECT 'a -- '; DROP TABLE t",
        "",
        "   ",
    ],
)
def test_read_only_rejects_writes(sql):
    assert server.is_read_only_sql(sql) is False


# ---------------------------------------------------------------------------
# Identifier validation for LIKE clauses (SEC-H1, CQ-C2)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["PROD_DB", "PROD_%", "abc123", "%_DEV"])
def test_valid_like_patterns(value):
    assert server.validate_like_pattern(value, "pattern") == value


@pytest.mark.parametrize(
    "value",
    ["'; DROP TABLE t --", "a' OR '1'='1", "a b", "a;b", "a'b", "../x", "a|b"],
)
def test_invalid_like_patterns_raise(value):
    with pytest.raises(ValueError):
        server.validate_like_pattern(value, "pattern")


# ---------------------------------------------------------------------------
# Limit coercion (SEC-H2)
# ---------------------------------------------------------------------------
def test_limit_clamped_to_max(monkeypatch):
    monkeypatch.setattr(server, "MAX_QUERY_LIMIT", 50)
    assert server._coerce_limit(10_000) == 50


def test_limit_string_is_coerced(monkeypatch):
    monkeypatch.setattr(server, "MAX_QUERY_LIMIT", 50000)
    assert server._coerce_limit("25") == 25


def test_limit_invalid_raises():
    with pytest.raises(ValueError):
        server._coerce_limit("1 UNION SELECT password")


def test_limit_bool_rejected():
    # bool is a subclass of int; True must not silently become a limit of 1.
    with pytest.raises(ValueError):
        server._coerce_limit(True)
    with pytest.raises(ValueError):
        server._coerce_limit(False)


def test_default_limit_applied_to_bare_select(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_QUERY_LIMIT", 1000)
    # No explicit client limit means the chokepoint applies the default cap.
    assert server._coerce_limit(None) is None
    assert server._default_row_cap("SELECT * FROM t") == 1000
    # A query that already constrains itself is left alone.
    assert server._default_row_cap("SELECT * FROM t LIMIT 5") is None
    assert server._default_row_cap("SHOW DATABASES") is None


# ---------------------------------------------------------------------------
# Markdown rendering safety (CQ-L2)
# ---------------------------------------------------------------------------
def test_markdown_escapes_pipes_and_nulls():
    table = server._format_markdown_table([{"a": "x|y", "b": None}])
    lines = table.splitlines()
    assert "x\\|y" in lines[2]
    # None renders as an empty cell, not the string "None".
    assert "None" not in table


# ---------------------------------------------------------------------------
# Lazy config loading (CQ-C3)
# ---------------------------------------------------------------------------
def test_get_snowflake_config_requires_credentials(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    with pytest.raises(server.SnowflakeConfigError):
        server.get_snowflake_config()


def test_get_snowflake_config_reads_env_lazily(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_USER", "alice")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    cfg = server.get_snowflake_config()
    assert cfg["user"] == "alice"
    # Statement timeout is applied as a session parameter.
    assert "STATEMENT_TIMEOUT_IN_SECONDS" in cfg["session_parameters"]


def test_safe_config_echo_excludes_password(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_USER", "alice")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    echo = server.safe_config_echo()
    assert "password" not in echo
    assert "secret" not in echo.values()


# ---------------------------------------------------------------------------
# Config validation & path traversal (SEC-M4)
# ---------------------------------------------------------------------------
def test_config_path_traversal_is_blocked(monkeypatch):
    monkeypatch.setenv("CONFIG_FILE", "../../../../etc/passwd")
    resolved = server._resolve_config_path()
    assert resolved.is_relative_to(server.REPO_ROOT)
    assert resolved.name == "config.yaml"


def test_log_path_traversal_is_blocked():
    resolved = server._resolve_log_file_path("../../../../etc/passwd")
    expected = (server.REPO_ROOT / "logs" / "server.log").resolve()
    assert resolved == expected
    assert resolved.is_relative_to((server.REPO_ROOT / "logs").resolve())


def test_log_path_escape_via_absolute_path_is_blocked():
    resolved = server._resolve_log_file_path("/tmp/outside.log")
    expected = (server.REPO_ROOT / "logs" / "server.log").resolve()
    assert resolved == expected


def test_log_path_inside_logs_is_allowed():
    resolved = server._resolve_log_file_path("logs/custom/server.log")
    assert resolved == (server.REPO_ROOT / "logs" / "custom" / "server.log").resolve()


def test_validate_config_coerces_types():
    cfg = server._validate_config({"snowflake": {"read_only": "false", "max_query_limit": "abc"}})
    # A string "false" must become a real bool, not stay truthy.
    assert cfg["snowflake"]["read_only"] is False
    # A non-integer limit falls back to the validated default.
    assert isinstance(cfg["snowflake"]["max_query_limit"], int)


# ---------------------------------------------------------------------------
# Rate limiter (SEC-M3)
# ---------------------------------------------------------------------------
def test_rate_limiter_blocks_after_max():
    limiter = server._RateLimiter(max_calls=2, window_seconds=60)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False


# ---------------------------------------------------------------------------
# Read-only guard must NOT false-reject legitimate reads (regression)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        # Semicolon inside a string literal (e.g. LISTAGG separator).
        "SELECT LISTAGG(name, ';') FROM t",
        "SELECT * FROM t WHERE x = 'a;b'",
        # Write-token words used as functions inside a CTE.
        "WITH c AS (SELECT REPLACE(a, 'x', 'y') AS r FROM t) SELECT * FROM c",
        "WITH c AS (SELECT GET(v, 'k') AS g FROM t) SELECT * FROM c",
        # Keyword-like word inside a quoted identifier.
        'WITH c AS (SELECT 1 AS "DELETE") SELECT * FROM c',
    ],
)
def test_read_only_does_not_false_reject(sql):
    assert server.is_read_only_sql(sql) is True


# ---------------------------------------------------------------------------
# Default row cap must cover CTE reads and tolerate "LIMIT"-substring names
# ---------------------------------------------------------------------------
def test_default_limit_covers_cte_reads(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_QUERY_LIMIT", 1000)
    assert server._default_row_cap("WITH x AS (SELECT * FROM t) SELECT * FROM x") == 1000


def test_default_limit_not_suppressed_by_limit_substring(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_QUERY_LIMIT", 1000)
    # "delimiter_col" contains the substring LIMIT but no LIMIT clause.
    assert server._default_row_cap("SELECT delimiter_col FROM t") == 1000


def test_valid_dollar_identifier_pattern():
    assert server.validate_like_pattern("SALES$2024", "database") == "SALES$2024"


# ---------------------------------------------------------------------------
# End-to-end enforcement through the tool dispatch
# ---------------------------------------------------------------------------
async def test_execute_snowflake_sql_enforces_read_only(monkeypatch):
    """SEC-C1: the previously-unguarded tool must now reject writes."""
    connect = MagicMock(side_effect=AssertionError("must not connect for a rejected write"))
    monkeypatch.setattr(server.snowflake.connector, "connect", connect)

    result = await server.handle_call_tool(
        "execute-snowflake-sql", {"sql": "DROP TABLE PROD.PUBLIC.CUSTOMERS"}
    )

    assert "read-only" in result[0].text.lower()
    connect.assert_not_called()


async def test_execute_query_ignores_client_read_only_flag(monkeypatch):
    """SEC-C2/SEC-M1: a client-supplied read_only:false must not relax the policy."""
    connect = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(server.snowflake.connector, "connect", connect)

    result = await server.handle_call_tool(
        "execute-query", {"sql": "DELETE FROM t", "read_only": False}
    )

    assert "read-only" in result[0].text.lower()
    connect.assert_not_called()


async def test_cte_fronted_dml_rejected(monkeypatch):
    connect = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(server.snowflake.connector, "connect", connect)

    result = await server.handle_call_tool(
        "execute-query",
        {"sql": "WITH x AS (SELECT 1) DELETE FROM prod.public.orders"},
    )

    assert "read-only" in result[0].text.lower()


async def test_sql_injection_pattern_rejected(make_connection, patch_connect):
    patch_connect(make_connection())
    result = await server.handle_call_tool("list-databases", {"pattern": "x'; DROP TABLE t --"})
    assert "Invalid request" in result[0].text


async def test_db_error_is_not_leaked_to_client(monkeypatch):
    """SEC-H3: raw Snowflake error text must not reach the client."""
    secret = "account=topsecret123 table=PROD.SSN"
    monkeypatch.setattr(
        server.snowflake.connector,
        "connect",
        MagicMock(side_effect=Exception(secret)),
    )

    result = await server.handle_call_tool("execute-query", {"sql": "SELECT 1"})

    text = result[0].text
    assert "topsecret123" not in text
    assert "ref" in text.lower()


async def test_note_count_is_capped(monkeypatch):
    """SEC-L2: the in-memory notes store must be bounded."""
    monkeypatch.setattr(server, "_NOTES_MAX_COUNT", 2)
    monkeypatch.setattr(server, "_NOTES_MAX_LENGTH", 10000)
    server.notes.clear()

    await server.handle_call_tool("add-note", {"name": "a", "content": "1"})
    await server.handle_call_tool("add-note", {"name": "b", "content": "1"})
    result = await server.handle_call_tool("add-note", {"name": "c", "content": "1"})

    assert "limit reached" in result[0].text.lower()
    server.notes.clear()


# ---------------------------------------------------------------------------
# Connection liveness: a stale reused connection is retried once
# ---------------------------------------------------------------------------
async def test_stale_connection_reconnects_and_retries(make_connection, monkeypatch):
    import snowflake.connector.errors as sferr

    # Simulate a connection cached from a prior call that has since gone stale.
    stale = make_connection()
    stale.cursor.return_value.__enter__.return_value.execute.side_effect = sferr.OperationalError(
        "network gone"
    )
    server._connection = stale

    fresh = make_connection(columns=["N"], rows=[("1",)])
    monkeypatch.setattr(server.snowflake.connector, "connect", lambda **kw: fresh)

    result = await server.handle_call_tool("execute-query", {"sql": "SELECT 1 AS N"})

    assert result[0].text.startswith("| N |")
    stale.close.assert_called()  # the stale connection was discarded


async def test_fresh_connection_error_is_not_retried(monkeypatch):
    """A brand-new connection failing must NOT loop; surfaces a generic error."""
    import snowflake.connector.errors as sferr

    server._connection = None  # no cached connection -> first use is fresh
    calls = {"n": 0}

    def _connect(**kw):
        calls["n"] += 1
        raise sferr.OperationalError("cannot reach host")

    monkeypatch.setattr(server.snowflake.connector, "connect", _connect)

    result = await server.handle_call_tool("execute-query", {"sql": "SELECT 1"})

    assert "error" in result[0].text.lower()
    assert calls["n"] == 1  # connected once, no retry storm


# ---------------------------------------------------------------------------
# Config validation: malformed server.connection must not crash (SEC-M4)
# ---------------------------------------------------------------------------
def test_validate_config_normalizes_malformed_server_connection():
    cfg = server._validate_config({"server": {"connection": None}})
    assert isinstance(cfg["server"]["connection"], dict)
    assert cfg["server"]["connection"]["timeout"] == 30
    assert cfg["server"]["connection"]["test_on_startup"] is True


def test_validate_config_tolerates_scalar_subtrees():
    cfg = server._validate_config({"server": "oops", "snowflake": None})
    assert isinstance(cfg["server"]["connection"], dict)
    assert cfg["snowflake"]["read_only"] is True


# ---------------------------------------------------------------------------
# execute-snowflake-sql applies the same default row cap as execute-query
# ---------------------------------------------------------------------------
async def test_execute_snowflake_sql_applies_default_cap(
    make_connection, patch_connect, monkeypatch
):
    monkeypatch.setattr(server, "DEFAULT_QUERY_LIMIT", 1000)
    conn = make_connection(columns=["N"], rows=[("1",)])
    patch_connect(conn)

    await server.handle_call_tool("execute-snowflake-sql", {"sql": "SELECT * FROM big"})

    cur = conn.cursor.return_value.__enter__.return_value
    # One extra row is fetched so truncation can be detected (then trimmed back).
    cur.fetchmany.assert_called_once_with(1001)


# ---------------------------------------------------------------------------
# Capped results are reported, never silently dropped
# ---------------------------------------------------------------------------
async def test_truncation_is_reported_and_rows_trimmed(make_connection, patch_connect, monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_QUERY_LIMIT", 2)
    # The mock returns 3 rows regardless of the fetchmany size, simulating a
    # result larger than the cap (the chokepoint fetches cap+1 to detect this).
    conn = make_connection(columns=["N"], rows=[("1",), ("2",), ("3",)])
    patch_connect(conn)

    result = await server.handle_call_tool(
        "execute-query", {"sql": "SELECT * FROM big", "format": "json"}
    )

    import json as _json

    data = _json.loads(result[0].text)
    assert len(data) == 2  # trimmed back to the cap
    # A second content block warns that the result was truncated.
    assert any("truncated" in c.text.lower() for c in result[1:])


async def test_no_truncation_notice_when_within_cap(make_connection, patch_connect, monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_QUERY_LIMIT", 1000)
    conn = make_connection(columns=["N"], rows=[("1",)])
    patch_connect(conn)

    result = await server.handle_call_tool("execute-query", {"sql": "SELECT 1 AS N"})

    assert all("truncated" not in c.text.lower() for c in result)
