"""Tests for get_cross_language_bridges handler."""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.bridges import (
    BridgesRequest,
    get_cross_language_bridges,
)


@pytest.fixture
def conn_with_bridges(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Seed two Symbol nodes (swift + objc) and a BridgesTo edge.
    for sid, name, usr, lang in [
        ("T:s:swiftFunc", "swiftFunc", "s:swiftFunc", "swift"),
        ("T:c:objcMethod", "objcMethod", "c:objcMethod", "objc"),
        ("T:s:noBridge", "noBridge", "s:noBridge", "swift"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', "
            f"precise_id: '', name: '{name}', language: '{lang}', "
            f"kind: 'function', module: 'M', target_id: 'T', file_path: '', "
            f"signature: '', container_usr: '', access_level: 'public', "
            f"origin: 'symbolgraph', is_generated: false}})"
        )
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:swiftFunc'}), (b:Symbol {id: 'T:c:objcMethod'}) "
        "CREATE (a)-[:BridgesTo {bridge_kind: 'name_match', provenance: 'derive/bridge', "
        "confidence: 0.85, build_id: 'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_get_bridges_returns_edge(conn_with_bridges):
    """Query outgoing bridges for swiftFunc — should find the edge."""
    req = BridgesRequest(usr="s:swiftFunc", target_id="T", build_id="b1")
    resp = get_cross_language_bridges(conn_with_bridges, req)
    assert len(resp.data) == 1
    assert resp.data[0]["bridge_kind"] == "name_match"
    assert resp.data[0]["confidence"] == 0.85
    assert resp.data[0]["target_usr"] == "c:objcMethod"


def test_get_bridges_none(conn_with_bridges):
    """Query a symbol with no BridgesTo edges — should return empty."""
    req = BridgesRequest(usr="s:noBridge", target_id="T", build_id="b1")
    resp = get_cross_language_bridges(conn_with_bridges, req)
    assert resp.data == []
    assert "no bridges found for this symbol" in resp.open_gaps
