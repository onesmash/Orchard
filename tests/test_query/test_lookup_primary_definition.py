"""Tests for primary_definition_usr in GraphLookup."""
from orchard.graph.db import get_connection, init_schema
from orchard.query.lookup import GraphLookup
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord


def _seed(conn):
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:single", name="single", kind="class", module="Test",
                     language="swift", file_path="/c.swift", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:multiTarget", name="multiTarget", kind="class", module="Test",
                     language="swift", file_path="/a.swift", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:multiTarget", name="multiTarget", kind="class", module="Other",
                     language="swift", file_path="/b.swift", signature="",
                     access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, [syms[0], syms[1]], "Test")
    upsert_symbols(conn, [syms[2]], "Other")


def test_primary_definition_returns_symbol_id():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    result = g.primary_definition_usr("s:single")
    assert result is not None
    assert result == "s:single"


def test_primary_definition_deterministic_across_targets():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    # USR alone provides deterministic identity
    r1 = g.primary_definition_usr("s:multiTarget")
    r2 = g.primary_definition_usr("s:multiTarget")
    assert r1 is not None
    assert r2 is not None
    assert r1 == r2


def test_primary_definition_not_found():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert g.primary_definition_usr("nonexistent") is None


def test_primary_definition_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    g = GraphLookup(conn)
    assert g.primary_definition_usr("anything") is None
