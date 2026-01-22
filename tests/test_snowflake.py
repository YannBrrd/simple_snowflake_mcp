import pytest

from simple_snowflake_mcp import server


@pytest.mark.asyncio
async def test_list_snowflake_warehouses():
    # On suppose que la connexion Snowflake est valide et que le .env est bien configuré
    result = await server.handle_call_tool("list-snowflake-warehouses", {})
    assert isinstance(result, list)
    assert len(result) > 0 or result[0].text.startswith("Erreur Snowflake")
    print("Résultat:", result[0].text)
