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
import re
import hashlib
from pathlib import Path
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.ingest.lock import LOCK_BUSY_EXIT_CODE, graph_db_lock_path
from orchard.normalize.identity import upsert_symbols, upsert_build_snapshot
from orchard.build.context import BuildContext, make_build_id
from orchard.handlers.symbol_context import get_symbol_context, SymbolContextRequest
from orchard.handlers.callers import find_callers, CallerRequest
from orchard.validation.freshness import freshness_for
from orchard.cli import (
    cmd_find_callers,
    cmd_find_callees,
    cmd_find_references,
    cmd_indexd,
    cmd_search,
    cmd_stats,
    cmd_update,
)


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
    upsert_symbols(conn, symbols, scope_id="MyLib")
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
    ], scope_id="MyLib")
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


def test_cli_find_callers_uses_latest_build_globally(tmp_db_path, capsys):
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
    ], scope_id="T1")
    conn.execute(
        "MATCH (a:Symbol {id:'s:caller'}), (b:Symbol {id:'s:callee'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:$bid}]->(b)",
        {"bid": ctx1.build_id},
    )
    conn.close()

    cmd_find_callers(["--usr", "s:callee", "--target", "T1", "--db", tmp_db_path])
    payload = json.loads(capsys.readouterr().out)
    assert payload["build_id"] == ctx2.build_id


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
    ], scope_id="MyLib")
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
    ], scope_id="MyLib")
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
    ], scope_id="MyLib")
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
    assert outgoing[0]["name"] == "called()"
    assert len(incoming) == 1, "caller calls self → 1 incoming"
    assert incoming[0]["name"] == "caller()"


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


def test_cmd_ingest_uses_compiled_targets_from_derived_data(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    def fake_read_index_store(
        index_store_path,
        scope_id,
        source_root=None,
        source_roots=None,
        incremental_since=None,
        targets=None,
    ):
        captured["scope_id"] = scope_id
        captured["targets"] = targets
        return IndexStoreResult(), None

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: ["/repo/ios-client", "/repo/client-app-video/zPSApp"],
        raising=False,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
    ])

    assert captured["scope_id"] == "Zoom"
    assert captured["targets"] == ["Zoom", "zPSApp"]


def test_cmd_ingest_passes_project_config_source_roots_for_compiled_targets(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    def fake_read_index_store(
        index_store_path,
        scope_id,
        source_root=None,
        source_roots=None,
        incremental_since=None,
        targets=None,
    ):
        captured["scope_id"] = scope_id
        captured["targets"] = targets
        captured["source_roots"] = source_roots
        return IndexStoreResult(), None

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: ["/repo/ios-client", "/repo/client-app-video/zPSApp"],
        raising=False,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
    ])

    assert captured["scope_id"] == "Zoom"
    assert captured["targets"] == ["Zoom", "zPSApp"]
    assert captured["source_roots"] == ["/repo/ios-client", "/repo/client-app-video/zPSApp"]


def test_cmd_ingest_runs_global_community_and_process_derivation_once(tmp_path, monkeypatch, capsys):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {
        "community_scope_ids": [],
        "process_scope_ids": [],
    }

    class DummyConn:
        def close(self):
            return None

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (IndexStoreResult(), None),
    )
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: ["/repo/ios-client", "/repo/client-app-video/zPSApp"],
        raising=False,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr("orchard.normalize.identity.upsert_files", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        "orchard.derive.community_detection.run_community_detection",
        lambda _conn, scope_id: captured["community_scope_ids"].append(scope_id) or {
            "communities_found": 2,
            "members_assigned": 4,
        },
    )

    class DummyProcessNode:
        process_type = "cross_community"

    monkeypatch.setattr(
        "orchard.derive.process_detection.detect_processes",
        lambda _conn, scope_id, changed_files=None: captured["process_scope_ids"].append(scope_id) or [DummyProcessNode()],
    )
    monkeypatch.setattr(
        "orchard.derive.notification_graph.persist_notification_graph",
        lambda *args, **kwargs: 0,
    )

    cmd_ingest([
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
    ])

    out = capsys.readouterr().out
    assert len(captured["community_scope_ids"]) == 1
    assert len(captured["process_scope_ids"]) == 1
    assert captured["community_scope_ids"][0].startswith("build-")
    assert captured["process_scope_ids"] == captured["community_scope_ids"]
    assert "communities: 2 communities, 4 members" in out
    assert "communities (Zoom):" not in out
    assert "communities (zPSApp):" not in out
    assert "processes: 1 detected (1 cross-community)" in out
    assert "processes (Zoom):" not in out
    assert "processes (zPSApp):" not in out


