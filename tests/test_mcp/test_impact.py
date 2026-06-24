"""Tests for impact_analysis handler."""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.impact import ImpactRequest, impact_analysis


@pytest.fixture
def impact_graph(tmp_db_path):
    """Seed 3 symbols: targetFn (queried), directCaller, indirectCaller."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    T = "T"
    # Create 3 symbols: targetFunc (queried), directCaller, indirectCaller.
    for sid, name, usr in [
        ("T:s:targetFn", "targetFn", "s:targetFn"),
        ("T:s:directCaller", "directCaller", "s:directCaller"),
        ("T:s:indirectCaller", "indirectCaller", "s:indirectCaller"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', "
            f"precise_id: '', name: '{name}', language: 'swift', "
            f"kind: 'function', module: 'M', target_id: '{T}', file_path: '', "
            f"signature: '', container_usr: '', access_level: 'public', "
            f"origin: 'symbolgraph', is_generated: false}})"
        )
    # directCaller -> targetFn (Calls)
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:directCaller'}), (b:Symbol {id: 'T:s:targetFn'}) "
        "CREATE (a)-[:Calls {source: 'test', confidence: 1.0}]->(b)"
    )
    # indirectCaller -> directCaller (Calls)
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:indirectCaller'}), (b:Symbol {id: 'T:s:directCaller'}) "
        "CREATE (a)-[:Calls {source: 'test', confidence: 1.0}]->(b)"
    )
    yield conn
    conn.close()


def test_impact_returns_callers_by_depth(impact_graph):
    """Verify d1 has directCaller, d2 has indirectCaller."""
    req = ImpactRequest(usr="s:targetFn", target_id="T", build_id="b1")
    resp = impact_analysis(impact_graph, req)
    by_depth = resp.data["by_depth"]
    # d=1: directCaller
    assert any(d["usr"] == "s:directCaller" for d in by_depth.get("d1", []))
    # d=2: indirectCaller
    assert any(d["usr"] == "s:indirectCaller" for d in by_depth.get("d2", []))
    assert resp.freshness == "stale"


def test_impact_none(impact_graph):
    """Query a symbol with no callers, assert empty d1."""
    req = ImpactRequest(usr="s:indirectCaller", target_id="T", build_id="b1")
    resp = impact_analysis(impact_graph, req)
    assert isinstance(resp.data, dict)
    assert "by_depth" in resp.data
    assert resp.data["by_depth"].get("d1", []) == []  # no direct callers


def test_impact_response_has_risk(impact_graph):
    """Assert risk field is a string (low/medium/high/critical)."""
    req = ImpactRequest(usr="s:targetFn", target_id="T", build_id="b1")
    resp = impact_analysis(impact_graph, req)
    assert "risk" in resp.data
    assert isinstance(resp.data["risk"], str)
    assert resp.data["risk"] in ("low", "medium", "high", "critical")


@pytest.fixture
def bridges_graph(tmp_db_path):
    """Seed symbols with a BridgesTo edge for cross-language traversal."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    T = "T"
    # Create swift target and objc caller connected via BridgesTo.
    for sid, name, usr, lang in [
        ("T:s:swiftFn", "swiftFn", "s:swiftFn", "swift"),
        ("T:c:objcCaller", "objcCaller", "c:objcCaller", "objc"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', "
            f"precise_id: '', name: '{name}', language: '{lang}', "
            f"kind: 'function', module: 'M', target_id: '{T}', file_path: '', "
            f"signature: '', container_usr: '', access_level: 'public', "
            f"origin: 'symbolgraph', is_generated: false}})"
        )
    # objcCaller -[BridgesTo]-> swiftFn
    conn.execute(
        "MATCH (a:Symbol {id: 'T:c:objcCaller'}), (b:Symbol {id: 'T:s:swiftFn'}) "
        "CREATE (a)-[:BridgesTo {bridge_kind: 'name_match', provenance: 'derive/bridge', "
        "confidence: 0.85, build_id: 'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_impact_traverses_bridges_to(bridges_graph):
    """Verify BridgesTo edges are traversed when include_bridge_edges=True.

    The fixture has objcCaller -[BridgesTo]-> swiftFn. For impact analysis
    (finding dependents), we follow incoming edges. Querying swiftFn should
    find objcCaller via incoming BridgesTo traversal.
    """
    req = ImpactRequest(usr="s:swiftFn", target_id="T", build_id="b1")
    resp = impact_analysis(bridges_graph, req)
    by_depth = resp.data["by_depth"]
    # objcCaller -[BridgesTo]-> swiftFn, so querying swiftFn finds objcCaller
    # at depth 1 via incoming BridgesTo.
    assert any(d["usr"] == "c:objcCaller" for d in by_depth.get("d1", []))
