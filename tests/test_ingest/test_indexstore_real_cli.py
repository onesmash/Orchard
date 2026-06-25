"""Real IndexStore integration test: exercises the orchard-indexstore-reader
Swift CLI end-to-end (no mocking).

Skips unless:
  - the built CLI exists at <repo-root>/bin/orchard-indexstore-reader (run
    `swift/build-cli.sh` first), AND
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
from orchard.mcp.handlers.callers import CallerRequest, find_callers
from orchard.mcp.handlers.callees import CalleeRequest, find_callees

REPO_ROOT = Path(__file__).resolve().parents[2]

LIB_SWIFT = """\
public func callee() -> Int { return 1 }
public func caller() -> Int { return callee() }
public struct Thing { public func method() {} }
"""


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


def test_real_cli_read_index_store_produces_calledby(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    index_path = tmp_path / "idx"
    lib_path = _build_index(src_dir, index_path)

    # Real CLI run + real parse (no mock).
    result = read_index_store(str(index_path), target_id="T")

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
    callers = find_callers(conn, CallerRequest(usr=callee_usr, target_id="T", build_id="b-real"))
    assert any(d["usr"] == caller_usr for d in callers.data)

    callees = find_callees(conn, CalleeRequest(usr=caller_usr, target_id="T", build_id="b-real"))
    assert any(d["usr"] == callee_usr for d in callees.data)
    conn.close()
