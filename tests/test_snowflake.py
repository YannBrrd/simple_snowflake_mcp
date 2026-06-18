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


async def test_add_and_delete_note():
    created = await server.handle_call_tool("add-note", {"name": "n", "content": "c"})
    assert "created" in created[0].text
    deleted = await server.handle_call_tool("delete-note", {"name": "n"})
    assert "deleted" in deleted[0].text


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
