import pytest
from orchard.normalize.identity import (
    make_symbol_id,
    prune_missing_symbols,
    upsert_symbols,
    upsert_symbol_rels,
    upsert_calls,
    upsert_references,
)
from orchard.ingest.indexstore import RelationRecord
from orchard.ingest.symbolgraph import SymbolRecord, SymbolRelRecord
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_db_path):
    c = get_connection(tmp_db_path)
    init_schema(c)
    yield c
    c.close()


def test_make_symbol_id():
    assert make_symbol_id("s:MyFunc") == "s:MyFunc"


def test_upsert_symbols_inserts_nodes(conn):
    symbols = [
        SymbolRecord(
            usr="s:MyFunc", precise_id="s:MyFunc", name="MyFunc()",
            kind="swift.func", module="MyModule", language="swift",
            file_path="/src/f.swift", signature="func MyFunc()",
            access_level="internal",
        )
    ]
    count = upsert_symbols(conn, symbols, scope_id="T1")
    assert count == 1
    rows = conn.execute("MATCH (s:Symbol {id: 's:MyFunc'}) RETURN s.name").get_all()
    assert rows[0][0] == "MyFunc()"


def test_upsert_symbols_idempotent(conn):
    symbols = [
        SymbolRecord(usr="s:A", precise_id="s:A", name="A", kind="swift.class",
                     module="M", language="swift", file_path=None, signature=None,
                     access_level="public")
    ]
    upsert_symbols(conn, symbols, scope_id="T1")
    upsert_symbols(conn, symbols, scope_id="T1")
    rows = conn.execute("MATCH (s:Symbol) RETURN count(s)").get_all()
    assert rows[0][0] == 1


def test_upsert_symbols_updates_existing_node_fields(conn):
    original = [
        SymbolRecord(usr="s:A", precise_id="s:A", name="swiftName(_:)", kind="method",
                     module="M", language="objc", file_path="/wrong/Ref.swift", signature="",
                     access_level="public", swift_display_name="swiftName(_:)")
    ]
    corrected = [
        SymbolRecord(usr="s:A", precise_id="s:A", name="objcName:", kind="method",
                     module="M", language="objc", file_path="/right/A.m", signature="",
                     access_level="public", swift_display_name="swiftName(_:)")
    ]
    upsert_symbols(conn, original, scope_id="T1")
    upsert_symbols(conn, corrected, scope_id="T1")
    rows = conn.execute(
        "MATCH (s:Symbol {id: 's:A'}) RETURN s.name, s.file_path, s.swift_display_name"
    ).get_all()
    assert rows == [["objcName:", "/right/A.m", "swiftName(_:)"]]


def test_upsert_symbols_skips_redundant_updates_for_existing_metadata(conn):
    sym = SymbolRecord(
        usr="s:Shared",
        precise_id="s:Shared",
        name="Shared",
        kind="swift.struct",
        module="M",
        language="swift",
        file_path="/src/shared.swift",
        signature="",
        access_level="public",
    )
    upsert_symbols(conn, [sym], scope_id="TargetA")

    class RecordingConn:
        def __init__(self, inner):
            self.inner = inner
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append(query)
            if params is None:
                return self.inner.execute(query)
            return self.inner.execute(query, params)

    recording = RecordingConn(conn)
    upsert_symbols(recording, [sym], scope_id="TargetB")

    redundant_updates = [
        q for q in recording.queries
        if "SET s.precise_id=$precise_id" in q
    ]
    assert redundant_updates == []


def test_upsert_different_targets_no_collision(conn):
    """USR-only IDs mean one symbol per USR globally; second upsert updates metadata."""
    sym = SymbolRecord(usr="s:Shared", precise_id="s:Shared", name="Shared",
                       kind="swift.struct", module="M", language="swift",
                       file_path=None, signature=None, access_level="public")
    upsert_symbols(conn, [sym], scope_id="TargetA")
    upsert_symbols(conn, [sym], scope_id="TargetB")
    rows = conn.execute("MATCH (s:Symbol) RETURN s.id ORDER BY s.id").get_all()
    assert len(rows) == 1
    assert rows[0][0] == "s:Shared"  # USR-only ID


def _seed_two_symbols(conn, scope_id):
    """Seed two minimal Symbol records: caller() and callee()."""
    syms = [
        SymbolRecord(usr="c:caller()", precise_id="", name="caller",
                     kind="function", module="M", language="swift",
                     file_path=None, signature=None,
                     access_level="public", container_usr=None),
        SymbolRecord(usr="c:callee()", precise_id="", name="callee",
                     kind="function", module="M", language="swift",
                     file_path=None, signature=None,
                     access_level="public", container_usr=None),
    ]
    upsert_symbols(conn, syms, scope_id)


def test_upsert_calls_writes_calledby_edge_with_caller_as_to(conn):
    scope_id = "MyLib"
    _seed_two_symbols(conn, scope_id)
    # role calledBy: from_usr (callee) is called by to_usr (caller)
    # => caller calls callee => Calls(caller -> callee)
    rels = [RelationRecord(from_usr="c:callee()", to_usr="c:caller()", role="calledBy")]
    written = upsert_calls(conn, rels, scope_id, source="indexstore",
                           build_id="build-1")
    assert written == 1
    rows = conn.execute(
        "MATCH (a:Symbol)-[r:Calls]->(b:Symbol) RETURN a.usr, b.usr, r.reason"
    ).get_all()
    assert [tuple(r) for r in rows] == [("c:caller()", "c:callee()", "indexstore_relation_only")]


