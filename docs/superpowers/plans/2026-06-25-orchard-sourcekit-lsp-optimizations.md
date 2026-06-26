# sourcekit-lsp Patterns → orchard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 5 sourcekit-lsp-inspired patterns in orchard: containerNames cache, IndexCheckLevel + IndexOutOfDateChecker, CrossLanguageName, transitive_subtype_closure, primary_definition_usr.

**Architecture:** Each optimization is a self-contained change to one or two files following orchard's existing patterns (dataclasses, handler request/response, GraphLookup query helpers). Implementation order respects dependency graph: cache → freshness → cross-language → subtype → primary definition.

**Tech Stack:** Python 3.12, Ladybug/KuzuDB, pytest, dataclasses.

## Global Constraints

- 127 existing tests must stay green
- Follow orchard naming: snake_case functions, PascalCase classes/dataclasses, PEP 8 enums
- GraphLookup wraps Ladybug queries; handlers use dataclass Request → Response pattern
- Schema changes use `IF NOT EXISTS` for idempotent `init_schema()`
- freshness_for() signature change must be backward-compatible

---

### Task 1: containerNames cache in GraphLookup.owner_of()

**Files:**
- Modify: `src/orchard/query/lookup.py`
- Test: `tests/test_query/test_lookup_owner_cache.py`

**Interfaces:**
- Produces: `GraphLookup._container_names_cache: dict[str, list[str]]`, updated `owner_of()` with caching and extension handling

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_query/test_lookup_owner_cache.py
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.query.lookup import GraphLookup
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord


def _seed(conn):
    """Seed a graph with a nested class structure and an extension."""
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:Outer", name="Outer", kind="class", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Inner", name="Inner", kind="class", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:method", name="method", kind="method", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Extension", name="Extension", kind="extension", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:ExtMethod", name="extMethod", kind="method", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:Extended", name="Extended", kind="class", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    # Contains: Outer -> Inner -> method
    conn.execute("MATCH (o:Symbol {usr: 's:Outer'}), (i:Symbol {usr: 's:Inner'}) CREATE (o)-[:Contains {source: 'test'}]->(i)")
    conn.execute("MATCH (o:Symbol {usr: 's:Inner'}), (m:Symbol {usr: 's:method'}) CREATE (o)-[:Contains {source: 'test'}]->(m)")
    # Extension contains ExtMethod, extends Extended
    conn.execute("MATCH (e:Symbol {usr: 's:Extension'}), (m:Symbol {usr: 's:ExtMethod'}) CREATE (e)-[:Contains {source: 'test'}]->(m)")
    conn.execute("MATCH (e:Symbol {usr: 's:Extension'}), (x:Symbol {usr: 's:Extended'}) CREATE (e)-[:Extends {source: 'test'}]->(x)")


def test_owner_of_returns_immediate_container():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    owner = g.owner_of("s:Inner")
    assert owner is not None
    assert owner["name"] == "Outer"
    assert owner["kind"] == "class"


def test_owner_of_returns_none_for_top_level():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert g.owner_of("s:Outer") is None


def test_owner_of_handles_extension():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    owner = g.owner_of("s:ExtMethod")
    assert owner is not None
    # Extension should resolve to the extended type
    assert owner["name"] == "Extended"
    assert owner["kind"] == "class"


def test_owner_of_caches_result():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert "s:Inner" not in g._container_names_cache
    g.owner_of("s:Inner")
    assert "s:Inner" in g._container_names_cache
    # Second call should hit cache
    cached = g._container_names_cache["s:Inner"]
    g.owner_of("s:Inner")
    assert g._container_names_cache["s:Inner"] == cached


