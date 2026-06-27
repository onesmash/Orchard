"""Acceptance tests for M0-M2 per spec §12.

Validates acceptance scenarios A, D, and H. Scenarios B, C, E, F, and G
are deferred to M3-M5 (they require bridge filtering, multi-target merge,
or other later-milestone capabilities).

Uses an in-process Ladybug graph populated directly with SymbolRecord /
edge data, so the real ``orchard-indexstore-reader`` Swift CLI and Xcode
are not required to run these tests.
"""
import pytest
import json
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.normalize.identity import upsert_symbols, upsert_build_snapshot
from orchard.build.context import BuildContext, make_build_id
from orchard.handlers.symbol_context import get_symbol_context, SymbolContextRequest
from orchard.handlers.callers import find_callers, CallerRequest
from orchard.validation.freshness import freshness_for
from orchard.cli import cmd_find_callers, cmd_find_callees, cmd_find_references, cmd_search, cmd_stats


@pytest.fixture
def populated_db(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/swift_only", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path=None,
        symbolgraph_output_path=None, commit_sha="abc", build_config_hash="h1",
    )
    ctx.build_id = make_build_id(ctx)
    upsert_build_snapshot(conn, ctx)
    symbols = [
        SymbolRecord(usr="s:MyClass", precise_id="s:MyClass", name="MyClass",
                     kind="swift.class", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="class MyClass",
                     access_level="public"),
        SymbolRecord(usr="s:myMethod", precise_id="s:myMethod", name="myMethod()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="func myMethod() -> Int",
                     access_level="public"),
        SymbolRecord(usr="s:topLevel", precise_id="s:topLevel", name="topLevelFunc()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="func topLevelFunc()",
                     access_level="public"),
    ]
    upsert_symbols(conn, symbols, target_id="MyLib")
    # topLevelFunc calls myMethod
    conn.execute(
        "MATCH (a:Symbol {id:'s:topLevel'}), (b:Symbol {id:'s:myMethod'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:'b1'}]->(b)"
    )
    yield conn, ctx
    conn.close()


# Scenario A: Single-target Swift-only
def test_a_get_symbol_context_returns_structure(populated_db):
    conn, ctx = populated_db
    req = SymbolContextRequest(usr="s:MyClass", build_id=ctx.build_id)
    resp = get_symbol_context(conn, req)
    assert resp.data is not None
    assert resp.data["name"] == "MyClass"
    assert resp.freshness in ("fresh", "stale", "build_mismatch", "toolchain_mismatch", "partially_stale")
    assert len(resp.evidence_sources) > 0


def test_a_find_callers_of_mymethod(populated_db):
    conn, ctx = populated_db
    req = CallerRequest(usr="s:myMethod", build_id=ctx.build_id)
    resp = find_callers(conn, req)
    names = [item["name"] for item in resp.data]
    assert "topLevelFunc()" in names


