# Implementation Plan — orchard 查询层优化

**Design spec**: `docs/superpowers/specs/2026-06-26-orchard-query-optimization-from-root-cause-analysis.md`
**Execution mode**: subagent-driven
**Date**: 2026-06-26

## Task Breakdown

### Task 1: GraphLookup.methods_of() + auto-expand in find_callers (P0)

**Files**: `src/orchard/query/lookup.py`, `src/orchard/handlers/callers.py`

1.1 Add `GraphLookup.methods_of(usr, target_id)` to `query/lookup.py`:
  - Cypher: `MATCH (parent:Symbol {id: $id})-[:Contains]->(child:Symbol) WHERE child.kind = 'method' RETURN ...`
  - Return `list[dict]` with usr, name, kind, language

1.2 Modify `find_callers()` in `handlers/callers.py`:
  - After resolving symbol, check `kind in ("class", "struct", "enum", "protocol")`
  - If match: call `methods_of()`, iterate `callers_of()` for each method
  - Annotate each caller with `via_method = method["name"]`
  - Dedup by caller USR
  - Also update MCP server `orchard_find_callers` handler (server.py `_do_handler()`)

1.3 Update `find_callees` in `handlers/callees.py`:
  - Same auto-expand for class-level USR
  - Different dedup: **group-by-callee** (not dedup-by-USR)
  - Output shape: `{callee: {usr, name, ...}, calling_methods: ["method1", "method2"]}`
  - Limit: expand top-50 methods (by name ordering) for classes with many methods
  - Update MCP server `orchard_find_callees` handler

**Verification**: `orchard find_callers --usr "c:objc(cs)ExampleHelper" --target MyApp` → 8+ callers grouped by method

---

### Task 2: search --class flag (P0)

**Files**: `src/orchard/cli.py`, `src/orchard/server.py`, `src/orchard/handlers/search.py` (or new)

2.1 Add `--class` / `-c` flag to `cmd_search()` in `cli.py`:
  - Parse `--class <ClassName>` argument
  - First: search for class by name to get its USR
  - Then: call `GraphLookup.methods_of()` to get all methods
  - Return: `{owner: {name, usr}, methods: [{name, usr, kind, language}]}`

2.2 Add `orchard_search` MCP tool parameter `class_name` in `server.py`

2.3 Pipe mode support: handle `{"cmd": "search", "args": {"class": "ExampleHelper"}}`

**Verification**: `orchard search --class ExampleHelper --target MyApp` → 15 methods listed

---

### Task 3: C++ operator noise filter (P1)

**Files**: `src/orchard/query/noise_filter.py` (new), `src/orchard/cli.py`, `src/orchard/server.py`

3.1 Create `src/orchard/query/noise_filter.py`:
  - `CPP_NOISE_PREFIXES`: 25 operator patterns (startswith match)
  - `CPP_NOISE_EXACT`: 10 exact-match helpers (GetMinLogLevel, NSLog, etc.)
  - `is_noise(name: str) -> bool`
  - `filter_noise(items: list[dict], name_key="name") -> tuple[list[dict], int]`

3.2 Add `--include-noise` flag to `cmd_find_callees()`:
  - Default: filter noise, annotate `metadata.noise_removed`
  - With flag: return all unfiltered

3.3 Apply noise filter to `find_callers` callers too

3.4 MCP server: add `include_noise` boolean to `orchard_find_callees` tool schema

**Verification**: `orchard find_callees --usr "c:objc(cs)ExampleViewController(im)tryAutoLogin" --target MyApp` → ~9 callees (was 89)

---

### Task 4: Framework boundary annotation (P1)

**Files**: `src/orchard/query/lookup.py`

4.1 Add `is_framework_callback(name: str) -> bool` to `lookup.py`:
  - Regex patterns for UIApplicationDelegate, UISceneDelegate, UIViewController lifecycle
  - Anchored to known selector prefixes: `^(application|scene|viewDid|viewWill|tableView|collectionView)`

4.2 Modify `callers_of()` return: when `data == []` and `is_framework_callback(name)`:
  - Add `open_gaps: ["No callers found — likely called by system framework (UIKit/AppKit). Use reverse tracing via find_callees."]`

**Verification**: `orchard find_callers --usr "c:objc(cs)ExampleAppDelegate(im)application:didFinishLaunchingWithOptions:" --target MyApp` → `open_gaps` annotation present

---

### Task 5: orchard audit command (P1)

**Files**: `src/orchard/cli.py`, `src/orchard/query/lookup.py`

5.1 Add `GraphLookup.module_stats()` to `lookup.py`:
  - `MATCH (s:Symbol) RETURN s.module, s.kind, count(*) AS c ORDER BY c DESC`

5.2 Add `cmd_audit()` to `cli.py`:
  - Parse `--project-dir`, `--format {table|json}`
  - If `--project-dir` given: detect Xcode workspace, list targets
  - Query graph for per-module symbol counts by kind
  - Report: targets with 0 symbols (potential gaps), unexpected gaps (< 100 symbols for a framework target)
  - Table/JSON output

5.3 Register `audit` in `COMMANDS` dict

**Verification**: `orchard audit --project-dir . --format table` → module coverage report

---

### Task 6: Multi-target IndexStore ingest (P1)

**Files**: `src/orchard/cli.py`, `src/orchard/ingest/` (investigate current structure)

6.1 Add `--all-targets` flag to `orchard ingest`:
  - When set with `--project-dir`: discover all Xcode schemes from workspace
  - For each scheme: find/build IndexStore, run ingest, merge into same graph.db
  - Skip already-ingested targets (dedup by build_id or target_id)

6.2 Alternative: `orchard ingest --target MyLogin --index-store <path>` for manual multi-target:
  - Allow multiple `--target` values: `orchard ingest --target MyApp,MyLogin,MyServiceManager`

6.3 Update `ingest-state.json` to record multi-target state

**Verification**: After re-ingest with MyLogin target, `orchard search --name "ExampleLoginHelper"` → 1+ results

---

## Execution Order

```
Task 1 (auto-expand) ──┬──> Task 2 (search --class) ──> Task 3 (noise filter) ──> Task 4 (framework) ──> Task 5 (audit) ──> Task 6 (multi-ingest)
                        │
                        └── Tasks 1+2 are independent but both need GraphLookup.methods_of()
                        Tasks 3+4 are independent P1 enhancements
                        Tasks 5+6 are independent P1 additions
```

**Parallelization**: Tasks 1+2 can be done together (both touch lookup.py + cli.py). Tasks 3, 4, 5, and 6 are independent of each other once 1+2 are done.

## Risk Mitigation

- **Breaking changes**: auto-expand changes `find_callers` class-USR output from `[]` to `[{caller..., via_method}]`. Document in changelog. MCP server response schema adds optional `via_method` field.
- **Noise filter default-on**: consumers of `find_callees` raw output may see reduced lists. `--include-noise` restores full output.
- **Performance**: class with 100+ methods → 100 individual Cypher queries. Task 1.3 limits to top-50 methods. Future optimization: single UNWIND batch query.

## Testing Strategy

- Unit tests: `tests/test_lookup.py` for `methods_of()`, `is_noise()`, `is_framework_callback()`
- Integration: test CLI commands end-to-end with test graph fixtures
- Regression: existing `find_callers`/`find_callees` tests must pass unchanged (method-level USR queries unaffected)
- Manual verification: use example project graph for real-world testing of all 6 solutions
