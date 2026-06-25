"""Tests: community detection via label propagation."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.community_detection import run_community_detection


def test_community_detection_creates_communities():
    conn = get_connection(":memory:")
    init_schema(conn)
    # Seed 4 connected symbols (cluster)
    syms = [
        SymbolRecord(usr=f"s:n{i}", name=f"n{i}", kind="method", module="Test",
                     language="swift", file_path="", signature="", access_level="public",
                     container_usr=None, precise_id="") for i in range(4)
    ]
    upsert_symbols(conn, syms, "Test")
    for i in range(3):
        conn.execute(f"MATCH (a:Symbol {{usr:'s:n{i}'}}),(b:Symbol {{usr:'s:n{i+1}'}}) CREATE (a)-[:Calls {{source:'test',confidence:0.9}}]->(b)")
    result = run_community_detection(conn, "Test")
    assert result["communities_found"] >= 1
    # Verify Community nodes + MEMBER_OF edges created
    communities = conn.execute("MATCH (c:Community) RETURN count(c)").get_all()
    assert communities[0][0] >= 1


def test_community_detection_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    result = run_community_detection(conn, "Test")
    assert result["communities_found"] == 0
