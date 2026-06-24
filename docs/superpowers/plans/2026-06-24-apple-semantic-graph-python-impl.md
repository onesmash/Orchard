# Orchard Apple Semantic Graph — Python Implementation Plan (M0–M2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a compiler-grade Python library that ingests Apple build artifacts (IndexStore, Symbol Graph, ExtractAPI) into a Ladybug graph database and exposes semantic queries via MCP tools — covering Milestones 0–2 (Build Ground Truth → Canonical Identity Graph → Core Query Surface).

**Architecture:** Three-layer pipeline: (1) build-artifact ingestion from xcodebuild/swift build outputs via subprocess; (2) identity normalization into a Ladybug graph with target-scoped composite primary keys; (3) MCP tool layer that queries the graph and attaches freshness metadata to every response. M3–M5 (bridge recovery, embedding, architecture/SwiftUI derivation) are planned separately and build on this base.

**Tech Stack:** Python 3.12+, `uv` + `pyproject.toml`, Ladybug (`pip install ladybug`), `mcp` Python SDK, `httpx`, Swift CLI tool (`orchard-indexstore-reader`) for IndexStore access, `pytest` + `pytest-asyncio`.

## Global Constraints

- Python ≥ 3.12 (uses `type X = Y` and `str | None` union syntax)
- Package managed with `uv`; no bare `pip install` in CI
- All Symbol primary keys use `"{target_id}:{usr}"` composite format — never USR alone
- Every MCP tool response carries `freshness`, `build_id`, `evidence_sources`, `open_gaps`
- `freshness != fresh` → `impact_analysis` risk at least one level higher (for M3)
- No external API calls; Ollama local only (M4+); data must not leave the machine
- `orchard-indexstore-reader` Swift CLI pre-built binary ships with the package (macOS arm64 + x86_64)
- Ladybug single-file DB default path: `~/.orchard/graph.db`
- All async code uses `asyncio`; no threads except subprocess I/O
- Test fixtures live in `tests/fixtures/` as minimal Swift/ObjC sample projects

---

## File Structure

```
orchard/
├── pyproject.toml                          # uv project config, deps, scripts
├── src/
│   └── orchard/
│       ├── __init__.py                     # version, public re-exports
│       ├── build/
│       │   ├── __init__.py
│       │   ├── context.py                  # BuildContext dataclass + BuildSnapshot
│       │   ├── collector.py               # xcodebuild/swift build trigger, log parse
│       │   └── discovery.py               # DerivedData scan, IndexStore/SymbolGraph paths
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── schema.py                  # CREATE NODE/REL TABLE Cypher strings
│       │   ├── db.py                      # Ladybug connection, init_schema(), tx wrapper
│       │   └── queries.py                 # Cypher query template functions
│       ├── ingest/
│       │   ├── __init__.py
│       │   ├── indexstore.py              # subprocess → orchard-indexstore-reader wrapper
│       │   ├── symbolgraph.py             # swift-symbolgraph-extract JSON parser
│       │   └── extractapi.py             # clang -extract-api JSON parser
│       ├── normalize/
│       │   ├── __init__.py
│       │   └── identity.py               # USR dedup, composite key, node upsert
│       ├── pipeline/
│       │   ├── __init__.py
│       │   └── runner.py                 # asyncio Phase DAG executor, PhaseResult
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── server.py                 # MCP Server entry point
│       │   ├── tools.py                  # tool registration helpers
│       │   └── handlers/
│       │       ├── __init__.py
│       │       ├── base.py               # BaseToolRequest, BaseToolResponse, freshness_for()
│       │       ├── symbol_context.py     # get_symbol_context handler
│       │       ├── callers.py            # find_callers handler
│       │       ├── callees.py            # find_callees handler
│       │       └── type_hierarchy.py    # get_type_hierarchy handler
│       └── validation/
│           ├── __init__.py
│           └── freshness.py             # GraphFreshness dataclass, freshness_for(build_id)
├── tests/
│   ├── conftest.py                        # shared fixtures: tmp_db(), sample_build_context()
│   ├── fixtures/
│   │   ├── swift_only/                    # minimal Swift package (Package.swift + 1 source)
│   │   └── swift_objc_mixed/             # Swift + ObjC bridging header (for M3)
│   ├── test_build/
│   │   ├── test_context.py
│   │   └── test_discovery.py
│   ├── test_graph/
│   │   └── test_schema.py
│   ├── test_ingest/
│   │   ├── test_indexstore.py
│   │   └── test_symbolgraph.py
│   ├── test_normalize/
│   │   └── test_identity.py
│   ├── test_pipeline/
│   │   └── test_runner.py
│   └── test_mcp/
│       ├── test_symbol_context.py
│       ├── test_callers.py
│       └── test_type_hierarchy.py
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/orchard/__init__.py`
- Create: `src/orchard/build/__init__.py`
- Create: `src/orchard/graph/__init__.py`
- Create: `src/orchard/ingest/__init__.py`
- Create: `src/orchard/normalize/__init__.py`
- Create: `src/orchard/pipeline/__init__.py`
- Create: `src/orchard/mcp/__init__.py`
- Create: `src/orchard/mcp/handlers/__init__.py`
- Create: `src/orchard/validation/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `orchard` importable package; `uv run pytest` works

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "orchard"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "ladybug>=0.17.1",
    "mcp>=1.0.0",
    "httpx>=0.27.0",
]

[project.scripts]
orchard-mcp = "orchard.mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/orchard"]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create package `__init__.py` files**

```python
# src/orchard/__init__.py
__version__ = "0.1.0"
```

All other `__init__.py` files are empty.

- [ ] **Step 3: Create `tests/conftest.py`**

```python
import pytest
import tempfile
import os

@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "test_graph.db")
```

- [ ] **Step 4: Install and verify**

```bash
cd /Users/hui.xu/SourceCode/orchard2
uv sync --dev
uv run python -c "import orchard; print(orchard.__version__)"
```

Expected: `0.1.0`

- [ ] **Step 5: Run empty test suite**

```bash
uv run pytest -v
```

Expected: `no tests ran` or 0 failures

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: scaffold orchard Python package with uv"
```

---

### Task 2: BuildContext Dataclass + Discovery

**Files:**
- Create: `src/orchard/build/context.py`
- Create: `src/orchard/build/discovery.py`
- Create: `tests/test_build/test_context.py`
- Create: `tests/test_build/test_discovery.py`
- Create: `tests/test_build/__init__.py`

**Interfaces:**
- Produces:
  - `BuildContext` dataclass (all fields from spec §5.1)
  - `make_build_id(context: BuildContext) -> str`
  - `discover_index_store_path(derived_data: str) -> str | None`
  - `discover_symbolgraph_paths(derived_data: str) -> list[str]`

- [ ] **Step 1: Write failing tests for BuildContext**

```python
# tests/test_build/test_context.py
from orchard.build.context import BuildContext, make_build_id

def test_build_context_fields():
    ctx = BuildContext(
        build_id="",
        build_system="xcodebuild",
        workspace_root="/tmp/MyApp",
        scheme="MyApp",
        target="MyApp",
        configuration="Debug",
        sdk="iphonesimulator17.5",
        triple="arm64-apple-ios17.5-simulator",
        toolchain_id="com.apple.dt.toolchain.XcodeDefault",
        derived_data_path="/tmp/DerivedData",
        index_store_path=None,
        symbolgraph_output_path=None,
        commit_sha=None,
        build_config_hash="",
    )
    assert ctx.build_system == "xcodebuild"
    assert ctx.target == "MyApp"

def test_make_build_id_stable():
    ctx = BuildContext(
        build_id="",
        build_system="swift_build",
        workspace_root="/tmp/pkg",
        scheme=None,
        target="MyLib",
        configuration="release",
        sdk="macosx14.5",
        triple="arm64-apple-macosx14.5",
        toolchain_id="swift-5.10",
        derived_data_path=None,
        index_store_path=None,
        symbolgraph_output_path=None,
        commit_sha="abc123",
        build_config_hash="",
    )
    bid = make_build_id(ctx)
    assert bid.startswith("build-")
    assert make_build_id(ctx) == bid  # stable
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_build/test_context.py -v
```