def test_cmd_ingest_full_notification_graph_reuses_full_file_list(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (
            IndexStoreResult(),
            {"changed": [], "all": ["/repo/ios-client/Observer.m", "/repo/ios-client/Poster.m"]},
        ),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr("orchard.normalize.identity.upsert_files", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.derive.community_detection.run_community_detection", lambda *args, **kwargs: {"communities_found": 0, "members_assigned": 0})
    monkeypatch.setattr("orchard.derive.process_detection.detect_processes", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "orchard.derive.notification_graph.persist_notification_graph",
        lambda _conn, source_root="", build_id="", changed_files=None: captured.setdefault("changed_files", changed_files) or 0,
    )

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
        "--full",
    ])

    assert captured["changed_files"] == ["/repo/ios-client/Observer.m", "/repo/ios-client/Poster.m"]


def test_graph_db_lock_path_hashes_graph_db(tmp_path):
    graph_db_a = tmp_path / ".orchard" / "graph.db"
    graph_db_b = tmp_path / ".orchard" / "other.db"
    graph_db_a.parent.mkdir(parents=True, exist_ok=True)
    graph_db_a.write_text("", encoding="utf-8")

    alias_root = tmp_path / "alias"
    alias_root.symlink_to(graph_db_a.parent, target_is_directory=True)
    graph_db_a_alias = alias_root / graph_db_a.name

    lock_path_a = Path(graph_db_lock_path(str(graph_db_a)))
    lock_path_b = Path(graph_db_lock_path(str(graph_db_b)))
    lock_path_a_alias = Path(graph_db_lock_path(str(graph_db_a_alias)))
    expected_hash_a = hashlib.sha256(str(graph_db_a.resolve()).encode("utf-8")).hexdigest()
    expected_hash_b = hashlib.sha256(str(graph_db_b.resolve()).encode("utf-8")).hexdigest()

    assert lock_path_a.parent == Path.home() / ".orchard" / "locks"
    assert re.fullmatch(r"orchard-ingest-[0-9a-f]{64}\.lock", lock_path_a.name)
    assert lock_path_a.name == f"orchard-ingest-{expected_hash_a}.lock"
    assert lock_path_a_alias == lock_path_a
    assert lock_path_b.name == f"orchard-ingest-{expected_hash_b}.lock"
    assert lock_path_a != lock_path_b


def test_cmd_ingest_returns_lock_busy_when_lock_held(tmp_path, monkeypatch, capsys):
    from orchard import cli as cli_mod
    from orchard.ingest.lock import try_acquire_graph_db_lock

    graph_db = tmp_path / ".orchard" / "graph.db"
    graph_db.parent.mkdir(parents=True, exist_ok=True)
    graph_db.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        cli_mod,
        "_conn",
        lambda _path, read_only=False: (_ for _ in ()).throw(AssertionError("should not open db")),
    )

    held_lock = try_acquire_graph_db_lock(str(graph_db))
    assert held_lock is not None
    with held_lock:
        with pytest.raises(SystemExit) as excinfo:
            cli_mod.cmd_ingest([
                "--index-store", "/tmp/IndexStore",
                "--project-dir", str(tmp_path),
                "--target", "Zoom",
                "--db", str(graph_db),
            ])

    assert excinfo.value.code == LOCK_BUSY_EXIT_CODE
    assert capsys.readouterr().err.strip() == "INGEST_LOCK_BUSY"


