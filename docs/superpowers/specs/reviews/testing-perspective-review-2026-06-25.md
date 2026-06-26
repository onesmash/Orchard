# Testing Perspective Review

# Test Review: SourceKit-LSP Pattern Optimizations

> Reviewed against `2026-06-25-orchard-sourcekit-lsp-optimizations.md`.  Test patterns surveyed from `tests/test_derive/test_bridge.py`, `tests/test_validate/test_freshness.py`, `tests/test_normalize/test_identity.py`, `tests/test_mcp/test_impact.py`, `tests/test_derive/test_architecture.py`, `tests/test_mcp/test_callers.py`, `tests/test_mcp/test_type_hierarchy.py`, and `tests/conftest.py`.

---

## 1. Per-Optimization Required Test Cases

### 1.1  containerNames Cache (`src/orchard/query/lookup.py`) -- P0

Cache USR -> container name chain in `owner_of()`, including extension symbol support via `Extends` edges.

| # | Test case | Rationale |
|---|-----------|-----------|
| 1 | `test_owner_of_caches_result` | Call `owner_of()` twice for the same USR; assert the 2nd call returns the same result without issuing a duplicate Cypher query. The per-request cache must live on the `GraphLookup` instance. |
| 2 | `test_owner_of_no_mutation_after_cache` | Verify that symbols created *after* the first `owner_of()` call are NOT reflected in the cached result (the cache is per-request, not a database cache). |
| 3 | `test_owner_of_extension_symbol` | Seed: `ExtensionX --[:Extends]--> SomeClass`. Call `owner_of()` for `ExtensionX`; assert the returned owner is `SomeClass`'s USR/name/kind, not `ExtensionX`'s own. |
| 4 | `test_owner_of_extension_symbol_chain` | Seed: `ExtensionX --[:Extends]--> SomeClass --[:Contains]--> SomeNested`. Call `owner_of()` on a symbol inside `ExtensionX`; assert the container chain resolves through `Extends` to `SomeClass`. |
| 5 | `test_owner_of_no_extends_edge` | Symbol has no `Contains` parent and no `Extends` edge; assert `owner_of()` returns `None`. |
| 6 | `test_owner_of_empty_graph` | Graph has no symbols at all; `owner_of("nonexistent")` returns `None` without error. |
| 7 | `test_owner_of_multi_level_containers` | Deeply nested: `Outer --[:Contains]--> Middle --[:Contains]--> Inner --[:Contains]--> Leaf`. Call `owner_of()` on `Leaf`; assert it returns `Inner` (the immediate parent, not the root). |
| 8 | `test_owner_of_cache_scoped_to_instance` | Two separate `GraphLookup` instances on the same connection do not share caches; `owner_of()` on instance A does not pre-populate instance B's cache. |
| 9 | `test_owner_of_extension_on_swift_type` | Swift extension (`language="swift"`) extends a Swift struct; verify the `Extends` edge is followed. |
| 10 | `test_owner_of_objc_category` | ObjC category extends an ObjC class; verify the `Extends` edge resolves the owning class correctly. |

**Edge cases specific to this optimization:**
- Extension symbol that *extends itself* (self-referential `Extends` edge in buggy data).
- An extension with a `Contains` parent AND an `Extends` edge (which wins? spec says "follow Extends to get extended type's container name", so Extends should take precedence).
- Symbol whose `Contains` parent is an extension (transitive Extends).

---

### 1.2  CrossLanguageName (`src/orchard/derive/bridge.py`) -- P0

New `CrossLanguageName` dataclass; ObjC `-[Class method:]` <-> Swift `Class.method()` mapping; stored on `BridgesTo` edge properties.

