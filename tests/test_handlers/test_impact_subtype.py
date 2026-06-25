"""Tests for transitive_subtype_closure in impact analysis."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.handlers.impact import _subtype_closure


def _seed_hierarchy(conn):
    """Seed: Protocol P → Class A (conforms) → Class B (inherits A), Class C (conforms P)."""
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:P", name="P", kind="protocol", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:A", name="A", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:B", name="B", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:C", name="C", kind="class", module="Test", language="swift",
                     file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    conn.execute("MATCH (a:Symbol {usr: 's:A'}), (p:Symbol {usr: 's:P'}) CREATE (a)-[:ConformsTo {source: 'test'}]->(p)")
    conn.execute("MATCH (b:Symbol {usr: 's:B'}), (a:Symbol {usr: 's:A'}) CREATE (b)-[:Inherits {source: 'test'}]->(a)")
    conn.execute("MATCH (c:Symbol {usr: 's:C'}), (p:Symbol {usr: 's:P'}) CREATE (c)-[:ConformsTo {source: 'test'}]->(p)")


def test_subtype_closure_finds_transitive_chain():
    conn = get_connection(":memory:")
    _seed_hierarchy(conn)
    closure = _subtype_closure(conn, "s:P")
    assert "s:A" in closure
    assert "s:B" in closure  # transitive via A
    assert "s:C" in closure


def test_subtype_closure_respects_max_depth():
    conn = get_connection(":memory:")
    _seed_hierarchy(conn)
    closure = _subtype_closure(conn, "s:P", max_depth=1)
    assert "s:A" in closure
    assert "s:B" not in closure  # depth 2


def test_subtype_closure_leaf_empty():
    conn = get_connection(":memory:")
    _seed_hierarchy(conn)
    assert len(_subtype_closure(conn, "s:B")) == 0


def test_subtype_closure_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    assert len(_subtype_closure(conn, "nonexistent")) == 0
