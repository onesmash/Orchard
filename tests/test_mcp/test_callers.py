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
    req = CallerRequest(usr="s:A", build_id="b1")
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
    req = CalleeRequest(usr="s:A", build_id="b1")
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
    req = CallerRequest(usr='s:A', build_id='b1')
    resp = find_callers(conn_with_calls, req)
    assert [item["name"] for item in resp.data] == ["B"]
    assert resp.data[0]["reason"] == "source_direct"


def test_find_callees_prefers_source_direct_when_available(conn_with_calls):
    conn_with_calls.execute(
        "MATCH (a:Symbol {id:'s:A'}), (b:Symbol {id:'s:B'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', build_id:'b1', "
        "reason:'source_direct'}]->(b)"
    )
    req = CalleeRequest(usr='s:A', build_id='b1')
    resp = find_callees(conn_with_calls, req)
    assert [item["name"] for item in resp.data] == ["B"]
    assert resp.data[0]["reason"] == "source_direct"


def test_find_callers_none(conn_with_calls):
    req = CallerRequest(usr="s:C", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    assert resp.data == []


# ── AC-1: reason_to_confidence 映射 ──────────────────────────────
def test_reason_to_confidence_maps_source_direct():
    from orchard.handlers.base import reason_to_confidence
    assert reason_to_confidence("source_direct") == "compiler-verified"


def test_reason_to_confidence_maps_indexstore_relation_only():
    from orchard.handlers.base import reason_to_confidence
    assert reason_to_confidence("indexstore_relation_only") == "inferred"


def test_reason_to_confidence_maps_none_to_compiler_verified():
    from orchard.handlers.base import reason_to_confidence
    assert reason_to_confidence(None) == "compiler-verified"


def test_reason_to_confidence_maps_unknown_to_compiler_verified():
    from orchard.handlers.base import reason_to_confidence
    assert reason_to_confidence("some_unknown_reason") == "compiler-verified"


# ── AC-2: caller/callee 响应包含 confidence 字段 ─────────────────
def test_find_callers_includes_confidence_field(conn_with_calls):
    """AC-2.1: Each caller result has a confidence label derived from reason."""
    req = CallerRequest(usr="s:A", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    for item in resp.data:
        assert "confidence" in item, f"caller {item['name']} missing confidence"
        assert "provenance" in item, f"caller {item['name']} missing provenance"
        assert item["confidence"] in ("compiler-verified", "inferred")


def test_find_callees_includes_confidence_field(conn_with_calls):
    """AC-2.2: Each callee result has a confidence label derived from reason."""
    req = CalleeRequest(usr="s:A", build_id="b1")
    resp = find_callees(conn_with_calls, req)
    for item in resp.data:
        assert "confidence" in item, f"callee {item['name']} missing confidence"
        assert "provenance" in item, f"callee {item['name']} missing provenance"
        assert item["confidence"] in ("compiler-verified", "inferred")


def test_find_callers_source_direct_becomes_compiler_verified(conn_with_calls):
    """AC-2.3: source_direct reason → 'compiler-verified' confidence."""
    conn_with_calls.execute(
        "MATCH (b:Symbol {id:'s:B'}), (a:Symbol {id:'s:A'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(a)"
    )
    req = CallerRequest(usr="s:A", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    caller_b = next(item for item in resp.data if item["name"] == "B")
    assert caller_b["confidence"] == "compiler-verified"
    assert caller_b["provenance"] == "source_direct"


# ── AC-3: semantic_role in callees ─────────────────────────────────
def test_find_callees_includes_semantic_role_for_objc(conn_with_calls):
    """AC-3: ObjC callees get semantic_role; Swift callees don't."""
    conn_with_calls.execute(
        "CREATE (:Symbol {id: 's:addObs', usr: 's:addObs', precise_id: '', "
        "name: 'addObserver:selector:name:object:', language: 'objc', "
        "kind: 'objc.method', module: 'M', target_id: 'T1', "
        "file_path: '/src/a.mm', signature: '', container_usr: '', "
        "access_level: 'internal', origin: 'derived', is_generated: false})"
    )
    conn_with_calls.execute(
        "MATCH (a:Symbol {id:'s:A'}), (nc:Symbol {id:'s:addObs'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(nc)"
    )
    # Use include_inferred=True so NULL-reason edges aren't filtered out
    # alongside the new source_direct edge (existing global filter quirk).
    req = CalleeRequest(usr="s:A", build_id="b1",
                        include_inferred=True,
                        relation_types=["Calls"])
    resp = find_callees(conn_with_calls, req)

    observer = next(item for item in resp.data if item["name"] == "addObserver:selector:name:object:")
    assert observer.get("semantic_role") == "notification_observer"

    swift = next((item for item in resp.data if item["name"] == "B"), None)
    if swift is not None:
        assert "semantic_role" not in swift
