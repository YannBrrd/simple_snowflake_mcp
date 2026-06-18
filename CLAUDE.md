# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A single-process MCP (Model Context Protocol) server that exposes Snowflake to MCP clients (Claude Desktop, VS Code) over stdio. Designed to run behind a corporate proxy. Python ≥3.10, packaged with `uv`/hatchling, published to PyPI as `simple-snowflake-mcp`.

## Commands

```bash
uv sync --all-extras        # Install all deps incl. dev (creates venv)
uv run simple-snowflake-mcp # Run the server (stdio)
uv run pytest               # Run tests
uv run pytest tests/test_snowflake.py::test_list_snowflake_warehouses  # Single test
uv run ruff check .         # Lint
uv run ruff format .        # Format
uv run mypy src             # Type check
uv build                    # Build distribution
```

Tests are async (`asyncio_mode = "auto"`) and **mock `snowflake.connector.connect`** — they run in CI with no credentials and never touch a live account. `tests/test_security.py` holds the security regression tests (one per audit finding); `tests/test_snowflake.py` holds tool-dispatch smoke tests.

CI: `.github/workflows/ci.yml` runs ruff + pytest on push/PR; `.github/workflows/workflow.yml` builds and publishes to PyPI on `v*` tags.

## Architecture

Essentially everything lives in `src/simple_snowflake_mcp/server.py` (~900 lines). `__init__.py` just calls `asyncio.run(server.main())`.

The server is built on the `mcp` library's `Server` object (module-global `server`). MCP capabilities are registered via decorators, all defined in `server.py`:
- `@server.list_tools()` / `@server.call_tool()` — the tools
- `@server.list_resources()` / `@server.read_resource()` / `@server.subscribe_resource()` / `@server.unsubscribe_resource()` — Snowflake resources + subscription notifications
- `@server.list_prompts()` / `@server.get_prompt()` — prompts

`handle_call_tool` is a single large `if/elif` dispatch on tool name. **All Snowflake access funnels through one chokepoint, `_safe_snowflake_execute(query, description, *, is_user_sql=False, row_limit=None)`** — this is deliberate and security-critical. It applies the read-only guard, executes via a reused connection (`_get_connection`/`_reset_connection`) under a `threading.Lock`, bounds rows via `fetchmany`, and returns `{"success", "data", "error"}` with client-safe (generic) error text while logging full detail. The two user SQL tools both route through `_run_user_query`, which calls the chokepoint with `is_user_sql=True`. Internal server queries (resources/prompts) call it with the default `is_user_sql=False`.

### Security invariants (do not regress — see `security-audit-report.pdf` and `tests/test_security.py`)
- **Read-only is server-governed only.** `READ_ONLY` derives from config + the `MCP_READ_ONLY` env var; there is **no** client `read_only` argument. Enforcement lives in `is_read_only_sql` (strips comments, rejects multi-statement and CTE-fronted DML) and is applied inside `_safe_snowflake_execute`, so no tool can skip it. Keyword filtering is defense-in-depth; the real boundary is a least-privilege Snowflake role (documented in README).
- **Never string-concatenate user input into SQL.** `LIKE` values go through `validate_like_pattern` (allow-list regex); `limit` goes through `_coerce_limit` (bounded int, applied at the driver).
- **Never return raw `str(e)` to the client.** Use the generic-message-plus-`ref` pattern; log detail via `_log_exception`.
- **Don't log SQL/args at INFO** — only a hash/length; full text is DEBUG-only.

### Configuration
1. **`config.yaml`** — loaded by `load_config()` at import into global `CONFIG`, deep-merged onto `DEFAULT_CONFIG` and run through `_validate_config` (coerces security-sensitive types/bounds). The path comes from `_resolve_config_path`, which constrains `CONFIG_FILE` to `REPO_ROOT` (anti-traversal). Controls logging, query limits, statement timeout, connection reuse, rate limiting, notes bounds, and MCP capabilities.
2. **`.env`** — Snowflake credentials read **lazily** at connect time by `get_snowflake_config()` (not at import), so container/test env injection works. `SNOWFLAKE_USER`/`PASSWORD`/`ACCOUNT` required; `WAREHOUSE`/`DATABASE`/`SCHEMA` optional. Missing required vars raise `SnowflakeConfigError`. Use `safe_config_echo()` (never the raw config) when returning connection info — it excludes the password.

Precedence: env vars override `config.yaml` override `DEFAULT_CONFIG`.

### Tool set
The code implements 8 tools in `handle_list_tools`/`handle_call_tool`: `get-connection-info`, `add-note`, `delete-note`, `execute-snowflake-sql`, `list-snowflake-warehouses`, `list-databases`, `execute-query`, `export-schema`. (The README's tool list now matches.) When adding a tool, update both the schema in `handle_list_tools` and the dispatch branch — and if it runs user SQL, route it through `_run_user_query` so the guard applies.

## Conventions

- Comments and some log messages are in French; UI/tool text is mixed French/English. Match the surrounding language.
- Ruff: line length 100, target py310, rules `E,F,I,N,W,UP`. CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, and `pytest` on push/PR — keep all three green.
- Errors are returned to the client as `TextContent` strings via the `_text(...)` helper, not raised — preserve this so the MCP client gets a readable message rather than a transport failure.
- Tests mock `snowflake.connector.connect`; they never hit a live account. Patch `server.READ_ONLY`/`server._RATE_LIMIT_ENABLED` and reset `server._connection` in fixtures (see `tests/`).
