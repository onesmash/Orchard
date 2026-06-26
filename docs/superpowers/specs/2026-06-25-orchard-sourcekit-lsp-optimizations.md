# Orchard sourcekit-lsp Pattern Optimizations

> Approved design, revised after 3-perspective subagent review (dev/design/test).
> Review artifacts: `docs/superpowers/specs/reviews/*-review-2026-06-25.md`

## P0 — Implement First (Correctness & Performance)

### 1. containerNames Cache (`src/orchard/query/lookup.py`)
**Review:** Dev ✅ | Design ✅ | Test ✅ (10 test cases)
- Add per-request `_container_names_cache: dict[str, list[str]]` to `GraphLookup`
- Cache USR → container name chain (outermost→innermost) in `owner_of()`
- Handle extension: when owner.kind == "extension", follow `Extends` edge to get extended type before caching
- Fetch full ancestor chain in one Cypher query (not per-node) for performance

### 2. CheckedIndex Freshness Filter + IndexCheckLevel (`src/orchard/validation/freshness.py`)
**Review:** Dev ✅ | Design ✅ | Test ⚠️ (10+8=18 test cases, highest risk: freshness_for() signature change)
- **Merged from original #3 and #5** (duplicate scope)
- Add `IndexCheckLevel` enum: `DELETED_FILES` | `MODIFIED_FILES` | `IN_MEMORY_MODIFIED_FILES` (PEP 8 upper snake_case)
- Add `IndexOutOfDateChecker` class with modTime cache (single-request lifetime, filesystem I/O dependency-injected)
- `is_up_to_date(location)` compares file mtime vs index timestamp per check level
- `in_memory_modified_files` level: no associated value needed in offline batch mode
- **Backward compatibility**: existing `freshness_for()` consumers must not break

## P1 — Quality Improvements

### 3. CrossLanguageName (`src/orchard/derive/bridge.py`)
**Review:** Dev ⚠️ (missing schema+handler) | Design ⚠️ (depends on M4 USR correlation) | Test ✅ (10 test cases)
- **Moved from P0 to P1**: depends on M4 USR correlation for reliability
- Add `CrossLanguageName` dataclass: `clang_name: str | None`, `swift_name: str | None`, `definition_language: str`
- ObjC: `-[ClassName methodName:]` for instance, `+[ClassName methodName:]` for class methods
- Swift: `ClassName.methodName(_:)` with argument labels
- Store `clang_name`, `swift_name`, `definition_language` as BridgesTo edge properties
- Add columns to `schema.py` BridgesTo table
- Surface new fields in `handlers/bridges.py` response

### 4. transitive_subtype_closure (`src/orchard/handlers/impact.py`)
**Review:** Dev ⚠️ (ConformsTo not Implements) | Design ⚠️ (max_depth guard) | Test ✅ (12 test cases)
- **Fixed edge type**: `ConformsTo` (protocol conformance), not `Implements` (method override)
- Add `_subtype_closure(conn, usr, max_depth=20)` → `set[str]`
- Walk `Inherits:FROM`, `ConformsTo:FROM`, `Extends:FROM` recursively with visited guard
- Guard against deep hierarchies with `max_depth` parameter
- Integrate into impact analysis depth computation and risk scoring

## P2 — Polish (Lower Priority)

### 5. Primary Definition (`src/orchard/query/lookup.py`)
**Review:** Dev ⚠️ (wrong module + missing target_id) | Design ⚠️ (no fallback + sort incomplete) | Test ✅ (8 test cases)
- **Relocated** from `normalize/identity.py` (write-side) to `query/lookup.py` (read-side)
- Add `primary_definition_usr(conn, usr, target_id="")` as `GraphLookup` method
- 2-step fallback: definitions → declarations → None (matching sourcekit-lsp)
- Deterministic sort: `(file_path, usr)` — usable with current Symbol node fields
- Add `target_id` parameter to match codebase conventions

## Implementation Order

| Phase | Items | Risk | Schema Change |
|-------|-------|------|---------------|
| 1 | #1 containerNames cache | Low | No |
| 2 | #3 CrossLanguageName | Medium | Yes (BridgesTo columns) |
| 3 | #2 CheckedIndex + IndexCheckLevel | High | No (signature change) |
| 4 | #4 transitive_subtype_closure | Medium | No |
| 5 | #5 primary_definition_usr | Low | No |

## Files Changed
| File | Changes |
|------|---------|
| `src/orchard/query/lookup.py` | #1 containerNames cache + #5 primary_definition_usr |
| `src/orchard/derive/bridge.py` | #3 CrossLanguageName dataclass + populate |
| `src/orchard/validation/freshness.py` | #2 IndexCheckLevel + IndexOutOfDateChecker |
| `src/orchard/handlers/impact.py` | #4 transitive_subtype_closure |
| `src/orchard/graph/schema.py` | #3 BridgesTo column additions |
| `src/orchard/handlers/bridges.py` | #3 surface new CrossLanguageName fields |
