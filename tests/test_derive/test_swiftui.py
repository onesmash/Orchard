"""Tests for swiftui_derivation phase (ConformsTo + Calls data)."""
from orchard.graph.db import get_connection, init_schema
from orchard.derive.swiftui import run_swiftui_derivation


def _sym(conn, sid, usr, name, kind="struct", mod="M"):
    conn.execute(
        f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', "
        f"name: '{name}', language: 'swift', kind: '{kind}', module: '{mod}', "
        f"file_path: '', signature: '', container_usr: '', "
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

    _sym(conn, f"s:C", "s:C", "ContentView")
    _sym(conn, f"s:H", "s:H", "HeaderView")
    _sym(conn, f"v:V", "v:V", "View", "protocol", "SwiftUI")
    _sym(conn, f"s:Cbody", "s:Cbody", "body", "instanceProperty")
    _conforms(conn, f"s:C", f"v:V")
    _conforms(conn, f"s:H", f"v:V")
    _calls(conn, f"s:Cbody", f"s:H")

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

    _sym(conn, f"s:R", "s:R", "RootView")
    _sym(conn, f"s:S", "s:S", "SubView")
    _sym(conn, f"s:fmt", "s:fmt", "fmt", "function")
    _sym(conn, f"v:V", "v:V", "View", "protocol", "SwiftUI")
    _sym(conn, f"s:Rbody", "s:Rbody", "body", "instanceProperty")
    _conforms(conn, f"s:R", f"v:V")
    _conforms(conn, f"s:S", f"v:V")
    _calls(conn, f"s:Rbody", f"s:S")
    _calls(conn, f"s:Rbody", f"s:fmt")

    stats = run_swiftui_derivation(conn, tid, build_id="b1")
    assert stats["view_tree_edges"] == 1  # SubView only
    conn.close()


def test_idempotent(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    tid = "T"
    _sym(conn, f"s:A", "s:A", "AView")
    _sym(conn, f"s:B", "s:B", "BView")
    _sym(conn, f"v:V", "v:V", "View", "protocol", "SwiftUI")
    _sym(conn, f"s:A:body", "s:A:body", "body", "instanceProperty")
    _conforms(conn, f"s:A", f"v:V")
    _conforms(conn, f"s:B", f"v:V")
    _calls(conn, f"s:A:body", f"s:B")

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


def test_viewtree_reads_whole_compiled_scope(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    conn.execute(
        "CREATE (:Symbol {id: 's:Root', usr: 's:Root', precise_id: '', "
        "name: 'RootView', language: 'swift', kind: 'struct', module: 'Zoom', "
        "file_path: '', signature: '', container_usr: '', "
        "access_level: 'public', origin: 'symbolgraph', is_generated: false})"
    )
    conn.execute(
        "CREATE (:Symbol {id: 's:Child', usr: 's:Child', precise_id: '', "
        "name: 'ChildView', language: 'swift', kind: 'struct', module: 'zPSApp', "
        "file_path: '', signature: '', container_usr: '', "
        "access_level: 'public', origin: 'symbolgraph', is_generated: false})"
    )
    conn.execute(
        "CREATE (:Symbol {id: 'v:V', usr: 'v:V', precise_id: '', "
        "name: 'View', language: 'swift', kind: 'protocol', module: 'SwiftUI', "
        "file_path: '', signature: '', container_usr: '', "
        "access_level: 'public', origin: 'symbolgraph', is_generated: false})"
    )
    conn.execute(
        "CREATE (:Symbol {id: 's:Root:body', usr: 's:Root:body', precise_id: '', "
        "name: 'body', language: 'swift', kind: 'instanceProperty', module: 'Zoom', "
        "file_path: '', signature: '', container_usr: '', "
        "access_level: 'public', origin: 'symbolgraph', is_generated: false})"
    )
    _conforms(conn, "s:Root", "v:V")
    _conforms(conn, "s:Child", "v:V")
    _calls(conn, "s:Root:body", "s:Child")

    stats = run_swiftui_derivation(conn, "compiled-scope", build_id="b1")
    assert stats["view_tree_edges"] == 1

    rows = conn.execute(
        "MATCH (a:Symbol)-[:ViewTree]->(b:Symbol) RETURN a.name, b.name"
    ).get_all()
    assert rows == [["RootView", "ChildView"]]
    conn.close()
