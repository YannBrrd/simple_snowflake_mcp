"""Tool-dispatch smoke tests using a mocked Snowflake connection.

Shared fixtures (make_connection, patch_connect, reset_server_state) live in
conftest.py. Security-focused unit tests live in test_security.py.
"""

import json
import logging

import mcp.types as types
import pytest
from pydantic import AnyUrl

from simple_snowflake_mcp import server


async def test_execute_query_returns_markdown(make_connection, patch_connect):
    patch_connect(make_connection(columns=["N"], rows=[("1",)]))

    result = await server.handle_call_tool("execute-query", {"sql": "SELECT 1 AS N"})

    assert result[0].text.startswith("| N |")


async def test_list_databases_names_only(make_connection, patch_connect):
    patch_connect(make_connection(columns=["name"], rows=[("DB1",), ("DB2",)]))

    result = await server.handle_call_tool("list-databases", {})

    assert "DB1" in result[0].text and "DB2" in result[0].text


async def test_unknown_tool_is_rejected(make_connection, patch_connect):
    patch_connect(make_connection())
    result = await server.handle_call_tool("does-not-exist", {})
    assert "Invalid request" in result[0].text


async def test_list_warehouses_names_only(make_connection, patch_connect):
    patch_connect(make_connection(columns=["name"], rows=[("WH1",), ("WH2",)]))

    result = await server.handle_call_tool("list-snowflake-warehouses", {"include_details": False})

    assert "WH1" in result[0].text and "WH2" in result[0].text


async def test_get_connection_info_reports_status(make_connection, patch_connect):
    patch_connect(make_connection(columns=["CURRENT_USER()"], rows=[("ALICE",)]))

    result = await server.handle_call_tool("get-connection-info", {})

    payload = json.loads(result[0].text)
    assert payload["connection_status"] == "connected"
    assert payload["read_only_mode"] is True


