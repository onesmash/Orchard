"""Tests: CrossLanguageName fields populated in BridgesTo edges."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.bridge import run_bridge_recovery


def _seed(conn):
    init_schema(conn)
    syms = [
        SymbolRecord(usr="c:objc(cs)MyClass(im)doThing:", name="doThing", kind="method",
                     module="Test", language="objc", file_path="/a.m",
                     signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:MyClassC5doThingyyF", name="doThing", kind="method",
                     module="Test", language="swift", file_path="/a.swift",
                     signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")


def test_bridge_populates_cross_language_names():
    conn = get_connection(":memory:")
    _seed(conn)
    run_bridge_recovery(conn, "Test", "build-1")
    rows = conn.execute(
        "MATCH ()-[r:BridgesTo]->() "
        "RETURN r.clang_name, r.swift_name, r.definition_language LIMIT 5"
    ).get_all()
    assert len(rows) > 0
    clang_names = {r[0] for r in rows if r[0]}
    swift_names = {r[1] for r in rows if r[1]}
    # ObjC name captured
    assert any("doThing" in n for n in clang_names)
    # Swift name captured
    assert any("doThing" in n for n in swift_names)
    # definition_language populated
    def_langs = {r[2] for r in rows if r[2]}
    assert def_langs <= {"objc", "swift"}
