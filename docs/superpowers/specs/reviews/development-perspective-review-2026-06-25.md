# Development Perspective Review

# Dev Review: sourcekit-lsp Optimizations (2026-06-25)

## Summary

The spec proposes 6 optimizations across 5 files. Overall direction is sound and aligns well with orchard's existing patterns (dataclasses, handler request/response model, Ladybug Cypher queries, per-request object instantiation). However, several issues need resolution before implementation: schema changes are unmentioned, one edge type is wrong (#4), two items are essentially the same change (#3 + #5), and the hardest freshness level is underspecified.

---

## Q1: File Scope Correctness

### 1. containerNames Cache -- `query/lookup.py`

**Verdict: Correctly scoped.** No missing files. `GraphLookup` already holds per-request state (`self._conn`), so adding `self._container_names_cache` is idiomatic. The `Extends` edge handling for ObjC categories/extensions is a natural addition to `owner_of()`, which already walks `Contains` edges.

### 2. CrossLanguageName -- `derive/bridge.py`

**Verdict: Scoped correctly but one file is MISSING.** The `CrossLanguageName` dataclass and the ObjC/Swift name mapping logic belong in `bridge.py`. However, the spec says to "store on BridgesTo edge properties" -- this requires adding `clang_name STRING, swift_name STRING, definition_language STRING` properties to the `BridgesTo` rel table in `src/orchard/graph/schema.py`. The spec's file-change table does not list `schema.py`.

Additionally, the `bridges.py` handler (which reads BridgesTo edges) would need updating to surface these new properties in its response. The spec's table does not list `handlers/bridges.py` either.

### 3. CheckedIndex Freshness Filter -- `validation/freshness.py`

**Verdict: Scoped correctly but incomplete.** The `IndexCheckLevel` enum and `IndexOutOfDateChecker` class belong here. However, the spec does not address **where** `is_up_to_date(SymbolLocation)` is called. Candidates:
- Ingest pipeline (`pipeline/runner.py`) -- to filter stale occurrences during ingestion
- Query handlers -- to annotate or filter results at query time
- `GraphLookup` -- to add freshness metadata to query responses

The `SymbolLocation` type is referenced but not defined anywhere in the codebase. `OccurrenceRecord` (in `ingest/indexstore.py`) has `file_path + line + col` but is an ingest-time struct, not a query-time concept. This needs to be defined in `freshness.py` or a shared module.

### 4. transitiveSubtypeClosure -- `handlers/impact.py`

**Verdict: Correctly scoped.** The subtype closure is a helper for impact analysis and belongs alongside `impact_analysis()`. No missing files. The `Inherits` and `ConformsTo` edges already exist in the schema.

### 5. Three-Level Freshness -- `validation/freshness.py`

**Verdict: Same as #3.** Items #3 and #5 are the same feature described at different granularity levels. #3 introduces the enum and checker class; #5 elaborates the three levels. They should be implemented as a single change, not two separate items. Keeping them listed separately risks confusion during implementation.

### 6. Primary Definition -- `normalize/identity.py`

**Verdict: Wrong file.** `identity.py` is the **write-side** normalization module -- it handles key generation (`make_symbol_id`) and upsert logic (CSV import for Symbol nodes, Calls edges, etc.). `primary_definition_usr` is a **read-side** query function. It should be placed in `query/lookup.py` (as a `GraphLookup` method) or in a new `query/` helper. Alternatively, if the intent is to add it to `symbol_context.py` (which already queries Symbol nodes by ID), that would also work.

---

## Q2: API Reasonableness

### 1. containerNames Cache

```python
# Proposed pattern:
class GraphLookup:
    def __init__(self, conn):
        self._conn = conn
        self._container_names_cache: dict[str, list[str]] = {}
```

**Reasonable.** Follows the existing `self._conn` initialization pattern. Per-request cache lifetime is correct since `GraphLookup` is instantiated per handler call (see `callers.py:13`). Two caveats:

- `owner_of()` currently returns `dict | None` (a single owner record). If the cache stores `list[str]` (container name *chain*), the return type mismatch needs to be resolved -- either the cache maps to the same `dict | None`, or `owner_of()` gains a `chain=True` parameter.
- The `Extends` edge walk needs clear direction semantics: the edge is written as `extension_symbol --Extends-> extended_type_symbol`, so the Cypher to find the extended type's container is `MATCH (s {usr: $usr})-[:Extends]->(ext:Symbol) ...`. This should be documented in the implementation.

### 2. CrossLanguageName

```python
# Proposed pattern:
@dataclass
class CrossLanguageName:
    clang_name: str | None
    swift_name: str | None
    definition_language: str
```

