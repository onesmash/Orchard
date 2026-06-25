"""Tests for containerNames cache and extension handling in GraphLookup.owner_of()."""
from orchard.graph.db import get_connection, init_schema
from orchard.query.lookup import GraphLookup
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord


def _seed(conn):
    """Seed a nested class + extension structure."""
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:Outer", name="Outer", kind="class", module="Test",
                     language="swift", file_path="", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Inner", name="Inner", kind="class", module="Test",
                     language="swift", file_path="", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:method", name="method", kind="method", module="Test",
                     language="swift", file_path="", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Extension", name="Extension", kind="extension", module="Test",
                     language="swift", file_path="", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:ExtMethod", name="extMethod", kind="method", module="Test",
                     language="swift", file_path="", signature="",
                     access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Extended", name="Extended", kind="class", module="Test",
                     language="swift", file_path="", signature="",
                     access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    # Contains: Outer -> Inner -> method
    conn.execute(
        "MATCH (o:Symbol {usr: 's:Outer'}), (i:Symbol {usr: 's:Inner'}) "
        "CREATE (o)-[:Contains {source: 'test'}]->(i)")
    conn.execute(
        "MATCH (o:Symbol {usr: 's:Inner'}), (m:Symbol {usr: 's:method'}) "
        "CREATE (o)-[:Contains {source: 'test'}]->(m)")
    # Extension -> Contains -> ExtMethod, Extension -> Extends -> Extended
    conn.execute(
        "MATCH (e:Symbol {usr: 's:Extension'}), (m:Symbol {usr: 's:ExtMethod'}) "
        "CREATE (e)-[:Contains {source: 'test'}]->(m)")
    conn.execute(
        "MATCH (e:Symbol {usr: 's:Extension'}), (x:Symbol {usr: 's:Extended'}) "
        "CREATE (e)-[:Extends {source: 'test'}]->(x)")


def test_owner_of_returns_immediate_container():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    owner = g.owner_of("s:Inner")
    assert owner is not None
    assert owner["name"] == "Outer"
    assert owner["kind"] == "class"


def test_owner_of_returns_none_for_top_level():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert g.owner_of("s:Outer") is None


def test_owner_of_handles_extension():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    owner = g.owner_of("s:ExtMethod")
    assert owner is not None
    assert owner["name"] == "Extended"
    assert owner["kind"] == "class"


def test_owner_of_caches_result():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert "s:Inner" not in g._container_names_cache
    g.owner_of("s:Inner")
    assert "s:Inner" in g._container_names_cache
    cached = g._container_names_cache["s:Inner"]
    # Second call hits cache — no change
    g.owner_of("s:Inner")
    assert g._container_names_cache["s:Inner"] == cached


def test_owner_of_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    g = GraphLookup(conn)
    assert g.owner_of("nonexistent") is None