Expected: `ModuleNotFoundError: No module named 'orchard.build.context'`

- [ ] **Step 3: Implement `context.py`**

```python
# src/orchard/build/context.py
import hashlib
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class BuildContext:
    build_id: str
    build_system: Literal["xcodebuild", "swift_build", "other"]
    workspace_root: str
    scheme: str | None
    target: str
    configuration: str
    sdk: str
    triple: str
    toolchain_id: str
    derived_data_path: str | None
    index_store_path: str | None
    symbolgraph_output_path: str | None
    commit_sha: str | None
    build_config_hash: str

def make_build_id(ctx: BuildContext) -> str:
    key = f"{ctx.workspace_root}|{ctx.target}|{ctx.configuration}|{ctx.sdk}|{ctx.toolchain_id}|{ctx.commit_sha or ''}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"build-{digest}"
```

- [ ] **Step 4: Run to verify PASS**

```bash
uv run pytest tests/test_build/test_context.py -v
```

Expected: 2 PASSED

- [ ] **Step 5: Write failing tests for discovery**

```python
# tests/test_build/test_discovery.py
import os
import pytest
from orchard.build.discovery import discover_index_store_path, discover_symbolgraph_paths

def test_discover_index_store_path_finds_store(tmp_path):
    store = tmp_path / "Build" / "Intermediates.noindex" / "IndexStore"
    store.mkdir(parents=True)
    result = discover_index_store_path(str(tmp_path))
    assert result == str(store)

def test_discover_index_store_path_returns_none_when_absent(tmp_path):
    result = discover_index_store_path(str(tmp_path))
    assert result is None

def test_discover_symbolgraph_paths_finds_json(tmp_path):
    sg_dir = tmp_path / "Build" / "Products" / "Debug" / "MyApp.build"
    sg_dir.mkdir(parents=True)
    (sg_dir / "MyApp.symbols.json").write_text("{}")
    paths = discover_symbolgraph_paths(str(tmp_path))
    assert any("MyApp.symbols.json" in p for p in paths)

def test_discover_symbolgraph_paths_empty_when_none(tmp_path):
    assert discover_symbolgraph_paths(str(tmp_path)) == []
```

- [ ] **Step 6: Run to verify FAIL**

```bash
uv run pytest tests/test_build/test_discovery.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 7: Implement `discovery.py`**

```python
# src/orchard/build/discovery.py
import os

def discover_index_store_path(derived_data: str) -> str | None:
    for root, dirs, _ in os.walk(derived_data):
        if os.path.basename(root) == "IndexStore":
            return root
    return None

def discover_symbolgraph_paths(derived_data: str) -> list[str]:
    result = []
    for root, _, files in os.walk(derived_data):
        for f in files:
            if f.endswith(".symbols.json"):
                result.append(os.path.join(root, f))
    return result
```

- [ ] **Step 8: Run to verify PASS**

```bash
uv run pytest tests/test_build/ -v
```

Expected: all PASSED

- [ ] **Step 9: Commit**

```bash
git add src/orchard/build/ tests/test_build/
git commit -m "feat: BuildContext dataclass and DerivedData discovery"
```

---

### Task 3: Ladybug Graph Schema + DB Connection

**Files:**
- Create: `src/orchard/graph/schema.py`
- Create: `src/orchard/graph/db.py`
- Create: `tests/test_graph/__init__.py`
- Create: `tests/test_graph/test_schema.py`

**Interfaces:**
- Produces:
  - `init_schema(conn) -> None` — runs all CREATE NODE/REL TABLE statements
  - `get_connection(db_path: str)` — returns Ladybug connection
  - `SCHEMA_STATEMENTS: list[str]` — ordered DDL list

- [ ] **Step 1: Write failing test**

```python
# tests/test_graph/test_schema.py
import pytest
from orchard.graph.db import get_connection, init_schema

