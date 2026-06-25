"""Acceptance tests for M0-M2 per spec §12.

Validates acceptance scenarios A, D, and H. Scenarios B, C, E, F, and G
are deferred to M3-M5 (they require bridge filtering, multi-target merge,
or other later-milestone capabilities).

Uses an in-process Ladybug graph populated directly with SymbolRecord /
edge data, so the real ``orchard-indexstore-reader`` Swift CLI and Xcode
are not required to run these tests.
"""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.normalize.identity import upsert_symbols, upsert_build_snapshot
from orchard.build.context import BuildContext, make_build_id
from orchard.handlers.symbol_context import get_symbol_context, SymbolContextRequest
from orchard.handlers.callers import find_callers, CallerRequest
from orchard.validation.freshness import freshness_for


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
        "MATCH (a:Symbol {id:'MyLib:s:topLevel'}), (b:Symbol {id:'MyLib:s:myMethod'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:'b1'}]->(b)"
    )
    yield conn, ctx
    conn.close()


# Scenario A: Single-target Swift-only
def test_a_get_symbol_context_returns_structure(populated_db):
    conn, ctx = populated_db
    req = SymbolContextRequest(usr="s:MyClass", target_id="MyLib", build_id=ctx.build_id)
    resp = get_symbol_context(conn, req)
    assert resp.data is not None
    assert resp.data["name"] == "MyClass"
    assert resp.freshness in ("fresh", "stale", "build_mismatch", "toolchain_mismatch", "partially_stale")
    assert len(resp.evidence_sources) > 0


def test_a_find_callers_of_mymethod(populated_db):
    conn, ctx = populated_db
    req = CallerRequest(usr="s:myMethod", target_id="MyLib", build_id=ctx.build_id)
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
    req = SymbolContextRequest(usr="s:MyClass", target_id="MyLib", build_id=ctx.build_id)
    resp = get_symbol_context(conn, req)
    assert hasattr(resp, "open_gaps")
    assert isinstance(resp.open_gaps, list)
    # No low-confidence bridges present -> nothing recorded as an open gap.
    assert resp.open_gaps == []
