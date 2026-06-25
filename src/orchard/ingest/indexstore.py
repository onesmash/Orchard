import json
import subprocess
import shutil
from dataclasses import dataclass, field
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


@dataclass
class SymbolLineRecord:
    """Symbol metadata extracted from IndexStore (name, kind, language, module)."""
    usr: str
    name: str
    symbol_kind: str
    language: str
    module: str


@dataclass
class IndexStoreResult:
    occurrences: list[OccurrenceRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)
    symbols: list[SymbolLineRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _cli_path() -> str:
    bundled = Path(__file__).parent.parent.parent.parent / "bin" / "orchard-indexstore-reader"
    if bundled.exists():
        return str(bundled)
    on_path = shutil.which("orchard-indexstore-reader")
    if on_path:
        return on_path
    raise FileNotFoundError("orchard-indexstore-reader not found; build the Swift CLI first")


def _run_cli(index_store_path: str, source_root: str | None = None):
    """Run the CLI and yield JSONL lines one at a time (no buffering)."""
    cmd = [_cli_path(), index_store_path]
    if source_root:
        cmd += ["--source-root", source_root]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        proc.stdout.close()
        rc = proc.wait()
        if rc != 0:
            err = proc.stderr.read()
            raise subprocess.CalledProcessError(rc, cmd, output=None, stderr=err)


def read_index_store(
    index_store_path: str, target_id: str, source_root: str | None = None
) -> IndexStoreResult:
    result = IndexStoreResult()
    for line in _run_cli(index_store_path, source_root=source_root):
        line = line.strip()
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
                ))
            elif obj["kind"] == "symbol":
                result.symbols.append(SymbolLineRecord(
                    usr=obj["usr"],
                    name=obj["name"],
                    symbol_kind=obj["symbol_kind"],
                    language=obj["language"],
                    module=obj.get("module", ""),
                ))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            snippet = line[:80] + ("..." if len(line) > 80 else "")
            result.warnings.append(f"invalid JSONL line ({exc}): {snippet}")
    return result
