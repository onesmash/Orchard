import json
import os
import platform
import socket
import subprocess
import shutil
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

from orchard import __version__ as ORCHARD_VERSION


_INDEXD_PROTOCOL_VERSION = 1


@dataclass
class OccurrenceRecord:
    usr: str
    file_path: str
    line: int
    col: int
    role: str


@dataclass
class RelationRecord:
    from_usr: str
    to_usr: str
    role: str
    occurrence_role: str = ""
    file_path: str = ""
    line: int = 0
    col: int = 0
    from_usr_name: str = ""
    to_usr_name: str = ""


@dataclass
class SymbolLineRecord:
    """Symbol metadata extracted from IndexStore."""
    usr: str
    name: str
    symbol_kind: str
    language: str
    module: str
    file_path: str = ""


@dataclass
class IndexStoreResult:
    occurrences: list[OccurrenceRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)
    symbols: list[SymbolLineRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


def _cli_path() -> str:
    packaged = _packaged_binary_path("orchard-indexstore-reader")
    if packaged:
        return packaged
    repo_root = Path(__file__).parent.parent.parent.parent
    candidates = [
        repo_root / "swift" / "orchard-indexstore-reader" / ".build" / "release" / "orchard-indexstore-reader",
        repo_root / "bin" / "orchard-indexstore-reader",
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    on_path = shutil.which("orchard-indexstore-reader")
    if on_path:
        return on_path
    raise FileNotFoundError("orchard-indexstore-reader not found; build the Swift CLI first")


def _packaged_binary_path(binary_name: str) -> str | None:
    rel = _packaged_binary_relpath(binary_name)
    if rel is None:
        return None
    try:
        pkg = import_module("orchard._bin")
    except ModuleNotFoundError:
        return None
    candidate = Path(pkg.__file__).resolve().parent / rel
    if not candidate.exists():
        return None
    if not os.access(candidate, os.X_OK):
        try:
            candidate.chmod(candidate.stat().st_mode | 0o111)
        except OSError:
            pass
    return str(candidate)


def _packaged_binary_relpath(binary_name: str) -> Path | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return Path("darwin-arm64") / binary_name
    if system == "darwin" and machine == "x86_64":
        return Path("darwin-x86_64") / binary_name
    return None


def _packaged_cli_path() -> str | None:
    return _packaged_binary_path("orchard-indexstore-reader")


def _packaged_cli_relpath() -> Path | None:
    return _packaged_binary_relpath("orchard-indexstore-reader")


def _run_cli(
    index_store_path: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    list_files: bool = False,
    targets: list[str] | None = None,
    emit_occurrences: bool = False,
    dump_unit_output_paths: bool = False,
):
    """Run the CLI and return ``(stdout_lines, stderr)``."""
    cmd = [_cli_path(), index_store_path]
    if source_root:
        cmd += ["--source-root", source_root]
    if source_roots:
        for root in source_roots:
            cmd += ["--source-root", root]
    if targets:
        for target in targets:
            cmd += ["--target", target]
    if incremental_since is not None:
        cmd += ["--incremental-since", str(int(incremental_since))]
    if list_files:
        cmd += ["--list-files"]
    if emit_occurrences:
        cmd += ["--emit-occurrences"]
    if dump_unit_output_paths:
        cmd += ["--dump-unit-output-paths"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stream_stderr = hasattr(proc.stderr, "__iter__")

    def _drain_stderr() -> None:
        for line in proc.stderr:
            stderr_lines.append(line)
            stripped = line.lstrip()
            if stripped.startswith("[orchard-indexstore-reader"):
                print(line.rstrip("\n"), file=sys.stderr, flush=True)

    stderr_thread = None
    if stream_stderr:
        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()
    try:
        for line in proc.stdout:
            stdout_lines.append(line.rstrip("\n"))
    finally:
        proc.stdout.close()
        rc = proc.wait()
        if stderr_thread is not None:
            stderr_thread.join()
            stderr_data = "".join(stderr_lines)
        else:
            stderr_data = proc.stderr.read()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd, output=None, stderr=stderr_data)
    return stdout_lines, stderr_data


def _indexd_path() -> str:
    packaged = _packaged_binary_path("orchard-indexd")
    if packaged:
        return packaged
    repo_root = Path(__file__).parent.parent.parent.parent
    candidates = [
        repo_root / "swift" / "orchard-indexstore-reader" / ".build" / "release" / "orchard-indexd",
        repo_root / "bin" / "orchard-indexd",
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    on_path = shutil.which("orchard-indexd")
    if on_path:
        return on_path
    raise FileNotFoundError("orchard-indexd not found; build the Swift daemon first")


def _indexd_autostart_enabled() -> bool:
    return os.environ.get("ORCHARD_INDEXD_AUTOSTART", "1") != "0"


def _default_indexd_socket_path() -> str:
    run_dir = Path.home() / ".orchard" / "run"
    return str(run_dir / "orchard-indexd.sock")


def _indexd_socket_path() -> str:
    configured = os.environ.get("ORCHARD_INDEXD_SOCKET")
    if configured:
        return configured
    if _indexd_autostart_enabled():
        return _default_indexd_socket_path()
    return ""


def _indexd_log_path() -> str:
    log_dir = Path.home() / ".orchard" / "logs"
    return str(log_dir / "orchard-indexd.log")


def _indexd_pid_path(socket_path: str | None = None) -> str:
    resolved = socket_path or _indexd_socket_path()
    socket_file = Path(resolved)
    return str(socket_file.with_suffix(".pid"))


_INDEXD_START_LOCK = threading.Lock()


def _current_indexd_binary_info() -> dict[str, int | str]:
    binary_path = Path(_indexd_path()).resolve()
    stat = binary_path.stat()
    return {
        "protocol_version": _INDEXD_PROTOCOL_VERSION,
        "orchard_version": ORCHARD_VERSION,
        "executable_path": str(binary_path),
        "binary_size": stat.st_size,
        "binary_mtime_ns": stat.st_mtime_ns,
    }


def _daemon_matches_current_build(info: dict) -> bool:
    try:
        current = _current_indexd_binary_info()
    except Exception:
        return False
    return (
        info.get("protocolVersion") == current["protocol_version"]
        and info.get("executablePath") == current["executable_path"]
        and info.get("binarySize") == current["binary_size"]
        and info.get("binaryMTimeNs") == current["binary_mtime_ns"]
    )


def _read_indexd_pid(pid_path: str) -> int | None:
    try:
        raw = Path(pid_path).read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return int(raw)
    except (OSError, ValueError):
        return None


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cleanup_stale_indexd_socket(socket_path: str, pid_path: str) -> None:
    pid = _read_indexd_pid(pid_path)
    if pid is not None and _is_process_alive(pid):
        return
    with suppress(FileNotFoundError):
        Path(socket_path).unlink()
    with suppress(FileNotFoundError):
        Path(pid_path).unlink()


def _wait_for_indexd(socket_path: str, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    client = _IndexdClient(socket_path)
    while time.monotonic() < deadline:
        with suppress(Exception):
            info = client.ping()
            if info and _daemon_matches_current_build(info):
                return True
        time.sleep(0.1)
    return False


def _start_indexd_process(socket_path: str) -> subprocess.Popen[str]:
    socket_file = Path(socket_path)
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    pid_path = _indexd_pid_path(socket_path)
    _cleanup_stale_indexd_socket(socket_path, pid_path)

    log_path = Path(_indexd_log_path())
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    return subprocess.Popen(
        [_indexd_path(), "--socket", socket_path, "--pid-file", pid_path],
        stdout=log_handle,
        stderr=log_handle,
        text=True,
        start_new_session=True,
    )


def _ensure_indexd_running(socket_path: str) -> bool:
    if not socket_path:
        return False
    pid_path = _indexd_pid_path(socket_path)
    _cleanup_stale_indexd_socket(socket_path, pid_path)
    with suppress(Exception):
        info = _IndexdClient(socket_path).ping()
        if info and _daemon_matches_current_build(info):
            return True
        if info:
            _IndexdClient(socket_path).shutdown()

    if not _indexd_autostart_enabled():
        return False

    with _INDEXD_START_LOCK:
        _cleanup_stale_indexd_socket(socket_path, pid_path)
        with suppress(Exception):
            info = _IndexdClient(socket_path).ping()
            if info and _daemon_matches_current_build(info):
                return True
            if info:
                _IndexdClient(socket_path).shutdown()

        proc = _start_indexd_process(socket_path)
        if _wait_for_indexd(socket_path):
            return True
        with suppress(Exception):
            proc.terminate()
        _cleanup_stale_indexd_socket(socket_path, pid_path)
        return False


class _IndexdClient:
    def __init__(self, socket_path: str):
        if not socket_path:
            raise ConnectionError("ORCHARD_INDEXD_SOCKET not configured")
        self.socket_path = socket_path

    def _request(self, payload: dict) -> list[dict]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(self.socket_path)
            sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            data = sock.recv(65536)
            chunks = [data]
            while data:
                data = sock.recv(65536)
                if data:
                    chunks.append(data)
            raw = b"".join(chunks).decode("utf-8")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def ping(self) -> dict | None:
        responses = self._request({
            "id": "ping",
            "method": "ping",
            "params": {},
        })
        if not responses or not responses[0].get("ok"):
            return None
        return responses[0].get("result")

    def shutdown(self) -> None:
        self._request({
            "id": "shutdown",
            "method": "shutdown",
            "params": {},
        })

    def warm(
        self,
        index_store_path: str,
        source_roots: list[str] | None,
        targets: list[str] | None,
    ) -> str:
        responses = self._request({
            "id": "warm",
            "method": "warm",
            "params": {
                "storePath": index_store_path,
                "sourceRoots": source_roots or [],
                "targets": targets or [],
            },
        })
        if not responses or not responses[0].get("ok"):
            raise ConnectionError(f"indexd warm failed: {responses}")
        return responses[0]["result"]["sessionId"]

    def scan(
        self,
        session_id: str,
        incremental_since: float | None,
        emit_occurrences: bool,
    ) -> tuple[list[str], str]:
        responses = self._request({
            "id": "scan",
            "method": "scan",
            "params": {
                "sessionId": session_id,
                "incrementalSince": incremental_since,
                "emitOccurrences": emit_occurrences,
            },
        })
        if not responses:
            raise ConnectionError("indexd scan returned no responses")
        lines: list[str] = []
        file_status: dict | None = None
        for msg in responses:
            if msg.get("stream") == "chunk":
                for record in msg.get("records", []):
                    if isinstance(record, str):
                        lines.append(record)
                    else:
                        lines.append(json.dumps(record, ensure_ascii=False))
            elif msg.get("stream") == "end":
                file_status = msg.get("fileStatus")
        if file_status is None:
            raise ConnectionError(f"indexd scan missing fileStatus: {responses}")
        return lines, json.dumps(file_status, ensure_ascii=False)

    def list_files(self, session_id: str) -> list[str]:
        responses = self._request({
            "id": "list_files",
            "method": "list_files",
            "params": {"sessionId": session_id},
        })
        if not responses or not responses[0].get("ok"):
            raise ConnectionError(f"indexd list_files failed: {responses}")
        return responses[0]["result"]["files"]

    def dump_unit_output_paths(self, session_id: str) -> list[dict[str, str]]:
        responses = self._request({
            "id": "dump_unit_output_paths",
            "method": "dump_unit_output_paths",
            "params": {"sessionId": session_id},
        })
        if not responses or not responses[0].get("ok"):
            raise ConnectionError(f"indexd dump_unit_output_paths failed: {responses}")
        return responses[0]["result"]["output_path_mappings"]


def indexd_status(socket_path: str | None = None) -> dict[str, object]:
    resolved_socket = socket_path or _indexd_socket_path()
    pid_path = _indexd_pid_path(resolved_socket) if resolved_socket else ""
    pid = _read_indexd_pid(pid_path) if pid_path else None
    status: dict[str, object] = {
        "socket_path": resolved_socket,
        "pid_path": pid_path,
        "socket_exists": bool(resolved_socket and Path(resolved_socket).exists()),
        "pid_file_exists": bool(pid_path and Path(pid_path).exists()),
        "pid": pid,
        "pid_alive": bool(pid is not None and _is_process_alive(pid)),
        "autostart_enabled": _indexd_autostart_enabled(),
        "matches_current_build": False,
        "running": False,
        "ping": None,
    }
    if not resolved_socket:
        return status
    with suppress(Exception):
        info = _IndexdClient(resolved_socket).ping()
        if info:
            status["running"] = True
            status["ping"] = info
            status["matches_current_build"] = _daemon_matches_current_build(info)
    return status


def shutdown_indexd(socket_path: str | None = None) -> dict[str, object]:
    resolved_socket = socket_path or _indexd_socket_path()
    pid_path = _indexd_pid_path(resolved_socket) if resolved_socket else ""
    stopped = False
    pid = _read_indexd_pid(pid_path) if pid_path else None
    if resolved_socket:
        with suppress(Exception):
            _IndexdClient(resolved_socket).shutdown()
            stopped = True
    if pid is not None:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and _is_process_alive(pid):
            time.sleep(0.05)
    _cleanup_stale_indexd_socket(resolved_socket, pid_path)
    return {
        "socket_path": resolved_socket,
        "pid_path": pid_path,
        "stopped": stopped,
        "status": indexd_status(resolved_socket),
    }


def _run_indexd(
    index_store_path: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    list_files: bool = False,
    targets: list[str] | None = None,
    emit_occurrences: bool = False,
    dump_unit_output_paths: bool = False,
):
    client = _IndexdClient(_indexd_socket_path())
    roots = source_roots or ([source_root] if source_root else [])
    session_id = client.warm(index_store_path, roots, targets)
    if list_files:
        return [], json.dumps(client.list_files(session_id), ensure_ascii=False)
    if dump_unit_output_paths:
        return [json.dumps(client.dump_unit_output_paths(session_id), ensure_ascii=False)], ""
    return client.scan(session_id, incremental_since, emit_occurrences)


def _run_reader(
    index_store_path: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    list_files: bool = False,
    targets: list[str] | None = None,
    emit_occurrences: bool = False,
    dump_unit_output_paths: bool = False,
):
    socket_path = _indexd_socket_path()
    if socket_path and _ensure_indexd_running(socket_path):
        try:
            return _run_indexd(
                index_store_path,
                source_root=source_root,
                source_roots=source_roots,
                incremental_since=incremental_since,
                list_files=list_files,
                targets=targets,
                emit_occurrences=emit_occurrences,
                dump_unit_output_paths=dump_unit_output_paths,
            )
        except Exception:
            pass
    return _run_cli(
        index_store_path,
        source_root=source_root,
        source_roots=source_roots,
        incremental_since=incremental_since,
        list_files=list_files,
        targets=targets,
        emit_occurrences=emit_occurrences,
        dump_unit_output_paths=dump_unit_output_paths,
    )


def _parse_file_status(stderr: str) -> dict | None:
    """Extract the file-status JSON dict from the CLI's stderr.

    The CLI writes progress lines to stderr followed by a final JSON line
    containing ``{"changed": [...], "all": [...]}``.  We find the last
    parseable JSON object and return it.
    """
    lines = stderr.strip().split("\n")
    # Try lines from the end — the status is the last JSON object.
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if "changed" in obj and "all" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def _unit_dir_mtime(index_store_path: str) -> float:
    """Return the latest mtime of files under ``v5/units/``, or 0.

    This is a fast O(1) proxy for "did Xcode build anything since last
    ingest" — inspired by sourcekit-lsp's ``pollForUnitChangesAndWait``.
    """
    import os as _os
    unit_dir = _os.path.join(index_store_path, "v5", "units")
    if not _os.path.isdir(unit_dir):
        # Older Xcode versions use a flat layout.
        unit_dir = index_store_path
    latest = 0.0
    try:
        with _os.scandir(unit_dir) as it:
            for entry in it:
                if entry.is_file():
                    mtime = entry.stat().st_mtime
                    if mtime > latest:
                        latest = mtime
                        # Early exit: already newer than any realistic threshold.
                        # Keep scanning — we need the MAX.
    except OSError:
        return 0.0
    return latest


def read_index_store(
    index_store_path: str,
    scope_id: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    targets: list[str] | None = None,
    emit_occurrences: bool = False,
) -> tuple[IndexStoreResult, dict | None, list[dict[str, str]] | None]:
    t0 = time.monotonic()
    result = IndexStoreResult()
    lines, stderr = _run_reader(
        index_store_path,
        source_root=source_root,
        source_roots=source_roots,
        incremental_since=incremental_since,
        targets=targets,
        emit_occurrences=emit_occurrences,
    )
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj["kind"] == "occurrence":
                if emit_occurrences:
                    result.occurrences.append(OccurrenceRecord(
                        usr=obj["usr"],
                        file_path=obj["file"],
                        line=obj["line"],
                        col=obj["column"],
                        role=obj["role"],
                    ))
            elif obj["kind"] == "relation":
                result.relations.append(RelationRecord(
                    from_usr=obj["from_usr"],
                    to_usr=obj["to_usr"],
                    role=obj["role"],
                    occurrence_role=obj.get("occurrence_role", ""),
                    file_path=obj.get("file", ""),
                    line=obj.get("line", 0) or 0,
                    col=obj.get("column", 0) or 0,
                    from_usr_name=obj.get("from_usr_name", ""),
                    to_usr_name=obj.get("to_usr_name", ""),
                ))
            elif obj["kind"] == "symbol":
                result.symbols.append(SymbolLineRecord(
                    usr=obj["usr"],
                    name=obj["name"],
                    symbol_kind=obj["symbol_kind"],
                    language=obj["language"],
                    module=obj.get("module", ""),
                    file_path=obj.get("file", ""),
                ))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            snippet = line[:80] + ("..." if len(line) > 80 else "")
            result.warnings.append(f"invalid JSONL line ({exc}): {snippet}")
    result.elapsed_s = round(time.monotonic() - t0, 3)
    file_status = _parse_file_status(stderr)
    output_path_mappings = None
    if file_status is not None:
        raw_mappings = file_status.get("output_path_mappings")
        if isinstance(raw_mappings, list):
            output_path_mappings = raw_mappings
    return result, file_status, output_path_mappings


def list_source_files(
    index_store_path: str,
    source_root: str | None = None,
) -> list[str]:
    """Return the list of source files under *source_root* (via --list-files)."""
    _, payload = _run_reader(index_store_path, source_root=source_root, list_files=True)
    lines = payload.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return []


def dump_unit_output_paths(
    index_store_path: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    targets: list[str] | None = None,
) -> list[dict[str, str]]:
    """Return unit/main-file/output-file mappings from the index store."""
    lines, _ = _run_reader(
        index_store_path,
        source_root=source_root,
        source_roots=source_roots,
        targets=targets,
        dump_unit_output_paths=True,
    )
    for raw in reversed(lines):
        line = raw.strip()
        if line.startswith("["):
            try:
                obj = json.loads(line)
                if isinstance(obj, list):
                    return obj
            except json.JSONDecodeError:
                continue
    return []