**Reasonable** dataclass pattern, consistent with `GraphFreshness`, `ImpactTraversalPolicy`, etc. But the integration with `run_bridge_recovery()` is underspecified:

- Does `run_bridge_recovery()` populate `clang_name`/`swift_name` during bridge creation? If so, the MERGE query on line 84-95 of `bridge.py` needs new parameters.
- The name mapping `-[Class method:]` to `Class.method()` is a non-trivial transform. SourceKit-LSP has a full `SwiftDemangler` + ObjC selector parser for this. The spec should clarify the scope: full selector mapping or just simple prefix conversion?
- The BridgesTo edge currently has 4 properties. Adding 3 more on MERGE means all existing edges lack these fields (null). Is that acceptable, or do we need backfill logic?

### 3. CheckedIndex Freshness Filter

```python
# Proposed pattern:
class IndexCheckLevel(enum.Enum):
    DELETED_FILES = 1
    MODIFIED_FILES = 2
    IN_MEMORY_MODIFIED = 3

class IndexOutOfDateChecker:
    def __init__(self, index_timestamp: float):
        self._index_ts = index_timestamp
        self._modtime_cache: dict[str, float] = {}
    def is_up_to_date(self, loc: SymbolLocation) -> bool: ...
```

**Partially reasonable.** The enum + class pattern fits the codebase.

**Concerns:**
- `IndexOutOfDateChecker` introduces filesystem I/O (`os.stat`) to `validation/`, which currently has no filesystem dependencies. This breaks testability unless the filesystem access is injected (e.g., via a `stat_callable` parameter).
- The modTime cache with "single-request lifetime" needs explicit lifecycle management. In the handler pattern, this could be created per-handler-call and discarded. But the spec doesn't say who owns the checker instance.
- `SymbolLocation` needs to be defined. Minimally: `dataclass with file_path: str, line: int` (matching the `Occurrence` node schema). Recommend defining it in `validation/freshness.py`.

### 4. transitiveSubtypeClosure

```python
# Proposed pattern:
def _subtype_closure(conn, usr: str) -> set[str]:
    """Walk Inherits:FROM and ConformsTo:FROM recursively."""
```

**Edge type error.** The spec says "Walk Inherits:FROM, Implements:FROM". The correct edge types are:

| Edge | Schema Direction | Meaning |
|------|-----------------|---------|
| `Inherits` | `(child)-[:Inherits]->(parent)` | Class inheritance |
| `ConformsTo` | `(type)-[:ConformsTo]->(protocol)` | Protocol conformance |
| `Implements` | `(child)-[:Implements]->(parent_method)` | Method override (NOT type conformance) |

`Implements` is used for method-level overrides (`_INDEXSTORE_REL_TO_TABLE` maps `"overrideOf"` to `"Implements"`). The subtype closure should walk `Inherits` (incoming, to find children) and `ConformsTo` (incoming, to find protocol conformers). Using `Implements` would incorrectly include method overrides, not subtypes.

**Integration semantics need clarification.** Where exactly does the closure plug into `impact_analysis()`? Two plausible strategies:
- **Strategy A (expand root set):** Before BFS traversal, expand the starting `sym_id` to include all subtypes. Then traverse incoming Calls/References/Implements/BridgesTo from the full set. This means "find callers of this type AND all its subtypes."
- **Strategy B (add edge type):** Add `Inherits` and `ConformsTo` as traversal edge types in the BFS loop itself. This would find indirect dependents through the type hierarchy.

