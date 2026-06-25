"""Tests: freshness annotation wired into impact_analysis."""
import os
import time
from datetime import datetime, timezone
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.handlers.impact import ImpactRequest, impact_analysis


def test_impact_annotates_stale_dependents(tmp_path):
    """File modified after build snapshot created_at → stale annotation."""
    conn = get_connection(":memory:")
    init_schema(conn)
    # Build snapshot created in the past
    past_iso = datetime.fromtimestamp(time.time() - 10000, tz=timezone.utc).isoformat()
    conn.execute(
        "CREATE (b:BuildSnapshot {id: 'build-1', created_at: $ts})",
        {"ts": past_iso},
    )
    # File modified NOW (after snapshot)
    stale_file = tmp_path / "stale.swift"
    stale_file.write_text("x")
    time.sleep(0.01)
    syms = [
        SymbolRecord(usr="s:target", name="target", kind="method", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:caller", name="caller", kind="method", module="Test",
                     language="swift", file_path=str(stale_file), signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (a:Symbol {usr:'s:caller'}),(t:Symbol {usr:'s:target'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(t)")
    r = impact_analysis(conn, ImpactRequest(usr="s:target", target_id="Test", build_id="build-1"))
    # NOT filtered out
    d1_usrs = {d["usr"] for d in r.data["by_depth"].get("d1", [])}
    assert "s:caller" in d1_usrs
    # Annotated stale
    assert any("stale" in g.lower() for g in r.open_gaps)


def test_impact_no_snapshot_no_annotation(tmp_path):
    """No build snapshot → no freshness annotation."""
    conn = get_connection(":memory:")
    init_schema(conn)
    stale_file = tmp_path / "stale.swift"
    stale_file.write_text("x")
    syms = [
        SymbolRecord(usr="s:target", name="target", kind="method", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:caller", name="caller", kind="method", module="Test",
                     language="swift", file_path=str(stale_file), signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (a:Symbol {usr:'s:caller'}),(t:Symbol {usr:'s:target'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(t)")
    r = impact_analysis(conn, ImpactRequest(usr="s:target", target_id="Test"))
    assert not any("stale" in g.lower() for g in r.open_gaps)


def test_impact_skips_empty_filepath(tmp_path):
    conn = get_connection(":memory:")
    init_schema(conn)
    past_iso = datetime.fromtimestamp(time.time() - 10000, tz=timezone.utc).isoformat()
    conn.execute("CREATE (b:BuildSnapshot {id: 'build-1', created_at: $ts})", {"ts": past_iso})
    syms = [
        SymbolRecord(usr="s:target", name="target", kind="method", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:caller", name="caller", kind="method", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (a:Symbol {usr:'s:caller'}),(t:Symbol {usr:'s:target'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(t)")
    r = impact_analysis(conn, ImpactRequest(usr="s:target", target_id="Test", build_id="build-1"))
    assert not any("stale" in g.lower() for g in r.open_gaps)