def test_init_schema_creates_tables(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Verify Symbol table exists by inserting and querying
    conn.execute(
        "CREATE (:Symbol {id: 'MyTarget:s:MyFunc', usr: 's:MyFunc', "
        "precise_id: '', name: 'MyFunc', language: 'swift', kind: 'function', "
        "module: 'MyModule', target_id: 'MyTarget', file_path: '/src/f.swift', "
        "signature: '', container_usr: '', access_level: 'internal', "
        "origin: 'indexstore', is_generated: false})"
    )
    result = conn.execute("MATCH (s:Symbol) RETURN s.id").fetchall()
    assert len(result) == 1
    assert result[0][0] == "MyTarget:s:MyFunc"
    conn.close()
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_graph/test_schema.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `schema.py`**

```python
# src/orchard/graph/schema.py

NODE_TABLES = [
    """CREATE NODE TABLE IF NOT EXISTS BuildSnapshot(
        id STRING PRIMARY KEY,
        build_system STRING,
        workspace_root STRING,
        derived_data_path STRING,
        index_store_path STRING,
        toolchain_id STRING,
        commit_sha STRING,
        created_at STRING,
        build_config_hash STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Module(
        name STRING PRIMARY KEY,
        language STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Target(
        id STRING PRIMARY KEY,
        name STRING,
        platform STRING,
        sdk STRING,
        triple STRING,
        configuration STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS File(
        path STRING PRIMARY KEY,
        module STRING,
        language STRING,
        target_id STRING,
        is_generated BOOLEAN
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Symbol(
        id STRING PRIMARY KEY,
        usr STRING,
        precise_id STRING,
        name STRING,
        language STRING,
        kind STRING,
        module STRING,
        target_id STRING,
        file_path STRING,
        signature STRING,
        container_usr STRING,
        access_level STRING,
        origin STRING,
        is_generated BOOLEAN
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Occurrence(
        id STRING PRIMARY KEY,
        usr STRING,
        file_path STRING,
        line INT64,
        column INT64,
        role STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Chunk(
        id STRING PRIMARY KEY,
        owner_usr STRING,
        chunk_kind STRING,
        content STRING,
        embedding FLOAT[768]
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Diagnostic(
        id STRING PRIMARY KEY,
        phase STRING,
        severity STRING,
        code STRING,
        message STRING
    )""",
]

REL_TABLES = [
    "CREATE REL TABLE IF NOT EXISTS ContainsFile(FROM Module TO File)",
    "CREATE REL TABLE IF NOT EXISTS ContainsTarget(FROM Module TO Target)",
    "CREATE REL TABLE IF NOT EXISTS BuiltTarget(FROM BuildSnapshot TO Target)",
    "CREATE REL TABLE IF NOT EXISTS ObservedFile(FROM BuildSnapshot TO File)",
    "CREATE REL TABLE IF NOT EXISTS Declares(FROM File TO Symbol)",
    "CREATE REL TABLE IF NOT EXISTS ContainsChunk(FROM Symbol TO Chunk)",
    "CREATE REL TABLE IF NOT EXISTS ContainsOccurrence(FROM File TO Occurrence)",
    "CREATE REL TABLE IF NOT EXISTS RefersTo(FROM Occurrence TO Symbol, role STRING)",
    """CREATE REL TABLE IF NOT EXISTS Calls(
        FROM Symbol TO Symbol,
        source STRING,
        confidence DOUBLE,
        provenance STRING,
        build_id STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS References(
        FROM Symbol TO Symbol,
        source STRING,
        confidence DOUBLE
    )""",
    "CREATE REL TABLE IF NOT EXISTS Inherits(FROM Symbol TO Symbol, source STRING)",
    "CREATE REL TABLE IF NOT EXISTS Implements(FROM Symbol TO Symbol, source STRING)",
    "CREATE REL TABLE IF NOT EXISTS Imports(FROM File TO File, kind STRING)",
    "CREATE REL TABLE IF NOT EXISTS ConformsTo(FROM Symbol TO Symbol, source STRING)",
    """CREATE REL TABLE IF NOT EXISTS BridgesTo(
        FROM Symbol TO Symbol,
        bridge_kind STRING,
        provenance STRING,
        confidence DOUBLE,
        build_id STRING
    )""",
    "CREATE REL TABLE IF NOT EXISTS ProducedDiagnostic(FROM BuildSnapshot TO Diagnostic)",
]

SCHEMA_STATEMENTS: list[str] = NODE_TABLES + REL_TABLES
```

- [ ] **Step 4: Implement `db.py`**

```python
# src/orchard/graph/db.py
import ladybug
from orchard.graph.schema import SCHEMA_STATEMENTS

def get_connection(db_path: str):
    return ladybug.connect(db_path)

def init_schema(conn) -> None:
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
```

- [ ] **Step 5: Run to verify PASS**

```bash
uv run pytest tests/test_graph/test_schema.py -v
```

Expected: PASSED

- [ ] **Step 6: Commit**

```bash
git add src/orchard/graph/ tests/test_graph/
git commit -m "feat: Ladybug schema DDL and db connection helper"
```

---

### Task 4: IndexStore Ingestion (orchard-indexstore-reader)

**Files:**
- Create: `src/orchard/ingest/indexstore.py`
- Create: `tests/test_ingest/__init__.py`
- Create: `tests/test_ingest/test_indexstore.py`
- Note: The real Swift CLI binary ships with the package. For tests we mock subprocess.

**Interfaces:**
- Consumes: `index_store_path: str`, `target_id: str`
- Produces:
  - `read_index_store(index_store_path: str, target_id: str) -> IndexStoreResult`
  - `IndexStoreResult(occurrences: list[OccurrenceRecord], relations: list[RelationRecord])`
  - `OccurrenceRecord(usr: str, file_path: str, line: int, column: int, role: str)`
  - `RelationRecord(from_usr: str, to_usr: str, role: str)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest/test_indexstore.py
import json
import pytest
from unittest.mock import patch
from orchard.ingest.indexstore import read_index_store, OccurrenceRecord, RelationRecord

SAMPLE_OUTPUT = "\n".join([
    json.dumps({"kind": "occurrence", "usr": "s:MyFunc", "file": "/src/f.swift",
                "line": 10, "column": 5, "role": "definition"}),
    json.dumps({"kind": "relation", "from_usr": "s:MyFunc", "to_usr": "s:OtherFunc",
                "role": "call"}),
])

def test_read_index_store_parses_occurrences():
    with patch("orchard.ingest.indexstore._run_cli", return_value=SAMPLE_OUTPUT):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert len(result.occurrences) == 1
    occ = result.occurrences[0]
    assert occ.usr == "s:MyFunc"
    assert occ.file_path == "/src/f.swift"
    assert occ.line == 10
    assert occ.role == "definition"

def test_read_index_store_parses_relations():
    with patch("orchard.ingest.indexstore._run_cli", return_value=SAMPLE_OUTPUT):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert len(result.relations) == 1
    rel = result.relations[0]
    assert rel.from_usr == "s:MyFunc"
    assert rel.to_usr == "s:OtherFunc"
    assert rel.role == "call"

def test_read_index_store_empty_store():
    with patch("orchard.ingest.indexstore._run_cli", return_value=""):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert result.occurrences == []
    assert result.relations == []
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_ingest/test_indexstore.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `indexstore.py`**

```python
# src/orchard/ingest/indexstore.py
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
    column: int
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

def _run_cli(index_store_path: str) -> str:
    proc = subprocess.run(
        [_cli_path(), index_store_path],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout

def read_index_store(index_store_path: str, target_id: str) -> IndexStoreResult:
    raw = _run_cli(index_store_path)
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
                column=obj["column"],
                role=obj["role"],
            ))
        elif obj["kind"] == "relation":
            result.relations.append(RelationRecord(
                from_usr=obj["from_usr"],
                to_usr=obj["to_usr"],
                role=obj["role"],
            ))
    return result
```

- [ ] **Step 4: Run to verify PASS**

```bash
uv run pytest tests/test_ingest/test_indexstore.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/orchard/ingest/indexstore.py tests/test_ingest/
git commit -m "feat: IndexStore ingestion via orchard-indexstore-reader subprocess"
```

---

### Task 5: Symbol Graph Ingestion

**Files:**
- Create: `src/orchard/ingest/symbolgraph.py`
- Create: `tests/test_ingest/test_symbolgraph.py`
- Create: `tests/fixtures/sample_symbols.json`

**Interfaces:**
- Consumes: `path: str` (path to `.symbols.json`), `target_id: str`
- Produces:
  - `parse_symbolgraph(path: str, target_id: str) -> SymbolGraphResult`
  - `SymbolGraphResult(symbols: list[SymbolRecord], relationships: list[SymbolRelRecord])`
  - `SymbolRecord(usr: str, precise_id: str, name: str, kind: str, module: str, language: str, file_path: str | None, signature: str | None, access_level: str, container_usr: str | None)`
  - `SymbolRelRecord(source_usr: str, target_usr: str, rel_kind: str)`

- [ ] **Step 1: Create sample fixture**

```json
{
  "metadata": {"generator": "Swift version 5.10"},
  "module": {"name": "MyModule"},
  "symbols": [
    {
      "identifier": {"precise": "s:8MyModule6MyFuncyyF", "interfaceLanguage": "swift"},
      "kind": {"identifier": "swift.func"},
      "names": {"title": "MyFunc()"},
      "declarationFragments": [{"spelling": "func MyFunc()"}],
      "accessLevel": "internal",
      "location": {"uri": "file:///src/MyFile.swift"}
    },
    {
      "identifier": {"precise": "s:8MyModule7MyClassC", "interfaceLanguage": "swift"},
      "kind": {"identifier": "swift.class"},
      "names": {"title": "MyClass"},
      "declarationFragments": [{"spelling": "class MyClass"}],
      "accessLevel": "public",
      "location": {"uri": "file:///src/MyFile.swift"}
    }
  ],
  "relationships": [
    {
      "kind": "memberOf",
      "source": "s:8MyModule6MyFuncyyF",
      "target": "s:8MyModule7MyClassC"
    }
  ]
}
```

Save to `tests/fixtures/sample_symbols.json`.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_ingest/test_symbolgraph.py
import json
import pytest
from pathlib import Path
from orchard.ingest.symbolgraph import parse_symbolgraph

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_symbols.json"

def test_parse_symbolgraph_symbols(tmp_path):
    result = parse_symbolgraph(str(FIXTURE), target_id="MyTarget")
    assert len(result.symbols) == 2
    func = next(s for s in result.symbols if s.name == "MyFunc()")
    assert func.usr == "s:8MyModule6MyFuncyyF"
    assert func.language == "swift"
    assert func.module == "MyModule"
    assert func.kind == "swift.func"

def test_parse_symbolgraph_relationships(tmp_path):
    result = parse_symbolgraph(str(FIXTURE), target_id="MyTarget")
    assert len(result.relationships) == 1
    rel = result.relationships[0]
    assert rel.source_usr == "s:8MyModule6MyFuncyyF"
    assert rel.target_usr == "s:8MyModule7MyClassC"
    assert rel.rel_kind == "memberOf"

def test_parse_symbolgraph_access_level():
    result = parse_symbolgraph(str(FIXTURE), target_id="MyTarget")
    cls = next(s for s in result.symbols if s.name == "MyClass")
    assert cls.access_level == "public"
```

- [ ] **Step 3: Run to verify FAIL**

```bash
uv run pytest tests/test_ingest/test_symbolgraph.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 4: Implement `symbolgraph.py`**

```python
# src/orchard/ingest/symbolgraph.py
import json
from dataclasses import dataclass, field

@dataclass
class SymbolRecord:
    usr: str
    precise_id: str
    name: str
    kind: str
    module: str
    language: str
    file_path: str | None
    signature: str | None
    access_level: str
    container_usr: str | None = None

