# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/).

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
