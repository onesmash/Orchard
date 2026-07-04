"""Tests for architecture_derivation phase."""
from orchard.graph.db import get_connection, init_schema
from orchard.derive.architecture import run_architecture_derivation


def _seed_sym(conn, sid, usr, name, mod, scope_id="T", kind="function", lang="swift"):
    conn.execute(
        "MERGE (s:Symbol {id: $id}) "
        "SET s.usr=$usr, s.precise_id='', s.name=$name, s.language=$lang, "
        "s.kind=$kind, s.module=$mod, s.file_path='', "
        "s.signature='', s.container_usr='', s.access_level='public', "
        "s.origin='test', s.is_generated=false",
        {"id": sid, "usr": usr, "name": name, "mod": mod, "lang": lang, "kind": kind},
    )


def _seed_calls(conn, caller_id, callee_id, build_id="b1", source="test"):
    conn.execute(
        "MATCH (a:Symbol {id: $a}), (b:Symbol {id: $b}) "
        "MERGE (a)-[:Calls {source: $src, build_id: $bid}]->(b)",
        {"a": caller_id, "b": callee_id, "src": source, "bid": build_id},
    )


def _seed_refs(conn, src_id, tgt_id):
    conn.execute(
        "MATCH (a:Symbol {id: $a}), (b:Symbol {id: $b}) "
        "MERGE (a)-[:References {source: 'test'}]->(b)",
        {"a": src_id, "b": tgt_id},
    )


def test_architecture_derivation_builds_depends_on(tmp_db_path):
    """Cross-module Calls produce DependsOn edges between Module nodes."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    scope_id = "T"

    _seed_sym(conn, "T:s:UI_view", "s:UI_view", "renderView", "UIKit", scope_id)
    _seed_sym(conn, "T:s:Data_repo", "s:Data_repo", "fetchItems", "DataLayer", scope_id)
    _seed_sym(conn, "T:s:Same_mod", "s:Same_mod", "helper", "UIKit", scope_id)
    _seed_calls(conn, "T:s:UI_view", "T:s:Data_repo")
    # Same-module call should NOT produce a DependsOn edge.
    _seed_calls(conn, "T:s:UI_view", "T:s:Same_mod")

    stats = run_architecture_derivation(conn, scope_id, build_id="b1")
    assert stats["module_deps"] == 1
    assert stats["cycles_detected"] == 0

    rows = conn.execute(
        "MATCH (src:Module)-[d:DependsOn]->(tgt:Module) "
        "RETURN src.name, tgt.name, d.source, d.build_id"
    ).get_all()
    assert len(rows) == 1
    assert rows[0][0] == "UIKit"
    assert rows[0][1] == "DataLayer"
    assert rows[0][2] == "derive/architecture"
    assert rows[0][3] == "b1"
    conn.close()


def test_architecture_derivation_includes_references(tmp_db_path):
    """Cross-module References also produce DependsOn edges."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    scope_id = "T"

    _seed_sym(conn, "T:s:A", "s:A", "funcA", "ModA", scope_id)
    _seed_sym(conn, "T:s:B", "s:B", "funcB", "ModB", scope_id)
    _seed_refs(conn, "T:s:A", "T:s:B")

    stats = run_architecture_derivation(conn, scope_id, build_id="b1")
    assert stats["module_deps"] == 1
    conn.close()


def test_architecture_derivation_idempotent(tmp_db_path):
    """Second run with same data reports zero new deps (idempotent via MERGE)."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    scope_id = "T"

    _seed_sym(conn, "T:s:A", "s:A", "funcA", "ModA", scope_id)
    _seed_sym(conn, "T:s:B", "s:B", "funcB", "ModB", scope_id)
    _seed_calls(conn, "T:s:A", "T:s:B")

    stats1 = run_architecture_derivation(conn, scope_id, build_id="b1")
    assert stats1["module_deps"] == 1

    stats2 = run_architecture_derivation(conn, scope_id, build_id="b1")
    assert stats2["module_deps"] == 1  # Same count, no duplicates.

    rows = conn.execute("MATCH ()-[d:DependsOn]->() RETURN count(d)").get_all()
    assert rows[0][0] == 1
    conn.close()


def test_architecture_derivation_no_cross_module(tmp_db_path):
    """When all calls are within the same module, zero deps are produced."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    scope_id = "T"

    _seed_sym(conn, "T:s:1", "s:1", "f1", "SameMod", scope_id)
    _seed_sym(conn, "T:s:2", "s:2", "f2", "SameMod", scope_id)
    _seed_calls(conn, "T:s:1", "T:s:2")

    stats = run_architecture_derivation(conn, scope_id, build_id="b1")
    assert stats["module_deps"] == 0
    assert stats["cycles_detected"] == 0
    conn.close()


def test_architecture_derivation_detects_cycle(tmp_db_path):
    """A 2-module cycle is detected and counted."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    scope_id = "T"

    _seed_sym(conn, "T:s:A", "s:A", "funcA", "ModA", scope_id)
    _seed_sym(conn, "T:s:B", "s:B", "funcB", "ModB", scope_id)
    _seed_calls(conn, "T:s:A", "T:s:B")
    _seed_calls(conn, "T:s:B", "T:s:A")

    stats = run_architecture_derivation(conn, scope_id, build_id="b1")
    assert stats["module_deps"] == 2
    assert stats["cycles_detected"] >= 1
    conn.close()


def test_architecture_derivation_reads_whole_compiled_scope(tmp_db_path):
    """Cross-module edges remain visible even when symbols came from old per-target shards."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    _seed_sym(conn, "s:MyAppA", "s:MyAppA", "myappA", "MyAppMod", scope_id="MyApp")
    _seed_sym(conn, "s:MyPSB", "s:MyPSB", "zpsB", "MyPSMod", scope_id="MyPSApp")
    _seed_calls(conn, "s:MyAppA", "s:MyPSB")

    stats = run_architecture_derivation(conn, "compiled-scope", build_id="b1")
    assert stats["module_deps"] == 1

    rows = conn.execute(
        "MATCH (src:Module)-[:DependsOn]->(tgt:Module) RETURN src.name, tgt.name"
    ).get_all()
    assert rows == [["MyAppMod", "MyPSMod"]]
    conn.close()