def test_cmd_ingest_fails_closed_when_project_config_roots_missing_for_compiled_targets(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest

    class DummyConn:
        def close(self):
            return None

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"
    called = {"read_index_store": False}

    def fake_read_index_store(*_args, **_kwargs):
        called["read_index_store"] = True
        raise AssertionError("read_index_store should not be called when roots are unresolved")

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: [],
        raising=False,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as excinfo:
        cmd_ingest([
            "--project-dir", str(tmp_path),
            "--target", "Zoom",
        ])

    assert excinfo.value.code == 2
    assert called["read_index_store"] is False


def test_cmd_ingest_defaults_to_incremental(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    def fake_read_index_store(
        index_store_path,
        scope_id,
        source_root=None,
        source_roots=None,
        incremental_since=None,
        targets=None,
    ):
        captured["incremental_since"] = incremental_since
        return IndexStoreResult(), None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.state.load_state",
        lambda _project_dir: {"last_ingest_ts": 123.0, "compiled_targets": ["T"], "index_store_path": "/fake/store"},
    )
    monkeypatch.setattr("orchard.ingest.indexstore._unit_dir_mtime", lambda _path: 124.0)
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
    ])

    assert captured["incremental_since"] == 123.0


def test_cmd_ingest_full_disables_incremental(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    def fake_read_index_store(
        index_store_path,
        scope_id,
        source_root=None,
        source_roots=None,
        incremental_since=None,
        targets=None,
    ):
        captured["incremental_since"] = incremental_since
        return IndexStoreResult(), None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.state.load_state",
        lambda _project_dir: {"last_ingest_ts": 123.0, "compiled_targets": ["T"], "index_store_path": "/fake/store"},
    )
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
        "--full",
    ])

    assert captured["incremental_since"] is None


def test_cmd_ingest_full_rebuilds_graph_db_before_open(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    db_path = tmp_path / "graph.db"
    db_path.mkdir()
    (db_path / "stale.txt").write_text("old graph", encoding="utf-8")

    class DummyConn:
        def close(self):
            return None

    def fake_conn(path, *args, **kwargs):
        assert path == str(db_path)
        assert not db_path.exists()
        return DummyConn()

    monkeypatch.setattr("orchard.cli._conn", fake_conn)
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (
            IndexStoreResult(),
            {"changed": [], "all": []},
        ),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(db_path),
        "--full",
    ])


def test_cmd_ingest_full_persists_file_list_in_state(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (
            IndexStoreResult(),
            {
                "changed": [],
                "all": [
                    "/repo/ios-client/Zoom/AppDelegate.swift",
                    "/repo/ios-client/Zoom/LoginViewController.m",
                ],
            },
        ),
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore.list_source_files",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full ingest should reuse file list from read_index_store")
        ),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
        "--full",
    ])

    state_path = tmp_path / ".orchard" / "ingest-state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["files"] == [
        "/repo/ios-client/Zoom/AppDelegate.swift",
        "/repo/ios-client/Zoom/LoginViewController.m",
    ]


def test_cmd_ingest_full_writes_candidate_output_paths_manifest(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (
            IndexStoreResult(),
            {"changed": [], "all": ["/repo/ios-client/Zoom/AppDelegate.swift"]},
            [
                {
                    "main_file": "/repo/ios-client/Zoom/AppDelegate.swift",
                    "output_file": "/tmp/opaque/AppDelegate-1.o",
                    "unit_name": "AppDelegate-1.o-opaque",
                }
            ],
        ),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
        "--full",
    ])

    manifest_path = tmp_path / ".orchard" / "candidate-output-paths.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["index_store_path"] == "/fake/store"
    assert data["compiled_targets"] == ["T"]
    assert data["output_paths"] == ["/tmp/opaque/AppDelegate-1.o"]
    assert data["mappings"] == [
        {
            "main_file": "/repo/ios-client/Zoom/AppDelegate.swift",
            "output_file": "/tmp/opaque/AppDelegate-1.o",
            "unit_name": "AppDelegate-1.o-opaque",
        }
    ]