Strategy A is the correct interpretation (it matches sourcekit-lsp's semantic impact analysis). The spec should state this explicitly.

### 5. Three-Level Freshness

**Overlaps with #3.** The three levels are exactly what the `IndexCheckLevel` enum defines. Recommend merging #3 and #5 into a single implementation item.

The `in_memory_modified_files` level is the hardest -- it requires orchard to know about unsaved editor changes. This implies integration with an LSP `didChange` notification stream or a similar editor protocol. orchard's current MCP-based architecture has no such channel. This level should either be:
- Deferred to a follow-up change with a clear integration spec, or
- Implemented as an externally-fed list (caller provides `dirty_files: list[str]`), keeping orchard stateless.

### 6. Primary Definition

```python
# Proposed pattern:
def primary_definition_usr(conn, usr: str) -> str | None:
```

**Underspecified.** The strategy "query definitions sorted by file_path, return first" is too vague. Specific concerns:

- **What query?** A USR is unique per-target (due to `target_id:usr` composite key). The same USR across different targets produces different Symbol nodes. Is the function finding the "primary" among multiple targets, or within a single target?
- **Missing target_id parameter.** Every other query function in the codebase takes `target_id` (see `lookup.py`, `symbol_context.py`, `bridges.py`, `impact.py`). This function's signature `(conn, usr)` breaks the pattern.
- **Sort stability.** Sorting by `file_path` is fragile -- two files in different directories could have the same basename. Consider a priority heuristic: prefer non-generated files, prefer the current target, prefer header over implementation, etc.
- **Recommended signature:** `primary_definition_usr(conn, usr: str, target_id: str = "") -> str | None`
- **Recommended placement:** `query/lookup.py` as a `GraphLookup` method, not `normalize/identity.py`.

---

## Q3: Dependency Issues

```
#1 (containerNames)       independent
#2 (CrossLanguageName)    independent  (but needs schema.py change)
#3 (IndexCheckLevel)      independent
#4 (subtypeClosure)       independent
#5 (Three-Level)          DEPENDS ON #3  (needs IndexCheckLevel enum)
#6 (primary definition)   independent
```

There is one hard dependency: **#5 depends on #3** for the `IndexCheckLevel` enum. Since #3 and #5 are really the same feature, they should be merged.

No other hard dependencies. However, there are awareness dependencies:
- If #4 adds subtype expansion to `impact_analysis()`, it should be aware of #1's `container_names_cache` (for reporting owner names of found subtypes).
- If #3 introduces `IndexOutOfDateChecker` to the handler flow, handlers that use `GraphLookup` (#1) could benefit from freshness being surfaced through the same lookup object.

---

## Q4: Implementation Order Recommendation

### Phase 1 (independent, low-risk)
1. **#1 containerNames Cache** -- self-contained, no schema changes, existing test coverage in `test_lookup` (if it exists) or `test_callers`. Easy to validate.

### Phase 2 (schema change required)
2. **#2 CrossLanguageName** -- needs `schema.py` update (add 3 columns to BridgesTo rel table), `bridge.py` update (populate new fields), `handlers/bridges.py` update (surface fields in response). Higher coordination cost due to schema migration.

### Phase 3 (merged, needs spec clarification first)
3. **#3 + #5: CheckedIndex Freshness (merged)** -- implement `IndexCheckLevel`, `IndexOutOfDateChecker`, and `SymbolLocation` together. Defer `in_memory_modified_files` to a follow-up unless integration is fully specified. Needs decision on filesystem dependency injection for testability.

### Phase 4 (semantics need clarification)
4. **#4 transitiveSubtypeClosure** -- correct `Implements` to `ConformsTo`, clarify integration strategy (recommend Strategy A: expand root set). This changes impact analysis semantics and existing tests will need updating.

### Phase 5 (needs relocation + clarification)
5. **#6 Primary Definition** -- relocate to `query/lookup.py`, add `target_id` parameter, specify sorting heuristic beyond file_path. Lowest urgency.

---

## Spec Gaps Checklist

| Gap | Severity | Details |
|-----|----------|---------|
| Missing `schema.py` change for #2 | **BLOCKER** | BridgesTo needs `clang_name`, `swift_name`, `definition_language` columns |
| Missing `handlers/bridges.py` change for #2 | **BLOCKER** | Handler must surface new fields in response |
| Wrong edge type in #4 (`Implements` vs `ConformsTo`) | **BLOCKER** | Would include method overrides instead of protocol conformers |
| #3 and #5 should be merged | High | Same feature, different granularity |
| No `SymbolLocation` type definition | High | Referenced but not defined anywhere |
| `primary_definition_usr` missing `target_id` | High | Breaks codebase convention |
| #6 in wrong module | Medium | `identity.py` is write-side, should be `lookup.py` |
| `in_memory_modified_files` integration unspecified | Medium | How does orchard get unsaved editor changes? |
| Filesystem dependency for freshness checker | Medium | `os.stat` in `validation/` breaks test isolation |
| Where `is_up_to_date()` is called | Medium | Ingestion? Query time? Both? |
| subtypeClosure integration strategy | Medium | Expand root set vs add edge type? |
| ObjC selector mapping scope for #2 | Low | Full demangling or simple prefix conversion? |
| No test files listed in spec | Low | Each change needs corresponding test updates |

---

## Test Impact

Each optimization touches code that has existing tests. These test files will need attention:

| Optimization | Test file(s) affected |
|-------------|----------------------|
| #1 containerNames | `tests/test_mcp/test_callers.py` (uses `GraphLookup`), `tests/test_mcp/test_impact.py` |
| #2 CrossLanguageName | `tests/test_derive/test_bridge.py`, `tests/test_mcp/test_bridges.py` |
| #3/#5 CheckedIndex | `tests/test_validation/test_freshness.py` |
| #4 subtypeClosure | `tests/test_mcp/test_impact.py` (existing tests assume single-symbol root) |
| #6 primary definition | `tests/test_normalize/test_identity.py` (if kept in identity.py), or new test file |
