import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.callers import find_callers, CallerRequest
from orchard.mcp.handlers.callees import find_callees


@pytest.fixture
def conn_with_calls(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sym_id, name in [("T1:s:A", "A"), ("T1:s:B", "B"), ("T1:s:C", "C")]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: 'swift.func', module: 'M', "
            f"target_id: 'T1', file_path: '', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )
    # B calls A, C calls A
    conn.execute(
        "MATCH (b:Symbol {id:'T1:s:B'}), (a:Symbol {id:'T1:s:A'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(a)"
    )
    conn.execute(
        "MATCH (c:Symbol {id:'T1:s:C'}), (a:Symbol {id:'T1:s:A'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(a)"
    )
    # A calls B (for callees test)
    conn.execute(
        "MATCH (a:Symbol {id:'T1:s:A'}), (b:Symbol {id:'T1:s:B'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_find_callers_returns_callers(conn_with_calls):
    req = CallerRequest(usr="s:A", target_id="T1", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    names = {item["name"] for item in resp.data}
    assert "B" in names
    assert "C" in names
    assert "A" not in names


def test_find_callees_returns_callees(conn_with_calls):
    req = CallerRequest(usr="s:A", target_id="T1", build_id="b1")
    resp = find_callees(conn_with_calls, req)
    names = {item["name"] for item in resp.data}
    assert "B" in names


def test_find_callers_none(conn_with_calls):
    req = CallerRequest(usr="s:C", target_id="T1", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    assert resp.data == []
