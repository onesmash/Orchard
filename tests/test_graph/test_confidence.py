"""Tests for confidence + reason columns on all rel tables."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord


def _seed_two_symbols(conn):
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:A", name="A", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
        SymbolRecord(usr="s:B", name="B", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public",
                     container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")


def _check_rel_table(conn, table_name: str):
    conn.execute(f"""
        MATCH (a:Symbol {{id: 's:A'}}), (b:Symbol {{id: 's:B'}})
        CREATE (a)-[:{table_name} {{source: 'test', confidence: 0.85, reason: 'unit_test'}}]->(b)
    """)
    rows = conn.execute(f"""
        MATCH ()-[r:{table_name}]->()
        WHERE r.confidence = 0.85 AND r.reason = 'unit_test'
        RETURN count(r)
    """).get_all()
    assert rows[0][0] >= 1, f"{table_name}: missing confidence or reason column"


def test_calls_has_confidence():
    conn = get_connection(":memory:")
    _seed_two_symbols(conn)
    _check_rel_table(conn, "Calls")


def test_contains_has_confidence():
    conn = get_connection(":memory:")
    _seed_two_symbols(conn)
    _check_rel_table(conn, "Contains")


def test_inherits_has_confidence():
    conn = get_connection(":memory:")
    _seed_two_symbols(conn)
    _check_rel_table(conn, "Inherits")


def test_implements_has_confidence():
    conn = get_connection(":memory:")
    _seed_two_symbols(conn)
    _check_rel_table(conn, "Implements")


def test_extends_has_confidence():
    conn = get_connection(":memory:")
    _seed_two_symbols(conn)
    _check_rel_table(conn, "Extends")


def test_conforms_to_has_confidence():
    conn = get_connection(":memory:")
    _seed_two_symbols(conn)
    _check_rel_table(conn, "ConformsTo")