| # | Test case | Rationale |
|---|-----------|-----------|
| 1 | `test_cross_language_name_swift_to_objc` | Input: swift_name=`"MyClass.doSomething(_:)"`; expect `clang_name="-[MyClass doSomething:]"`. |
| 2 | `test_cross_language_name_objc_to_swift` | Input: clang_name=`"-[MyClass doSomething:]"`; expect `swift_name="MyClass.doSomething(_:)"`. |
| 3 | `test_cross_language_name_stores_on_bridge_edge` | After `run_bridge_recovery()`, verify `BridgesTo` edges carry `clang_name` and `swift_name` properties (or a single `cross_language_name` JSON property). |
| 4 | `test_cross_language_name_none_values` | A symbol with only a Swift name and no ObjC counterpart has `clang_name=None`. |
| 5 | `test_cross_language_name_roundtrip` | ObjC -> Swift -> ObjC roundtrip produces the original ObjC name. |
| 6 | `test_cross_language_name_init_method` | ObjC `-[MyClass initWithFrame:]` maps to Swift `MyClass.init(frame:)`. |
| 7 | `test_cross_language_name_class_method` | ObjC `+[MyClass sharedInstance]` maps to Swift `MyClass.sharedInstance()`. |
| 8 | `test_cross_language_name_definition_language` | The `definition_language` field reflects where the symbol is *defined* (not where it is bridged to). |
| 9 | `test_cross_language_name_no_params` | ObjC `-[MyClass refresh]` maps to Swift `MyClass.refresh()`. Method with no parameters must NOT leave a trailing `:` in the ObjC name. |
| 10 | `test_bridge_edge_carries_cross_language_name` | Full integration: seed ObjC+Swift symbols, run `run_bridge_recovery()`, then MATCH the BridgesTo edge and assert the CrossLanguageName fields are present. |

**Edge cases specific to this optimization:**
- Swift name includes argument labels with backticks (e.g., `` `default` `` keyword escape).
- ObjC protocol methods vs. class methods (leading `+` vs `-`).
- Swift names with multiple parameter clauses (e.g., `func foo(_:)(_:)`).
- Malformed names that don't match either pattern (should not crash, should return `None` for the corresponding field).

---

### 1.3  CheckedIndex Freshness Filter (`src/orchard/validation/freshness.py`) -- P1

New `IndexCheckLevel` enum + `IndexOutOfDateChecker` class with modTime cache; `is_up_to_date(SymbolLocation)` compares file mtime vs index timestamp.

| # | Test case | Rationale |
|---|-----------|-----------|
| 1 | `test_index_check_level_enum_values` | Assert `DELETED_FILES`, `MODIFIED_FILES`, `IN_MEMORY_MODIFIED` are defined members. |
| 2 | `test_is_up_to_date_file_deleted` | File in `SymbolLocation` does not exist on disk; `is_up_to_date()` returns `False`. |
| 3 | `test_is_up_to_date_file_not_modified` | File exists and mtime <= index timestamp; `is_up_to_date()` returns `True`. |
| 4 | `test_is_up_to_date_file_modified` | File exists and mtime > index timestamp; `is_up_to_date()` returns `False`. |
| 5 | `test_is_up_to_date_modtime_cache` | Call `is_up_to_date()` twice for the same file path; the second call should use the cached mtime, not stat the file again. |
| 6 | `test_is_up_to_date_different_levels` | Verify `DELETED_FILES` only checks file existence, `MODIFIED_FILES` checks mtime, `IN_MEMORY_MODIFIED` checks in-memory buffer (requires mock or test hook). |
| 7 | `test_out_of_date_checker_single_request_lifetime` | `IndexOutOfDateChecker` instantiates with a fresh modTime cache; calling `is_up_to_date()` on the same path across two separate instances does NOT share cache. |
| 8 | `test_is_up_to_date_missing_file_in_mode` | In `DELETED_FILES` mode, symlinks or moved files are treated as "not up to date" if the path does not resolve. |
| 9 | `test_out_of_date_checker_empty_symbol_location` | `SymbolLocation` with empty `file_path`; `is_up_to_date()` should handle gracefully (return `False` or raise a well-defined exception). |
| 10 | `test_filter_occurrences` | Integration: given a list of `SymbolLocation` objects, `IndexOutOfDateChecker.filter_occurrences()` returns only those that are up-to-date. |

**Edge cases specific to this optimization:**
- File path is a symlink pointing to a deleted target.
- File path is a directory (not a regular file).
- mtime comparison across timezone boundaries (index timestamp in UTC, filesystem in local).
- Very large number of distinct file paths (modTime cache memory pressure).
- File path normalization (relative vs absolute, trailing slashes).

---

### 1.4  transitiveSubtypeClosure (`src/orchard/handlers/impact.py`) -- P1

New `_subtype_closure(conn, usr)` -> `set[str]`; walks `Inherits:FROM` and `Implements:FROM` recursively; integrates into impact analysis depth computation.

