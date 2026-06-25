"""M5 acceptance tests: swiftui_derivation + get_view_tree + find_navigation_flow."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.swiftui import run_swiftui_derivation
from orchard.mcp.handlers.view_tree import ViewTreeRequest, get_view_tree
from orchard.mcp.handlers.navigation_flow import NavigationFlowRequest, find_navigation_flow


def test_m5_seed_structs_derive_and_query_handlers(tmp_db_path):
    """Full M5 flow: seed struct Symbols -> run swiftui_derivation ->
    assert both handlers return edges."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "M5Target"

    # Seed SwiftUI-like struct Symbols
    upsert_symbols(
        conn,
        [
            SymbolRecord(
                usr="s:AppRootView", precise_id="", name="AppRootView",
                kind="struct", module="M5App", language="swift",
                file_path="/src/App.swift", signature="",
                access_level="public", container_usr=None,
            ),
            SymbolRecord(
                usr="s:HomeView", precise_id="", name="HomeView",
                kind="struct", module="M5App", language="swift",
                file_path="/src/Home.swift", signature="",
                access_level="public", container_usr=None,
            ),
            SymbolRecord(
                usr="s:SettingsNav", precise_id="", name="SettingsNav",
                kind="struct", module="M5App", language="swift",
                file_path="/src/Settings.swift", signature="",
                access_level="public", container_usr=None,
            ),
        ],
        target_id,
    )

    # Run derivation
    stats = run_swiftui_derivation(conn, target_id, build_id="m5")
    assert stats["view_tree_edges"] >= 1
    # SettingsNav contains "Nav" -> should produce NavigationFlow edges
    assert stats["nav_flow_edges"] >= 1

    # Query ViewTree handler
    vt_resp = get_view_tree(conn, ViewTreeRequest(build_id="m5"))
    assert vt_resp.data["edge_count"] >= 1
    tree_edges = vt_resp.data["tree_edges"]
    # AppRootView (first alphabetically) should be the root
    roots = {e["source_name"] for e in tree_edges}
    assert "AppRootView" in roots
    assert vt_resp.freshness is not None
    assert "swiftui_derivation" in vt_resp.evidence_sources

    # Query NavigationFlow handler
    nf_resp = find_navigation_flow(conn, NavigationFlowRequest(build_id="m5"))
    assert nf_resp.data["edge_count"] >= 1
    nav_edges = nf_resp.data["nav_flow_edges"]
    sources = {e["source_name"] for e in nav_edges}
    assert "SettingsNav" in sources
    assert nf_resp.freshness is not None
    assert "swiftui_derivation" in nf_resp.evidence_sources

    conn.close()