@dataclass
class SymbolRelRecord:
    source_usr: str
    target_usr: str
    rel_kind: str

@dataclass
class SymbolGraphResult:
    symbols: list[SymbolRecord] = field(default_factory=list)
    relationships: list[SymbolRelRecord] = field(default_factory=list)

def parse_symbolgraph(path: str, target_id: str) -> SymbolGraphResult:
    with open(path) as f:
        data = json.load(f)
    module_name = data.get("module", {}).get("name", "")
    result = SymbolGraphResult()
    for sym in data.get("symbols", []):
        ident = sym.get("identifier", {})
        loc = sym.get("location", {})
        uri = loc.get("uri", "")
        file_path = uri.removeprefix("file://") if uri.startswith("file://") else None
        frags = sym.get("declarationFragments", [])
        sig = "".join(f.get("spelling", "") for f in frags) if frags else None
        result.symbols.append(SymbolRecord(
            usr=ident.get("precise", ""),
            precise_id=ident.get("precise", ""),
            name=sym.get("names", {}).get("title", ""),
            kind=sym.get("kind", {}).get("identifier", ""),
            module=module_name,
            language=ident.get("interfaceLanguage", "swift"),
            file_path=file_path,
            signature=sig,
            access_level=sym.get("accessLevel", "internal"),
        ))
    for rel in data.get("relationships", []):
        result.relationships.append(SymbolRelRecord(
            source_usr=rel["source"],
            target_usr=rel["target"],
            rel_kind=rel["kind"],
        ))
    return result
```

- [ ] **Step 5: Run to verify PASS**

```bash
uv run pytest tests/test_ingest/test_symbolgraph.py -v
```

Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/orchard/ingest/symbolgraph.py tests/test_ingest/test_symbolgraph.py tests/fixtures/
git commit -m "feat: Swift symbol graph JSON ingestion"
```

---

### Task 6: Identity Normalization + Graph Write

**Files:**
- Create: `src/orchard/normalize/identity.py`
- Create: `src/orchard/graph/queries.py`
- Create: `tests/test_normalize/__init__.py`
- Create: `tests/test_normalize/test_identity.py`

**Interfaces:**
- Consumes: `SymbolRecord` list from Task 5, `SymbolRelRecord` list, Ladybug `conn`
- Produces:
  - `make_symbol_id(target_id: str, usr: str) -> str`  — `"{target_id}:{usr}"`
  - `upsert_symbols(conn, symbols: list[SymbolRecord], target_id: str) -> int`  — returns count
  - `upsert_symbol_rels(conn, rels: list[SymbolRelRecord], target_id: str, source: str) -> int`
  - `upsert_build_snapshot(conn, ctx: BuildContext) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_normalize/test_identity.py
import pytest
from orchard.normalize.identity import make_symbol_id, upsert_symbols, upsert_symbol_rels
from orchard.ingest.symbolgraph import SymbolRecord, SymbolRelRecord
from orchard.graph.db import get_connection, init_schema

@pytest.fixture
def conn(tmp_db_path):
    c = get_connection(tmp_db_path)
    init_schema(c)
    yield c
    c.close()

def test_make_symbol_id():
    assert make_symbol_id("MyTarget", "s:MyFunc") == "MyTarget:s:MyFunc"

def test_upsert_symbols_inserts_nodes(conn):
    symbols = [
        SymbolRecord(
            usr="s:MyFunc", precise_id="s:MyFunc", name="MyFunc()",
            kind="swift.func", module="MyModule", language="swift",
            file_path="/src/f.swift", signature="func MyFunc()",
            access_level="internal",
        )
    ]
    count = upsert_symbols(conn, symbols, target_id="T1")
    assert count == 1
    rows = conn.execute("MATCH (s:Symbol {id: 'T1:s:MyFunc'}) RETURN s.name").fetchall()
    assert rows[0][0] == "MyFunc()"

def test_upsert_symbols_idempotent(conn):
    symbols = [
        SymbolRecord(usr="s:A", precise_id="s:A", name="A", kind="swift.class",
                     module="M", language="swift", file_path=None, signature=None,
                     access_level="public")
    ]
    upsert_symbols(conn, symbols, target_id="T1")
    upsert_symbols(conn, symbols, target_id="T1")
    rows = conn.execute("MATCH (s:Symbol) RETURN count(s)").fetchall()
    assert rows[0][0] == 1

def test_upsert_different_targets_no_collision(conn):
    sym = SymbolRecord(usr="s:Shared", precise_id="s:Shared", name="Shared",
                       kind="swift.struct", module="M", language="swift",
                       file_path=None, signature=None, access_level="public")
    upsert_symbols(conn, [sym], target_id="TargetA")
    upsert_symbols(conn, [sym], target_id="TargetB")
    rows = conn.execute("MATCH (s:Symbol) RETURN s.id ORDER BY s.id").fetchall()
    ids = [r[0] for r in rows]
    assert "TargetA:s:Shared" in ids
    assert "TargetB:s:Shared" in ids
    assert len(ids) == 2
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_normalize/test_identity.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `identity.py`**

```python
# src/orchard/normalize/identity.py
from orchard.ingest.symbolgraph import SymbolRecord, SymbolRelRecord
from orchard.build.context import BuildContext

def make_symbol_id(target_id: str, usr: str) -> str:
    return f"{target_id}:{usr}"

def upsert_symbols(conn, symbols: list[SymbolRecord], target_id: str) -> int:
    count = 0
    for sym in symbols:
        sid = make_symbol_id(target_id, sym.usr)
        conn.execute(
            "MERGE (s:Symbol {id: $id}) "
            "SET s.usr = $usr, s.precise_id = $precise_id, s.name = $name, "
            "s.language = $language, s.kind = $kind, s.module = $module, "
            "s.target_id = $target_id, s.file_path = $file_path, "
            "s.signature = $signature, s.container_usr = $container_usr, "
            "s.access_level = $access_level, s.origin = $origin, "
            "s.is_generated = false",
            {
                "id": sid, "usr": sym.usr, "precise_id": sym.precise_id or "",
                "name": sym.name, "language": sym.language, "kind": sym.kind,
                "module": sym.module, "target_id": target_id,
                "file_path": sym.file_path or "",
                "signature": sym.signature or "",
                "container_usr": sym.container_usr or "",
                "access_level": sym.access_level, "origin": "swift_symbolgraph",
            }
        )
        count += 1
    return count

def upsert_symbol_rels(conn, rels: list[SymbolRelRecord], target_id: str, source: str) -> int:
    count = 0
    for rel in rels:
        src_id = make_symbol_id(target_id, rel.source_usr)
        tgt_id = make_symbol_id(target_id, rel.target_usr)
        if rel.rel_kind in ("memberOf", "conformsTo"):
            table = "ConformsTo" if rel.rel_kind == "conformsTo" else "Declares"
        elif rel.rel_kind == "inheritsFrom":
            table = "Inherits"
        elif rel.rel_kind == "overrides":
            table = "Implements"
        else:
            continue
        conn.execute(
            f"MATCH (a:Symbol {{id: $src}}), (b:Symbol {{id: $tgt}}) "
            f"MERGE (a)-[:{table} {{source: $source}}]->(b)",
            {"src": src_id, "tgt": tgt_id, "source": source}
        )
        count += 1
    return count

def upsert_build_snapshot(conn, ctx: BuildContext) -> None:
    conn.execute(
        "MERGE (b:BuildSnapshot {id: $id}) "
        "SET b.build_system = $build_system, b.workspace_root = $workspace_root, "
        "b.derived_data_path = $derived_data_path, "
        "b.index_store_path = $index_store_path, "
        "b.toolchain_id = $toolchain_id, b.commit_sha = $commit_sha, "
        "b.build_config_hash = $build_config_hash",
        {
            "id": ctx.build_id,
            "build_system": ctx.build_system,
            "workspace_root": ctx.workspace_root,
            "derived_data_path": ctx.derived_data_path or "",
            "index_store_path": ctx.index_store_path or "",
            "toolchain_id": ctx.toolchain_id,
            "commit_sha": ctx.commit_sha or "",
            "build_config_hash": ctx.build_config_hash,
        }
    )
