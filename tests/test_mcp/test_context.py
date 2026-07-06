"""Integration tests for orchard_context handler."""

import pytest
from orchard.handlers.context import ContextRequest, get_context
from orchard.graph.db import get_connection
from orchard.normalize.identity import make_symbol_id


@pytest.fixture
def conn():
    """Open a read-only connection to the test database."""
    import os
    db_path = os.environ.get("ORCHARD_TEST_DB", "")
    if not db_path:
        pytest.skip("ORCHARD_TEST_DB not set")
    c = get_connection(db_path, read_only=True)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# USR-based lookup
# ---------------------------------------------------------------------------

def test_context_missing_both_usr_and_name(conn):
    """Neither usr nor name → not_found."""
    req = ContextRequest(usr="", name="")
    resp = get_context(conn, req)
    assert resp.data["status"] == "not_found"


def test_context_not_found_by_usr(conn):
    """Non-existent USR → not_found with open_gaps guidance."""
    req = ContextRequest(usr="c:objc(cs)nonexistent12345")
    resp = get_context(conn, req)
    assert resp.data["status"] == "not_found"
    assert len(resp.open_gaps) > 0
    assert any("orchard_search" in g for g in resp.open_gaps)


def test_context_by_name_not_found(conn):
    """Non-existent name → not_found."""
    req = ContextRequest(name="ZZZZNonExistentSymbolName999")
    resp = get_context(conn, req)
    assert resp.data["status"] == "not_found"


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

def test_context_response_has_freshness(conn):
    """Response includes freshness field."""
    req = ContextRequest(usr="")
    resp = get_context(conn, req)
    assert resp.freshness is not None


def test_context_not_found_has_open_gaps(conn):
    """not_found response includes actionable open_gaps."""
    req = ContextRequest(usr="c:objc(cs)nonexistent12345")
    resp = get_context(conn, req)
    assert len(resp.open_gaps) > 0
