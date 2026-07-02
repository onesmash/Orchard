from orchard.graph.db import get_connection, init_schema


def test_read_only_connection_requires_reopen_to_see_external_writes(tmp_path):
    db_path = str(tmp_path / "graph.db")

    writer_conn = get_connection(db_path)
    init_schema(writer_conn)
    writer_conn.execute(
        "CREATE (:Symbol {id: 's:one', usr: 's:one', precise_id: '', name: 'old', kind: 'function', language: 'swift', module: 'Tests', file_path: '', signature: '', container_usr: '', access_level: 'public', origin: 'symbolgraph', is_generated: false})"
    )

    reader_conn = get_connection(db_path, read_only=True)
    before_rows = reader_conn.execute(
        "MATCH (s:Symbol) RETURN s.usr ORDER BY s.usr"
    ).get_all()
    assert before_rows == [["s:one"]]

    writer_conn.execute(
        "CREATE (:Symbol {id: 's:two', usr: 's:two', precise_id: '', name: 'new', kind: 'function', language: 'swift', module: 'Tests', file_path: '', signature: '', container_usr: '', access_level: 'public', origin: 'symbolgraph', is_generated: false})"
    )

    same_conn_rows = reader_conn.execute(
        "MATCH (s:Symbol) RETURN s.usr ORDER BY s.usr"
    ).get_all()
    assert same_conn_rows == [["s:one"]]

    reader_conn.close()
    reopened_reader_conn = get_connection(db_path, read_only=True)
    reopened_rows = reopened_reader_conn.execute(
        "MATCH (s:Symbol) RETURN s.usr ORDER BY s.usr"
    ).get_all()
    assert reopened_rows == [["s:one"], ["s:two"]]

    reopened_reader_conn.close()
    writer_conn.close()