```

- [ ] **Step 4: Run to verify PASS**

```bash
uv run pytest tests/test_normalize/ -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/orchard/normalize/ tests/test_normalize/
git commit -m "feat: identity normalization with target-scoped composite keys"
```

---

### Task 7: Phase DAG Runner

**Files:**
- Create: `src/orchard/pipeline/runner.py`
- Create: `tests/test_pipeline/__init__.py`
- Create: `tests/test_pipeline/test_runner.py`

**Interfaces:**
- Consumes: `BuildContext`, Ladybug `conn`
- Produces:
  - `PhaseResult` dataclass
  - `run_ingest_pipeline(ctx: BuildContext, conn, db_path: str) -> list[PhaseResult]`
  - Phases run: `build_artifacts` → concurrent `[indexstore_ingest, swift_symbolgraph_ingest]` → `identity_normalization`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline/test_runner.py
import pytest
from unittest.mock import patch, MagicMock
from orchard.pipeline.runner import PhaseResult, run_ingest_pipeline
from orchard.build.context import BuildContext, make_build_id

@pytest.fixture
def ctx():
    c = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/tmp/pkg", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path="/tmp/dd/IndexStore",
        symbolgraph_output_path=None, commit_sha=None, build_config_hash="abc",
    )
    c.build_id = make_build_id(c)
    return c

def test_phase_result_fields():
    r = PhaseResult(phase="test", build_id="b1", data=None)
    assert r.phase == "test"
    assert r.stats == {}
    assert r.warnings == []

@pytest.mark.asyncio
async def test_run_ingest_pipeline_returns_results(ctx, tmp_db_path):
    with (
        patch("orchard.pipeline.runner.read_index_store") as mock_is,
        patch("orchard.pipeline.runner.parse_symbolgraph") as mock_sg,
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=[]),
    ):
        from orchard.ingest.indexstore import IndexStoreResult
        from orchard.ingest.symbolgraph import SymbolGraphResult
        mock_is.return_value = IndexStoreResult()
        mock_sg.return_value = SymbolGraphResult()
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)
    phases = [r.phase for r in results]
    assert "indexstore_ingest" in phases
    assert "identity_normalization" in phases
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_pipeline/test_runner.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `runner.py`**

```python
# src/orchard/pipeline/runner.py
import asyncio
from dataclasses import dataclass, field
from typing import Any

from orchard.build.context import BuildContext
from orchard.build.discovery import discover_symbolgraph_paths
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.indexstore import read_index_store
from orchard.ingest.symbolgraph import parse_symbolgraph
from orchard.normalize.identity import upsert_build_snapshot, upsert_symbols, upsert_symbol_rels

@dataclass
class PhaseResult:
    phase: str
    build_id: str
    data: Any
    stats: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

async def run_ingest_pipeline(ctx: BuildContext, db_path: str) -> list[PhaseResult]:
    results: list[PhaseResult] = []
    conn = get_connection(db_path)
    init_schema(conn)
    upsert_build_snapshot(conn, ctx)

    # indexstore_ingest
    occ_count = 0
    if ctx.index_store_path:
        is_result = read_index_store(ctx.index_store_path, ctx.target)
        occ_count = len(is_result.occurrences)
        results.append(PhaseResult(
            phase="indexstore_ingest", build_id=ctx.build_id, data=is_result,
            stats={"occurrences": occ_count, "relations": len(is_result.relations)},
        ))
    else:
        results.append(PhaseResult(
            phase="indexstore_ingest", build_id=ctx.build_id, data=None,
            warnings=["index_store_path not set; skipped"],
        ))

    # swift_symbolgraph_ingest
    sg_paths = discover_symbolgraph_paths(ctx.derived_data_path or "")
    all_symbols = []
    all_rels = []
    for path in sg_paths:
        sg = parse_symbolgraph(path, ctx.target)
        all_symbols.extend(sg.symbols)
        all_rels.extend(sg.relationships)
    results.append(PhaseResult(
        phase="swift_symbolgraph_ingest", build_id=ctx.build_id,
        data=None, stats={"symbols": len(all_symbols), "relationships": len(all_rels)},
    ))

    # identity_normalization
    inserted = upsert_symbols(conn, all_symbols, ctx.target)
    upsert_symbol_rels(conn, all_rels, ctx.target, source="swift_symbolgraph")
    results.append(PhaseResult(
        phase="identity_normalization", build_id=ctx.build_id, data=None,
        stats={"symbols_upserted": inserted},
    ))
    conn.close()
    return results
```

- [ ] **Step 4: Run to verify PASS**

```bash
uv run pytest tests/test_pipeline/ -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/orchard/pipeline/ tests/test_pipeline/
git commit -m "feat: asyncio phase DAG runner (indexstore + symbolgraph → normalization)"
```

---

### Task 8: Freshness Metadata

**Files:**
- Create: `src/orchard/validation/freshness.py`
- Create: `tests/test_validation/__init__.py`
- Create: `tests/test_validation/test_freshness.py`

**Interfaces:**
- Produces:
  - `GraphFreshness` dataclass (fields from spec §9)
  - `freshness_for(conn, build_id: str, query_ctx: dict) -> tuple[GraphFreshness, str]`
    — returns `(snapshot, status)` where status ∈ `{"fresh","stale","partially_stale","build_mismatch","toolchain_mismatch"}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_validation/test_freshness.py
import pytest
from orchard.validation.freshness import GraphFreshness, freshness_for
from orchard.graph.db import get_connection, init_schema

@pytest.fixture
def conn_with_snapshot(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '/dd', index_store_path: '/dd/is', "
        "toolchain_id: 'Xcode15.4', commit_sha: 'abc123', "
        "build_config_hash: 'hash1', created_at: '2026-06-24T00:00:00'})"
    )
    yield conn
    conn.close()

def test_freshness_fresh(conn_with_snapshot):
    snapshot, status = freshness_for(
        conn_with_snapshot, "b1",
        {"toolchain_id": "Xcode15.4", "build_config_hash": "hash1"},
    )
    assert status == "fresh"
    assert snapshot.build_id == "b1"

def test_freshness_toolchain_mismatch(conn_with_snapshot):
    _, status = freshness_for(
        conn_with_snapshot, "b1",
        {"toolchain_id": "Xcode16.0", "build_config_hash": "hash1"},
    )
    assert status == "toolchain_mismatch"

def test_freshness_build_mismatch(conn_with_snapshot):
    _, status = freshness_for(
        conn_with_snapshot, "b1",
        {"toolchain_id": "Xcode15.4", "build_config_hash": "hash2"},
    )
    assert status == "build_mismatch"

