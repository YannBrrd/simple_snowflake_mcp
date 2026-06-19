"""Tool-dispatch smoke tests using a mocked Snowflake connection.

Shared fixtures (make_connection, patch_connect, reset_server_state) live in
conftest.py. Security-focused unit tests live in test_security.py.
"""

import json

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

    async def fake_execute(query, description="Query", *, is_user_sql=False, row_limit=None):
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
