# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-06-19

Feature release: granular discovery tools and fuller MCP protocol coverage.

### Added
- Five Snowflake discovery tools: `list-schemas`, `list-tables`, `list-views`,
  `describe-table`, and `query-view`. These provide lightweight, granular
  navigation of the object hierarchy without a full `export-schema`. Client-
  supplied object names are constrained by a new `validate_identifier`
  allow-list so the interpolated SQL stays injection-safe, and `query-view`
  routes through the read-only chokepoint like the other user-SQL tools.
- Tests for the new discovery tools (including identifier-injection rejection)
  plus backfilled coverage for `list-snowflake-warehouses` and
  `get-connection-info`.
- MCP completion support: a `completion/complete` handler that suggests
  enumerated prompt-argument values and live database/schema/table names for
  the resource templates. The capability is now advertised from the real
  handler rather than an inert experimental flag.
- MCP logging support: a `logging/setLevel` handler so clients can adjust the
  server's log verbosity at runtime.
- Resource templates for browsing the object hierarchy by URI
  (`snowflake://database/{database}/schemas`,
  `snowflake://database/{database}/schema/{schema}/tables`,
  `snowflake://table/{database}/{schema}/{table}`), resolved through the same
  validated, injection-safe identifier path as the discovery tools.
- Offset-based pagination on `execute-query` and `query-view` via a bounded
  `offset` argument. Rows are skipped at the driver (the SQL text is never
  rewritten), and a truncated result reports the `offset` for the next page.

### Documentation
- README now lists the five discovery tools, documents the four MCP prompts
  (`summarize-notes`, `analyze-snowflake-schema`, `generate-sql-query`,
  `troubleshoot-connection`) and their arguments, and reflects the new
  completion/logging/resource-template capabilities.
- Corrected the tool inventory in `CLAUDE.md` (now 15 tools).

## [0.3.0]

Security-hardening and reliability release.

### Breaking
- Removed the client-supplied `read_only` argument on `execute-query`. Read-only
  mode is now governed **solely** by server configuration (`snowflake.read_only`)
  and the `MCP_READ_ONLY` environment variable; it can no longer be relaxed by the
  caller.
- `execute-snowflake-sql` is now subject to the read-only guard (it was previously
  unguarded). While the server is in read-only mode, write/DDL statements are
  rejected.

### Added
- **Read-only SQL guard** at a single execution chokepoint: comments are stripped,
  multi-statement input and CTE-fronted DML (e.g. `WITH ... DELETE`) are rejected,
  and Snowflake backslash-escaped quotes are handled so valid reads are not
  false-rejected.
- **Input validation:** `pattern` / `database` arguments are checked against a
  strict allow-list before being placed into `LIKE` clauses; `limit` is coerced to
  a bounded integer and applied at the driver via `fetchmany`, never concatenated
  into SQL.
- **Result truncation is reported:** row-producing reads without an explicit
  `LIMIT` are capped at `default_query_limit`, and a capped result returns an
  explicit truncation notice instead of silently dropping rows.
- **In-process rate limiting** across all tool calls (`security.rate_limit`).
- **Server-side statement timeout** (`snowflake.statement_timeout_seconds`) and
  **connection reuse** (`snowflake.connection_reuse`) with automatic reconnect on
  a stale connection.
- **Bounded in-memory notes store** (`security.notes.max_count` /
  `max_content_length`).
- **Config validation and path-traversal protection:** `config.yaml` values are
  coerced to safe types/bounds, and `CONFIG_FILE` is constrained to the repository
  root.
- `LOG_LEVEL` environment variable override for logging.
- `config.yaml` is now copied into the Docker images.

### Changed
- Blocking Snowflake I/O is offloaded to a worker thread so a slow query no longer
  blocks the asyncio event loop or the rate limiter.
- Snowflake errors are no longer returned verbatim to clients: a generic message
  with a server-side reference id is returned and full detail is logged.
- SQL is no longer logged at `INFO` (only a length + hash); full text is `DEBUG`-only.
- Documentation and in-code comments are now entirely in English.

### Fixed
- Corrected the README tool list, which previously documented several tools
  (`query-view`, `list-schemas`, `describe-table`, etc.) that were never
  implemented.

## [0.2.0]

- YAML-based configuration system, resource subscriptions, and expanded MCP
  protocol support.