| # | Test case | Rationale |
|---|-----------|-----------|
| 1 | `test_subtype_closure_single_inheritance` | `Child --[:Inherits]--> Parent`; `_subtype_closure(conn, "Parent")` returns `{"Child"}`. |
| 2 | `test_subtype_closure_multi_level` | `Grandchild -> Child -> Parent`; closure for `Parent` returns `{Child, Grandchild}`. |
| 3 | `test_subtype_closure_protocol_conformance` | `MyClass --[:ConformsTo]--> MyProto`; the closure should NOT include conformers unless `Implements` edges are used (verify edge type mapping — spec says `Implements:FROM`). |
| 4 | `test_subtype_closure_implements_edge` | IndexStore `overrideOf` produces `Implements` edges; verify these are walked in the closure. |
| 5 | `test_subtype_closure_no_subtypes` | Symbol with no incoming `Inherits` or `Implements` edges returns an empty set, not `None`. |
| 6 | `test_subtype_closure_cycle_guard` | `A --[:Inherits]--> B --[:Inherits]--> A`; the closure must terminate without infinite recursion (visited set). |
| 7 | `test_subtype_closure_multiple_targets` | Same USR exists in target A and target B; `_subtype_closure()` with `target_id` disambiguates. |
| 8 | `test_subtype_closure_empty_graph` | No symbols in graph; returns empty set without error. |
| 9 | `test_impact_includes_subtypes_in_depth` | Integration: seed `targetFn`, `SubType --[:Inherits]--> targetFn`, `SubCaller --[:Calls]--> SubType`. Query impact of `targetFn`; assert `SubCaller` appears in the by_depth result (reached through the subtype closure). |
| 10 | `test_impact_subtype_closure_depth_accounting` | `Grandchild -> Child -> Parent`. Caller calls `Grandchild`. Impact analysis on `Parent` should show the caller at depth=3 (or depth=2 + subtype depth), not depth=1. The depth accounting must be documented and tested. |
| 11 | `test_subtype_closure_diamond_inheritance` | `D --[:Inherits]--> B1`, `D --[:Inherits]--> B2`, `B1 --[:Inherits]--> A`, `B2 --[:Inherits]--> A`. Closure for `A` returns `{B1, B2, D}` (each subtype once, no duplicates). |
| 12 | `test_subtype_closure_mixed_relations` | `Child --[:Inherits]--> Parent`, `Child --[:ConformsTo]--> Proto`, `Other --[:Implements]--> Child`. Closure for `Parent` should include `Child` and `Other` (following the full subtype + implementor chain). |

**Edge cases specific to this optimization:**
- A symbol that inherits from itself (malformed data).
- `Inherits:FROM` edges from IndexStore vs `Inherits` edges from symbolgraph — are both traversed?
- The closure should filter by `target_id` to avoid leaking subtypes across targets.
- Performance: deep inheritance chain of 50+ levels (UIKit view hierarchy); must not exceed recursion limit.

---

### 1.5  Three-Level Freshness (`src/orchard/validation/freshness.py`) -- P2

Replace binary fresh/stale status with `IndexCheckLevel`; three granular levels.

| # | Test case | Rationale |
|---|-----------|-----------|
| 1 | `test_three_level_regression_binary_fresh` | Existing `freshness_for()` tests (fresh, stale, toolchain_mismatch, build_mismatch) MUST still pass with the same return values. |
| 2 | `test_three_level_regression_binary_stale` | No snapshot present still returns `status="stale"`. |
| 3 | `test_three_level_deleted_files_check` | `DELETED_FILES` level: a symbol whose source file was deleted is filtered out. |
| 4 | `test_three_level_modified_files_check` | `MODIFIED_FILES` level (default): a symbol whose source file's mtime is newer than the index timestamp is filtered out. |
| 5 | `test_three_level_in_memory_modified_check` | `IN_MEMORY_MODIFIED` level: unsaved editor changes cause symbols to be filtered out (requires a test hook or mock for in-memory buffer tracking). |
| 6 | `test_three_level_per_request_config` | A handler can pass `IndexCheckLevel.DELETED_FILES` to get a lighter check or `IN_MEMORY_MODIFIED` for strict checking. |
| 7 | `test_three_level_integration_with_impact` | Impact analysis handler using freshness now receives `IndexCheckLevel`-aware results; verify the risk level computation is unchanged for stale vs fresh. |
| 8 | `test_three_level_integration_with_lookup` | `GraphLookup.freshness()` returns `IndexCheckLevel`-aware data; callers that relied on the status string must be compatible. |

