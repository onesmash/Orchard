import pytest
from orchard.normalize.identity import make_symbol_id, upsert_symbols, upsert_symbol_rels
from orchard.ingest.symbolgraph import SymbolRecord, SymbolRelRecord
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_db_path):
    c = get_connection(tmp_db_path)
    init_schema(c)
    yield c
    c.close()


def test_make_symbol_id():
    assert make_symbol_id("MyTarget", "s:MyFunc") == "MyTarget:s:MyFunc"


def test_upsert_symbols_inserts_nodes(conn):
    symbols = [
        SymbolRecord(
            usr="s:MyFunc", precise_id="s:MyFunc", name="MyFunc()",
            kind="swift.func", module="MyModule", language="swift",
            file_path="/src/f.swift", signature="func MyFunc()",
            access_level="internal",
        )
    ]
    count = upsert_symbols(conn, symbols, target_id="T1")
    assert count == 1
    rows = conn.execute("MATCH (s:Symbol {id: 'T1:s:MyFunc'}) RETURN s.name").get_all()
    assert rows[0][0] == "MyFunc()"


def test_upsert_symbols_idempotent(conn):
    symbols = [
        SymbolRecord(usr="s:A", precise_id="s:A", name="A", kind="swift.class",
                     module="M", language="swift", file_path=None, signature=None,
                     access_level="public")
    ]
    upsert_symbols(conn, symbols, target_id="T1")
    upsert_symbols(conn, symbols, target_id="T1")
    rows = conn.execute("MATCH (s:Symbol) RETURN count(s)").get_all()
    assert rows[0][0] == 1


def test_upsert_different_targets_no_collision(conn):
    sym = SymbolRecord(usr="s:Shared", precise_id="s:Shared", name="Shared",
                       kind="swift.struct", module="M", language="swift",
                       file_path=None, signature=None, access_level="public")
    upsert_symbols(conn, [sym], target_id="TargetA")
    upsert_symbols(conn, [sym], target_id="TargetB")
    rows = conn.execute("MATCH (s:Symbol) RETURN s.id ORDER BY s.id").get_all()
    ids = [r[0] for r in rows]
    assert "TargetA:s:Shared" in ids
    assert "TargetB:s:Shared" in ids
    assert len(ids) == 2
