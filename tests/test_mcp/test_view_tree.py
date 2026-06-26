"""Tests for get_view_tree handler."""
import pytest
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn_with_views(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    # Seed Module + Symbol nodes.
    conn.execute("MERGE (m:Module {name: 'MyApp'}) SET m.language='swift'")
    for name, usr in [("RootView", "s:RootView"), ("SubView", "s:SubView"),
                      ("OtherView", "s:OtherView")]:
        conn.execute(
            "MERGE (s:Symbol {id: $id}) "
            "SET s.usr=$usr, s.precise_id='', s.name=$name, s.language='swift', "
            "s.kind='struct', s.module='MyApp', s.target_id='T', s.file_path='', "
            "s.signature='', s.container_usr='', s.access_level='public', "
            "s.origin='test', s.is_generated=false",
            {"id": usr, "usr": usr, "name": name},
        )

    # Create ViewTree rel table + edges.
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ViewTree("
        "  FROM Symbol TO Symbol, derived_from STRING, confidence DOUBLE, build_id STRING)"
    )
    conn.execute(
        "MATCH (a:Symbol {id: 's:RootView'}), (b:Symbol {id: 's:SubView'}) "
        "MERGE (a)-[:ViewTree {derived_from:'derive/swiftui', confidence:0.70, build_id:'b1'}]->(b)"
    )
    conn.execute(
        "MATCH (a:Symbol {id: 's:RootView'}), (b:Symbol {id: 's:OtherView'}) "
        "MERGE (a)-[:ViewTree {derived_from:'derive/swiftui', confidence:0.70, build_id:'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_get_view_tree_all(conn_with_views):
    """Returns all ViewTree edges."""
    from orchard.handlers.view_tree import ViewTreeRequest, get_view_tree

    req = ViewTreeRequest(build_id="b1")
    resp = get_view_tree(conn_with_views, req)

    assert resp.data["edge_count"] == 2
    edges = resp.data["tree_edges"]
    assert len(edges) == 2

    sources = {e["source_name"] for e in edges}
    targets = {e["target_name"] for e in edges}
    assert sources == {"RootView"}
    assert targets == {"SubView", "OtherView"}

    for e in edges:
        assert e["confidence"] == 0.70
        assert e["derived_from"] == "derive/swiftui"
        assert e["build_id"] == "b1"

    assert resp.freshness is not None
    assert "swiftui_derivation" in resp.evidence_sources


def test_get_view_tree_filter(conn_with_views):
    """Module filter restricts results."""
    from orchard.handlers.view_tree import ViewTreeRequest, get_view_tree

    req = ViewTreeRequest(module="MyApp", build_id="b1")
    resp = get_view_tree(conn_with_views, req)
    assert resp.data["edge_count"] == 2

    req2 = ViewTreeRequest(module="Nonexistent", build_id="b1")
    resp2 = get_view_tree(conn_with_views, req2)
    assert resp2.data["edge_count"] == 0
    assert "no ViewTree edges found" in resp2.open_gaps


def test_get_view_tree_empty(tmp_db_path):
    """Empty database returns zero edges with open_gap message."""
    from orchard.handlers.view_tree import ViewTreeRequest, get_view_tree

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Ensure ViewTree rel table exists but has no edges.
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ViewTree("
        "  FROM Symbol TO Symbol, derived_from STRING, confidence DOUBLE, build_id STRING)"
    )

    req = ViewTreeRequest(build_id="b1")
    resp = get_view_tree(conn, req)
    assert resp.data["edge_count"] == 0
    assert "no ViewTree edges found" in resp.open_gaps
    conn.close()
