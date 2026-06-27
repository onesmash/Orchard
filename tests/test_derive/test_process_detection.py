"""Tests: process detection via BFS from entry points."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.process_detection import detect_processes


def test_process_detection_creates_process_nodes():
    conn = get_connection(":memory:")
    init_schema(conn)
    # entry (no callers) → callee1 → callee2
    syms = [
        SymbolRecord(usr="s:entry", name="handleStart", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:c1", name="c1", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:c2", name="c2", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:c3", name="c3", kind="function", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    # entry has >=3 outgoing Calls — qualifies as entry point
    conn.execute("MATCH (a:Symbol {usr:'s:entry'}),(b:Symbol {usr:'s:c1'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:entry'}),(b:Symbol {usr:'s:c2'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:entry'}),(b:Symbol {usr:'s:c3'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    procs = detect_processes(conn, "Test")
    assert len(procs) >= 1
    processes = conn.execute("MATCH (p:Process) RETURN count(p)").get_all()
    assert processes[0][0] >= 1
    steps = conn.execute("MATCH ()-[r:STEP_IN_PROCESS]->() RETURN count(r)").get_all()
    assert steps[0][0] >= 1


def test_process_detection_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    procs = detect_processes(conn, "Test")
    assert len(procs) == 0


def test_entry_scoring_boosts_known_patterns():
    """handle*/application: patterns should score higher than generic names."""
    from orchard.derive.process_detection import _entry_point_score
    conn = get_connection(":memory:")
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:handle", name="handlePush:", kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:generic", name="doWork", kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:getter", name="getter:body", kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:ca", name="ca", kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:cb", name="cb", kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:cc", name="cc", kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "T")
    for s in ("s:handle", "s:generic", "s:getter"):
        for t in ("s:ca", "s:cb", "s:cc"):
            conn.execute(f"MATCH (a:Symbol {{usr:'{s}'}}),(b:Symbol {{usr:'{t}'}}) CREATE (a)-[:Calls {{source:'test',confidence:0.9}}]->(b)")
    entries = _entry_point_score(conn, 20)
    names = {e["name"]: e["score"] for e in entries}
    # handlePush should score above doWork and getter:body
    assert names["handlePush:"] > names["doWork"], "handlePush should rank higher than doWork"
    assert names["handlePush:"] > names["getter:body"], "handlePush should rank above getter"
    # getter:body penalized but NOT excluded (GitNexus soft penalty)
    assert "getter:body" in names, "getter:body should appear but with lower score"