# Scenario D: Stale graph
def test_d_stale_freshness_returned_when_no_snapshot(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    _, status = freshness_for(conn, "nonexistent_build", {})
    assert status == "stale"
    conn.close()


def test_d_toolchain_mismatch_detected(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id:'b1', build_system:'xcodebuild', workspace_root:'/app', "
        "derived_data_path:'', index_store_path:'', toolchain_id:'Xcode15.4', "
        "commit_sha:'', build_config_hash:'h1', created_at:'2026-06-24'})"
    )
    _, status = freshness_for(conn, "b1", {"toolchain_id": "Xcode16.0"})
    assert status == "toolchain_mismatch"
    conn.close()


# Scenario H: confidence < 0.70 gate (structure check — bridge filtering in M3)
def test_h_symbol_context_has_open_gaps_field(populated_db):
    """Baseline: with only a high-confidence Calls edge (1.0) and no
    low-confidence bridges present, open_gaps is empty on the normal path.

    Bridge filtering itself is M3 and out of scope here; this just pins the
    baseline that, absent low-confidence (< 0.70) bridges, nothing is
    recorded in open_gaps.
    """
    conn, ctx = populated_db
    # populated_db seeds a single Calls edge with confidence=1.0 (high).
    req = SymbolContextRequest(usr="s:MyClass", build_id=ctx.build_id)
    resp = get_symbol_context(conn, req)
    assert hasattr(resp, "open_gaps")
    assert isinstance(resp.open_gaps, list)
    # No low-confidence bridges present -> nothing recorded as an open gap.
    assert resp.open_gaps == []


def test_cli_find_callers_defaults_to_latest_build(tmp_db_path, capsys):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/swift_only", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path=None,
        symbolgraph_output_path=None, commit_sha="abc", build_config_hash="h1",
    )
    ctx.build_id = make_build_id(ctx)
    upsert_build_snapshot(conn, ctx)
    upsert_symbols(conn, [
        SymbolRecord(usr="s:callee", precise_id="s:callee", name="callee()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="func callee()",
                     access_level="public"),
        SymbolRecord(usr="s:caller", precise_id="s:caller", name="caller()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="func caller()",
                     access_level="public"),
    ], target_id="MyLib")
    conn.execute(
        "MATCH (a:Symbol {id:'s:caller'}), (b:Symbol {id:'s:callee'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:$bid}]->(b)",
        {"bid": ctx.build_id},
    )
    conn.close()

    cmd_find_callers(["--usr", "s:callee", "--target", "MyLib", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["freshness"] == "fresh"
    assert payload["build_id"] == ctx.build_id


def test_cli_find_callers_prefers_latest_build_for_target(tmp_db_path, capsys):
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    ctx1 = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/t1", scheme=None, target="T1",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd1", index_store_path=None,
        symbolgraph_output_path=None, commit_sha="abc", build_config_hash="h1",
    )
    ctx1.build_id = make_build_id(ctx1)
    upsert_build_snapshot(conn, ctx1)

    ctx2 = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/t2", scheme=None, target="T2",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd2", index_store_path=None,
        symbolgraph_output_path=None, commit_sha="def", build_config_hash="h2",
    )
    ctx2.build_id = make_build_id(ctx2)
    upsert_build_snapshot(conn, ctx2)

    upsert_symbols(conn, [
        SymbolRecord(usr="s:callee", precise_id="s:callee", name="callee()",
                     kind="swift.func", module="T1", language="swift",
                     file_path="/src/T1.swift", signature="func callee()",
                     access_level="public"),
        SymbolRecord(usr="s:caller", precise_id="s:caller", name="caller()",
                     kind="swift.func", module="T1", language="swift",
                     file_path="/src/T1.swift", signature="func caller()",
                     access_level="public"),
    ], target_id="T1")
    conn.execute(
        "MATCH (a:Symbol {id:'T1:s:caller'}), (b:Symbol {id:'T1:s:callee'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:$bid}]->(b)",
        {"bid": ctx1.build_id},
    )
    conn.close()

    cmd_find_callers(["--usr", "s:callee", "--target", "T1", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["build_id"] == ctx1.build_id


def test_find_callees_with_relation_types(tmp_db_path, capsys):
    """--relation-types flag traverses non-Calls edges in multi-hop BFS."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    upsert_symbols(conn, [
        SymbolRecord(usr="s:a", precise_id="s:a", name="a()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/a.swift",
                     signature="", access_level="public"),
        SymbolRecord(usr="s:b", precise_id="s:b", name="b()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/b.swift",
                     signature="", access_level="public"),
        SymbolRecord(usr="s:c", precise_id="s:c", name="c()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/c.swift",
                     signature="", access_level="public"),
    ], target_id="MyLib")
    # a -[:Calls]-> b, b -[:Inherits]-> c
    conn.execute("MATCH (a:Symbol {id:'s:a'}), (b:Symbol {id:'s:b'}) "
                 "CREATE (a)-[:Calls {source:'test',confidence:1.0}]->(b)")
    conn.execute("MATCH (b:Symbol {id:'s:b'}), (c:Symbol {id:'s:c'}) "
                 "CREATE (b)-[:Inherits {source:'test',confidence:1.0}]->(c)")
    conn.close()

    # Default (Calls only, depth=2) — a→b then b→? (no outgoing Calls from b)
    cmd_find_callees(["--usr", "s:a", "--target", "MyLib", "--db", tmp_db_path, "--depth", "2"])
    payload_default = json.loads(capsys.readouterr().out)
    callee_names = {c["name"] for c in payload_default["data"]}
    assert "b()" in callee_names, "direct callee via Calls should be found"
    assert "c()" not in callee_names, "Inherits should not be traversed by default"

    # With Calls+Inherits (depth=2) — a→b (Calls), then b→c (Inherits)
    cmd_find_callees(["--usr", "s:a", "--target", "MyLib", "--db", tmp_db_path,
                      "--depth", "2", "--relation-types", "Calls,Inherits"])
    payload_rt = json.loads(capsys.readouterr().out)
    callee_names_rt = {c["name"] for c in payload_rt["data"]}
    assert "b()" in callee_names_rt, "still reachable via Calls (d=1)"
    assert "c()" in callee_names_rt, "should be reachable via Inherits edge (d=2)"


def test_search_by_file_path(tmp_db_path, capsys):
    """--file flag filters symbols by file_path substring/pattern."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    upsert_symbols(conn, [
        SymbolRecord(usr="s:a", precise_id="s:a", name="a()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/MyLib.swift",
                     signature="", access_level="public"),
        SymbolRecord(usr="s:b", precise_id="s:b", name="b()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/Other.swift",
                     signature="", access_level="public"),
    ], target_id="MyLib")
    conn.close()

    # --file matches file_path substring
    cmd_search(["--name", "a", "--file", "MyLib", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1, "should find a() in MyLib.swift"

    cmd_search(["--name", "b", "--file", "Other", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1, "should find b() in Other.swift"

    cmd_search(["--name", "b", "--file", "NotExists", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 0, "no symbols in NotExists file"


def test_find_references_returns_incoming_and_outgoing(tmp_db_path, capsys):
    """find_references returns outgoing calls + incoming callers."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    upsert_symbols(conn, [
        SymbolRecord(usr="s:self", precise_id="s:self", name="self()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/Ref.swift",
                     signature="", access_level="public"),
        SymbolRecord(usr="s:called", precise_id="s:called", name="called()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/Ref.swift",
                     signature="", access_level="public"),
        SymbolRecord(usr="s:caller", precise_id="s:caller", name="caller()", kind="swift.func",
                     module="MyLib", language="swift", file_path="/src/Ref.swift",
                     signature="", access_level="public"),
    ], target_id="MyLib")
    # self calls called, caller calls self
    conn.execute("MATCH (a:Symbol {id:'s:self'}), (b:Symbol {id:'s:called'}) "
                 "CREATE (a)-[:Calls {source:'test',confidence:1.0}]->(b)")
    conn.execute("MATCH (a:Symbol {id:'s:caller'}), (b:Symbol {id:'s:self'}) "
                 "CREATE (a)-[:Calls {source:'test',confidence:1.0}]->(b)")
    conn.close()

    cmd_find_references(["--usr", "s:self", "--target", "MyLib", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    outgoing = payload["data"]["outgoing"]
    incoming = payload["data"]["incoming"]
    assert len(outgoing) == 1, "self calls called → 1 outgoing"
    assert outgoing[0]["target_name"] == "called()"
    assert len(incoming) == 1, "caller calls self → 1 incoming"
    assert incoming[0]["caller_name"] == "caller()"


def test_cmd_stats_prints_db_path_and_snapshot_metadata(tmp_db_path, capsys):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/swift_only", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path="/tmp/dd/IndexStore",
        symbolgraph_output_path=None, commit_sha="abc", build_config_hash="h1",
    )
    ctx.build_id = make_build_id(ctx)
    upsert_build_snapshot(conn, ctx)
    conn.close()

    cmd_stats(["--db", tmp_db_path])
    out = capsys.readouterr().out
    assert f"Database: {tmp_db_path}" in out
    assert f"Build ID: {ctx.build_id}" in out
    assert "IndexStore: /tmp/dd/IndexStore" in out


def test_cmd_ingest_resolves_relative_source_root(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, str | None] = {}

    def fake_read_index_store(index_store_path, target_id, source_root=None, incremental_since=None):
        captured["source_root"] = source_root
        return IndexStoreResult(), None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.ingest.indexstore.list_source_files", lambda *a, **kw: [])
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)

    db_path = tmp_path / "graph.db"
    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--source-root", ".",
        "--target", "T",
        "--db", str(db_path),
    ])

    assert captured["source_root"] == str(tmp_path.resolve())


def test_cmd_stats_reports_parent_database_discovery(tmp_path, capsys, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "child"
    (parent / ".orchard").mkdir(parents=True)
    child.mkdir(parents=True)

    db_path = parent / ".orchard" / "graph.db"
    conn = get_connection(str(db_path))
    init_schema(conn)
    conn.close()

    monkeypatch.chdir(child)
    cmd_stats([])
    out = capsys.readouterr().out
    assert f"Using database at {db_path} (found in parent directory)" in out


def test_cli_json_output_not_polluted_by_parent_db_notice(tmp_path, capsys, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "child"
    (parent / ".orchard").mkdir(parents=True)
    child.mkdir(parents=True)
    db_path = parent / ".orchard" / "graph.db"
    conn = get_connection(str(db_path))
    init_schema(conn)
    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/swift_only", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path=None,
        symbolgraph_output_path=None, commit_sha="abc", build_config_hash="h1",
    )
    ctx.build_id = make_build_id(ctx)
    upsert_build_snapshot(conn, ctx)
    upsert_symbols(conn, [
        SymbolRecord(usr="s:callee", precise_id="s:callee", name="callee()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="", access_level="public"),
        SymbolRecord(usr="s:caller", precise_id="s:caller", name="caller()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="", access_level="public"),
    ], target_id="MyLib")
    conn.execute(
        "MATCH (a:Symbol {id:'s:caller'}), (b:Symbol {id:'s:callee'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:$bid}]->(b)",
        {"bid": ctx.build_id},
    )
    conn.close()

    monkeypatch.chdir(child)
    cmd_find_callers(["--usr", "s:callee", "--target", "MyLib"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["data"]
    assert f"Using database at {db_path} (found in parent directory)" in captured.err


def test_cmd_ingest_defaults_db_to_real_project_directory(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    project_dir = tmp_path / "ios-client"
    project_dir.mkdir()
    project = project_dir / "Zoom.xcworkspace"
    project.mkdir()
    cwd_parent = tmp_path / "workspace-root"
    cwd_parent.mkdir()

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    def fake_conn(db_path=""):
        captured["db_path"] = db_path
        return DummyConn()

    monkeypatch.chdir(cwd_parent)
    monkeypatch.setattr("orchard.cli._conn", fake_conn)
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(tmp_path / "dd"), str(tmp_path / "dd/Index.noindex/DataStore"), "2026-06-26T00:00:00Z")]
    )
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", lambda *args, **kwargs: (IndexStoreResult(), None))
    monkeypatch.setattr("orchard.ingest.indexstore.list_source_files", lambda *a, **kw: [])
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)

    cmd_ingest(["--project-dir", str(cwd_parent), "--target", "Zoom"])

    assert captured["db_path"] == str(project_dir / ".orchard" / "graph.db")