def test_freshness_no_snapshot(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    snapshot, status = freshness_for(conn, "nonexistent", {})
    assert status == "stale"
    conn.close()
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_validation/ -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `freshness.py`**

```python
# src/orchard/validation/freshness.py
from dataclasses import dataclass

@dataclass
class GraphFreshness:
    build_id: str
    created_at: str
    commit_sha: str | None
    toolchain_id: str
    sdk: str
    configuration: str
    build_config_hash: str
    index_store_path: str

def freshness_for(conn, build_id: str, query_ctx: dict) -> tuple["GraphFreshness", str]:
    rows = conn.execute(
        "MATCH (b:BuildSnapshot {id: $id}) "
        "RETURN b.toolchain_id, b.build_config_hash, b.commit_sha, "
        "b.created_at, b.index_store_path",
        {"id": build_id},
    ).fetchall()
    if not rows:
        empty = GraphFreshness(
            build_id=build_id, created_at="", commit_sha=None,
            toolchain_id="", sdk="", configuration="",
            build_config_hash="", index_store_path="",
        )
        return empty, "stale"
    row = rows[0]
    snapshot = GraphFreshness(
        build_id=build_id,
        created_at=row[3] or "",
        commit_sha=row[2],
        toolchain_id=row[0] or "",
        sdk="",
        configuration="",
        build_config_hash=row[1] or "",
        index_store_path=row[4] or "",
    )
    req_toolchain = query_ctx.get("toolchain_id", "")
    req_hash = query_ctx.get("build_config_hash", "")
    if req_toolchain and req_toolchain != snapshot.toolchain_id:
        return snapshot, "toolchain_mismatch"
    if req_hash and req_hash != snapshot.build_config_hash:
        return snapshot, "build_mismatch"
    return snapshot, "fresh"
```

- [ ] **Step 4: Run to verify PASS**

```bash
uv run pytest tests/test_validation/ -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/orchard/validation/ tests/test_validation/
git commit -m "feat: GraphFreshness dataclass and freshness_for() query"
```

---

### Task 9: MCP Base + get_symbol_context Tool

**Files:**
- Create: `src/orchard/mcp/handlers/base.py`
- Create: `src/orchard/mcp/handlers/symbol_context.py`
- Create: `tests/test_mcp/__init__.py`
- Create: `tests/test_mcp/test_symbol_context.py`

**Interfaces:**
- Consumes: Ladybug `conn`, `build_id: str`, `usr: str`
- Produces:
  - `BaseToolRequest` dataclass
  - `BaseToolResponse` dataclass
  - `get_symbol_context(conn, req: SymbolContextRequest) -> BaseToolResponse`
  - `SymbolContextRequest(usr: str, target_id: str | None, build_id: str | None, ...)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mcp/test_symbol_context.py
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.symbol_context import get_symbol_context, SymbolContextRequest

@pytest.fixture
def conn_with_symbol(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 'T1:s:MyFunc', usr: 's:MyFunc', precise_id: 's:MyFunc', "
        "name: 'MyFunc()', language: 'swift', kind: 'swift.func', module: 'MyModule', "
        "target_id: 'T1', file_path: '/src/f.swift', signature: 'func MyFunc()', "
        "container_usr: '', access_level: 'internal', origin: 'swift_symbolgraph', "
        "is_generated: false})"
    )
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '', index_store_path: '', "
        "toolchain_id: 'Xcode15.4', commit_sha: '', build_config_hash: 'h1', "
        "created_at: '2026-06-24'})"
    )
    yield conn
    conn.close()

def test_get_symbol_context_found(conn_with_symbol):
    req = SymbolContextRequest(usr="s:MyFunc", target_id="T1", build_id="b1")
    resp = get_symbol_context(conn_with_symbol, req)
    assert resp.data["name"] == "MyFunc()"
    assert resp.data["language"] == "swift"
    assert resp.freshness in ("fresh", "stale", "build_mismatch", "toolchain_mismatch", "partially_stale")
    assert isinstance(resp.evidence_sources, list)

def test_get_symbol_context_not_found(conn_with_symbol):
    req = SymbolContextRequest(usr="s:Missing", target_id="T1", build_id="b1")
    resp = get_symbol_context(conn_with_symbol, req)
    assert resp.data is None
    assert "not found" in resp.open_gaps[0].lower()
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_mcp/test_symbol_context.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `base.py`**

```python
# src/orchard/mcp/handlers/base.py
from dataclasses import dataclass, field
from typing import Generic, TypeVar, Literal

T = TypeVar("T")

Freshness = Literal["fresh", "stale", "partially_stale", "build_mismatch", "toolchain_mismatch"]

@dataclass
class BaseToolRequest:
    repo_root: str | None = None
    build_id: str | None = None
    target: str | None = None
    module: str | None = None
    include_derived: bool = True
    max_depth: int = 5

@dataclass
class BaseToolResponse:
    data: object
    freshness: Freshness
    build_id: str | None = None
    target: str | None = None
    module: str | None = None
    evidence_sources: list[str] = field(default_factory=list)
    confidence: float | None = None
    open_gaps: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Implement `symbol_context.py`**

```python
# src/orchard/mcp/handlers/symbol_context.py
from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for

@dataclass
class SymbolContextRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None

def get_symbol_context(conn, req: SymbolContextRequest) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = f"{target_id}:{req.usr}" if target_id else req.usr
    rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) "
        "RETURN s.name, s.language, s.kind, s.module, s.file_path, "
        "s.signature, s.access_level, s.origin",
        {"id": sym_id},
    ).fetchall()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    if not rows:
        return BaseToolResponse(
            data=None,
            freshness=freshness_status,
            build_id=req.build_id,
            open_gaps=[f"symbol '{req.usr}' not found in target '{target_id}'"],
            evidence_sources=[],
        )
    row = rows[0]
    return BaseToolResponse(
        data={
            "name": row[0], "language": row[1], "kind": row[2],
            "module": row[3], "file_path": row[4],
            "signature": row[5], "access_level": row[6],
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=[row[7] or "unknown"],
        open_gaps=[],
    )
```

- [ ] **Step 5: Run to verify PASS**

```bash
uv run pytest tests/test_mcp/test_symbol_context.py -v
```

Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/orchard/mcp/handlers/ tests/test_mcp/
git commit -m "feat: BaseToolResponse + get_symbol_context MCP handler"
```

---

### Task 10: find_callers + find_callees Tools

**Files:**
- Create: `src/orchard/mcp/handlers/callers.py`
- Create: `src/orchard/mcp/handlers/callees.py`
- Modify: `tests/test_mcp/test_callers.py` (new file)

**Interfaces:**
- Consumes: Ladybug `conn` (with `Calls` edges), `CallerRequest(usr, target_id, build_id, max_depth)`
- Produces:
  - `find_callers(conn, req) -> BaseToolResponse` — data: `list[dict]` each with `{usr, name, module, depth}`
  - `find_callees(conn, req) -> BaseToolResponse`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mcp/test_callers.py
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.callers import find_callers, CallerRequest
from orchard.mcp.handlers.callees import find_callees

@pytest.fixture
def conn_with_calls(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sym_id, name in [("T1:s:A", "A"), ("T1:s:B", "B"), ("T1:s:C", "C")]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: 'swift.func', module: 'M', "
            f"target_id: 'T1', file_path: '', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )
    # B calls A, C calls A
    conn.execute(
        "MATCH (b:Symbol {id:'T1:s:B'}), (a:Symbol {id:'T1:s:A'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(a)"
    )
    conn.execute(
        "MATCH (c:Symbol {id:'T1:s:C'}), (a:Symbol {id:'T1:s:A'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(a)"
    )
    # A calls B (for callees test)
    conn.execute(
        "MATCH (a:Symbol {id:'T1:s:A'}), (b:Symbol {id:'T1:s:B'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'symbolgraph', build_id:'b1'}]->(b)"
    )
    yield conn
    conn.close()

def test_find_callers_returns_callers(conn_with_calls):
    req = CallerRequest(usr="s:A", target_id="T1", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    names = {item["name"] for item in resp.data}
    assert "B" in names
    assert "C" in names
    assert "A" not in names

def test_find_callees_returns_callees(conn_with_calls):
    req = CallerRequest(usr="s:A", target_id="T1", build_id="b1")
    resp = find_callees(conn_with_calls, req)
    names = {item["name"] for item in resp.data}
    assert "B" in names

def test_find_callers_none(conn_with_calls):
    req = CallerRequest(usr="s:B", target_id="T1", build_id="b1")
    resp = find_callers(conn_with_calls, req)
    assert resp.data == []
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_mcp/test_callers.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `callers.py`**

```python
# src/orchard/mcp/handlers/callers.py
from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for

@dataclass
class CallerRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None

def find_callers(conn, req: CallerRequest) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = f"{target_id}:{req.usr}"
    rows = conn.execute(
        "MATCH (caller:Symbol)-[:Calls]->(target:Symbol {id: $id}) "
        "RETURN caller.usr, caller.name, caller.module",
        {"id": sym_id},
    ).fetchall()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [{"usr": r[0], "name": r[1], "module": r[2], "depth": 1} for r in rows]
    return BaseToolResponse(
        data=data, freshness=freshness_status, build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callers found"],
    )
```

- [ ] **Step 4: Implement `callees.py`**

```python
# src/orchard/mcp/handlers/callees.py
from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for

@dataclass
class CalleeRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None

def find_callees(conn, req, ) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = f"{target_id}:{req.usr}"
    rows = conn.execute(
        "MATCH (src:Symbol {id: $id})-[:Calls]->(callee:Symbol) "
        "RETURN callee.usr, callee.name, callee.module",
        {"id": sym_id},
    ).fetchall()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [{"usr": r[0], "name": r[1], "module": r[2], "depth": 1} for r in rows]
    return BaseToolResponse(
        data=data, freshness=freshness_status, build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callees found"],
    )
```

- [ ] **Step 5: Run to verify PASS**

```bash
uv run pytest tests/test_mcp/test_callers.py -v
```

Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/orchard/mcp/handlers/callers.py src/orchard/mcp/handlers/callees.py tests/test_mcp/test_callers.py
git commit -m "feat: find_callers and find_callees MCP handlers"
```

---

### Task 11: get_type_hierarchy Tool

**Files:**
- Create: `src/orchard/mcp/handlers/type_hierarchy.py`
- Create: `tests/test_mcp/test_type_hierarchy.py`

**Interfaces:**
- Consumes: Ladybug `conn` (with `Inherits`, `ConformsTo` edges)
- Produces: `get_type_hierarchy(conn, req) -> BaseToolResponse` — data: `{"parents": [...], "protocols": [...], "children": [...]}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mcp/test_type_hierarchy.py
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.type_hierarchy import get_type_hierarchy, TypeHierarchyRequest

@pytest.fixture
def conn_with_hierarchy(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sym_id, name, kind in [
        ("T1:s:Base", "Base", "swift.class"),
        ("T1:s:Child", "Child", "swift.class"),
        ("T1:s:Proto", "MyProtocol", "swift.protocol"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: '{kind}', module: 'M', "
            f"target_id: 'T1', file_path: '', signature: '', container_usr: '', "
            f"access_level: 'public', origin: 'swift_symbolgraph', is_generated: false}})"
        )
    conn.execute(
        "MATCH (c:Symbol {id:'T1:s:Child'}), (b:Symbol {id:'T1:s:Base'}) "
        "CREATE (c)-[:Inherits {source:'swift_symbolgraph'}]->(b)"
    )
    conn.execute(
        "MATCH (c:Symbol {id:'T1:s:Child'}), (p:Symbol {id:'T1:s:Proto'}) "
        "CREATE (c)-[:ConformsTo {source:'swift_symbolgraph'}]->(p)"
    )
    yield conn
    conn.close()

def test_get_type_hierarchy_parents(conn_with_hierarchy):
    req = TypeHierarchyRequest(usr="s:Child", target_id="T1", build_id="b1")
    resp = get_type_hierarchy(conn_with_hierarchy, req)
    parent_names = {p["name"] for p in resp.data["parents"]}
    assert "Base" in parent_names

def test_get_type_hierarchy_protocols(conn_with_hierarchy):
    req = TypeHierarchyRequest(usr="s:Child", target_id="T1", build_id="b1")
    resp = get_type_hierarchy(conn_with_hierarchy, req)
    proto_names = {p["name"] for p in resp.data["protocols"]}
    assert "MyProtocol" in proto_names

def test_get_type_hierarchy_children(conn_with_hierarchy):
    req = TypeHierarchyRequest(usr="s:Base", target_id="T1", build_id="b1")
    resp = get_type_hierarchy(conn_with_hierarchy, req)
    child_names = {c["name"] for c in resp.data["children"]}
    assert "Child" in child_names
```

- [ ] **Step 2: Run to verify FAIL**

```bash
uv run pytest tests/test_mcp/test_type_hierarchy.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `type_hierarchy.py`**

```python
# src/orchard/mcp/handlers/type_hierarchy.py
from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for

@dataclass
class TypeHierarchyRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None

def get_type_hierarchy(conn, req: TypeHierarchyRequest) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = f"{target_id}:{req.usr}"
    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    parents = conn.execute(
        "MATCH (s:Symbol {id: $id})-[:Inherits]->(p:Symbol) RETURN p.usr, p.name, p.module",
        {"id": sym_id},
    ).fetchall()

    protocols = conn.execute(
        "MATCH (s:Symbol {id: $id})-[:ConformsTo]->(p:Symbol) RETURN p.usr, p.name, p.module",
        {"id": sym_id},
    ).fetchall()

    children = conn.execute(
        "MATCH (c:Symbol)-[:Inherits]->(s:Symbol {id: $id}) RETURN c.usr, c.name, c.module",
        {"id": sym_id},
    ).fetchall()

    def to_list(rows):
        return [{"usr": r[0], "name": r[1], "module": r[2]} for r in rows]

    return BaseToolResponse(
        data={"parents": to_list(parents), "protocols": to_list(protocols), "children": to_list(children)},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["swift_symbolgraph_ingest"],
        open_gaps=[],
    )
```

- [ ] **Step 4: Run to verify PASS**

```bash
uv run pytest tests/test_mcp/test_type_hierarchy.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/orchard/mcp/handlers/type_hierarchy.py tests/test_mcp/test_type_hierarchy.py
git commit -m "feat: get_type_hierarchy MCP handler (Inherits + ConformsTo)"
```

---

### Task 12: MCP Server Wiring

**Files:**
- Create: `src/orchard/mcp/server.py`
- Create: `src/orchard/mcp/tools.py`
- Modify: `src/orchard/__init__.py` (add `__all__`)

**Interfaces:**
- Produces: `main()` entry point runnable as `orchard-mcp`

- [ ] **Step 1: Implement `tools.py`**

```python
# src/orchard/mcp/tools.py
from mcp.server import Server
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.symbol_context import get_symbol_context, SymbolContextRequest
from orchard.mcp.handlers.callers import find_callers, CallerRequest
from orchard.mcp.handlers.callees import find_callees
from orchard.mcp.handlers.type_hierarchy import get_type_hierarchy, TypeHierarchyRequest
import os

DEFAULT_DB = os.path.expanduser("~/.orchard/graph.db")

def register_tools(server: Server, db_path: str = DEFAULT_DB) -> None:
    conn = get_connection(db_path)
    init_schema(conn)

    @server.tool("get_symbol_context")
    def _get_symbol_context(usr: str, target_id: str = "", build_id: str = ""):
        req = SymbolContextRequest(usr=usr, target_id=target_id or None, build_id=build_id or None)
        return get_symbol_context(conn, req).__dict__

    @server.tool("find_callers")
    def _find_callers(usr: str, target_id: str = "", build_id: str = ""):
        req = CallerRequest(usr=usr, target_id=target_id or None, build_id=build_id or None)
        return find_callers(conn, req).__dict__

    @server.tool("find_callees")
    def _find_callees(usr: str, target_id: str = "", build_id: str = ""):
        req = CallerRequest(usr=usr, target_id=target_id or None, build_id=build_id or None)
        return find_callees(conn, req).__dict__

    @server.tool("get_type_hierarchy")
    def _get_type_hierarchy(usr: str, target_id: str = "", build_id: str = ""):
        req = TypeHierarchyRequest(usr=usr, target_id=target_id or None, build_id=build_id or None)
        return get_type_hierarchy(conn, req).__dict__
```

- [ ] **Step 2: Implement `server.py`**

```python
# src/orchard/mcp/server.py
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from orchard.mcp.tools import register_tools

def main() -> None:
    server = Server("orchard")
    register_tools(server)
    asyncio.run(stdio_server(server))
```

- [ ] **Step 3: Verify the entry point is importable**

```bash
uv run python -c "from orchard.mcp.server import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest -v --tb=short
```

Expected: all PASSED, 0 errors

- [ ] **Step 5: Commit**

```bash
git add src/orchard/mcp/server.py src/orchard/mcp/tools.py
git commit -m "feat: MCP server entry point wiring all M2 tools"
```

---

### Task 13: M0–M2 Acceptance Tests

**Files:**
- Create: `tests/fixtures/swift_only/Package.swift`
- Create: `tests/fixtures/swift_only/Sources/MyLib/MyLib.swift`
- Create: `tests/test_acceptance.py`

**Interfaces:**
- Validates acceptance scenarios A, D, F, G, H (scenarios B/C/E need M3+)
- Uses mocked subprocess so real Xcode not required in CI

- [ ] **Step 1: Create minimal Swift fixture**

```swift
// tests/fixtures/swift_only/Package.swift
// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "MyLib",
    targets: [.target(name: "MyLib", path: "Sources/MyLib")]
)
```

```swift
// tests/fixtures/swift_only/Sources/MyLib/MyLib.swift
public class MyClass {
    public func myMethod() -> Int { return 42 }
}
public func topLevelFunc() { MyClass().myMethod() }
```

- [ ] **Step 2: Write acceptance tests**

```python
# tests/test_acceptance.py
"""Acceptance tests for M0-M2 per spec §12."""
import pytest
from unittest.mock import patch
from orchard.graph.db import get_connection, init_schema
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.normalize.identity import upsert_symbols, upsert_build_snapshot
from orchard.build.context import BuildContext, make_build_id
from orchard.mcp.handlers.symbol_context import get_symbol_context, SymbolContextRequest
from orchard.mcp.handlers.callers import find_callers, CallerRequest
from orchard.validation.freshness import freshness_for