def test_cmd_ingest_incremental_prints_diagnostics_and_fast_path(tmp_path, monkeypatch, capsys):
    from orchard.cli import cmd_ingest

    class DummyConn:
        def close(self):
            return None

    events: list[str] = []

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.state.load_state",
        lambda _project_dir: {"last_ingest_ts": 123.0, "compiled_targets": ["T"], "index_store_path": "/fake/store"},
    )
    monkeypatch.setattr("orchard.ingest.indexstore._unit_dir_mtime", lambda _path: 120.0)
    monkeypatch.setattr(
        "orchard.ingest.indexstore.register_indexd_session",
        lambda **kwargs: events.append("register") or {"sessionId": "session-fast-path"},
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore.warm_indexd_session_async",
        lambda *args, **kwargs: events.append("warm") or True,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setenv("ORCHARD_LOG_LEVEL", "debug")

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
        "--incremental",
    ])

    out = capsys.readouterr().out
    state_path = tmp_path / ".orchard" / "ingest-state.json"
    assert f"incremental: state path {state_path}" in out
    assert "incremental: last_ingest_ts 123.0" in out
    assert "incremental: index-store /fake/store" in out
    assert "incremental: unit_ts 120.0" in out
    assert "ingest: reading index store..." not in out
    assert "incremental: fast path hit" in out
    assert events == ["register", "warm"]


def test_cmd_ingest_fast_path_hides_incremental_detail_logs_by_default(tmp_path, monkeypatch, capsys):
    from orchard.cli import cmd_ingest

    events: list[str] = []

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.state.load_state",
        lambda _project_dir: {
            "last_ingest_ts": 123.0,
            "compiled_targets": ["T"],
            "index_store_path": "/fake/store",
        },
    )
    monkeypatch.setattr("orchard.ingest.indexstore._unit_dir_mtime", lambda _path: 120.0)
    monkeypatch.setattr(
        "orchard.ingest.indexstore.register_indexd_session",
        lambda **kwargs: events.append("register") or {"sessionId": "session-fast-path"},
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore.warm_indexd_session_async",
        lambda *args, **kwargs: events.append("warm") or True,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)
    monkeypatch.delenv("ORCHARD_LOG_LEVEL", raising=False)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "T",
        "--db", str(tmp_path / "graph.db"),
        "--incremental",
    ])

    out = capsys.readouterr().out
    state_path = tmp_path / ".orchard" / "ingest-state.json"
    assert f"incremental: state path {state_path}" not in out
    assert "incremental: last_ingest_ts 123.0" not in out
    assert "incremental: index-store /fake/store" not in out
    assert "incremental: unit_ts 120.0" not in out
    assert "incremental: fast path hit" in out
    assert events == ["register", "warm"]


def test_cmd_ingest_replaces_state_with_latest_compiled_scope(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.state.load_state",
        lambda _project_dir: {
            "last_ingest_ts": 123.0,
            "compiled_targets": ["Zoom"],
            "index_store_path": "/old/store",
        },
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (IndexStoreResult(), None),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/new/store",
        "--project-dir", str(tmp_path),
        "--target", "zPSApp",
        "--db", str(tmp_path / "graph.db"),
        "--full",
    ])

    state_path = tmp_path / ".orchard" / "ingest-state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["compiled_targets"] == ["zPSApp"]
    assert data["index_store_path"] == "/new/store"


