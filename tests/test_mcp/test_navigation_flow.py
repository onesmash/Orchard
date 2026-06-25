"""Tests for find_navigation_flow handler."""
import pytest
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn_with_nav_flows(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    # Seed Symbol nodes.
    for name, usr in [("HomeView", "s:HomeView"), ("SettingsLink", "s:SettingsLink"),
                      ("ProfileRouter", "s:ProfileRouter")]:
        conn.execute(
            "MERGE (s:Symbol {id: $id}) "
            "SET s.usr=$usr, s.precise_id='', s.name=$name, s.language='swift', "
            "s.kind='struct', s.module='MyApp', s.target_id='T', s.file_path='', "
            "s.signature='', s.container_usr='', s.access_level='public', "
            "s.origin='test', s.is_generated=false",
            {"id": f"T:{usr}", "usr": usr, "name": name},
        )

    # Create NavigationFlow rel table + edges.
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS NavigationFlow("
        "  FROM Symbol TO Symbol, derived_from STRING, confidence DOUBLE, build_id STRING)"
    )
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:SettingsLink'}), (b:Symbol {id: 'T:s:HomeView'}) "
        "MERGE (a)-[:NavigationFlow {derived_from:'derive/swiftui', confidence:0.70, build_id:'b1'}]->(b)"
    )
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:ProfileRouter'}), (b:Symbol {id: 'T:s:HomeView'}) "
        "MERGE (a)-[:NavigationFlow {derived_from:'derive/swiftui', confidence:0.70, build_id:'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_find_navigation_flow_all(conn_with_nav_flows):
    """Returns all NavigationFlow edges."""
    from orchard.handlers.navigation_flow import NavigationFlowRequest, find_navigation_flow

    req = NavigationFlowRequest(build_id="b1")
    resp = find_navigation_flow(conn_with_nav_flows, req)

    assert resp.data["edge_count"] == 2
    edges = resp.data["nav_flow_edges"]
    assert len(edges) == 2

    targets = {e["target_name"] for e in edges}
    sources = {e["source_name"] for e in edges}
    assert targets == {"HomeView"}
    assert sources == {"SettingsLink", "ProfileRouter"}

    for e in edges:
        assert e["confidence"] == 0.70
        assert e["derived_from"] == "derive/swiftui"
        assert e["build_id"] == "b1"

    assert resp.freshness is not None
    assert "swiftui_derivation" in resp.evidence_sources


def test_find_navigation_flow_filter(conn_with_nav_flows):
    """Module filter restricts results."""
    from orchard.handlers.navigation_flow import NavigationFlowRequest, find_navigation_flow

    req = NavigationFlowRequest(module="MyApp", build_id="b1")
    resp = find_navigation_flow(conn_with_nav_flows, req)
    assert resp.data["edge_count"] == 2

    req2 = NavigationFlowRequest(module="Unknown", build_id="b1")
    resp2 = find_navigation_flow(conn_with_nav_flows, req2)
    assert resp2.data["edge_count"] == 0
    assert "no NavigationFlow edges found" in resp2.open_gaps


def test_find_navigation_flow_empty(tmp_db_path):
    """Empty database returns zero edges with open_gap message."""
    from orchard.handlers.navigation_flow import NavigationFlowRequest, find_navigation_flow

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS NavigationFlow("
        "  FROM Symbol TO Symbol, derived_from STRING, confidence DOUBLE, build_id STRING)"
    )

    req = NavigationFlowRequest(build_id="b1")
    resp = find_navigation_flow(conn, req)
    assert resp.data["edge_count"] == 0
    assert "no NavigationFlow edges found" in resp.open_gaps
    conn.close()
