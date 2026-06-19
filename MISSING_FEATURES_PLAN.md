# Missing Features — Implementation Plan

Status: proposal · Target baseline: v0.3.0 · Owner: maintainers

This document inventories gaps between what `simple-snowflake-mcp` currently
ships and what users (and the MCP protocol) reasonably expect, then proposes a
prioritized, security-preserving plan to close them. Every proposal here must
respect the existing security invariants in `CLAUDE.md` — in particular, **all
Snowflake access must continue to funnel through `_safe_snowflake_execute`**,
and any tool running user SQL must route through `_run_user_query`.

## Where we are today

The server (`src/simple_snowflake_mcp/server.py`) implements **10 tools**,
**3 resource types**, and **4 prompts**:

- Tools: `get-connection-info`, `add-note`, `delete-note`, `list-notes`,
  `get-note`, `execute-snowflake-sql`, `execute-query`,
  `list-snowflake-warehouses`, `list-databases`, `export-schema`.
- Resources: notes (`note://internal/{name}`),
  `snowflake://schema/metadata`, `snowflake://status/connection`
  (with subscribe/unsubscribe).
- Prompts: `summarize-notes`, `analyze-snowflake-schema`,
  `generate-sql-query`, `troubleshoot-connection`.

Security posture is strong (single chokepoint, server-governed read-only,
input validation, rate limiting, statement timeout, connection reuse). The
gaps below are about **coverage and completeness**, not security regressions.

---

## Gap analysis

### A. Functional gaps — Snowflake discovery tools (highest user value)

`export-schema` can dump a whole schema tree, but there are **no granular,
cheap discovery tools**. These were documented in older READMEs and removed in
v0.3.0 because they were never implemented (see CHANGELOG). They are the most
commonly requested operations for an interactive client and are far lighter
than a full `export-schema`:

| Missing tool       | Purpose                                              |
|--------------------|------------------------------------------------------|
| `list-schemas`     | Schemas in a database (`SHOW SCHEMAS IN DATABASE`)   |
| `list-tables`      | Tables in a schema (`SHOW TABLES IN SCHEMA`)         |
| `list-views`       | Views in a schema (`SHOW VIEWS IN SCHEMA`)           |
| `describe-table`   | Columns/types for one object (`DESCRIBE TABLE`)      |
| `query-view`       | Read a view with the read-only guard + row limit     |

All are read-only `SHOW`/`DESCRIBE`/`SELECT` operations and slot naturally into
the existing chokepoint. Object identifiers must be validated (see Risks).

### B. MCP protocol gaps

1. **Completion support** — `config.yaml` exposes
   `mcp.experimental_features.completion_support` and the capability is
   advertised when enabled (server.py ~line 1742), but **there is no
   `completion/complete` handler**. Today flipping the flag advertises a
   capability the server can't service. Either implement argument completion
   (e.g. prompt arguments, database/schema names) or stop advertising it.
2. **MCP logging** — no `logging/setLevel` handler; clients cannot ask the
   server to emit MCP log notifications. Internal Python logging exists; the
   MCP-side bridge does not.
3. **Pagination / cursors** — large results are returned whole with a
   `truncated` flag; there is no `offset`/`cursor` to page through results.
4. **Resource templates** — resources are static URIs; no templated URIs
   (e.g. `snowflake://database/{db}/schema/{schema}`) for navigable browsing.

### C. Documentation gaps

1. **README** mentions prompts exist but **does not list the 4 prompts** or
   their arguments. Users can't discover them without reading source.
2. **CLAUDE.md** says "8 tools" but the server has **10** (it omits
   `list-notes` and `get-note`). Tool-set section needs updating.

### D. Test coverage gaps

`tests/` is security-heavy but has functional happy-path holes:

- No tests for `list-snowflake-warehouses`.
- No tests for `get-connection-info`.
- No format coverage (markdown/csv) for `execute-snowflake-sql`.
- No tests for prompts (`handle_list_prompts` / `handle_get_prompt`).
- No tests for resource subscribe/unsubscribe handlers.

---

## Proposed plan (phased)

### Phase 1 — Discovery tools (Gap A) — **highest priority**
Implement `list-schemas`, `list-tables`, `list-views`, `describe-table`,
`query-view`. For each:
- Add the JSON Schema entry in `handle_list_tools` and a handler in the
  `_TOOL_HANDLERS` registry.
- Route `query-view` through `_run_user_query`; route the `SHOW`/`DESCRIBE`
  tools through `_safe_snowflake_execute` (read-only internal queries).
- Validate every identifier (database/schema/table/view) with a strict
  allow-list validator — **never string-concatenate raw identifiers**. Add a
  helper analogous to `validate_like_pattern` (e.g. `validate_identifier`)
  or use parameterized `IDENTIFIER(...)` binding where the driver supports it.
- Add unit tests (mocked connector) per tool, including an injection case.
- Update README tool list.

### Phase 2 — Documentation & metadata sync (Gaps C)
- Document the 4 prompts and their arguments in README.
- Correct CLAUDE.md tool count/list to 10.
- Add a short "Prompts" and "Resources" section to README.
Cheap, no code risk; can land alongside Phase 1.

### Phase 3 — Test backfill (Gap D)
- Add functional tests for `list-snowflake-warehouses` and
  `get-connection-info`.
- Add format coverage for `execute-snowflake-sql`.
- Add prompt generation tests and resource subscribe/unsubscribe tests.

### Phase 4 — MCP protocol completeness (Gap B) — **largest scope**
- **Completion**: implement a `completion/complete` handler (prompt args +
  database/schema name completion via cached metadata), then keep the
  capability flag honest. If deferred, change the default so the capability is
  not advertised without a handler.
- **MCP logging**: add a `logging/setLevel` handler that maps to the existing
  logger and emits MCP log notifications.
- **Pagination**: add optional `offset`/`cursor` to row-returning tools,
  threaded through `_safe_snowflake_execute`'s `fetchmany` bounding.
- **Resource templates**: expose templated Snowflake URIs for browsing.

Phase 4 items are independent and can be picked up individually.

---

## Suggested sequencing & effort

| Phase | Scope                          | Effort | Risk  | Priority |
|-------|--------------------------------|--------|-------|----------|
| 1     | 5 discovery tools + tests      | M      | Med   | P0       |
| 2     | README/CLAUDE.md sync          | S      | Low   | P0       |
| 3     | Test backfill                  | S–M    | Low   | P1       |
| 4a    | Completion handler / honest cap| M      | Med   | P1       |
| 4b    | MCP logging                    | S      | Low   | P2       |
| 4c    | Pagination                     | M      | Med   | P2       |
| 4d    | Resource templates             | M      | Med   | P3       |

## Risks & invariants to preserve
- **Identifier injection.** The new discovery tools take object names; these
  must be validated/bound, not concatenated. This is the single biggest risk
  and gates Phase 1.
- **Read-only governance.** New SQL must go through the chokepoint; no tool may
  introduce its own connection or bypass `is_read_only_sql`.
- **Client-safe errors.** Continue the generic-message-plus-`ref` pattern; no
  raw `str(e)` to clients.
- **CI green.** `ruff check`, `ruff format --check`, and `pytest` must stay
  green; add tests with each new tool.
- **Capabilities must be honest.** Don't advertise an MCP capability without a
  working handler (applies to completion support today).
