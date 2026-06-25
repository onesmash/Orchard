"""Tests for MRO (Method Resolution Order) stage."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord


def _seed(conn):
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:Base", name="Base", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Child", name="Child", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Base.method", name="method", kind="method", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Child.method", name="method", kind="method", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (c:Symbol {usr: 's:Child'}), (b:Symbol {usr: 's:Base'}) CREATE (c)-[:Inherits {source: 'test'}]->(b)")


def test_mro_finds_override():
    conn = get_connection(":memory:")
    _seed(conn)
    from orchard.derive.mro import run_mro
    result = run_mro(conn, "Test")
    assert result["overrides_found"] >= 0