**Edge cases specific to this optimization:**
- What happens when `IN_MEMORY_MODIFIED` is requested but no editor integration is active? Should degrade gracefully to `MODIFIED_FILES`.
- The `IndexOutOfDateChecker` modTime cache (from optimization 3) must be consistent with the `IndexCheckLevel` selected.
- Changing the default check level from binary to `MODIFIED_FILES` could cause previously "fresh" snapshots to become "stale" if source files were modified since indexing.

---

### 1.6  Primary Definition (`src/orchard/normalize/identity.py`) -- P2

`primary_definition_usr(conn, usr)` returns a deterministic single USR from potentially multiple definitions, sorted by `file_path`.

| # | Test case | Rationale |
|---|-----------|-----------|
| 1 | `test_primary_definition_single_definition` | One Symbol node for the given USR; `primary_definition_usr()` returns that USR. |
| 2 | `test_primary_definition_multiple_definitions` | Two Symbol nodes in different targets with the same USR; returns the one with the alphabetically-first `file_path`. |
| 3 | `test_primary_definition_deterministic_sort` | Same input always returns the same USR (no `LIMIT 1` without `ORDER BY`). |
| 4 | `test_primary_definition_no_definition` | No Symbol node for the given USR; raises a well-defined exception or returns `None`. |
| 5 | `test_primary_definition_different_targets` | Same USR in TargetA and TargetB with different file_paths; the sort order is deterministic. |
| 6 | `test_primary_definition_with_target_filter` | If `target_id` is provided, only definitions within that target are considered. |
| 7 | `test_primary_definition_empty_file_path` | One symbol has `file_path=""`, another has `file_path="/src/main.swift"`; the sort order is still deterministic (empty sorts first or last consistently). |
| 8 | `test_primary_definition_all_empty_file_paths` | All definitions have `file_path=""`; the function still returns exactly one USR deterministically (e.g., by `id` as tiebreaker). |

**Edge cases specific to this optimization:**
- USR that appears only in an extension target (e.g., app extension vs main app).
- Symbols from different languages (Swift vs ObjC) with overlapping USRs.
- Generated or synthetic symbols (`is_generated=true`) should they be deprioritized? Spec says "sorted by file_path", so no — but this is worth confirming.

---

## 2. Existing Tests That May Break

### 2.1  Definitely affected

| File | Test function | Why it may break |
|------|--------------|------------------|
| `tests/test_validation/test_freshness.py` | `test_freshness_fresh` | Optimization 5 changes the return type of `freshness_for()` from binary strings to `IndexCheckLevel`-aware values. If the response shape changes (e.g., tuple expanded from 2 to 3 elements), all callers break. |
| `tests/test_validation/test_freshness.py` | `test_freshness_toolchain_mismatch` | Same as above. |
| `tests/test_validation/test_freshness.py` | `test_freshness_build_mismatch` | Same as above. |
| `tests/test_validation/test_freshness.py` | `test_freshness_no_snapshot` | Same as above. |
| `tests/test_validation/test_freshness.py` | `test_freshness_reads_sdk_configuration_created_at` | Same as above. |

### 2.2  Probably affected

| File | Test function | Why it may break |
|------|--------------|------------------|
| `tests/test_mcp/test_impact.py` | `test_impact_returns_callers_by_depth` | Optimization 4 (transitiveSubtypeClosure) broadens which dependents are found. If the test graph has no subtype edges, it should be unchanged, but the depth accounting logic may shift. |
| `tests/test_mcp/test_impact.py` | `test_impact_none` | If `_subtype_closure` is always called and adds overhead but no matches, the test should still pass. Verify it does not accidentally find subtypes that don't exist. |
| `tests/test_mcp/test_impact.py` | `test_impact_response_has_risk` | Risk computation uses `freshness_ok`. Optimization 5 changes freshness representation; the risk level logic may receive a different type. |
| `tests/test_mcp/test_impact.py` | `test_impact_risk_not_critical_when_fresh` | Same as above — relies on freshness status string. |
| `tests/test_mcp/test_impact.py` | `test_impact_traverses_bridges_to` | If Optimization 2 adds `CrossLanguageName` properties to BridgesTo edges, the MATCH query for BridgesTo edges still works but the test may want to verify the new properties exist. The current test does not assert on edge properties beyond `reached_via`, so it should pass unchanged. |
| `tests/test_derive/test_bridge.py` | `test_bridge_recovery_name_match` | Optimization 2 adds properties to BridgesTo edges. The test's MATCH returns `r.bridge_kind, r.confidence` — still valid, but new properties on the edge won't be asserted. Should be augmented, not broken. |
| `tests/test_derive/test_bridge.py` | `test_bridge_recovery_idempotent` | If `run_bridge_recovery()` now writes CrossLanguageName properties, the idempotent check (zero new edges on second call) should still pass since MERGE is used. |

