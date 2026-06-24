import pytest
from orchard.graph.db import get_connection, init_schema


def test_get_connection_creates_missing_parent_dir(tmp_path):
    """get_connection must create the DB parent directory if absent (fresh install)."""
    nested = tmp_path / "deep" / "nested" / "dir" / "graph.db"
    assert not nested.parent.exists()
    conn = get_connection(str(nested))
    init_schema(conn)
    assert nested.parent.exists()
    conn.close()


def test_init_schema_creates_tables(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Verify Symbol table exists by inserting and querying
    conn.execute(
        "CREATE (:Symbol {id: 'MyTarget:s:MyFunc', usr: 's:MyFunc', "
        "precise_id: '', name: 'MyFunc', language: 'swift', kind: 'function', "
        "module: 'MyModule', target_id: 'MyTarget', file_path: '/src/f.swift', "
        "signature: '', container_usr: '', access_level: 'internal', "
        "origin: 'indexstore', is_generated: false})"
    )
    result = conn.execute("MATCH (s:Symbol) RETURN s.id").get_all()
    assert len(result) == 1
    assert result[0][0] == "MyTarget:s:MyFunc"
    conn.close()


def test_init_schema_is_idempotent(tmp_db_path):
    """Calling init_schema twice should not raise errors (IF NOT EXISTS)."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    init_schema(conn)  # second call must not fail
    conn.close()


def test_all_node_tables_created(tmp_db_path):
    """All expected node tables are present after init_schema."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    expected_node_tables = [
        "BuildSnapshot", "Module", "Target", "File",
        "Symbol", "Occurrence", "Chunk", "Diagnostic",
    ]
    tables_result = conn.execute("CALL show_tables() RETURN *").get_all()
    table_names = {row[1] for row in tables_result}  # name is column index 1

    for table in expected_node_tables:
        assert table in table_names, f"Node table {table!r} not found in schema"

    conn.close()


def test_all_rel_tables_created(tmp_db_path):
    """All expected relationship tables are present after init_schema."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    expected_rel_tables = [
        "ContainsFile", "ContainsTarget", "BuiltTarget", "ObservedFile",
        "Declares", "ContainsChunk", "ContainsOccurrence", "RefersTo",
        "Calls", "References", "Inherits", "Implements", "Imports",
        "ConformsTo", "BridgesTo", "ProducedDiagnostic",
    ]
    tables_result = conn.execute("CALL show_tables() RETURN *").get_all()
    table_names = {row[1] for row in tables_result}

    for table in expected_rel_tables:
        assert table in table_names, f"Rel table {table!r} not found in schema"

    conn.close()
