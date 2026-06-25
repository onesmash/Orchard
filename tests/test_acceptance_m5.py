"""M5 acceptance: swiftui_derivation (real ConformsTo + Calls) + handlers."""
from orchard.graph.db import get_connection, init_schema
from orchard.derive.swiftui import run_swiftui_derivation
from orchard.handlers.view_tree import ViewTreeRequest, get_view_tree
from orchard.handlers.navigation_flow import NavigationFlowRequest, find_navigation_flow


def _sym(conn, sid, usr, name, kind="struct", mod="M"):
    conn.execute(
        f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', "
        f"name: '{name}', language: 'swift', kind: '{kind}', module: '{mod}', "
        f"target_id: 'M5', file_path: '', signature: '', container_usr: '', "
        f"access_level: 'public', origin: 'symbolgraph', is_generated: false}})"
    )


def test_m5_full_flow_with_conforms_and_calls(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    tid = "M5"

    # Views.
    _sym(conn, f"{tid}:s:Root", "s:Root", "AppRootView")
    _sym(conn, f"{tid}:s:Home", "s:Home", "HomeView")
    _sym(conn, f"{tid}:s:Settings", "s:Settings", "SettingsNav")
    # View protocol.
    _sym(conn, f"{tid}:v:View", "v:View", "View", "protocol", "SwiftUI")
    for u in ("s:Root", "s:Home", "s:Settings"):
        conn.execute(f"MATCH (a:Symbol {{id: '{tid}:{u}'}}), (b:Symbol {{id: '{tid}:v:View'}}) CREATE (a)-[:ConformsTo]->(b)")

    # Body member of AppRootView calls HomeView.
    _sym(conn, f"{tid}:s:Root:body", "s:Root:body", "body", "instanceProperty")
    conn.execute(f"MATCH (a:Symbol {{id: '{tid}:s:Root:body'}}), (b:Symbol {{id: '{tid}:s:Home'}}) CREATE (a)-[:Calls]->(b)")

    # Body member of AppRootView also calls NavigationLink.
    conn.execute(f"CREATE (n:Symbol {{id: '{tid}:s:NavLink', usr: 's:NavLink', name: 'NavigationLink', language: 'swift', kind: 'struct', module: 'SwiftUI', target_id: '{tid}'}})")
    conn.execute(f"MATCH (a:Symbol {{id: '{tid}:s:Root:body'}}), (b:Symbol {{id: '{tid}:s:NavLink'}}) CREATE (a)-[:Calls]->(b)")

    stats = run_swiftui_derivation(conn, tid, build_id="m5")
    assert stats["view_tree_edges"] >= 1
    assert stats["nav_flow_edges"] >= 1

    vt = get_view_tree(conn, ViewTreeRequest(build_id="m5"))
    assert vt.data["edge_count"] >= 1
    assert "AppRootView" in {e["source_name"] for e in vt.data["tree_edges"]}
    assert vt.freshness is not None

    nf = find_navigation_flow(conn, NavigationFlowRequest(build_id="m5"))
    assert nf.data["edge_count"] >= 1
    assert nf.freshness is not None

    conn.close()
