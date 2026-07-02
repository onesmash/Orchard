import json
import pytest
from unittest.mock import patch
from orchard.ingest.indexstore import (
    _default_indexd_socket_path,
    _daemon_matches_current_build,
    _cli_path,
    _current_indexd_binary_info,
    _ensure_indexd_running,
    _indexd_pid_path,
    _indexd_path,
    _packaged_binary_path,
    _packaged_binary_relpath,
    _indexd_socket_path,
    _packaged_cli_path,
    _packaged_cli_relpath,
    dump_unit_output_paths,
    indexd_status,
    list_source_files,
    read_index_store,
    OccurrenceRecord,
    RelationRecord,
    shutdown_indexd,
)

_SAMPLE_LINES = [
    json.dumps({"kind": "occurrence", "usr": "s:MyFunc", "file": "/src/f.swift",
                "line": 10, "column": 5, "role": "definition"}),
    json.dumps({"kind": "relation", "from_usr": "s:MyFunc", "to_usr": "s:OtherFunc",
                "role": "calledBy", "occurrence_role": "call",
                "file": "/src/f.swift", "line": 10, "column": 12}),
]

def _mock_cli(lines):
    """Return ``(stdout_lines, stderr)`` like the real ``_run_cli``."""
    return list(lines), ""

def test_read_index_store_parses_occurrences():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result, _, _ = read_index_store("/fake/store", scope_id="MyTarget", emit_occurrences=True)
    assert len(result.occurrences) == 1
    occ = result.occurrences[0]
    assert occ.usr == "s:MyFunc"
    assert occ.file_path == "/src/f.swift"
    assert occ.line == 10
    assert occ.col == 5
    assert occ.role == "definition"

def test_read_index_store_parses_relations():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result, _, _ = read_index_store("/fake/store", scope_id="MyTarget")
    assert len(result.relations) == 1
    rel = result.relations[0]
    assert rel.from_usr == "s:MyFunc"
    assert rel.to_usr == "s:OtherFunc"
    assert rel.role == "calledBy"
    assert rel.occurrence_role == "call"
    assert rel.file_path == "/src/f.swift"
    assert rel.line == 10
    assert rel.col == 12

def test_read_index_store_empty_store():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli([])):
        result, _, _ = read_index_store("/fake/store", scope_id="MyTarget")
    assert result.occurrences == []
    assert result.relations == []


def test_read_index_store_skips_occurrences_by_default():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result, _, _ = read_index_store("/fake/store", scope_id="MyTarget")
    assert result.occurrences == []
    assert len(result.relations) == 1


def test_read_index_store_tolerates_malformed_lines():
    lines = [
        '{"kind":"occurrence","usr":"s:A","file":"f.swift","line":1,"column":1,"role":"definition"}',
        'NOT VALID JSON',
    ]
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(lines)):
        result, _, _ = read_index_store("/fake/store", scope_id="T", emit_occurrences=True)
    assert len(result.occurrences) == 1
    assert len(result.warnings) == 1
    assert "NOT VALID" in result.warnings[0]


def test_read_index_store_tolerates_missing_keys():
    lines = [
        '{"kind":"occurrence","usr":"s:A","file":"f.swift","line":1,"column":1}',
        '{"kind":"relation","from_usr":"a","to_usr":"b"}',
    ]
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(lines)):
        result, _, _ = read_index_store("/fake/store", scope_id="T", emit_occurrences=True)
    assert len(result.occurrences) == 0
    assert len(result.relations) == 0
    assert len(result.warnings) == 2


