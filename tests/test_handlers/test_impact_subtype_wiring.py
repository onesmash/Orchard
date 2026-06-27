"""Tests: subtype closure wired into impact_analysis."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.handlers.impact import ImpactRequest, impact_analysis


def _seed(conn):
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:P", name="P", kind="protocol", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:A", name="A", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:B", name="B", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (a:Symbol {usr:'s:A'}),(p:Symbol {usr:'s:P'}) CREATE (a)-[:ConformsTo {source:'test'}]->(p)")
    conn.execute("MATCH (b:Symbol {usr:'s:B'}),(p:Symbol {usr:'s:P'}) CREATE (b)-[:ConformsTo {source:'test'}]->(p)")


def test_impact_includes_subtypes_in_d1():
    conn = get_connection(":memory:")
    _seed(conn)
    r = impact_analysis(conn, ImpactRequest(usr="s:P"))
    d1 = r.data["by_depth"].get("d1", [])
    d1_usrs = {d["usr"] for d in d1}
    assert "s:A" in d1_usrs
    assert "s:B" in d1_usrs
    # Verify reached_via label
    subtypes = [d for d in d1 if d["usr"] in ("s:A", "s:B")]
    assert all(d["reached_via"] == "subtype_closure" for d in subtypes)


def test_impact_no_duplicate_when_conformer_also_calls():
    conn = get_connection(":memory:")
    _seed(conn)
    # A also calls P (would be reached via Calls too)
    conn.execute("MATCH (a:Symbol {usr:'s:A'}),(p:Symbol {usr:'s:P'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(p)")
    r = impact_analysis(conn, ImpactRequest(usr="s:P"))
    d1 = r.data["by_depth"].get("d1", [])
    a_entries = [d for d in d1 if d["usr"] == "s:A"]
    assert len(a_entries) == 1  # no duplicate
