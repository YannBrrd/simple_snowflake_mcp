"""Shared test fixtures.

All tests mock ``snowflake.connector.connect``; no live Snowflake account is
contacted. The connection-mock contract lives here so both suites stay in sync.
"""

from unittest.mock import MagicMock

import pytest

from simple_snowflake_mcp import server


def build_connection(columns=None, rows=None, rowcount=0):
    """Build a MagicMock Snowflake connection returning the given result set."""
    conn = MagicMock()
    cur = MagicMock()
    cur.description = [(c,) for c in columns] if columns else None
    cur.fetchall.return_value = list(rows or [])
    cur.fetchmany.return_value = list(rows or [])
    cur.rowcount = rowcount
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cur
    cursor_cm.__exit__.return_value = False
    conn.cursor.return_value = cursor_cm
    return conn


@pytest.fixture
def make_connection():
    """Factory fixture returning :func:`build_connection`."""
    return build_connection


@pytest.fixture
def patch_connect(monkeypatch):
    """Return a helper that patches the connector to yield the given connection."""

    def _patch(conn):
        monkeypatch.setattr(server.snowflake.connector, "connect", lambda **kw: conn)

    return _patch


@pytest.fixture(autouse=True)
def reset_server_state(monkeypatch):
    """Isolate global server state and provide dummy credentials per test."""
    server._connection = None
    monkeypatch.setattr(server, "_RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(server, "READ_ONLY", True)
    server.notes.clear()
    server._completion_cache.clear()
    monkeypatch.setenv("SNOWFLAKE_USER", "u")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "p")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "a")
    yield
    server._connection = None
    server.notes.clear()
