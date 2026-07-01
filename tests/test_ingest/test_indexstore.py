import json
import pytest
from unittest.mock import patch
from orchard.ingest.indexstore import (
    _cli_path,
    _packaged_cli_path,
    _packaged_cli_relpath,
    read_index_store,
    OccurrenceRecord,
    RelationRecord,
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
        result, _ = read_index_store("/fake/store", scope_id="MyTarget", emit_occurrences=True)
    assert len(result.occurrences) == 1
    occ = result.occurrences[0]
    assert occ.usr == "s:MyFunc"
    assert occ.file_path == "/src/f.swift"
    assert occ.line == 10
    assert occ.col == 5
    assert occ.role == "definition"

def test_read_index_store_parses_relations():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result, _ = read_index_store("/fake/store", scope_id="MyTarget")
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
        result, _ = read_index_store("/fake/store", scope_id="MyTarget")
    assert result.occurrences == []
    assert result.relations == []


def test_read_index_store_skips_occurrences_by_default():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result, _ = read_index_store("/fake/store", scope_id="MyTarget")
    assert result.occurrences == []
    assert len(result.relations) == 1


def test_read_index_store_tolerates_malformed_lines():
    lines = [
        '{"kind":"occurrence","usr":"s:A","file":"f.swift","line":1,"column":1,"role":"definition"}',
        'NOT VALID JSON',
    ]
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(lines)):
        result, _ = read_index_store("/fake/store", scope_id="T", emit_occurrences=True)
    assert len(result.occurrences) == 1
    assert len(result.warnings) == 1
    assert "NOT VALID" in result.warnings[0]


def test_read_index_store_tolerates_missing_keys():
    lines = [
        '{"kind":"occurrence","usr":"s:A","file":"f.swift","line":1,"column":1}',
        '{"kind":"relation","from_usr":"a","to_usr":"b"}',
    ]
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(lines)):
        result, _ = read_index_store("/fake/store", scope_id="T", emit_occurrences=True)
    assert len(result.occurrences) == 0
    assert len(result.relations) == 0
    assert len(result.warnings) == 2


def test_read_index_store_passes_targets_and_source_roots_to_cli(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_cli(index_store_path, source_root=None, source_roots=None, incremental_since=None, list_files=False, targets=None, emit_occurrences=False):
        captured["index_store_path"] = index_store_path
        captured["source_root"] = source_root
        captured["source_roots"] = source_roots
        captured["targets"] = targets
        captured["emit_occurrences"] = emit_occurrences
        return [], ""

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


def test_read_index_store_passes_emit_occurrences_to_cli(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_cli(index_store_path, source_root=None, source_roots=None, incremental_since=None, list_files=False, targets=None, emit_occurrences=False):
        captured["emit_occurrences"] = emit_occurrences
        return [], ""

    monkeypatch.setattr("orchard.ingest.indexstore._run_cli", fake_run_cli)

    read_index_store("/fake/store", scope_id="Zoom", emit_occurrences=True)

    assert captured["emit_occurrences"] is True


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
        _result, file_status = read_index_store("/fake/store", scope_id="MyTarget")

    assert file_status == {"changed": [], "all": ["/src/a.swift", "/src/b.swift"]}


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
    monkeypatch.setattr("orchard.ingest.indexstore._packaged_cli_path", lambda: "/pkg/orchard-indexstore-reader")
    assert _cli_path() == "/pkg/orchard-indexstore-reader"


def test_packaged_cli_relpath_maps_current_macos_arm(monkeypatch):
    monkeypatch.setattr("orchard.ingest.indexstore.platform.system", lambda: "Darwin")
    monkeypatch.setattr("orchard.ingest.indexstore.platform.machine", lambda: "arm64")
    assert _packaged_cli_relpath() == pytest.importorskip("pathlib").Path("darwin-arm64/orchard-indexstore-reader")


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
