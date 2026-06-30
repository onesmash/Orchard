from orchard.query.frame_lookup import lookup_frame, parse_frame_text


def test_parse_frame_text_extracts_owner_and_symbol():
    parsed = parse_frame_text("ssb::thread_wrapper_t::process_msg(unsigned int)")
    assert parsed == {
        "qualified_name": "ssb::thread_wrapper_t::process_msg",
        "owner": "thread_wrapper_t",
        "symbol": "process_msg",
        "signature": "unsigned int",
    }


def test_lookup_frame_falls_back_from_qualified_to_owner(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 'u1', usr: 'u1', precise_id: '', name: 'thread_wrapper_t', "
        "language: 'cxx', kind: 'cxx.class', module: 'Core', file_path: '/src/thread.cpp', "
        "signature: '', container_usr: '', access_level: 'internal', origin: 'derived', is_generated: false})"
    )

    result = lookup_frame(conn, "ssb::thread_wrapper_t::process_msg(unsigned int)")
    assert result["status"]["outcome"] in {"near_match", "no_match"}
    assert result["candidates"]["owners"] == ["thread_wrapper_t"]
    assert result["next"][0]["tool"] == "orchard_search"
    conn.close()
