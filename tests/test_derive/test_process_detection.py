"""Tests: process detection via BFS from entry points."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.process_detection import run_process_detection


def test_process_detection_creates_process_nodes():
    conn = get_connection(":memory:")
    init_schema(conn)
    # entry (no callers) → callee1 → callee2
    syms = [
        SymbolRecord(usr="s:entry", name="entry", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:c1", name="c1", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:c2", name="c2", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (a:Symbol {usr:'s:entry'}),(b:Symbol {usr:'s:c1'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:c1'}),(b:Symbol {usr:'s:c2'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    result = run_process_detection(conn, "Test")
    assert result["processes_found"] >= 1
    processes = conn.execute("MATCH (p:Process) RETURN count(p)").get_all()
    assert processes[0][0] >= 1
    steps = conn.execute("MATCH ()-[r:STEP_IN_PROCESS]->() RETURN count(r)").get_all()
    assert steps[0][0] >= 1


def test_process_detection_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    result = run_process_detection(conn, "Test")
    assert result["processes_found"] == 0