def test_read_index_store_passes_targets_and_source_roots_to_cli(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_cli(index_store_path, source_root=None, source_roots=None, incremental_since=None, list_files=False, targets=None, emit_occurrences=False, dump_unit_output_paths=False):
        captured["index_store_path"] = index_store_path
        captured["source_root"] = source_root
        captured["source_roots"] = source_roots
        captured["targets"] = targets
        captured["emit_occurrences"] = emit_occurrences
        captured["dump_unit_output_paths"] = dump_unit_output_paths
        return [], json.dumps({"changed": [], "all": []})

    monkeypatch.setattr("orchard.ingest.indexstore._run_cli", fake_run_cli)

    read_index_store(
        "/fake/store",
        scope_id="Zoom",
        source_roots=["/repo/ios-client", "/repo/client-app-common"],
        targets=["Zoom", "zPSApp"],
    )

    assert captured["index_store_path"] == "/fake/store"
    assert captured["source_roots"] == ["/repo/ios-client", "/repo/client-app-common"]
    assert captured["targets"] == ["Zoom", "zPSApp"]
    assert captured["emit_occurrences"] is False
    assert captured["dump_unit_output_paths"] is False


def test_read_index_store_passes_emit_occurrences_to_cli(monkeypatch):
    captured: dict[str, object] = {"emit_occurrences": []}

    def fake_run_cli(index_store_path, source_root=None, source_roots=None, incremental_since=None, list_files=False, targets=None, emit_occurrences=False, dump_unit_output_paths=False):
        captured["emit_occurrences"].append(emit_occurrences)
        return [], json.dumps({"changed": [], "all": []})

    monkeypatch.setattr("orchard.ingest.indexstore._run_cli", fake_run_cli)

    read_index_store("/fake/store", scope_id="Zoom", emit_occurrences=True)

    assert captured["emit_occurrences"][0] is True


def test_indexd_socket_path_reads_environment_dynamically(monkeypatch):
    monkeypatch.setenv("ORCHARD_INDEXD_SOCKET", "/tmp/a.sock")
    assert _indexd_socket_path() == "/tmp/a.sock"
    monkeypatch.setenv("ORCHARD_INDEXD_SOCKET", "/tmp/b.sock")
    assert _indexd_socket_path() == "/tmp/b.sock"


def test_indexd_socket_path_uses_default_when_autostart_enabled(monkeypatch):
    monkeypatch.delenv("ORCHARD_INDEXD_SOCKET", raising=False)
    monkeypatch.setenv("ORCHARD_INDEXD_AUTOSTART", "1")
    assert _indexd_socket_path() == _default_indexd_socket_path()


def test_indexd_pid_path_uses_socket_stem():
    assert _indexd_pid_path("/tmp/orchard-indexd.sock") == "/tmp/orchard-indexd.pid"


def test_daemon_matches_current_build_checks_protocol_and_binary(monkeypatch):
    monkeypatch.setattr(
        "orchard.ingest.indexstore._current_indexd_binary_info",
        lambda: {
            "protocol_version": 1,
            "orchard_version": "0.1.0",
            "executable_path": "/bin/orchard-indexd",
            "binary_size": 123,
            "binary_mtime_ns": 456,
        },
    )

    assert _daemon_matches_current_build({
        "protocolVersion": 1,
        "executablePath": "/bin/orchard-indexd",
        "binarySize": 123,
        "binaryMTimeNs": 456,
    }) is True
    assert _daemon_matches_current_build({
        "protocolVersion": 1,
        "executablePath": "/bin/orchard-indexd",
        "binarySize": 999,
        "binaryMTimeNs": 456,
    }) is False


def test_read_index_store_prefers_indexd_when_socket_is_configured(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_indexd(index_store_path, **kwargs):
        captured["index_store_path"] = index_store_path
        captured["kwargs"] = kwargs
        return [
            json.dumps({
                "kind": "symbol",
                "usr": "s:MyFunc",
                "name": "MyFunc",
                "symbol_kind": "source.lang.swift.decl.function.free",
                "language": "swift",
                "module": "MyTarget",
                "file": "/src/f.swift",
            })
        ], json.dumps({"changed": [], "all": ["/src/f.swift"]})

    monkeypatch.setenv("ORCHARD_INDEXD_SOCKET", "/tmp/indexd.sock")
    monkeypatch.setattr("orchard.ingest.indexstore._ensure_indexd_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("orchard.ingest.indexstore._run_indexd", fake_run_indexd)
    monkeypatch.setattr("orchard.ingest.indexstore._run_cli", lambda *a, **kw: pytest.fail("CLI fallback should not run"))

    result, file_status, _ = read_index_store(
        "/fake/store",
        scope_id="MyTarget",
        source_roots=["/src"],
        targets=["Zoom"],
    )

    assert captured["index_store_path"] == "/fake/store"
    assert len(result.symbols) == 1
    assert file_status == {"changed": [], "all": ["/src/f.swift"]}


def test_read_index_store_falls_back_to_cli_when_indexd_fails(monkeypatch):
    monkeypatch.setenv("ORCHARD_INDEXD_SOCKET", "/tmp/indexd.sock")
    monkeypatch.setattr("orchard.ingest.indexstore._ensure_indexd_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_indexd",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("down")),
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_cli",
        lambda *a, **kw: (
            [json.dumps({
                "kind": "symbol",
                "usr": "s:Fallback",
                "name": "Fallback",
                "symbol_kind": "source.lang.swift.decl.function.free",
                "language": "swift",
                "module": "MyTarget",
                "file": "/src/f.swift",
            })],
            json.dumps({"changed": [], "all": ["/src/f.swift"]}),
        ),
    )

    result, file_status, _ = read_index_store("/fake/store", scope_id="MyTarget")

    assert len(result.symbols) == 1
    assert result.symbols[0].usr == "s:Fallback"
    assert file_status == {"changed": [], "all": ["/src/f.swift"]}


def test_list_source_files_prefers_indexd_when_socket_is_configured(monkeypatch):
    monkeypatch.setenv("ORCHARD_INDEXD_SOCKET", "/tmp/indexd.sock")
    monkeypatch.setattr("orchard.ingest.indexstore._ensure_indexd_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_indexd",
        lambda *a, **kw: ([], json.dumps(["/src/a.swift", "/src/b.swift"])),
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_cli",
        lambda *a, **kw: pytest.fail("CLI fallback should not run"),
    )

    files = list_source_files("/fake/store", source_root="/src")

    assert files == ["/src/a.swift", "/src/b.swift"]


def test_dump_unit_output_paths_prefers_indexd_when_socket_is_configured(monkeypatch):
    monkeypatch.setenv("ORCHARD_INDEXD_SOCKET", "/tmp/indexd.sock")
    monkeypatch.setattr("orchard.ingest.indexstore._ensure_indexd_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_indexd",
        lambda *a, **kw: (
            [
                json.dumps([
                    {
                        "main_file": "/src/a.swift",
                        "output_file": "/tmp/opaque/A-1.o",
                        "unit_name": "A-1.o-opaque",
                    }
                ])
            ],
            "",
        ),
    )
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_cli",
        lambda *a, **kw: pytest.fail("CLI fallback should not run"),
    )

    mappings = dump_unit_output_paths("/fake/store", source_roots=["/src"], targets=["Zoom"])

    assert mappings == [
        {
            "main_file": "/src/a.swift",
            "output_file": "/tmp/opaque/A-1.o",
            "unit_name": "A-1.o-opaque",
        }
    ]


def test_ensure_indexd_running_starts_daemon_when_ping_fails(monkeypatch):
    calls: list[str] = []

    class FakeClient:
        def __init__(self, _socket_path):
            pass

        def ping(self):
            calls.append("ping")
            if len(calls) >= 2:
                return {"ok": True}
            raise ConnectionError("down")

    monkeypatch.setattr("orchard.ingest.indexstore._IndexdClient", FakeClient)
    monkeypatch.setattr("orchard.ingest.indexstore._start_indexd_process", lambda _socket_path: object())
    monkeypatch.setattr("orchard.ingest.indexstore._wait_for_indexd", lambda _socket_path: True)
    monkeypatch.setattr("orchard.ingest.indexstore._daemon_matches_current_build", lambda info: info == {"ok": True})
    monkeypatch.setattr("orchard.ingest.indexstore._cleanup_stale_indexd_socket", lambda *_args, **_kwargs: None)

    assert _ensure_indexd_running("/tmp/indexd.sock") is True
    assert calls == ["ping", "ping"]


def test_ensure_indexd_running_restarts_on_binary_mismatch(monkeypatch):
    actions: list[str] = []

    class FakeClient:
        def __init__(self, _socket_path):
            pass

        def ping(self):
            actions.append("ping")
            return {"protocolVersion": 1}

        def shutdown(self):
            actions.append("shutdown")

    monkeypatch.setattr("orchard.ingest.indexstore._IndexdClient", FakeClient)
    monkeypatch.setattr("orchard.ingest.indexstore._daemon_matches_current_build", lambda _info: False)
    monkeypatch.setattr("orchard.ingest.indexstore._start_indexd_process", lambda _socket_path: object())
    monkeypatch.setattr("orchard.ingest.indexstore._wait_for_indexd", lambda _socket_path: True)
    monkeypatch.setattr("orchard.ingest.indexstore._cleanup_stale_indexd_socket", lambda *_args, **_kwargs: None)

    assert _ensure_indexd_running("/tmp/indexd.sock") is True
    assert actions[:2] == ["ping", "shutdown"]


def test_run_reader_falls_back_to_cli_when_indexd_cannot_start(monkeypatch):
    monkeypatch.delenv("ORCHARD_INDEXD_SOCKET", raising=False)
    monkeypatch.setenv("ORCHARD_INDEXD_AUTOSTART", "1")
    monkeypatch.setattr("orchard.ingest.indexstore._ensure_indexd_running", lambda _socket_path: False)
    monkeypatch.setattr(
        "orchard.ingest.indexstore._run_cli",
        lambda *a, **kw: (["cli"], ""),
    )

    from orchard.ingest.indexstore import _run_reader

    lines, stderr = _run_reader("/fake/store")

    assert lines == ["cli"]
    assert stderr == ""


def test_indexd_status_reports_ping_and_build_match(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore._indexd_socket_path", lambda: "/tmp/indexd.sock")
    monkeypatch.setattr("orchard.ingest.indexstore._read_indexd_pid", lambda _pid_path: 123)
    monkeypatch.setattr("orchard.ingest.indexstore._is_process_alive", lambda pid: pid == 123)
    monkeypatch.setattr("orchard.ingest.indexstore._daemon_matches_current_build", lambda info: info == {"protocolVersion": 1})
    monkeypatch.setattr("pathlib.Path.exists", lambda self: str(self) in {"/tmp/indexd.sock", "/tmp/indexd.pid"})

    class FakeClient:
        def __init__(self, _socket_path):
            pass

        def ping(self):
            return {"protocolVersion": 1}

    monkeypatch.setattr("orchard.ingest.indexstore._IndexdClient", FakeClient)

    status = indexd_status()

    assert status["running"] is True
    assert status["matches_current_build"] is True
    assert status["pid"] == 123


def test_shutdown_indexd_invokes_client_and_cleans_files(monkeypatch):
    cleaned: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, _socket_path):
            pass

        def shutdown(self):
            return None

    monkeypatch.setattr("orchard.ingest.indexstore._IndexdClient", FakeClient)
    monkeypatch.setattr("orchard.ingest.indexstore._cleanup_stale_indexd_socket", lambda sock, pid: cleaned.append((sock, pid)))
    monkeypatch.setattr("orchard.ingest.indexstore.indexd_status", lambda socket_path=None: {"running": False, "socket_path": socket_path})

    result = shutdown_indexd("/tmp/indexd.sock")

    assert result["stopped"] is True
    assert cleaned == [("/tmp/indexd.sock", "/tmp/indexd.pid")]


def test_read_index_store_parses_file_status_without_incremental():
    lines = [
        json.dumps({"kind": "symbol", "usr": "s:MyFunc", "name": "MyFunc",
                    "symbol_kind": "source.lang.swift.decl.function.free",
                    "language": "swift", "module": "MyTarget",
                    "file": "/src/f.swift"}),
    ]
    stderr = "\n".join([
        "[orchard-indexstore-reader +0.1s] discovered 2 source files to inspect",
        json.dumps({"changed": [], "all": ["/src/a.swift", "/src/b.swift"]}),
    ])

    with patch(
        "orchard.ingest.indexstore._run_cli",
        side_effect=lambda *a, **kw: (list(lines), stderr),
    ):
        _result, file_status, output_path_mappings = read_index_store("/fake/store", scope_id="MyTarget")

    assert file_status == {"changed": [], "all": ["/src/a.swift", "/src/b.swift"]}
    assert output_path_mappings is None


def test_read_index_store_parses_output_path_mappings_for_full_ingest():
    call_count = {"value": 0}

    def fake_run_cli(*_args, **kwargs):
        call_count["value"] += 1
        return [], json.dumps(
            {
                "changed": [],
                "all": ["/src/a.swift"],
                "output_path_mappings": [
                    {
                        "main_file": "/src/a.swift",
                        "output_file": "/tmp/opaque/A-1.o",
                        "unit_name": "A-1.o-opaque",
                    }
                ],
            }
        )

    with patch("orchard.ingest.indexstore._run_cli", side_effect=fake_run_cli):
        _result, file_status, output_path_mappings = read_index_store("/fake/store", scope_id="MyTarget")

    assert call_count["value"] == 1
    assert file_status == {"changed": [], "all": ["/src/a.swift"], "output_path_mappings": [
        {
            "main_file": "/src/a.swift",
            "output_file": "/tmp/opaque/A-1.o",
            "unit_name": "A-1.o-opaque",
        }
    ]}
    assert output_path_mappings == [
        {
            "main_file": "/src/a.swift",
            "output_file": "/tmp/opaque/A-1.o",
            "unit_name": "A-1.o-opaque",
        }
    ]


def test_run_cli_expands_repeated_targets_and_source_roots(monkeypatch):
    captured: dict[str, object] = {}

    class DummyStdout:
        def __iter__(self):
            return iter(())

        def close(self):
            return None

    class DummyStderr:
        def read(self):
            return ""

    class DummyProc:
        def __init__(self, cmd, **_kwargs):
            captured["cmd"] = cmd
            self.stdout = DummyStdout()
            self.stderr = DummyStderr()

        def wait(self):
            return 0

    monkeypatch.setattr("orchard.ingest.indexstore.subprocess.Popen", DummyProc)
    monkeypatch.setattr("orchard.ingest.indexstore._cli_path", lambda: "/bin/orchard-indexstore-reader")

    from orchard.ingest.indexstore import _run_cli

    _run_cli(
        "/fake/store",
        source_roots=["/repo/ios-client", "/repo/client-app-common"],
        targets=["Zoom", "zPSApp"],
    )

    assert captured["cmd"] == [
        "/bin/orchard-indexstore-reader",
        "/fake/store",
        "--source-root", "/repo/ios-client",
        "--source-root", "/repo/client-app-common",
        "--target", "Zoom",
        "--target", "zPSApp",
    ]


def test_run_cli_adds_emit_occurrences_flag(monkeypatch):
    captured: dict[str, object] = {}

    class DummyStdout:
        def __iter__(self):
            return iter(())

        def close(self):
            return None

    class DummyStderr:
        def read(self):
            return ""

    class DummyProc:
        def __init__(self, cmd, **_kwargs):
            captured["cmd"] = cmd
            self.stdout = DummyStdout()
            self.stderr = DummyStderr()

        def wait(self):
            return 0

    monkeypatch.setattr("orchard.ingest.indexstore.subprocess.Popen", DummyProc)
    monkeypatch.setattr("orchard.ingest.indexstore._cli_path", lambda: "/bin/orchard-indexstore-reader")

    from orchard.ingest.indexstore import _run_cli

    _run_cli("/fake/store", emit_occurrences=True)

    assert captured["cmd"] == [
        "/bin/orchard-indexstore-reader",
        "/fake/store",
        "--emit-occurrences",
    ]


def test_run_cli_streams_progress_stderr(monkeypatch, capsys):
    class DummyStdout:
        def __iter__(self):
            return iter(())

        def close(self):
            return None

    class DummyStderr:
        def __iter__(self):
            return iter((
                "[orchard-indexstore-reader +0.1s] pass 1: scanning all files\n",
                "{\"changed\":[],\"all\":[]}\n",
            ))

        def read(self):
            return ""

    class DummyProc:
        def __init__(self, _cmd, **_kwargs):
            self.stdout = DummyStdout()
            self.stderr = DummyStderr()

        def wait(self):
            return 0

    monkeypatch.setattr("orchard.ingest.indexstore.subprocess.Popen", DummyProc)
    monkeypatch.setattr("orchard.ingest.indexstore._cli_path", lambda: "/bin/orchard-indexstore-reader")

    from orchard.ingest.indexstore import _run_cli

    stdout_lines, stderr = _run_cli("/fake/store")
    captured = capsys.readouterr()

    assert stdout_lines == []
    assert "[orchard-indexstore-reader +0.1s] pass 1: scanning all files" in captured.err
    assert '{"changed":[],"all":[]}' not in captured.err
    assert "[orchard-indexstore-reader +0.1s] pass 1: scanning all files" in stderr
    assert '{"changed":[],"all":[]}' in stderr

def test_cli_path_prefers_swiftpm_release_binary(monkeypatch):
    release_suffix = "swift/orchard-indexstore-reader/.build/release/orchard-indexstore-reader"
    bin_suffix = "bin/orchard-indexstore-reader"

    def fake_exists(path):
        s = str(path)
        return s.endswith(release_suffix) or s.endswith(bin_suffix)

    def fake_access(path, mode):
        return fake_exists(path)

    monkeypatch.setattr("pathlib.Path.exists", fake_exists)
    monkeypatch.setattr("orchard.ingest.indexstore.os.access", fake_access)
    monkeypatch.setattr("orchard.ingest.indexstore.shutil.which", lambda _: None)
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_cli_path", lambda: None)

    assert _cli_path().endswith(release_suffix)


def test_cli_path_falls_back_to_bin_when_release_missing(monkeypatch):
    bin_suffix = "bin/orchard-indexstore-reader"

    def fake_exists(path):
        return str(path).endswith(bin_suffix)

    def fake_access(path, mode):
        return fake_exists(path)

    monkeypatch.setattr("pathlib.Path.exists", fake_exists)
    monkeypatch.setattr("orchard.ingest.indexstore.os.access", fake_access)
    monkeypatch.setattr("orchard.ingest.indexstore.shutil.which", lambda _: None)
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_cli_path", lambda: None)

    assert _cli_path().endswith(bin_suffix)


def test_cli_path_prefers_packaged_binary(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_binary_path", lambda name: f"/pkg/{name}" if name == "orchard-indexstore-reader" else None)
    assert _cli_path() == "/pkg/orchard-indexstore-reader"


def test_indexd_path_prefers_packaged_binary(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_binary_path", lambda name: f"/pkg/{name}" if name == "orchard-indexd" else None)
    assert _indexd_path() == "/pkg/orchard-indexd"


def test_packaged_cli_relpath_maps_current_macos_arm(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore.platform.system", lambda: "Darwin")
    monkeypatch.setattr("orchard.ingest.indexstore.platform.machine", lambda: "arm64")
    assert _packaged_cli_relpath() == pytest.importorskip("pathlib").Path("darwin-arm64/orchard-indexstore-reader")


def test_packaged_binary_relpath_maps_indexd_current_macos_arm(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore.platform.system", lambda: "Darwin")
    monkeypatch.setattr("orchard.ingest.indexstore.platform.machine", lambda: "arm64")
    assert _packaged_binary_relpath("orchard-indexd") == pytest.importorskip("pathlib").Path("darwin-arm64/orchard-indexd")


def test_packaged_cli_path_uses_package_dir(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "orchard" / "_bin"
    binary = pkg_dir / "darwin-arm64" / "orchard-indexstore-reader"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    init_py = pkg_dir / "__init__.py"
    init_py.write_text('"""pkg"""')

    fake_module = type("M", (), {"__file__": str(init_py)})()
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_cli_relpath", lambda: pytest.importorskip("pathlib").Path("darwin-arm64/orchard-indexstore-reader"))
    monkeypatch.setattr("orchard.ingest.indexstore.import_module", lambda _: fake_module)

    assert _packaged_cli_path() == str(binary)


def test_packaged_binary_path_uses_package_dir_for_indexd(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "orchard" / "_bin"
    binary = pkg_dir / "darwin-arm64" / "orchard-indexd"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    init_py = pkg_dir / "__init__.py"
    init_py.write_text('"""pkg"""')

    fake_module = type("M", (), {"__file__": str(init_py)})()
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_binary_relpath", lambda name: pytest.importorskip("pathlib").Path(f"darwin-arm64/{name}"))
    monkeypatch.setattr("orchard.ingest.indexstore.import_module", lambda _: fake_module)

    assert _packaged_binary_path("orchard-indexd") == str(binary)

def test_unit_dir_mtime_returns_latest(tmp_path):
    from orchard.ingest.indexstore import _unit_dir_mtime
    units = tmp_path / "v5" / "units"
    units.mkdir(parents=True)
    # Create files with known timestamps
    f1 = units / "a.unit"
    f1.write_text("x")
    f2 = units / "b.unit"
    f2.write_text("y")
    ts = _unit_dir_mtime(str(tmp_path))
    assert ts > 0

def test_unit_dir_mtime_empty(tmp_path):
    from orchard.ingest.indexstore import _unit_dir_mtime
    units = tmp_path / "v5" / "units"
    units.mkdir(parents=True)
    assert _unit_dir_mtime(str(tmp_path)) == 0.0

def test_unit_dir_mtime_missing(tmp_path):
    from orchard.ingest.indexstore import _unit_dir_mtime
    assert _unit_dir_mtime(str(tmp_path / "nope")) == 0.0