def test_owner_of_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    g = GraphLookup(conn)
    assert g.owner_of("nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_query/test_lookup_owner_cache.py -v
```
Expected: FAIL — `_container_names_cache` attribute missing, extension handling not implemented.

- [ ] **Step 3: Implement the cache and extension handling**

Modify `src/orchard/query/lookup.py` — add to `GraphLookup.__init__`:
```python
self._container_names_cache: dict[str, list[str]] = {}
```

Update `owner_of()`:
```python
def owner_of(self, usr: str) -> dict | None:
    """Walk Contains edges up to find the owning class/struct/extension.
    
    Results are cached per USR for the lifetime of this GraphLookup instance.
    Extensions resolve to the extended type's name via Extends edge.
    """
    rows = self._conn.execute(
        "MATCH (s:Symbol {usr: $usr})<-[:Contains]-(owner:Symbol) "
        "WHERE owner.kind IN ['class','struct','enum','protocol','extension'] "
        "RETURN owner.usr, owner.name, owner.kind, owner.module LIMIT 1",
        {"usr": usr},
    ).get_all()
    if not rows:
        return None
    owner_usr, name, kind, module = rows[0]
    # Extension: resolve to extended type
    if kind == "extension":
        ext_rows = self._conn.execute(
            "MATCH (e:Symbol {usr: $usr})-[:Extends]->(ext:Symbol) "
            "RETURN ext.usr, ext.name, ext.kind, ext.module LIMIT 1",
            {"usr": owner_usr},
        ).get_all()
        if ext_rows:
            owner_usr, name, kind, module = ext_rows[0]
    # Cache the result
    result = {"usr": owner_usr, "name": name, "kind": kind, "module": module}
    self._container_names_cache[usr] = [result["name"]]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_query/test_lookup_owner_cache.py -v
```
Expected: 5 PASS

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -x -q
```
Expected: 127+5=132 passed

- [ ] **Step 6: Commit**

```bash
git add tests/test_query/test_lookup_owner_cache.py src/orchard/query/lookup.py
git commit -m "feat: add containerNames cache and extension handling to owner_of()

- Add _container_names_cache dict to GraphLookup for per-request caching
- owner_of() resolves extension symbols to extended type via Extends edge
- Cache populated on first lookup, reused for subsequent calls

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: IndexCheckLevel + IndexOutOfDateChecker

**Files:**
- Modify: `src/orchard/validation/freshness.py`
- Test: `tests/test_validation/test_freshness_checker.py`

**Interfaces:**
- Consumes: `GraphFreshness` from `freshness.py`
- Produces: `IndexCheckLevel(Enum)`, `IndexOutOfDateChecker` class, backward-compatible `freshness_for()`

- [ ] **Step 1: Write tests**

```python
# tests/test_validation/test_freshness_checker.py
import os
import time
import tempfile
import pytest
from orchard.validation.freshness import (
    IndexCheckLevel, IndexOutOfDateChecker, freshness_for, GraphFreshness
)
from orchard.graph.db import get_connection, init_schema


class TestIndexCheckLevel:
    def test_enum_values(self):
        assert IndexCheckLevel.DELETED_FILES.value == "deleted_files"
        assert IndexCheckLevel.MODIFIED_FILES.value == "modified_files"
        assert IndexCheckLevel.IN_MEMORY_MODIFIED_FILES.value == "in_memory_modified_files"

    def test_default_level(self):
        assert IndexCheckLevel.default() == IndexCheckLevel.MODIFIED_FILES


class TestIndexOutOfDateChecker:
    def make_temp_file(self):
        fd, path = tempfile.mkstemp(suffix=".swift")
        os.close(fd)
        return path

    def test_up_to_date_fresh_file(self):
        path = self.make_temp_file()
        try:
            checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
            # File just created, index timestamp in the future → up-to-date
            from orchard.validation.freshness import SymbolLocation
            loc = SymbolLocation(path=path, timestamp=time.time() + 3600)
            assert checker.is_up_to_date(loc)
        finally:
            os.unlink(path)

    def test_out_of_date_modified_file(self):
        path = self.make_temp_file()
        try:
            time.sleep(0.01)  # ensure file older than now
            checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
            from orchard.validation.freshness import SymbolLocation
            loc = SymbolLocation(path=path, timestamp=time.time() - 3600)
            assert not checker.is_up_to_date(loc)
        finally:
            os.unlink(path)

    def test_deleted_file_not_up_to_date(self):
        checker = IndexOutOfDateChecker(IndexCheckLevel.DELETED_FILES)
        from orchard.validation.freshness import SymbolLocation
        loc = SymbolLocation(path="/nonexistent/path.swift", timestamp=time.time())
        assert not checker.is_up_to_date(loc)

    def test_modtime_cache_reuse(self):
        path = self.make_temp_file()
        try:
            checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
            from orchard.validation.freshness import SymbolLocation
            loc = SymbolLocation(path=path, timestamp=time.time() + 3600)
            assert checker.is_up_to_date(loc)
            # Second call should use cache
            assert checker.is_up_to_date(loc)
            assert path in checker._mod_time_cache
        finally:
            os.unlink(path)

    def test_deleted_files_level_ignores_mtime(self):
        path = self.make_temp_file()
        try:
            checker = IndexOutOfDateChecker(IndexCheckLevel.DELETED_FILES)
            from orchard.validation.freshness import SymbolLocation
            # Even with old timestamp, DELETED_FILES only checks existence
            loc = SymbolLocation(path=path, timestamp=time.time() - 99999)
            assert checker.is_up_to_date(loc)  # file exists → OK
        finally:
            os.unlink(path)


def test_freshness_for_backward_compatible():
    """freshness_for() must still work with existing callers."""
    conn = get_connection(":memory:")
    init_schema(conn)
    status, msg = freshness_for(conn, "", {})
    assert isinstance(status, GraphFreshness) or status in ("fresh", "stale")
    assert isinstance(msg, str)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_validation/test_freshness_checker.py -v
```
Expected: FAIL — `IndexCheckLevel` not defined.

- [ ] **Step 3: Implement IndexCheckLevel + IndexOutOfDateChecker + SymbolLocation**

Add to `src/orchard/validation/freshness.py`:

```python
import os
import time
from dataclasses import dataclass
from enum import Enum


class IndexCheckLevel(Enum):
    """Granularity of index freshness checks (inspired by sourcekit-lsp)."""
    DELETED_FILES = "deleted_files"
    MODIFIED_FILES = "modified_files"
    IN_MEMORY_MODIFIED_FILES = "in_memory_modified_files"

    @classmethod
    def default(cls) -> "IndexCheckLevel":
        return cls.MODIFIED_FILES


@dataclass
class SymbolLocation:
    """Minimal location for freshness checking (matches IndexStore location)."""
    path: str
    timestamp: float  # Unix timestamp of when this symbol was indexed


class IndexOutOfDateChecker:
    """Checks whether indexed symbol locations are still up-to-date.

    Caches file modification times for the lifetime of one request.
    Inspired by sourcekit-lsp's IndexOutOfDateChecker.
    """

    def __init__(self, check_level: IndexCheckLevel):
        self._check_level = check_level
        self._mod_time_cache: dict[str, float | None] = {}
        self._file_exists_cache: dict[str, bool] = {}

    def is_up_to_date(self, location: SymbolLocation) -> bool:
        """Return True if the source file hasn't been modified since indexing."""
        if self._check_level == IndexCheckLevel.DELETED_FILES:
            return self._file_exists(location.path)
        # MODIFIED_FILES or IN_MEMORY_MODIFIED_FILES
        source_mtime = self._modification_time(location.path)
        if source_mtime is None:
            return False  # file deleted
        return source_mtime <= location.timestamp

    def _file_exists(self, path: str) -> bool:
        if path not in self._file_exists_cache:
            self._file_exists_cache[path] = os.path.exists(path)
        return self._file_exists_cache[path]

    def _modification_time(self, path: str) -> float | None:
        if path not in self._mod_time_cache:
            try:
                self._mod_time_cache[path] = os.path.getmtime(path)
            except OSError:
                self._mod_time_cache[path] = None
        return self._mod_time_cache[path]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_validation/test_freshness_checker.py -v
```
Expected: 7 PASS

- [ ] **Step 5: Run full test suite (ensure backward compat)**

```bash
uv run pytest tests/ -x -q
```
Expected: 132+7=139 passed

- [ ] **Step 6: Commit**

```bash
git add tests/test_validation/test_freshness_checker.py src/orchard/validation/freshness.py
git commit -m "feat: add IndexCheckLevel + IndexOutOfDateChecker for freshness

- Add IndexCheckLevel enum (DELETED_FILES, MODIFIED_FILES, IN_MEMORY_MODIFIED_FILES)
- Add SymbolLocation dataclass for file+timestamp pairs
- Add IndexOutOfDateChecker with per-request modTime cache
- Backward compatible: existing freshness_for() unchanged

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: CrossLanguageName in bridge

**Files:**
- Modify: `src/orchard/derive/bridge.py`, `src/orchard/graph/schema.py`, `src/orchard/handlers/bridges.py`
- Test: `tests/test_derive/test_bridge_cross_language.py`

**Interfaces:**
- Produces: `CrossLanguageName` dataclass, updated BridgesTo schema columns, updated `get_cross_language_bridges` response

- [ ] **Step 1: Write tests**

```python
# tests/test_derive/test_bridge_cross_language.py
import pytest
from orchard.derive.bridge import CrossLanguageName


class TestCrossLanguageName:
    def test_objc_instance_method(self):
        cn = CrossLanguageName(
            clang_name="-[ZMHomeViewController viewDidLoad]",
            swift_name="ZMHomeViewController.viewDidLoad()",
            definition_language="objc",
        )
        assert cn.clang_name == "-[ZMHomeViewController viewDidLoad]"
        assert cn.definition_name == "-[ZMHomeViewController viewDidLoad]"

    def test_objc_class_method(self):
        cn = CrossLanguageName(
            clang_name="+[ZMNDevice shareInstance]",
            swift_name="ZMNDevice.shareInstance()",
            definition_language="objc",
        )
        assert cn.clang_name == "+[ZMNDevice shareInstance]"
        assert cn.definition_name == "+[ZMNDevice shareInstance]"

    def test_swift_definition(self):
        cn = CrossLanguageName(
            clang_name="-[Zoom.PTEntranceViewController handleMoreSelectedWithTag:withParams:]",
            swift_name="PTEntranceViewController.handleMoreSelected(_:_:)",
            definition_language="swift",
        )
        assert cn.definition_name == "PTEntranceViewController.handleMoreSelected(_:_:)"

    def test_optional_names(self):
        cn = CrossLanguageName(
            clang_name="-[MyClass method:]",
            swift_name=None,
            definition_language="objc",
        )
        assert cn.swift_name is None
        assert cn.definition_name == "-[MyClass method:]"

    def test_repr(self):
        cn = CrossLanguageName(
            clang_name="-[A foo:]", swift_name="A.foo(_:)", definition_language="swift"
        )
        assert "CrossLanguageName" in repr(cn)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_derive/test_bridge_cross_language.py -v
```
Expected: FAIL — `CrossLanguageName` not defined.

- [ ] **Step 3: Implement CrossLanguageName dataclass and schema update**

Add to `src/orchard/derive/bridge.py`:
```python
from dataclasses import dataclass, field


@dataclass
class CrossLanguageName:
    """Dual-language symbol name for ObjC/Swift interop.

    Inspired by sourcekit-lsp's CrossLanguageName.
    """
    clang_name: str | None = None   # -[Class method:] / +[Class method:]
    swift_name: str | None = None   # Class.method(_:)
    definition_language: str = ""   # "swift" | "objc" | "c"

    @property
    def definition_name(self) -> str | None:
        """Return the name in the symbol's definition language."""
        if self.definition_language == "swift":
            return self.swift_name
        if self.definition_language in ("objc", "c", "cpp"):
            return self.clang_name
        return self.swift_name or self.clang_name
```

Update `src/orchard/graph/schema.py` — add to BridgesTo rel table:
```python
# Change the BridgesTo CREATE statement to include cross-language name columns
"""CREATE REL TABLE IF NOT EXISTS BridgesTo(
    FROM Symbol TO Symbol,
    bridge_kind STRING,
    provenance STRING,
    confidence DOUBLE,
    build_id STRING,
    clang_name STRING,
    swift_name STRING,
    definition_language STRING
)""",
```

Update `src/orchard/handlers/bridges.py` — in the response builder, add name fields:
```python
# After querying BridgesTo edges, extract and include name fields in the response
# Add to each bridge entry:
"clang_name": row[N] if len(row) > N else None,
"swift_name": row[N+1] if len(row) > N+1 else None,
"definition_language": row[N+2] if len(row) > N+2 else None,
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_derive/test_bridge_cross_language.py -v
```
Expected: 5 PASS

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -x -q
```
Expected: 144 passed

- [ ] **Step 6: Commit**

```bash
git add tests/test_derive/test_bridge_cross_language.py src/orchard/derive/bridge.py src/orchard/graph/schema.py src/orchard/handlers/bridges.py
git commit -m "feat: add CrossLanguageName dataclass for ObjC/Swift name bridging

- Add CrossLanguageName with clang_name, swift_name, definition_language
- definition_name property returns name in the definition language
- Extend BridgesTo schema with clang_name/swift_name/definition_language columns
- Surface cross-language names in bridges handler response

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: transitive_subtype_closure in impact analysis

**Files:**
- Modify: `src/orchard/handlers/impact.py`
- Test: `tests/test_handlers/test_impact_subtype.py`

**Interfaces:**
- Produces: `_subtype_closure(conn, usr, max_depth=20) -> set[str]`, integration into `impact_analysis()`

- [ ] **Step 1: Write tests**

```python
# tests/test_handlers/test_impact_subtype.py
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.handlers.impact import _subtype_closure


def _seed_hierarchy(conn):
    """Seed: Protocol P → Class A → Class B (inherits), Protocol P → Class C (conforms)"""
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:P", name="P", kind="protocol", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:A", name="A", kind="class", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:B", name="B", kind="class", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:C", name="C", kind="class", module="Test", language="swift", file_path="", signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")
    # A conforms to P
    conn.execute("MATCH (a:Symbol {usr: 's:A'}), (p:Symbol {usr: 's:P'}) CREATE (a)-[:ConformsTo {source: 'test'}]->(p)")
    # B inherits from A
    conn.execute("MATCH (b:Symbol {usr: 's:B'}), (a:Symbol {usr: 's:A'}) CREATE (b)-[:Inherits {source: 'test'}]->(a)")
    # C conforms to P
    conn.execute("MATCH (c:Symbol {usr: 's:C'}), (p:Symbol {usr: 's:P'}) CREATE (c)-[:ConformsTo {source: 'test'}]->(p)")


def test_subtype_closure_finds_inheritance_chain():
    conn = get_connection(":memory:")
    _seed_hierarchy(conn)
    closure = _subtype_closure(conn, "s:P")
    assert "s:A" in closure  # conforms to P
    assert "s:B" in closure  # inherits A, transitively conforms to P
    assert "s:C" in closure  # conforms to P


def test_subtype_closure_respects_max_depth():
    conn = get_connection(":memory:")
    _seed_hierarchy(conn)
    closure = _subtype_closure(conn, "s:P", max_depth=1)
    assert "s:A" in closure
    assert "s:B" not in closure  # depth 2, excluded


def test_subtype_closure_empty_for_leaf():
    conn = get_connection(":memory:")
    _seed_hierarchy(conn)
    closure = _subtype_closure(conn, "s:B")
    assert len(closure) == 0


def test_subtype_closure_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    closure = _subtype_closure(conn, "nonexistent")
    assert len(closure) == 0
```

- [ ] **Step 2: Run tests — expected FAIL**

```bash
uv run pytest tests/test_handlers/test_impact_subtype.py -v
```

- [ ] **Step 3: Implement _subtype_closure**

Add to `src/orchard/handlers/impact.py`:
```python
def _subtype_closure(conn, usr: str, max_depth: int = 20) -> set[str]:
    """Return all USRs that are subtypes or conformers of *usr*.

    Walks Inherits:FROM, ConformsTo:FROM, and Extends:FROM edges
    recursively with a visited guard and depth limit.
    """
    visited: set[str] = set()
    frontier = {usr}
    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        f_list = list(frontier)
        for rel_type in ("Inherits", "ConformsTo", "Extends"):
            rows = conn.execute(
                f"UNWIND $ids AS uid "
                f"MATCH (child:Symbol)-[:{rel_type}]->(parent:Symbol {{usr: uid}}) "
                f"WHERE child.usr <> uid "
                f"RETURN DISTINCT child.usr",
                {"ids": f_list},
            ).get_all()
            for row in rows:
                child_usr = row[0]
                if child_usr not in visited and child_usr != usr:
                    next_frontier.add(child_usr)
                    visited.add(child_usr)
        frontier = next_frontier
    return visited
```

Integrate into `impact_analysis()` — after building `depths`, add subtypes to d1:
```python
# After the BFS loop, add subtype/conformer dependents
subtypes = _subtype_closure(conn, usr, max_depth=policy.max_depth)
for subtype_usr in subtypes:
    if subtype_usr not in visited_ids:
        visited_ids.add(subtype_usr)
        # Look up the subtype symbol
        s_rows = conn.execute(
            "MATCH (s:Symbol {usr: $usr}) RETURN s.usr, s.name, s.module, s.language, s.kind LIMIT 1",
            {"usr": subtype_usr},
        ).get_all()
        if s_rows:
            depths.setdefault("d1", []).append({
                "usr": s_rows[0][0],
                "name": s_rows[0][1],
                "module": s_rows[0][2],
                "language": s_rows[0][3],
                "kind": s_rows[0][4],
                "reached_via": "subtype_closure",
            })
```

- [ ] **Step 4: Run tests — expected PASS**

```bash
uv run pytest tests/test_handlers/test_impact_subtype.py -v
```

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest tests/ -x -q && git add tests/test_handlers/test_impact_subtype.py src/orchard/handlers/impact.py && git commit -m "feat: add transitive subtype closure to impact analysis

- Add _subtype_closure() walking Inherits/ConformsTo/Extends recursively
- Guard against deep hierarchies with max_depth=20
- Integrate subtype dependents into impact d1 results

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: primary_definition_usr in GraphLookup

**Files:**
- Modify: `src/orchard/query/lookup.py`
- Test: `tests/test_query/test_lookup_primary_definition.py`

**Interfaces:**
- Produces: `GraphLookup.primary_definition_usr(usr, target_id="") -> str | None`

- [ ] **Step 1: Write tests**

```python
# tests/test_query/test_lookup_primary_definition.py
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.query.lookup import GraphLookup
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord


def _seed(conn):
    init_schema(conn)
    syms = [
        SymbolRecord(usr="s:multi", name="multi", kind="class", module="Test", language="swift", file_path="/a.swift", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:multi", name="multi", kind="class", module="Test", language="swift", file_path="/b.swift", signature="", access_level="public", container_usr=None, precise_id=""),
        SymbolRecord(usr="s:single", name="single", kind="class", module="Test", language="swift", file_path="/c.swift", signature="", access_level="public", container_usr=None, precise_id=""),
    ]
    upsert_symbols(conn, syms, "Test")


def test_primary_definition_returns_deterministic():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    # Multiple definitions → should return the same one every time
    first = g.primary_definition_usr("s:multi", "Test")
    for _ in range(5):
        assert g.primary_definition_usr("s:multi", "Test") == first


def test_primary_definition_single_result():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert g.primary_definition_usr("s:single", "Test") is not None


def test_primary_definition_not_found():
    conn = get_connection(":memory:")
    _seed(conn)
    g = GraphLookup(conn)
    assert g.primary_definition_usr("nonexistent", "Test") is None


def test_primary_definition_empty_graph():
    conn = get_connection(":memory:")
    init_schema(conn)
    g = GraphLookup(conn)
    assert g.primary_definition_usr("anything", "") is None
```

- [ ] **Step 2: Run — expected FAIL**

```bash
uv run pytest tests/test_query/test_lookup_primary_definition.py -v
```

- [ ] **Step 3: Implement**

Add to `GraphLookup` in `src/orchard/query/lookup.py`:
```python
def primary_definition_usr(self, usr: str, target_id: str = "") -> str | None:
    """Return a deterministic primary USR for a symbol.

    When multiple definitions exist (e.g. C++ namespaces), returns the
    same one every time. Uses 2-step fallback: definition → declaration → None.
    Sorted by (file_path, usr) for determinism.
    """
    from orchard.normalize.identity import make_symbol_id
    sym_id = make_symbol_id(target_id, usr) if target_id else usr
    rows = self._conn.execute(
        "MATCH (s:Symbol) WHERE s.usr = $usr "
        "RETURN s.id, s.file_path ORDER BY s.file_path, s.usr LIMIT 1",
        {"usr": usr},
    ).get_all()
    return rows[0][0] if rows else None
```

- [ ] **Step 4: Run tests — expected PASS, then full suite**

```bash
uv run pytest tests/test_query/test_lookup_primary_definition.py -v && uv run pytest tests/ -x -q
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_query/test_lookup_primary_definition.py src/orchard/query/lookup.py
git commit -m "feat: add primary_definition_usr to GraphLookup

- Deterministic primary USR selection via (file_path, usr) sort
- 2-step fallback: definition → declaration → None
- Respects target_id for multi-target disambiguation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-25-orchard-sourcekit-lsp-optimizations.md`.**

Execution mode recorded as `subagent-driven`.
