"""Tests for swiftui_derivation phase (ConformsTo + Calls data)."""
from orchard.graph.db import get_connection, init_schema
from orchard.derive.swiftui import run_swiftui_derivation


def _sym(conn, sid, usr, name, kind="struct", mod="M"):
    conn.execute(
        f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', "
        f"name: '{name}', language: 'swift', kind: '{kind}', module: '{mod}', "
        f"target_id: 'T', file_path: '', signature: '', container_usr: '', "
        f"access_level: 'public', origin: 'symbolgraph', is_generated: false}})"
    )


def _conforms(conn, usr_sid, proto_sid):
    conn.execute(
        f"MATCH (a:Symbol {{id: '{usr_sid}'}}), (b:Symbol {{id: '{proto_sid}'}}) "
        "CREATE (a)-[:ConformsTo {source: 'symbolgraph'}]->(b)"
    )


def _calls(conn, from_sid, to_sid):
    conn.execute(
        f"MATCH (a:Symbol {{id: '{from_sid}'}}), (b:Symbol {{id: '{to_sid}'}}) "
        "CREATE (a)-[:Calls {source: 'indexstore', confidence: 1.0}]->(b)"
    )


def test_viewtree_from_body_callee(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    tid = "T"

    _sym(conn, f"{tid}:s:C", "s:C", "ContentView")
    _sym(conn, f"{tid}:s:H", "s:H", "HeaderView")
    _sym(conn, f"{tid}:v:V", "v:V", "View", "protocol", "SwiftUI")
    _sym(conn, f"{tid}:s:Cbody", "s:Cbody", "body", "instanceProperty")
    _conforms(conn, f"{tid}:s:C", f"{tid}:v:V")
    _conforms(conn, f"{tid}:s:H", f"{tid}:v:V")
    _calls(conn, f"{tid}:s:Cbody", f"{tid}:s:H")

    stats = run_swiftui_derivation(conn, tid, build_id="b1")
    assert stats["view_tree_edges"] == 1

    rows = conn.execute(
        "MATCH (a:Symbol)-[r:ViewTree]->(b:Symbol) RETURN a.name, b.name, r.confidence"
    ).get_all()
    assert rows[0][0] == "ContentView"
    assert rows[0][1] == "HeaderView"
    assert float(rows[0][2]) == 0.75
    conn.close()


def test_excludes_non_view_callee(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    tid = "T"

    _sym(conn, f"{tid}:s:R", "s:R", "RootView")
    _sym(conn, f"{tid}:s:S", "s:S", "SubView")
    _sym(conn, f"{tid}:s:fmt", "s:fmt", "fmt", "function")
    _sym(conn, f"{tid}:v:V", "v:V", "View", "protocol", "SwiftUI")
    _sym(conn, f"{tid}:s:Rbody", "s:Rbody", "body", "instanceProperty")
    _conforms(conn, f"{tid}:s:R", f"{tid}:v:V")
    _conforms(conn, f"{tid}:s:S", f"{tid}:v:V")
    _calls(conn, f"{tid}:s:Rbody", f"{tid}:s:S")
    _calls(conn, f"{tid}:s:Rbody", f"{tid}:s:fmt")

    stats = run_swiftui_derivation(conn, tid, build_id="b1")
    assert stats["view_tree_edges"] == 1  # SubView only
    conn.close()


def test_idempotent(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    tid = "T"
    _sym(conn, f"{tid}:s:A", "s:A", "AView")
    _sym(conn, f"{tid}:s:B", "s:B", "BView")
    _sym(conn, f"{tid}:v:V", "v:V", "View", "protocol", "SwiftUI")
    _sym(conn, f"{tid}:s:A:body", "s:A:body", "body", "instanceProperty")
    _conforms(conn, f"{tid}:s:A", f"{tid}:v:V")
    _conforms(conn, f"{tid}:s:B", f"{tid}:v:V")
    _calls(conn, f"{tid}:s:A:body", f"{tid}:s:B")

    s1 = run_swiftui_derivation(conn, tid, build_id="b1")
    assert s1["view_tree_edges"] == 1
    s2 = run_swiftui_derivation(conn, tid, build_id="b1")
    assert s2["view_tree_edges"] == 0
    rows = conn.execute("MATCH ()-[r:ViewTree]->() RETURN count(r)").get_all()
    assert rows[0][0] == 1
    conn.close()


def test_no_views_no_edges(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    stats = run_swiftui_derivation(conn, "T", build_id="b1")
    assert stats["view_tree_edges"] == 0 and stats["nav_flow_edges"] == 0
    conn.close()
