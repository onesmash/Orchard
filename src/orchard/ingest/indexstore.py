import json
import os
import platform
import subprocess
import shutil
import time
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path


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
    packaged = _packaged_cli_path()
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


def _packaged_cli_path() -> str | None:
    rel = _packaged_cli_relpath()
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


def _packaged_cli_relpath() -> Path | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return Path("darwin-arm64") / "orchard-indexstore-reader"
    if system == "darwin" and machine == "x86_64":
        return Path("darwin-x86_64") / "orchard-indexstore-reader"
    return None


def _run_cli(
    index_store_path: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    list_files: bool = False,
    targets: list[str] | None = None,
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
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout_lines: list[str] = []
    try:
        for line in proc.stdout:
            stdout_lines.append(line.rstrip("\n"))
    finally:
        proc.stdout.close()
        rc = proc.wait()
        stderr_data = proc.stderr.read()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd, output=None, stderr=stderr_data)
    return stdout_lines, stderr_data


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
    target_id: str,
    source_root: str | None = None,
    source_roots: list[str] | None = None,
    incremental_since: float | None = None,
    targets: list[str] | None = None,
) -> tuple[IndexStoreResult, dict | None]:
    t0 = time.monotonic()
    result = IndexStoreResult()
    lines, stderr = _run_cli(
        index_store_path,
        source_root=source_root,
        source_roots=source_roots,
        incremental_since=incremental_since,
        targets=targets,
    )
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj["kind"] == "occurrence":
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
    file_status = _parse_file_status(stderr) if incremental_since is not None else None
    return result, file_status


def list_source_files(
    index_store_path: str,
    source_root: str | None = None,
) -> list[str]:
    """Return the list of source files under *source_root* (via --list-files)."""
    _, stderr = _run_cli(index_store_path, source_root=source_root, list_files=True)
    # The JSON array is the last line of stderr.
    lines = stderr.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return []
