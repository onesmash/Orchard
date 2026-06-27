"""Tests: Leiden community detection."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.community_detection import run_community_detection


def test_leiden_no_giant_component():
    """Leiden should produce balanced communities, not one giant component."""
    conn = get_connection(":memory:")
    init_schema(conn)
    syms = [
        SymbolRecord(usr=f"s:{name}", name=name, kind="function", module="T",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id="")
        for name in ("a", "b", "c", "d", "e", "f")
    ]
    upsert_symbols(conn, syms, "T")
    conn.execute("MATCH (a:Symbol {usr:'s:a'}),(b:Symbol {usr:'s:b'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:b'}),(b:Symbol {usr:'s:a'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:c'}),(b:Symbol {usr:'s:d'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:d'}),(b:Symbol {usr:'s:c'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:e'}),(b:Symbol {usr:'s:f'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")
    conn.execute("MATCH (a:Symbol {usr:'s:a'}),(b:Symbol {usr:'s:c'}) CREATE (a)-[:Calls {source:'test',confidence:0.9}]->(b)")

    result = run_community_detection(conn, "T")
    assert result["communities_found"] >= 2, "Should find >=2 communities"
    rows = conn.execute("MATCH (c:Community) RETURN c.size ORDER BY c.size DESC").get_all()
    total = sum(r[0] for r in rows)
    assert rows[0][0] / total < 0.67, f"Giant component: {rows[0][0]}/{total}"