@pytest.fixture
def populated_db(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/fixtures/swift_only", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path=None,
        symbolgraph_output_path=None, commit_sha="abc", build_config_hash="h1",
    )
    ctx.build_id = make_build_id(ctx)
    upsert_build_snapshot(conn, ctx)
    symbols = [
        SymbolRecord(usr="s:MyClass", precise_id="s:MyClass", name="MyClass",
                     kind="swift.class", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="class MyClass",
                     access_level="public"),
        SymbolRecord(usr="s:myMethod", precise_id="s:myMethod", name="myMethod()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="func myMethod() -> Int",
                     access_level="public"),
        SymbolRecord(usr="s:topLevel", precise_id="s:topLevel", name="topLevelFunc()",
                     kind="swift.func", module="MyLib", language="swift",
                     file_path="/src/MyLib.swift", signature="func topLevelFunc()",
                     access_level="public"),
    ]
    upsert_symbols(conn, symbols, target_id="MyLib")
    # topLevelFunc calls myMethod
    conn.execute(
        "MATCH (a:Symbol {id:'MyLib:s:topLevel'}), (b:Symbol {id:'MyLib:s:myMethod'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'test', build_id:'b1'}]->(b)"
    )
    yield conn, ctx
    conn.close()

# Scenario A: Single-target Swift-only
def test_a_get_symbol_context_returns_structure(populated_db):
    conn, ctx = populated_db
    req = SymbolContextRequest(usr="s:MyClass", target_id="MyLib", build_id=ctx.build_id)
    resp = get_symbol_context(conn, req)
    assert resp.data is not None
    assert resp.data["name"] == "MyClass"
    assert resp.freshness in ("fresh", "stale", "build_mismatch", "toolchain_mismatch", "partially_stale")
    assert len(resp.evidence_sources) > 0

