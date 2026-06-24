"""Tests for cross_language_bridge_recovery phase."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import make_symbol_id, upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.bridge import run_bridge_recovery


def _seed_mixed_symbols(conn, target_id):
    """Seed Swift and ObjC symbols that share names."""
    syms = [
        SymbolRecord(usr="s:swiftFunc", precise_id="", name="swiftFunc",
                     kind="function", module="M", language="swift",
                     file_path="/src/Lib.swift", signature="() -> Void",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="c:objcMethod", precise_id="", name="swiftFunc",
                     kind="function", module="M", language="objc",
                     file_path="/src/Lib.m", signature="() -> Void",
                     access_level="public", container_usr=None),
        SymbolRecord(usr="s:uniqueSwift()", precise_id="", name="uniqueSwift",
                     kind="function", module="M", language="swift",
                     file_path="/src/Lib.swift", signature="() -> Void",
                     access_level="public", container_usr=None),
    ]
    upsert_symbols(conn, syms, target_id)


def test_bridge_recovery_name_match(tmp_db_path):
    """Bridge recovery finds name-matched pairs and writes bidirectional
    BridgesTo edges with confidence 0.70."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyTarget"
    _seed_mixed_symbols(conn, target_id)
    stats = run_bridge_recovery(conn, target_id, build_id="b3")
    assert stats["bridges_by_name"] == 1
    assert stats["total"] == 2

    # Verify BridgesTo edges exist with correct metadata.
    rows = conn.execute(
        "MATCH (a:Symbol)-[r:BridgesTo]->(b:Symbol) "
        "RETURN a.usr, b.usr, r.bridge_kind, r.confidence"
    ).get_all()
    assert len(rows) == 2
    usr_pairs = {(r[0], r[1]) for r in rows}
    # Both directions must exist (bidirectional).
    assert ("s:swiftFunc", "c:objcMethod") in usr_pairs
    assert ("c:objcMethod", "s:swiftFunc") in usr_pairs
    for r in rows:
        assert r[2] == "name_match"
        assert float(r[3]) == 0.70
    conn.close()


def test_bridge_recovery_idempotent(tmp_db_path):
    """Running twice should report zero new edges on the second pass."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyTarget"
    _seed_mixed_symbols(conn, target_id)
    stats1 = run_bridge_recovery(conn, target_id, build_id="b3")
    assert stats1["bridges_by_name"] == 1
    assert stats1["total"] == 2

    stats2 = run_bridge_recovery(conn, target_id, build_id="b3")
    assert stats2["bridges_by_name"] == 0
    assert stats2["total"] == 0

    # Confirm exactly 2 BridgesTo edges exist (no duplicates).
    rows = conn.execute(
        "MATCH ()-[r:BridgesTo]->() RETURN count(r)"
    ).get_all()
    assert rows[0][0] == 2
    conn.close()