### 2.3  Unlikely affected

| File | Test function | Why it is safe |
|------|--------------|----------------|
| `tests/test_normalize/test_identity.py` | All tests | Optimization 6 adds a new function `primary_definition_usr()` but does not modify existing functions. No existing tests call it. |
| `tests/test_mcp/test_callers.py` | All tests | Optimization 1 (containerNames cache) is an internal optimization to `GraphLookup`. It does not change the public API. Callers/calls tests do not use `GraphLookup` directly. |
| `tests/test_derive/test_architecture.py` | All tests | None of the optimizations modify `architecture.py`. |
| `tests/test_mcp/test_type_hierarchy.py` | All tests | None of the optimizations modify type_hierarchy.py, though it does use `Inherits` and `ConformsTo` edges which are referenced by optimization 4's subtype closure. |

---

## 3. Edge Cases That Should Be Tested (Cross-Cutting)

### 3.1  Empty graph / no data

Every new function must handle the "no data" case gracefully:

- `owner_of()` with empty graph -> `None`
- `CrossLanguageName` with no matching language pairs -> `None` fields
- `is_up_to_date()` with no locations -> empty result
- `_subtype_closure()` with no subtypes -> empty `set`
- `primary_definition_usr()` with no definitions -> `None` or documented exception

### 3.2  Missing edges

- `owner_of()`: No `Contains` edge and no `Extends` edge -> `None`
- `_subtype_closure()`: No `Inherits` or `Implements` edges -> empty set
- `primary_definition_usr()`: Symbol node exists but has no `file_path` -> sorted deterministically
- Bridge: Symbols with different names, no overlap -> zero BridgesTo edges

### 3.3  Multi-target scenarios

A single USR can exist in multiple targets (e.g., same framework compiled for app and extension). The orchard identity model already scopes by target (`make_symbol_id` produces `target_id:usr`). New functions must either:
- Accept `target_id` parameter (like `primary_definition_usr()`)
- Work through the scoped ID system (like `_subtype_closure()` if it takes a full symbol ID)
- The containerNames cache must be keyed by target-scoped IDs, not raw USRs, to prevent cross-target contamination.

### 3.4  Circular structures

- `_subtype_closure()`: Inheritance cycles (A inherits B, B inherits A) -> must terminate with visited set.
- `owner_of()` with Extends: Extension A extends B, B contains extension A -> must not loop infinitely.
- CrossLanguageName: Roundtrip ObjC <-> Swift <-> ObjC must be idempotent.

### 3.5  Large-scale behavior

- `_subtype_closure()`: UIView has hundreds of subclasses in a typical iOS app; the closure must complete within reasonable time.
- `owner_of()` cache: A request that queries hundreds of distinct USRs must not exhaust memory.
- `IndexOutOfDateChecker` modTime cache: A project with 10,000+ source files must not OOM on the cache dict.
- BridgesTo scan: The current `run_bridge_recovery()` uses `LIMIT 5000` — this limit should be documented and tested with edge cases near the limit.

### 3.6  Cross-language boundaries

- Optimization 2 (CrossLanguageName): Both Swift -> ObjC and ObjC -> Swift must work.
- Optimization 1 (owner_of): Extensions in Swift extending ObjC classes must resolve correctly.
- Optimization 4 (subtypeClosure): ObjC classes that `Inherits` from Swift classes or vice versa must be included in the closure.

### 3.7  Concurrency / cache isolation

- The containerNames cache (opt 1) and modTime cache (opt 3) are described as "per-request" / "single-request lifetime". Tests must verify that different requests (separate `GraphLookup` or `IndexOutOfDateChecker` instances) do not share cached state. This is especially important if handlers are ever invoked concurrently.

---

## 4. Can Each Optimization Be Tested Independently?

### 4.1  Independently testable