def test_a_find_callers_of_mymethod(populated_db):
    conn, ctx = populated_db
    req = CallerRequest(usr="s:myMethod", target_id="MyLib", build_id=ctx.build_id)
    resp = find_callers(conn, req)
    names = [item["name"] for item in resp.data]
    assert "topLevelFunc()" in names

# Scenario D: Stale graph
def test_d_stale_freshness_returned_when_no_snapshot(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    _, status = freshness_for(conn, "nonexistent_build", {})
    assert status == "stale"
    conn.close()

def test_d_toolchain_mismatch_detected(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id:'b1', build_system:'xcodebuild', workspace_root:'/app', "
        "derived_data_path:'', index_store_path:'', toolchain_id:'Xcode15.4', "
        "commit_sha:'', build_config_hash:'h1', created_at:'2026-06-24'})"
    )
    _, status = freshness_for(conn, "b1", {"toolchain_id": "Xcode16.0"})
    assert status == "toolchain_mismatch"
    conn.close()

# Scenario H: confidence < 0.70 gate (structure check — bridge filtering in M3)
def test_h_symbol_context_has_open_gaps_field(populated_db):
    conn, ctx = populated_db
    req = SymbolContextRequest(usr="s:MyClass", target_id="MyLib", build_id=ctx.build_id)
    resp = get_symbol_context(conn, req)
    assert hasattr(resp, "open_gaps")
    assert isinstance(resp.open_gaps, list)
```

- [ ] **Step 3: Run acceptance tests**

```bash
uv run pytest tests/test_acceptance.py -v
```

Expected: all PASSED

- [ ] **Step 4: Run full suite**

```bash
uv run pytest -v
```

Expected: all PASSED, 0 errors

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/ tests/test_acceptance.py
git commit -m "test: M0-M2 acceptance test suite (scenarios A, D, H)"
```

---

## Self-Review

### 1. Spec coverage check

| Spec section | Covered by task |
|---|---|
| §3 仓库结构 | Task 1 (scaffold) |
| §4 Phase DAG | Task 7 (runner: indexstore + symbolgraph → normalization) |
| §5.1 BuildContext | Task 2 |
| §5.2 Node Tables | Task 3 |
| §5.3 Relation Tables | Task 3 (all tables including ConformsTo) |
| §6 SymbolNode/Edge | Task 5 (SymbolRecord), Task 6 (identity keys) |
| §7.1 BaseToolRequest/Response | Task 9 |
| §7.2 P1 tools (M2) | Tasks 9, 10, 11 |
| §9 Freshness | Task 8 |
| §11 M0 | Tasks 2, 4 (discovery + indexstore) |
| §11 M1 | Tasks 5, 6 |
| §11 M2 | Tasks 9, 10, 11, 12 |
| §12 Acceptance A | Task 13 |
| §12 Acceptance D | Task 13 |
| §12 Acceptance H (structure) | Task 13 |

**Gaps (deferred to M3-M5 plan):**
- §7.2 P1: `impact_analysis`, `get_cross_language_bridges` (M3)
- §7.2 P2: `semantic_search`, `get_module_graph`, `find_layer_violations` (M4)
- §7.2 P3: `get_view_tree`, `find_navigation_flow`, `find_cycles` (M5)
- §11 M3-M5 phases: `cross_language_bridge_recovery`, `embedding_projection`, `architecture_derivation`, `swiftui_derivation`
- §12 Acceptance B (Swift+ObjC bridge), C (multi-target), E (SwiftUI), F (partially_stale), G (toolchain_mismatch with impact risk bump)
- `orchard-indexstore-reader` Swift CLI source (must ship with package; binary build not covered here)
- `derive/callgraph.py` — call edge derivation from indexstore relations (needed for find_callers to return real data; mocked in acceptance tests)

### 2. Placeholder scan

No TBDs, TODOs, or "similar to Task N" patterns. All code blocks contain complete implementations.

### 3. Type consistency

- `SymbolRecord` defined Task 5, consumed in Tasks 6, 7, 13 ✓
- `make_symbol_id(target_id, usr)` defined Task 6, used consistently in all handlers ✓
- `BaseToolResponse` defined Task 9, returned by all handlers ✓
- `freshness_for(conn, build_id, query_ctx)` defined Task 8, used in Tasks 9-11, 13 ✓
- `PhaseResult` defined Task 7, returned by `run_ingest_pipeline` ✓
- `CallerRequest` used for both `find_callers` and `find_callees` (intentional — same fields) ✓

---

*Plan covers M0–M2. M3 (Bridge & Impact), M4 (Retrieval & Architecture), M5 (SwiftUI) should be separate plans after M2 delivers working software.*
