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
class IndexStoreResult:
    occurrences: list[OccurrenceRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)


def _cli_path() -> str:
    bundled = Path(__file__).parent.parent.parent.parent / "bin" / "orchard-indexstore-reader"
    if bundled.exists():
        return str(bundled)
    on_path = shutil.which("orchard-indexstore-reader")
    if on_path:
        return on_path
    raise FileNotFoundError("orchard-indexstore-reader not found; build the Swift CLI first")


def _run_cli(index_store_path: str, source_root: str | None = None) -> str:
    cmd = [_cli_path(), index_store_path]
    if source_root:
        cmd += ["--source-root", source_root]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return proc.stdout


def read_index_store(
    index_store_path: str, target_id: str, source_root: str | None = None
) -> IndexStoreResult:
    raw = _run_cli(index_store_path, source_root=source_root)
    result = IndexStoreResult()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
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
    return result
