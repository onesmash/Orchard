"""M3 acceptance tests: bridge recovery + impact analysis + bridge query end-to-end.

Validates that M3 features (bridge recovery, get_cross_language_bridges,
impact_analysis) work together correctly with mixed-language symbols.
"""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import make_symbol_id, upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.bridge import run_bridge_recovery
from orchard.mcp.handlers.bridges import BridgesRequest, get_cross_language_bridges
from orchard.mcp.handlers.impact import ImpactRequest, impact_analysis


def test_m3_bridge_recovery_and_impact(tmp_db_path):
    """End-to-end acceptance test for M3 features.

    Covers:
      - Seeding mixed-language symbols (Swift + ObjC) with name overlap
      - Bridge recovery producing BridgesTo edges
      - Querying bridges via get_cross_language_bridges
      - Adding a Calls edge and verifying impact_analysis traverses both
        Calls + BridgesTo
      - Checking response fields (freshness, evidence_sources, open_gaps)
    """
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "M3Target"

    # 1. Seed Swift + ObjC symbols with same name (loadData)
    upsert_symbols(conn, [
        SymbolRecord(usr="s:loadData", precise_id="", name="loadData", kind="function",
                     module="M", language="swift", file_path="/src/Data.swift",
                     signature="()->Data", access_level="public", container_usr=None),
        SymbolRecord(usr="c:loadData:", precise_id="", name="loadData", kind="function",
                     module="M", language="objc", file_path="/src/Data.m",
                     signature="()->Data", access_level="public", container_usr=None),
        SymbolRecord(usr="s:otherFunc", precise_id="", name="otherFunc", kind="function",
                     module="M", language="swift", file_path="/src/Data.swift",
                     signature="()", access_level="public", container_usr=None),
    ], target_id)

    # 2. Run bridge recovery
    stats = run_bridge_recovery(conn, target_id, build_id="m3")
    assert stats["total"] >= 2  # bidirectional BridgesTo

    # 3. Query bridges
    bridges = get_cross_language_bridges(
        conn, BridgesRequest(usr="s:loadData", target_id=target_id, build_id="m3"))
    assert len(bridges.data) >= 1
    assert any(b["bridge_kind"] == "name_match" for b in bridges.data)
    assert bridges.freshness is not None
    assert "cross_language_bridge_recovery" in bridges.evidence_sources

    # 4. Add call edge and verify impact_analysis
    conn.execute(
        "MATCH (a:Symbol {id: $caller}), (b:Symbol {id: $callee}) "
        "CREATE (a)-[:Calls {source:'test', confidence:1.0}]->(b)",
        {"caller": make_symbol_id(target_id, "s:otherFunc"),
         "callee": make_symbol_id(target_id, "s:loadData")},
    )
    impact = impact_analysis(
        conn, ImpactRequest(usr="s:loadData", target_id=target_id, build_id="m3"))
    assert isinstance(impact.data, dict)
    assert "by_depth" in impact.data
    assert "risk" in impact.data

    # d1 should have otherFunc (it calls loadData) and objc bridge target
    d1 = impact.data["by_depth"].get("d1", [])
    d1_usrs = [d["usr"] for d in d1]
    assert "s:otherFunc" in d1_usrs
    # c:loadData: should appear via incoming BridgesTo from bridge recovery
    assert "c:loadData:" in d1_usrs

    # 5. Verify response metadata fields
    assert impact.freshness is not None
    assert "call_graph_derivation" in impact.evidence_sources
    assert "cross_language_bridge_recovery" in impact.evidence_sources

    conn.close()