def test_cmd_ingest_persists_compiled_scope_state(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (IndexStoreResult(), None),
    )
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: ["/repo/ios-client", "/repo/client-app-video/zPSApp"],
        raising=False,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
    ])

    state_path = tmp_path / ".orchard" / "ingest-state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["compiled_targets"] == ["Zoom", "zPSApp"]
    assert data["index_store_path"] == str(derived_data / "Index.noindex" / "DataStore")
    assert "targets" not in data
    assert "index_store_paths" not in data


def test_cmd_ingest_registers_indexd_session_with_normalized_context(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"
    captured: dict[str, object] = {}
    events: list[str] = []

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: ["/repo/ios-client", "/repo/client-app-video/zPSApp"],
        raising=False,
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore.register_indexd_session",
        lambda **kwargs: events.append("register") or captured.update(kwargs) or {"sessionId": "session-1"},
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: events.append("read") or (IndexStoreResult(), None, None),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
    ])

    assert events[:2] == ["register", "read"]
    assert captured == {
        "project_dir": str(tmp_path.resolve()),
        "index_store_path": str((derived_data / "Index.noindex" / "DataStore").resolve()),
        "graph_db_path": str((tmp_path / ".orchard" / "graph.db").resolve()),
        "target_args": ["Zoom", "zPSApp"],
        "entry_target": "Zoom",
        "incremental": True,
    }


def test_cmd_ingest_logs_and_upserts_compiled_scope_once(tmp_path, monkeypatch, capsys):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult, SymbolLineRecord

    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()
    derived_data = tmp_path / "Zoom-abc"
    calls: dict[str, int] = {"symbols": 0, "calls": 0, "struct": 0}

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (
            IndexStoreResult(
                symbols=[
                    SymbolLineRecord(
                        usr="s:Shared",
                        name="Shared",
                        symbol_kind="function",
                        language="swift",
                        module="Zoom",
                        file_path="/src/Shared.swift",
                    )
                ]
            ),
            None,
        ),
    )
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(project))
    monkeypatch.setattr(
        "orchard.build.xcode_settings.match_derived_data",
        lambda _: [(str(derived_data), str(derived_data / "Index.noindex" / "DataStore"), "2026-06-29T00:00:00Z")],
    )
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr(
        "orchard.build.xcode_settings.resolve_source_roots_for_targets",
        lambda project_path, targets: ["/repo/ios-client", "/repo/client-app-video/zPSApp"],
        raising=False,
    )
    monkeypatch.setattr(
        "orchard.normalize.identity.upsert_symbols",
        lambda *args, **kwargs: calls.__setitem__("symbols", calls["symbols"] + 1) or 0,
    )
    monkeypatch.setattr(
        "orchard.normalize.identity.upsert_calls",
        lambda *args, **kwargs: calls.__setitem__("calls", calls["calls"] + 1) or 0,
    )
    monkeypatch.setattr(
        "orchard.normalize.identity.upsert_indexstore_rels",
        lambda *args, **kwargs: calls.__setitem__("struct", calls["struct"] + 1) or 0,
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_files", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
    ])

    out = capsys.readouterr().out
    assert "ingest: reading index store..." in out
    assert "scope: Zoom,zPSApp" in out
    assert "communities: import took" in out
    assert "notification-graph: scanning source files..." in out
    assert "processes: detecting execution flows..." in out
    assert "[1/2]" not in out
    assert "[2/2]" not in out
    assert calls == {"symbols": 1, "calls": 1, "struct": 1}


def test_cmd_ingest_persists_build_snapshot_for_freshness(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (IndexStoreResult(), None),
    )
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        "orchard.normalize.identity.upsert_build_snapshot",
        lambda _conn, ctx: captured.update(
            {
                "build_id": ctx.build_id,
                "target": ctx.target,
                "workspace_root": ctx.workspace_root,
                "index_store_path": ctx.index_store_path,
            },
        ),
    )

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
        "--db", str(tmp_path / "graph.db"),
        "--full",
    ])

    assert captured["build_id"]
    assert captured["target"] == "Zoom"
    assert captured["workspace_root"] == str(tmp_path.resolve())
    assert captured["index_store_path"] == "/fake/store"