| Opt | Module | Independent? | Notes |
|-----|--------|-------------|-------|
| 1 | `query/lookup.py` | **Yes** | `GraphLookup` takes only a connection. Seed symbols with `Contains` and `Extends` edges, call methods, assert results + cache behavior. No handler integration needed. |
| 2 | `derive/bridge.py` | **Partially** | `CrossLanguageName` dataclass can be unit-tested in isolation. The edge storage test requires `run_bridge_recovery()` to write the edges and a Cypher MATCH to read them back. |
| 6 | `normalize/identity.py` | **Yes** | `primary_definition_usr()` takes a connection and USR. Seed symbol nodes, call the function, assert the result. Pure unit test. |

### 4.2  Need integration tests

| Opt | Module | Why |
|-----|--------|-----|
| 3 | `validation/freshness.py` | `IndexOutOfDateChecker.is_up_to_date()` requires actual filesystem state (real files with known mtimes). Either use `tmp_path` fixtures to create real files, or mock `os.stat` / `os.path.exists`. The `is_up_to_date()` pure logic can be unit-tested with mocks, but the integration with actual file I/O is critical. |
| 4 | `handlers/impact.py` | `_subtype_closure()` can be unit-tested independently. BUT its integration into `impact_analysis()`'s depth computation must be tested via integration — seed a graph with subtypes and callers, then verify the full by_depth response. The existing `test_impact_*` tests in `tests/test_mcp/test_impact.py` are the right place for this. |
| 5 | `validation/freshness.py` | Modifies `freshness_for()` which is called by every handler that checks freshness (`impact_analysis`, `view_tree`, `navigation_flow`, etc.). A regression run of the full test suite is required after this change. |

### 4.3  Recommended test file organization

```
tests/
  test_derive/
    test_bridge.py          -> add CrossLanguageName tests here (unit + edge storage)
  test_validation/
    test_freshness.py       -> add IndexCheckLevel + IndexOutOfDateChecker tests here
    test_checked_freshness.py -> NEW FILE for filesystem-integrated freshness tests
  test_normalize/
    test_identity.py        -> add primary_definition_usr tests here
  test_mcp/
    test_impact.py          -> add subtypeClosure unit tests + integration depth tests here
  test_query/
    test_lookup.py          -> NEW FILE for GraphLookup cache + extension tests
```

---

## 5. Summary of Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Optimization 5 (Three-Level Freshness) changes return type of `freshness_for()` | **HIGH** | Must be a backward-compatible change OR all callers must be updated simultaneously. The existing 5 freshness tests serve as a regression guard. |
| Optimization 4 (subtypeClosure) changes impact depth computation | **MEDIUM** | The current impact handler BFS assumes 1 edge = 1 depth step. Subtype closure effectively adds 0-cost breadth expansion. Depth accounting must be clearly specified before implementation. |
| Optimization 3 (CheckedIndex) introduces filesystem dependency into what was a pure-graph system | **MEDIUM** | Tests need `tmp_path` fixtures with real files. Mocking `os.stat` is fragile because Python's stat cache can interfere. Use explicit filesystem setup in tests. |
| Optimization 2 (CrossLanguageName) mapping correctness | **LOW** | ObjC <-> Swift name mapping has many edge cases (protocols, categories, property accessors, init methods). Unit test exhaustively with known-good mappings from Apple documentation. |
| Optimization 1 (containerNames cache) cache invalidation | **LOW** | The cache is per-request, so there is no invalidation problem. The only risk is that the cache key (raw USR vs target-scoped ID) leaks across targets. |
| Optimization 6 (primary_definition_usr) determinism | **LOW** | "Sorted by file_path" is deterministic but fragile — adding a new source file with a name that sorts earlier could silently change which definition is "primary". Document this behavior. |

---

## 6. Test Execution Checklist (Before Merging)

- [ ] All existing tests pass without modification (or failing tests have been triaged and updated).
- [ ] Each of the 6 optimizations has at least the minimum test cases listed in Section 1.
- [ ] Cross-cutting edge cases from Section 3 are covered for each function.
- [ ] The full test suite (`pytest tests/`) passes with Ladybug in-memory backend.
- [ ] `gitnexus_detect_changes()` confirms only expected files are modified.
- [ ] Impact analysis (via GitNexus) on modified symbols shows LOW or MEDIUM risk; any HIGH/CRITICAL risks have been reviewed and acknowledged.
- [ ] Performance-sensitive code paths (subtypeClosure on deep hierarchies, modTime cache on large file sets) have a performance sanity test or benchmark comment.
