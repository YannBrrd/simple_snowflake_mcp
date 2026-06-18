"""Tool-dispatch smoke tests using a mocked Snowflake connection.

Shared fixtures (make_connection, patch_connect, reset_server_state) live in
conftest.py. Security-focused unit tests live in test_security.py.
"""

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