def test_cmd_ingest_writes_build_snapshot_to_db(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest, _default_build_id
    from orchard.ingest.indexstore import IndexStoreResult

    db_path = tmp_path / "graph.db"
    monkeypatch.setattr(
        "orchard.ingest.indexstore.read_index_store",
        lambda *args, **kwargs: (IndexStoreResult(), None),
    )

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "Zoom",
        "--db", str(db_path),
        "--full",
    ])

    conn = get_connection(str(db_path))
    build_id = _default_build_id(conn, "Zoom")
    rows = conn.execute(
        "MATCH (b:BuildSnapshot) RETURN b.id, b.index_store_path, b.workspace_root"
    ).get_all()
    conn.close()

    assert build_id is not None
    assert rows == [[build_id, "/fake/store", str(tmp_path.resolve())]]


def test_cmd_ingest_incremental_does_not_fast_path_new_target(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    def fake_read_index_store(
        index_store_path,
        scope_id,
        source_root=None,
        source_roots=None,
        incremental_since=None,
        targets=None,
    ):
        captured["scope_id"] = scope_id
        captured["incremental_since"] = incremental_since
        return IndexStoreResult(), None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr(
        "orchard.ingest.state.load_state",
        lambda _project_dir: {
            "last_ingest_ts": 123.0,
            "compiled_targets": ["Zoom"],
            "index_store_path": "/fake/store",
        },
    )
    monkeypatch.setattr("orchard.ingest.indexstore._unit_dir_mtime", lambda _path: 120.0)
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest([
        "--index-store", "/fake/store",
        "--project-dir", str(tmp_path),
        "--target", "zPSApp",
        "--db", str(tmp_path / "graph.db"),
        "--incremental",
    ])

    assert captured["scope_id"] == "zPSApp"
    assert captured["incremental_since"] == 123.0


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


def test_cmd_indexd_status_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        "orchard.ingest.indexstore.indexd_status",
        lambda _socket_path=None: {
            "socket_path": "/tmp/orchard-indexd.sock",
            "running": True,
            "matches_current_build": True,
        },
    )

    cmd_indexd(["status"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is True
    assert payload["matches_current_build"] is True


def test_cmd_indexd_shutdown_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        "orchard.ingest.indexstore.shutdown_indexd",
        lambda _socket_path=None: {
            "stopped": True,
            "status": {"running": False},
        },
    )

    cmd_indexd(["shutdown"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped"] is True
    assert payload["status"]["running"] is False


def test_cmd_update_invokes_uv_tool_upgrade(monkeypatch):
    import subprocess

    calls = []

    def fake_run(cmd, text=False):
        calls.append((cmd, text))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("orchard.cli.shutil.which", lambda name: "/opt/homebrew/bin/uv" if name == "uv" else None)
    monkeypatch.setattr("orchard.cli.subprocess.run", fake_run)

    cmd_update([])

    assert calls == [(["/opt/homebrew/bin/uv", "tool", "upgrade", "orchard"], True)]


def test_cmd_update_runs_setup_after_success(monkeypatch):
    import subprocess

    setup_calls = []

    monkeypatch.setattr("orchard.cli.shutil.which", lambda _name: "uv")
    monkeypatch.setattr(
        "orchard.cli.subprocess.run",
        lambda cmd, text=False: subprocess.CompletedProcess(cmd, 0),
    )
    monkeypatch.setattr("orchard.cli.cmd_setup", lambda args: setup_calls.append(args))

    cmd_update(["--setup"])

    assert setup_calls == [[]]


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
    ], scope_id="MyLib")
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
    monkeypatch.setattr("orchard.normalize.identity.upsert_build_snapshot", lambda *args, **kwargs: None)

    cmd_ingest(["--project-dir", str(cwd_parent), "--target", "Zoom"])

    assert captured["db_path"] == str(project_dir / ".orchard" / "graph.db")
