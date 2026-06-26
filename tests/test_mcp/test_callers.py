import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.handlers.callers import find_callers, CallerRequest
from orchard.handlers.callees import find_callees, CalleeRequest


@pytest.fixture
def conn_with_calls(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sym_id, name in [("s:A", "A"), ("s:B", "B"), ("s:C", "C")]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: 'swift.func', module: 'M', "
            f"target_id: 'T1', file_path: '/src/{name}.swift', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )
    conn.execute(
        "CREATE (:File {path: '/src/B.swift', module: 'M', language: 'swift', target_id: 'T1', is_generated: false})"
        "-[:ContainsOccurrence]->"
        "(:Occurrence {id: 'occ-b', usr: 's:B', file_path: '/src/B.swift', line: 17, col: 3, role: 'definition'})"
    )
    # B calls A, C calls A
    conn.execute(
        "MATCH (b:Symbol {id:'s:B'}), (a:Symbol {id:'s:A'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(a)"
    )
    conn.execute(
        "MATCH (c:Symbol {id:'s:C'}), (a:Symbol {id:'s:A'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(a)"
    )
    # A calls B (for callees test)
    conn.execute(
        "MATCH (a:Symbol {id:'s:A'}), (b:Symbol {id:'s:B'}) "
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
    caller_b = next(item for item in resp.data if item["name"] == "B")
    assert caller_b["file_path"] == "/src/B.swift"
    assert caller_b["line"] == 17
    assert caller_b["col"] == 3
    assert caller_b["reason"] == "indexstore_relation_only"


def test_find_callees_returns_callees(conn_with_calls):
    req = CalleeRequest(usr="s:A", target_id="T1", build_id="b1")
    resp = find_callees(conn_with_calls, req)
    names = {item["name"] for item in resp.data}
    assert "B" in names
    callee_b = next(item for item in resp.data if item["name"] == "B")
    assert callee_b["reason"] == "indexstore_relation_only"


def test_find_callers_prefers_source_direct_when_available(conn_with_calls):
    conn_with_calls.execute(
        "MATCH (b:Symbol {id:'s:B'}), (a:Symbol {id:'s:A'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', build_id:'b1', "
        "reason:'source_direct'}]->(a)"
    )
    req = CallerRequest(usr='s:A', target_id='T1', build_id='b1')
    resp = find_callers(conn_with_calls, req)
    assert [item["name"] for item in resp.data] == ["B"]
    assert resp.data[0]["reason"] == "source_direct"


def test_find_callees_prefers_source_direct_when_available(conn_with_calls):
    conn_with_calls.execute(
        "MATCH (a:Symbol {id:'s:A'}), (b:Symbol {id:'s:B'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', build_id:'b1', "
        "reason:'source_direct'}]->(b)"
    )
    req = CalleeRequest(usr='s:A', target_id='T1', build_id='b1')
    resp = find_callees(conn_with_calls, req)
    assert [item["name"] for item in resp.data] == ["B"]
    assert resp.data[0]["reason"] == "source_direct"


def test_find_callers_none(conn_with_calls):
    req = CallerRequest(usr="s:C", target_id="T1", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    assert resp.data == []
