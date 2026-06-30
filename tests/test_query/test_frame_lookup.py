from orchard.query.frame_lookup import lookup_crash_thread, lookup_frame, parse_frame_text


def test_parse_frame_text_extracts_owner_and_symbol():
    parsed = parse_frame_text("ssb::thread_wrapper_t::process_msg(unsigned int)")
    assert parsed == {
        "qualified_name": "ssb::thread_wrapper_t::process_msg",
        "owner": "thread_wrapper_t",
        "symbol": "process_msg",
        "signature": "unsigned int",
    }


def test_parse_frame_text_extracts_objc_owner_and_selector():
    parsed = parse_frame_text("0 Zoom -[AudioRouteController handleRouteChange:] + 32")
    assert parsed == {
        "qualified_name": "-[AudioRouteController handleRouteChange:]",
        "owner": "AudioRouteController",
        "symbol": "handleRouteChange:",
        "signature": "",
        "language_hint": "objc",
    }


def test_parse_frame_text_extracts_swift_owner_and_symbol():
    parsed = parse_frame_text("0 Zoom Zoom.AudioRouteController.startCall() + 12")
    assert parsed == {
        "qualified_name": "Zoom.AudioRouteController.startCall()",
        "owner": "AudioRouteController",
        "symbol": "startCall()",
        "signature": "",
        "language_hint": "swift",
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


def test_lookup_frame_prefers_objc_owner_when_method_only_matches_are_ambiguous(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sid, usr, container, file_path in [
        (
            "share_method",
            "c:objc(cs)ShareRouteController(im)handleRouteChange:",
            "c:objc(cs)ShareRouteController",
            "/src/share/ShareRouteController.m",
        ),
        (
            "audio_method",
            "c:objc(cs)AudioRouteController(im)handleRouteChange:",
            "c:objc(cs)AudioRouteController",
            "/src/audio/AudioRouteController.m",
        ),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', name: 'handleRouteChange:', "
            f"language: 'objc', kind: 'method', module: 'Zoom', file_path: '{file_path}', "
            f"signature: '', container_usr: '{container}', access_level: 'internal', "
            f"origin: 'derived', is_generated: false}})"
        )

    result = lookup_frame(
        conn,
        "0 Zoom -[AudioRouteController handleRouteChange:] + 32",
        target="Zoom",
        language="objc",
    )

    expected_usr = "c:objc(cs)AudioRouteController(im)handleRouteChange:"
    assert result["resolution"]["method"]["usr"] == expected_usr
    assert result["next"][0]["args"]["usr"] == expected_usr
    conn.close()


def test_lookup_frame_prefers_swift_owner_when_method_only_matches_are_ambiguous(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sid, usr, container, file_path in [
        (
            "share_method",
            "s:Zoom.ShareRouteController.startCall",
            "s:Zoom.ShareRouteController",
            "/src/share/ShareRouteController.swift",
        ),
        (
            "audio_method",
            "s:Zoom.AudioRouteController.startCall",
            "s:Zoom.AudioRouteController",
            "/src/audio/AudioRouteController.swift",
        ),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', name: 'startCall()', "
            f"language: 'swift', kind: 'method', module: 'Zoom', file_path: '{file_path}', "
            f"signature: '', container_usr: '{container}', access_level: 'internal', "
            f"origin: 'derived', is_generated: false}})"
        )

    result = lookup_frame(
        conn,
        "0 Zoom Zoom.AudioRouteController.startCall() + 12",
        target="Zoom",
        language="swift",
    )

    expected_usr = "s:Zoom.AudioRouteController.startCall"
    assert result["resolution"]["method"]["usr"] == expected_usr
    assert result["next"][0]["args"]["usr"] == expected_usr
    conn.close()


def test_lookup_frame_resolves_method_and_direct_callers(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sid, usr, name, kind, container in [
        ("c:@N@ps@S@CPSAudioDeviceRunCtx", "c:@N@ps@S@CPSAudioDeviceRunCtx", "CPSAudioDeviceRunCtx", "cxx.class", ""),
        (
            "c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#",
            "c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#",
            "GetUsingScene",
            "cxx.method",
            "c:@N@ps@S@CPSAudioDeviceRunCtx",
        ),
        (
            "c:@N@ps@S@CPSAudioDeviceController@F@GetMicUsingScene#",
            "c:@N@ps@S@CPSAudioDeviceController@F@GetMicUsingScene#",
            "GetMicUsingScene",
            "cxx.method",
            "",
        ),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', name: '{name}', "
            f"language: 'cxx', kind: '{kind}', module: 'Audio', file_path: '/src/audio.h', "
            f"signature: '', container_usr: '{container}', access_level: 'internal', "
            f"origin: 'derived', is_generated: false}})"
        )
    conn.execute(
        "MATCH (caller:Symbol {id: 'c:@N@ps@S@CPSAudioDeviceController@F@GetMicUsingScene#'}), "
        "(target:Symbol {id: 'c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#'}) "
        "CREATE (caller)-[:Calls {source: 'indexstore', confidence: 1.0, "
        "provenance: 'indexstore', build_id: 'b1', reason: 'source_direct'}]->(target)"
    )

    result = lookup_frame(conn, "ps::CPSAudioDeviceRunCtx::GetUsingScene()")

    assert result["status"]["outcome"] == "match"
    assert "resolved_by_owner_symbol_fallback" in result["diag"]
    assert result["resolution"]["owner"]["usr"] == "c:@N@ps@S@CPSAudioDeviceRunCtx"
    assert result["resolution"]["method"]["usr"] == "c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#"
    assert result["caller_summary"]["direct_callers"][0]["name"] == "GetMicUsingScene"
    assert result["next"][0] == {
        "tool": "orchard_find_references",
        "args": {"usr": "c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#"},
    }
    conn.close()


def test_lookup_frame_explains_hidden_caller_mismatch(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sid, name, container in [
        ("s:Owner", "Owner", ""),
        ("s:method", "crashHere", "s:Owner"),
        ("s:caller", "sourceCaller", ""),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{sid}', precise_id: '', name: '{name}', "
            f"language: 'cxx', kind: 'cxx.method', module: 'M', file_path: '/src/a.cpp', "
            f"signature: '', container_usr: '{container}', access_level: 'internal', "
            f"origin: 'derived', is_generated: false}})"
        )
    conn.execute(
        "MATCH (caller:Symbol {id: 's:caller'}), (target:Symbol {id: 's:method'}) "
        "CREATE (caller)-[:Calls {source: 'indexstore', confidence: 1.0, reason: 'source_direct'}]->(target)"
    )

    result = lookup_frame(
        conn,
        "ns::Owner::crashHere()\nssb::thread_wrapper_t::process_msg(unsigned int)",
    )

    assert "graph_caller_not_in_stack" in result["diag"]
    assert any("inlining" in note for note in result["notes"])
    conn.close()


def test_lookup_crash_thread_summarizes_first_indexed_symbol_and_dispatch(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sid, name, container in [
        ("s:Owner", "Owner", ""),
        ("s:method", "crashHere", "s:Owner"),
        ("s:caller", "sourceCaller", ""),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{sid}', precise_id: '', name: '{name}', "
            f"language: 'cxx', kind: 'cxx.method', module: 'M', file_path: '/src/a.cpp', "
            f"signature: '', container_usr: '{container}', access_level: 'internal', "
            f"origin: 'derived', is_generated: false}})"
        )
    conn.execute(
        "MATCH (caller:Symbol {id: 's:caller'}), (target:Symbol {id: 's:method'}) "
        "CREATE (caller)-[:Calls {source: 'indexstore', confidence: 1.0, reason: 'source_direct'}]->(target)"
    )

    result = lookup_crash_thread(
        conn,
        "Thread 41 Crashed:\n"
        "0 Zoom ns::Owner::crashHere() + 0\n"
        "1 Zoom ssb::thread_wrapper_t::process_msg(unsigned int) (thread.cpp:2077)",
    )

    assert result["status"]["outcome"] == "match"
    assert result["first_indexed_symbol"]["name"] == "crashHere"
    assert result["dispatch_boundaries"][0]["symbol"] == "process_msg"
    assert "graph_caller_not_in_stack" in result["diag"]
    conn.close()


def test_lookup_crash_thread_prefers_exact_owner_when_only_constructor_owner_is_found(tmp_db_path):
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    symbols = [
        (
            "audio_ctor",
            "c:@N@ps@S@CPSAudioDeviceRunCtx@F@CPSAudioDeviceRunCtx#*$@N@ps@S@CPSAudioDeviceController#",
            "CPSAudioDeviceRunCtx",
            "function",
            "",
            "/src/Media/Audio/Device/CPSAudioDeviceRunCtx.cpp",
        ),
        (
            "share_method",
            "c:@N@ps@S@CPSShareCaptureRunCtx@F@GetUsingScene#",
            "GetUsingScene",
            "method",
            "c:@N@ps@S@CPSShareCaptureRunCtx",
            "/src/Media/Share/Capture/CPSShareCaptureRunCtx.cpp",
        ),
        (
            "video_method",
            "c:@N@ps@S@CPSVideoDeviceRunCtx@F@GetUsingScene#",
            "GetUsingScene",
            "method",
            "c:@N@ps@S@CPSVideoDeviceRunCtx",
            "/src/Media/Video/Device/CPSVideoDeviceRunCtx.cpp",
        ),
        (
            "speaker_method",
            "c:@N@ps@S@CPSAudioSpkRunCtx@F@GetUsingScene#",
            "GetUsingScene",
            "method",
            "c:@N@ps@S@CPSAudioSpkRunCtx",
            "/src/Media/Audio/Device/CPSAudioSpkRunCtx.cpp",
        ),
        (
            "audio_method",
            "c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#",
            "GetUsingScene",
            "method",
            "c:@N@ps@S@CPSAudioDeviceRunCtx",
            "/src/Media/Audio/Device/CPSAudioDeviceRunCtx.cpp",
        ),
    ]
    for sid, usr, name, kind, container, file_path in symbols:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sid}', usr: '{usr}', precise_id: '', name: '{name}', "
            f"language: 'cxx', kind: '{kind}', module: 'Zoom', file_path: '{file_path}', "
            f"signature: '', container_usr: '{container}', access_level: 'internal', "
            f"origin: 'derived', is_generated: false}})"
        )

    result = lookup_crash_thread(
        conn,
        "Thread 41 Crashed:\n"
        "0 Zoom ps::CPSAudioDeviceRunCtx::GetUsingScene() + 0\n"
        "1 Zoom ssb::thread_wrapper_t::process_msg(unsigned int) (thread.cpp:2077)",
        target="Zoom",
        language="cxx",
    )

    expected_usr = "c:@N@ps@S@CPSAudioDeviceRunCtx@F@GetUsingScene#"
    assert result["first_indexed_symbol"]["usr"] == expected_usr
    assert result["summary"]["top_frame"]["usr"] == expected_usr
    assert result["frames"][0]["resolution"]["method"]["usr"] == expected_usr
    assert result["next"][0] == {
        "tool": "orchard_find_references",
        "args": {"usr": expected_usr},
    }
    conn.close()