async def test_list_schemas_builds_scoped_query(monkeypatch):
    captured = {}

    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        captured["query"] = query
        return {"success": True, "data": [{"name": "PUBLIC"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_call_tool("list-schemas", {"database": "DB1"})

    assert captured["query"] == "SHOW SCHEMAS IN DATABASE DB1"
    assert "PUBLIC" in result[0].text


async def test_list_tables_and_views_scope_to_schema(monkeypatch):
    captured = []

    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        captured.append(query)
        return {"success": True, "data": [{"name": "T1"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    await server.handle_call_tool("list-tables", {"database": "DB1", "schema": "PUBLIC"})
    await server.handle_call_tool("list-views", {"database": "DB1", "schema": "PUBLIC"})

    assert captured == [
        "SHOW TABLES IN SCHEMA DB1.PUBLIC",
        "SHOW VIEWS IN SCHEMA DB1.PUBLIC",
    ]


async def test_describe_table_renders_columns(monkeypatch):
    captured = {}

    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        captured["query"] = query
        return {"success": True, "data": [{"name": "ID", "type": "NUMBER"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_call_tool(
        "describe-table", {"database": "DB1", "schema": "PUBLIC", "table": "CUSTOMERS"}
    )

    assert captured["query"] == "DESCRIBE TABLE DB1.PUBLIC.CUSTOMERS"
    assert json.loads(result[0].text) == [{"name": "ID", "type": "NUMBER"}]


async def test_query_view_routes_through_user_query_path(monkeypatch):
    captured = {}

    async def fake_execute(
        query, description="Query", *, is_user_sql=False, row_limit=None, offset=0
    ):
        captured["query"] = query
        captured["is_user_sql"] = is_user_sql
        return {"success": True, "data": [{"ID": 1}], "truncated": False, "row_limit": row_limit}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_call_tool(
        "query-view", {"database": "DB1", "schema": "PUBLIC", "view": "V1", "format": "json"}
    )

    assert captured["query"] == "SELECT * FROM DB1.PUBLIC.V1"
    assert captured["is_user_sql"] is True
    assert json.loads(result[0].text) == [{"ID": 1}]


async def test_discovery_tools_reject_identifier_injection(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("query should be rejected before execution")

    monkeypatch.setattr(server, "_execute", fail_if_called)

    result = await server.handle_call_tool(
        "describe-table",
        {"database": "DB1", "schema": "PUBLIC", "table": "T1; DROP TABLE X"},
    )

    assert "Invalid request" in result[0].text


async def test_discovery_tools_require_scope(make_connection, patch_connect):
    patch_connect(make_connection())

    missing_db = await server.handle_call_tool("list-schemas", {})
    missing_schema = await server.handle_call_tool("list-tables", {"database": "DB1"})

    assert "Invalid request" in missing_db[0].text
    assert "Invalid request" in missing_schema[0].text


async def test_completion_completes_prompt_argument():
    completion = await server.handle_completion(
        types.PromptReference(type="ref/prompt", name="summarize-notes"),
        types.CompletionArgument(name="style", value="d"),
        None,
    )

    assert completion.values == ["detailed"]


async def test_completion_completes_database_names(monkeypatch):
    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        assert query == "SHOW DATABASES"
        return {"success": True, "data": [{"name": "DB1"}, {"name": "PROD"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    completion = await server.handle_completion(
        types.ResourceTemplateReference(
            type="ref/resource", uri="snowflake://database/{database}/schemas"
        ),
        types.CompletionArgument(name="database", value="d"),
        None,
    )

    assert completion.values == ["DB1"]


async def test_completion_schema_without_database_context_returns_empty(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("should not query without database context")

    monkeypatch.setattr(server, "_execute", fail_if_called)

    completion = await server.handle_completion(
        types.ResourceTemplateReference(
            type="ref/resource", uri="snowflake://database/{database}/schema/{schema}/tables"
        ),
        types.CompletionArgument(name="schema", value=""),
        None,
    )

    assert completion.values == []


async def test_set_logging_level_adjusts_root_logger():
    root = logging.getLogger()
    original = root.level
    try:
        await server.handle_set_logging_level("error")
        assert root.level == logging.ERROR
    finally:
        root.setLevel(original)


async def test_list_resource_templates_exposes_browsable_uris():
    templates = await server.handle_list_resource_templates()
    uris = {t.uriTemplate for t in templates}
    assert "snowflake://database/{database}/schemas" in uris
    assert "snowflake://table/{database}/{schema}/{table}" in uris


async def test_read_resource_template_describes_table(monkeypatch):
    captured = {}

    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        captured["query"] = query
        return {"success": True, "data": [{"name": "ID", "type": "NUMBER"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    body = await server.handle_read_resource(AnyUrl("snowflake://table/DB1/PUBLIC/CUSTOMERS"))

    assert captured["query"] == "DESCRIBE TABLE DB1.PUBLIC.CUSTOMERS"
    assert json.loads(body)["data"] == [{"name": "ID", "type": "NUMBER"}]


async def test_read_resource_template_rejects_bad_identifier(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("query should be rejected before execution")

    monkeypatch.setattr(server, "_execute", fail_if_called)

    with pytest.raises(ValueError):
        await server.handle_read_resource(AnyUrl("snowflake://database/DB1.X/schemas"))


async def test_add_and_delete_note():
    created = await server.handle_call_tool("add-note", {"name": "n", "content": "c"})
    assert "created" in created[0].text
    deleted = await server.handle_call_tool("delete-note", {"name": "n"})
    assert "deleted" in deleted[0].text


async def test_list_notes_returns_sorted_names():
    await server.handle_call_tool("add-note", {"name": "b", "content": "2"})
    await server.handle_call_tool("add-note", {"name": "a", "content": "1"})

    result = await server.handle_call_tool("list-notes", {})

    assert json.loads(result[0].text) == ["a", "b"]


async def test_get_note_returns_content_and_handles_missing_note():
    await server.handle_call_tool("add-note", {"name": "n", "content": "c"})

    existing = await server.handle_call_tool("get-note", {"name": "n"})
    missing = await server.handle_call_tool("get-note", {"name": "missing"})

    assert json.loads(existing[0].text) == {"name": "n", "content": "c"}
    assert "not found" in missing[0].text.lower()


async def test_export_schema_includes_hierarchical_metadata_and_samples(monkeypatch):
    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        data_by_query = {
            "SHOW DATABASES": [{"name": "DB1"}],
            'SHOW SCHEMAS IN DATABASE "DB1"': [{"name": "PUBLIC"}],
            'SHOW TABLES IN SCHEMA "DB1"."PUBLIC"': [{"name": "CUSTOMERS"}],
            'SHOW VIEWS IN SCHEMA "DB1"."PUBLIC"': [{"name": "CUSTOMERS_VIEW"}],
            'DESCRIBE TABLE "DB1"."PUBLIC"."CUSTOMERS"': [{"name": "ID", "type": "NUMBER"}],
            'DESCRIBE VIEW "DB1"."PUBLIC"."CUSTOMERS_VIEW"': [{"name": "ID", "type": "NUMBER"}],
            'SELECT * FROM "DB1"."PUBLIC"."CUSTOMERS" LIMIT 3': [{"ID": 1}],
        }
        if query not in data_by_query:
            return {"success": False, "error": f"unexpected query: {query}", "data": None}
        if query.startswith("SELECT * FROM "):
            assert row_limit == 3
        return {"success": True, "data": data_by_query[query]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_call_tool(
        "export-schema", {"format": "json", "include_data_samples": True}
    )

    payload = json.loads(result[0].text)
    assert payload["include_data_samples"] is True
    assert payload["sample_row_limit"] == 3
    assert payload["databases"][0]["name"] == "DB1"
    schema = payload["databases"][0]["schemas"][0]
    assert schema["name"] == "PUBLIC"
    assert schema["tables"][0]["name"] == "CUSTOMERS"
    assert schema["tables"][0]["sample_data"] == [{"ID": 1}]
    assert schema["views"][0]["name"] == "CUSTOMERS_VIEW"


async def test_export_schema_omits_samples_by_default(monkeypatch):
    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        data_by_query = {
            "SHOW DATABASES": [{"name": "DB1"}],
            'SHOW SCHEMAS IN DATABASE "DB1"': [{"name": "PUBLIC"}],
            'SHOW TABLES IN SCHEMA "DB1"."PUBLIC"': [{"name": "CUSTOMERS"}],
            'SHOW VIEWS IN SCHEMA "DB1"."PUBLIC"': [],
            'DESCRIBE TABLE "DB1"."PUBLIC"."CUSTOMERS"': [{"name": "ID", "type": "NUMBER"}],
        }
        if query not in data_by_query:
            return {"success": False, "error": f"unexpected query: {query}", "data": None}
        return {"success": True, "data": data_by_query[query]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_call_tool("export-schema", {"format": "json"})
    payload = json.loads(result[0].text)
    assert payload["include_data_samples"] is False
    assert "sample_row_limit" not in payload
    assert "sample_data" not in payload["databases"][0]["schemas"][0]["tables"][0]


# ---------------------------------------------------------------------------
# execute-snowflake-sql output formats
# ---------------------------------------------------------------------------
async def test_execute_snowflake_sql_defaults_to_json(make_connection, patch_connect):
    patch_connect(make_connection(columns=["N"], rows=[(1,)]))

    result = await server.handle_call_tool("execute-snowflake-sql", {"sql": "SELECT 1 AS N"})

    assert json.loads(result[0].text) == [{"N": 1}]


async def test_execute_snowflake_sql_markdown_format(make_connection, patch_connect):
    patch_connect(make_connection(columns=["N"], rows=[(1,)]))

    result = await server.handle_call_tool(
        "execute-snowflake-sql", {"sql": "SELECT 1 AS N", "format": "markdown"}
    )

    assert result[0].text.startswith("| N |")


async def test_execute_snowflake_sql_csv_format(make_connection, patch_connect):
    patch_connect(make_connection(columns=["N"], rows=[(1,)]))

    result = await server.handle_call_tool(
        "execute-snowflake-sql", {"sql": "SELECT 1 AS N", "format": "csv"}
    )

    assert result[0].text.splitlines()[0] == "N"
    assert "1" in result[0].text.splitlines()[1]


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------
async def test_summarize_notes_prompt_includes_notes_and_style():
    await server.handle_call_tool("add-note", {"name": "todo", "content": "ship release"})

    result = await server.handle_get_prompt("summarize-notes", {"style": "executive"})

    text = result.messages[0].content.text
    assert "todo: ship release" in text
    assert "executive summary" in text.lower()


async def test_analyze_snowflake_schema_prompt_uses_live_databases(monkeypatch):
    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        assert query == "SHOW DATABASES"
        return {"success": True, "data": [{"name": "DB1"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_get_prompt("analyze-snowflake-schema", {"focus": "tables"})

    text = result.messages[0].content.text
    assert "DB1" in text
    assert "tables" in text


async def test_generate_sql_query_prompt_embeds_intent(monkeypatch):
    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        return {"success": True, "data": []}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_get_prompt(
        "generate-sql-query", {"intent": "top customers by revenue", "complexity": "advanced"}
    )

    text = result.messages[0].content.text
    assert "top customers by revenue" in text
    assert "advanced" in text


async def test_troubleshoot_connection_prompt_reports_status(monkeypatch):
    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
        return {"success": True, "data": [{"CURRENT_VERSION()": "8.0"}]}

    monkeypatch.setattr(server, "_execute", fake_execute)

    result = await server.handle_get_prompt(
        "troubleshoot-connection", {"error_message": "timeout on connect"}
    )

    text = result.messages[0].content.text
    assert "timeout on connect" in text
    assert "Connected successfully" in text


async def test_get_prompt_rejects_unknown_name():
    with pytest.raises(ValueError):
        await server.handle_get_prompt("does-not-exist", {})


# ---------------------------------------------------------------------------
# Resource subscriptions
# ---------------------------------------------------------------------------
async def test_subscribe_and_unsubscribe_resource():
    uri = AnyUrl("snowflake://schema/metadata")

    await server.handle_subscribe_resource(uri)
    assert str(uri) in server.resource_subscriptions

    await server.handle_unsubscribe_resource(uri)
    assert str(uri) not in server.resource_subscriptions


# ---------------------------------------------------------------------------
# Pagination (offset)
# ---------------------------------------------------------------------------
def _paging_connection(all_rows, columns):
    """A connection mock whose cursor consumes rows positionally across fetches.

    Unlike the shared make_connection fixture (which returns the same rows on
    every fetchmany), this honors fetchmany(size)/fetchall so offset paging can
    be exercised end to end.
    """
    from unittest.mock import MagicMock

    conn = MagicMock()
    cur = MagicMock()
    cur.description = [(c,) for c in columns]
    state = {"pos": 0}

    def fetchmany(size):
        start = state["pos"]
        chunk = all_rows[start : start + size]
        state["pos"] = start + len(chunk)
        return chunk

    def fetchall():
        start = state["pos"]
        state["pos"] = len(all_rows)
        return all_rows[start:]

    cur.fetchmany.side_effect = fetchmany
    cur.fetchall.side_effect = fetchall
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cur
    cursor_cm.__exit__.return_value = False
    conn.cursor.return_value = cursor_cm
    return conn


def test_coerce_offset_bounds_and_validates():
    assert server._coerce_offset(None) == 0
    assert server._coerce_offset("5") == 5
    assert server._coerce_offset(10**9) == server.MAX_QUERY_LIMIT
    with pytest.raises(ValueError):
        server._coerce_offset(-1)
    with pytest.raises(ValueError):
        server._coerce_offset(True)
    with pytest.raises(ValueError):
        server._coerce_offset("not-an-int")


async def test_execute_query_offset_pages_rows(patch_connect):
    patch_connect(_paging_connection([(i,) for i in range(10)], ["N"]))

    result = await server.handle_call_tool(
        "execute-query", {"sql": "SELECT N FROM t", "format": "json", "limit": 3, "offset": 3}
    )

    assert [row["N"] for row in json.loads(result[0].text)] == [3, 4, 5]


async def test_execute_query_truncation_note_points_to_next_page(patch_connect):
    patch_connect(_paging_connection([(i,) for i in range(10)], ["N"]))

    result = await server.handle_call_tool(
        "execute-query", {"sql": "SELECT N FROM t", "limit": 3, "offset": 3}
    )

    assert len(result) == 2
    assert "offset: 6" in result[1].text


async def test_query_view_passes_offset_through(monkeypatch):
    captured = {}

    async def fake_execute(
        query, description="Query", *, is_user_sql=False, row_limit=None, offset=0
    ):
        captured["offset"] = offset
        return {
            "success": True,
            "data": [],
            "truncated": False,
            "row_limit": row_limit,
            "offset": offset,
        }

    monkeypatch.setattr(server, "_execute", fake_execute)

    await server.handle_call_tool(
        "query-view", {"database": "D", "schema": "S", "view": "V", "limit": 5, "offset": 10}
    )

    assert captured["offset"] == 10


async def test_execute_query_rejects_negative_offset(make_connection, patch_connect):
    patch_connect(make_connection())

    result = await server.handle_call_tool("execute-query", {"sql": "SELECT 1", "offset": -1})

    assert "Invalid request" in result[0].text