def test_upsert_calls_marks_source_direct_when_call_occurrence_present(conn):
    scope_id = "T"
    _seed_two_symbols(conn, scope_id)
    rels = [
        RelationRecord(
            from_usr="c:callee()",
            to_usr="c:caller()",
            role="calledBy",
            occurrence_role="call",
            file_path="/src/Test.swift",
            line=12,
            col=4,
        )
    ]
    upsert_calls(conn, rels, scope_id, source="indexstore", build_id="b1")
    rows = conn.execute(
        "MATCH (:Symbol {usr:'c:caller()'})-[r:Calls]->(:Symbol {usr:'c:callee()'}) RETURN r.reason"
    ).get_all()
    assert rows == [["source_direct"]]


def test_upsert_calls_skips_unknown_role(conn):
    scope_id = "MyLib"
    _seed_two_symbols(conn, scope_id)
    rels = [RelationRecord(from_usr="c:callee()", to_usr="c:caller()", role="childOf")]
    assert upsert_calls(conn, rels, scope_id, source="indexstore", build_id="b") == 0
    rows = conn.execute("MATCH ()-[r:Calls]->() RETURN count(r)").get_all()
    assert rows[0][0] == 0


def test_upsert_calls_reuses_existing_global_symbol_from_other_target(conn):
    upsert_symbols(conn, [
        SymbolRecord(usr="s:shared", precise_id="s:shared", name="shared", kind="swift.func",
                     module="M", language="swift", file_path="/src/shared.swift",
                     signature="", access_level="public"),
    ], scope_id="T1")

    rels = [RelationRecord(from_usr="s:shared", to_usr="s:newCaller", role="calledBy")]

    written = upsert_calls(conn, rels, scope_id="T2", source="indexstore", build_id="b2")

    assert written == 1
    rows = conn.execute(
        "MATCH (a:Symbol)-[:Calls]->(b:Symbol) RETURN a.usr, b.usr ORDER BY a.usr, b.usr"
    ).get_all()
    assert rows == [["s:newCaller", "s:shared"]]
    symbols = conn.execute("MATCH (s:Symbol) RETURN s.usr, s.origin ORDER BY s.usr").get_all()
    assert symbols == [
        ["s:newCaller", "indexstore_placeholder"],
        ["s:shared", "swift_symbolgraph"],
    ]


def test_upsert_references_writes_edge(conn):
    scope_id = "MyLib"
    _seed_two_symbols(conn, scope_id)
    rels = [RelationRecord(from_usr="c:caller()", to_usr="c:callee()", role="references")]
    written = upsert_references(conn, rels, scope_id, source="indexstore")
    assert written == 1
    rows = conn.execute(
        "MATCH (a:Symbol)-[:References]->(b:Symbol) RETURN a.usr, b.usr"
    ).get_all()
    assert [tuple(r) for r in rows] == [("c:caller()", "c:callee()")]


def test_prune_missing_symbols_removes_symbols_not_in_current_build(conn):
    upsert_symbols(conn, [
        SymbolRecord(usr="s:old", precise_id="s:old", name="old", kind="swift.func",
                     module="M", language="swift", file_path="/src/old.swift",
                     signature="", access_level="public"),
        SymbolRecord(usr="s:new", precise_id="s:new", name="new", kind="swift.func",
                     module="M", language="swift", file_path="/src/new.swift",
                     signature="", access_level="public"),
    ], scope_id="T1")

    removed = prune_missing_symbols(conn, "T1", {"s:new"})
    rows = conn.execute("MATCH (s:Symbol) RETURN s.usr ORDER BY s.usr").get_all()

    assert removed == 1
    assert rows == [["s:new"]]


def test_prune_missing_symbols_prunes_against_compiled_scope(conn):
    """Pruning now compares against the compiled scope's active USRs globally."""
    upsert_symbols(conn, [
        SymbolRecord(usr="s:shared", precise_id="s:shared", name="shared", kind="swift.func",
                     module="M", language="swift", file_path="/src/shared.swift",
                     signature="", access_level="public"),
    ], scope_id="T1")
    upsert_symbols(conn, [
        SymbolRecord(usr="s:a", precise_id="s:a", name="a", kind="swift.func",
                     module="M", language="swift", file_path="/src/a.swift",
                     signature="", access_level="public"),
    ], scope_id="T1")
    upsert_symbols(conn, [
        SymbolRecord(usr="s:b", precise_id="s:b", name="b", kind="swift.func",
                     module="M", language="swift", file_path="/src/b.swift",
                     signature="", access_level="public"),
    ], scope_id="T2")

    removed = prune_missing_symbols(conn, "T1", {"s:shared"})
    rows = conn.execute("MATCH (s:Symbol) RETURN s.id ORDER BY s.id").get_all()

    assert removed == 2
    assert rows == [["s:shared"]]
