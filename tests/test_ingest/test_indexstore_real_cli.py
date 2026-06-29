"""Real IndexStore integration test: exercises the orchard-indexstore-reader
Swift CLI end-to-end (no mocking).

Skips unless:
  - a built CLI exists either at the SwiftPM release path or at
    <repo-root>/bin/orchard-indexstore-reader (run `swift/build-cli.sh`
    first), AND
  - `swiftc` is available to generate a real IndexStore fixture.

Verifies the full unmocked path:
  swiftc -index-store-path  ->  orchard-indexstore-reader (real binary)
  ->  read_index_store (parses real JSONL)
  ->  upsert_calls  ->  Calls edge  ->  find_callers / find_callees.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchard.graph.db import get_connection, init_schema
from orchard.ingest.indexstore import _cli_path, read_index_store
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.normalize.identity import upsert_calls, upsert_symbols
from orchard.handlers.callers import CallerRequest, find_callers
from orchard.handlers.callees import CalleeRequest, find_callees

REPO_ROOT = Path(__file__).resolve().parents[2]

LIB_SWIFT = """\
public func callee() -> Int { return 1 }
public func caller() -> Int { return callee() }
public struct Thing { public func method() {} }
"""

REAL_ZOOM_STORE = Path(
    "/Users/hui.xu/Work/SourceCode/Xcode/Zoom-aenxrzlrezagxyceipvtgcusrnlu/Index.noindex/DataStore"
)
REAL_EXTENSION_SIRI_ROOT = Path(
    "/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/Zoom/ExtensionSiri"
)


def _cli_available() -> bool:
    try:
        _cli_path()
        return True
    except FileNotFoundError:
        return False


pytestmark = pytest.mark.skipif(
    not _cli_available() or shutil.which("swiftc") is None,
    reason="orchard-indexstore-reader CLI not built (run swift/build-cli.sh) or swiftc missing",
)


def _build_index(src_dir: Path, index_path: Path) -> str:
    """Compile Lib.swift with index-store emission; return the source file path."""
    src = src_dir / "Lib.swift"
    src.write_text(LIB_SWIFT)
    subprocess.run(
        [
            "swiftc",
            "-index-store-path", str(index_path),
            "-index-unit-output-path", str(src_dir / "Lib.o"),
            str(src),
            "-emit-library",
            "-o", str(src_dir / "libtest.dylib"),
        ],
        check=True,
        capture_output=True,
    )
    return str(src)


def _built_wheel() -> Path | None:
    wheels = sorted((REPO_ROOT / "dist").glob("orchard-*.whl"))
    return wheels[0] if wheels else None


def test_real_cli_read_index_store_produces_calledby(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    index_path = tmp_path / "idx"
    lib_path = _build_index(src_dir, index_path)

    # Real CLI run + real parse (no mock).
    result, _ = read_index_store(str(index_path), target_id="T")

    # Our source defines caller() and callee(); find their USRs.
    our_usrs = {occ.usr for occ in result.occurrences if occ.file_path == lib_path}
    assert our_usrs, "no occurrences recorded for our source file"

    # The caller->callee call must surface as a `calledBy` relation whose
    # endpoints are both our symbols: from_usr=callee, to_usr=caller.
    calledby = [
        r for r in result.relations
        if r.role == "calledBy" and r.from_usr in our_usrs and r.to_usr in our_usrs
    ]
    assert calledby, "no calledBy relation found between our symbols"
    callee_usr = calledby[0].from_usr
    caller_usr = calledby[0].to_usr

    # Seed Symbol nodes with the real USRs, then persist Calls edges.
    conn = get_connection(str(tmp_path / "graph.db"))
    init_schema(conn)
    upsert_symbols(
        conn,
        [
            SymbolRecord(usr=callee_usr, precise_id="", name="callee",
                         kind="function", module="test", language="swift",
                         file_path=lib_path, signature=None,
                         access_level="public", container_usr=None),
            SymbolRecord(usr=caller_usr, precise_id="", name="caller",
                         kind="function", module="test", language="swift",
                         file_path=lib_path, signature=None,
                         access_level="public", container_usr=None),
        ],
        target_id="T",
    )
    written = upsert_calls(conn, result.relations, target_id="T",
                           source="indexstore", build_id="b-real")
    assert written >= 1

    # find_callers(callee) -> caller ; find_callees(caller) -> callee
    callers = find_callers(conn, CallerRequest(usr=callee_usr, build_id="b-real"))
    assert any(d["usr"] == caller_usr for d in callers.data)

    callees = find_callees(conn, CalleeRequest(usr=caller_usr, build_id="b-real"))
    assert any(d["usr"] == callee_usr for d in callees.data)
    conn.close()


def test_installed_wheel_cli_can_ingest_minimal_index(tmp_path):
    wheel = _built_wheel()
    if wheel is None:
        pytest.skip("no built wheel found under dist/; run `uv build` first")
    if shutil.which("uv") is None:
        pytest.skip("uv is required for installed-wheel blackbox validation")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    index_path = tmp_path / "idx"
    lib_path = _build_index(src_dir, index_path)
    assert Path(lib_path).exists()

    venv_dir = tmp_path / "venv"
    python_bin = venv_dir / "bin" / "python"
    orchard_bin = venv_dir / "bin" / "orchard"
    subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python_bin), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    cli_path = subprocess.run(
        [
            str(python_bin),
            "-c",
            "from orchard.ingest.indexstore import _cli_path; print(_cli_path())",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "site-packages/orchard/_bin/" in cli_path

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db_path = tmp_path / "graph.db"
    ingest = subprocess.run(
        [
            str(orchard_bin),
            "ingest",
            "--index-store",
            str(index_path),
            "--project-dir",
            str(project_dir),
            "--source-root",
            str(src_dir),
            "--target",
            "T",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "syms" in ingest.stdout
    assert "rels" in ingest.stdout
    assert db_path.exists()

    stats = subprocess.run(
        [str(orchard_bin), "stats", "--db", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Symbol:" in stats
    assert "Calls:" in stats


@pytest.mark.skipif(
    not REAL_ZOOM_STORE.exists() or not REAL_EXTENSION_SIRI_ROOT.exists(),
    reason="requires local Zoom DerivedData store and ExtensionSiri sources",
)
def test_real_zoom_extension_siri_preserves_objc_sdk_symbols():
    result, _ = read_index_store(
        str(REAL_ZOOM_STORE),
        target_id="T",
        source_root=str(REAL_EXTENSION_SIRI_ROOT),
    )

    intent_handler_symbols = {
        symbol.name
        for symbol in result.symbols
        if symbol.file_path.endswith("IntentHandler.m")
    }

    assert "stringByAppendingString:" in intent_handler_symbols
    assert "INPersonHandleTypePhoneNumber" in intent_handler_symbols
    assert "ZMSiriCallIdentifierPrefix" in intent_handler_symbols
