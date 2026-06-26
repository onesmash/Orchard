# Testing Perspective Review — orchard Query Optimization

**Date**: 2026-06-26
**Perspective**: testing
**Based on**: Root Cause Analysis (orchard-vs-gitnexus-ios-client)  
**Reviewer focus**: Testability, acceptance criteria, regression risk, integration coverage

---

## 1. Acceptance Criteria & Verification Commands

### ISSUE — Verification depends on a real project that is not part of this repo

All five proposed verification commands reference the Zoom ios-client Xcode project (`/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client`), which is not checked into the orchard2 repository. These commands cannot be run in CI and cannot be automated with the existing `pytest`-based test suite. The expected result counts (e.g. "~8 callers", "~9 business-logic callees", "89 callees total") are tied to a specific snapshot of that external project's IndexStore data.

**Per-solution breakdown**:

| Solution | Verification given | Reproducible without external project? |
|----------|-------------------|----------------------------------------|
| 1 (auto-expand) | `orchard find_callers --usr "c:objc(cs)ZPJoinConfHelper"` | No |
| 2 (search --class) | `orchard search --class ZPJoinConfHelper --target Zoom` | No |
| 3 (noise filter) | `orchard find_callees --usr "…tryAutoLoginWhenAppLaunched"` | No |
| 4 (framework boundary) | No explicit verification command given | N/A |
| 5 (orchard audit) | `orchard audit --project-dir .` inside ios-client | No |

### SUGGESTION — Add mock-based acceptance criteria

The existing test suite (e.g. `tests/test_mcp/test_callers.py`) already demonstrates the pattern: seed a Ladybug in-memory DB with known Symbol nodes and Calls edges, call the handler directly, assert on response shapes and values. Each solution should have a corresponding mock-based unit-level acceptance criterion:

- **Solution 1**: Seed a class symbol with child method symbols (via Contains edges), seed callers on those methods, call `find_callers` with the class USR, assert aggregated callers appear with `via_method` annotations.
- **Solution 2**: Seed a class + methods, call the new search path, assert methods appear under the class.
- **Solution 3**: Seed callees containing known noise symbols and known valid symbols, call with default and `--include-noise`, verify counts and contents.
- **Solution 4**: Seed a delegate-named symbol with zero callers, assert `open_gaps` contains framework boundary annotation.
- **Solution 5**: Seed DB with module stats, mock `xcodebuild -list` output, assert coverage report generated.

### ISSUE — Solution 4 has no verification command at all

The spec (lines 186-228) describes the implementation but provides no verification command. This is the only solution missing an explicit acceptance test.

---

## 2. Test Cases Needed for Auto-Expand Feature

### ISSUE — Incomplete type coverage in spec

The spec (lines 44-48) mentions checking for `kind in ("class", "struct", "enum")` but does not list `protocol`. Protocols can have methods (via protocol requirements) and should also trigger auto-expand. Additionally, the following cases are not addressed:

**Required test cases**:

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| T1 | Class with instance methods, callers on those methods | Aggregated callers, `via_method` populated |
| T2 | Class with class methods (cm), callers on them | Class method callers included, `via_method` populated |
| T3 | Class with zero methods (no Contains edges) | Empty data with appropriate `open_gaps` ("class has no methods" vs generic "no callers found") |
| T4 | Struct with methods | Same auto-expand, struct methods aggregated |
| T5 | Enum with methods | Same auto-expand, enum methods aggregated |
| T6 | Protocol with requirements | Same auto-expand, protocol requirements aggregated |
| T7 | Extension (not class/struct/enum/protocol) | Should NOT auto-expand; existing single-symbol behavior |
| T8 | Class with methods, but zero callers across all methods | Empty data, meaningful `open_gaps` distinguishing "methods exist but no callers" from "no methods" |
| T9 | Deduplication: two different methods share a caller | Caller appears once, not N times |
| T10 | Single method with multiple callers, via_method annotation preserved | All entries carry correct `via_method` |

### SUGGESTION — Add protocol to the auto-expand kind list

The spec omits `protocol` from the kind check. Protocols that contain method requirements should also auto-expand.

### ISSUE — find_callees auto-expand has no distinct edge cases defined

The spec (line 68) says "同样修改 find_callees" with no further detail. The callees case has a subtle difference: multiple methods of a class may share callees (e.g. both call `NSLog`). Deduplication by callee USR is needed but not called out separately from the callers dedup logic.

---

## 3. Noise Filter Testing Strategy

### ISSUE — No false-positive risk analysis in the spec

The spec identifies a general trade-off in a single sentence (line 184): "噪音过滤可能导致极少数情况下的误判". No specific classes of false positives are enumerated. Three test categories are needed:

**Test category A: Default filtering removes known noise**

Seed callees including entries like `operator<<`, `operator&`, `operator->`, `operator()`, `operator[]`, `operator new`, `operator delete`, `GetMinLogLevel`, `LogMessage`, `LogMessageVoidify`, `defaultCenter`, `postNotificationName:object:`, `stream`, `str`, `c_str`, `StringPiece`. Verify they are removed.

**Test category B: `--include-noise` restores all**

Same seed data. Verify the flag produces `noise_removed: 0` and `total_raw` equals the full callee list length.

**Test category C: No false positives on legitimate operators**

This is the critical one. Seed legitimate (business-logic) callees whose names contain operator-like substrings:

| Symbol name | Should be filtered? | Rationale |
|-------------|---------------------|-----------|
| `postNotificationName:object:` | Yes (per spec) | NSNotificationCenter boilerplate |
| `postNotification:` (custom API) | Risk of false positive | Substring match `postNotificationName` → contains `postNotification`... actually no, the pattern is `postNotificationName:object:` which would not substring-match `postNotification:`. But the spec uses `in` (Python substring match), not exact match. |
| `streamData:` (custom) | Risk | Contains `stream` |
| `operator<<` | Yes |
| `customOperator` (business method) | Risk | Contains `operator` as substring |

The `is_noise()` function uses `pattern in name` which is a substring match. This means `stream` will match `streamData:`, `customStream`, `OutputStream:`, etc. The spec's noise patterns need to be verified as **prefix/suffix bounded** or the false-positive test cases formalized.

### SUGGESTION — Use stricter matching

`operator<<`-style patterns should use `name.startswith("operator")` instead of `"operator" in name`, which would preserve methods like `configureOperator:`. For `stream`, `str`, `c_str` — these are too generic as substring matches and should be bounded (e.g. `name == "str"` or `name == "c_str"`).

### ISSUE — Noise filtering applied to find_callers is under-specified

The spec (line 160) says "callers 中的噪音调用者也过滤". It is unclear whether this means:
- (a) Filter noise from the **caller symbols** themselves (their names), or
- (b) Filter callers that **call** a noise symbol (i.e., filtering by the callee's name in the caller set).

Case (a) could mask legitimate callers whose names happen to match noise patterns. The spec needs to clarify.

---

## 4. Regression Risks

### ISSUE — Response shape changes break JSON consumers

**Existing response shape** (from `handlers/callers.py` and `handlers/callees.py`):

```python
BaseToolResponse(
    data=[{usr, name, module, kind, language, file_path, line, col, reason, owner, depth}, ...],
    freshness=...,
    build_id=...,
    evidence_sources=[...],
    open_gaps=[...],
)
```

**Solution 1** adds `via_method` to each data item. This is backward-compatible (additive field) but **consumers that iterate over `data` items with fixed-key expectations may need updates**.

**Solution 3** changes default behavior: `find_callees` returns a **subset** of what it returned before. Any automation or script that relied on the full callee list (including noise) will silently break. The `--include-noise` flag is the safety net, but it requires the consumer to opt in.

### ISSUE — MCP server tool schemas not in sync with spec changes

The MCP server (`server.py`) defines its tool schemas independently of the CLI. The spec does not mention updates to:

- `orchard_find_callees` inputSchema: needs `include_noise` boolean property
- `orchard_search` inputSchema: needs `class` string property (for `--class`)
- `orchard_find_callers` inputSchema: may need documentation update for class USR behavior
- Potential new tool: `orchard_audit` — not mentioned in the server.py spec at all

If the MCP tool schemas are not updated, the MCP server will silently ignore new flags from clients.

### ISSUE — Pipe mode not addressed

The pipe mode (`_execute_pipe_cmd` in `cli.py`, lines 352-390) dispatches `find_callers`, `find_callees`, and `search` independently from `cmd_find_callers` and `cmd_find_callees`. Any new flags (`--include-noise`, `--class`) added to the CLI commands must also be wired through pipe mode. The spec does not mention pipe mode at all.

### Regression risk summary table

| Change | CLI cmd_* | MCP server | Pipe mode | Handler | Response shape |
|--------|-----------|------------|-----------|---------|----------------|
| S1: auto-expand callers | `cmd_find_callers` needs update | Uses same handler (auto) | Uses same handler (auto) | `callers.py` needs update | Additive (`via_method`) |
| S1: auto-expand callees | `cmd_find_callees` needs update | Uses same handler (auto) | Uses same handler (auto) | `callees.py` needs update | Additive (`via_method`) |
| S2: search --class | `cmd_search` needs update | `_do_search` needs update | `_pipe_search` needs update | N/A (inline search) | New top-level shape |
| S3: noise filter | `cmd_find_callees` + new flag | `TOOLS` schema + `_do_handler` flag passthrough | `_execute_pipe_cmd` flag passthrough | `callees.py` + `callers.py` | BREAKING default (subset), metadata change |
| S4: framework boundary | Auto (via handler) | Auto (via handler) | Auto (via handler) | `callers.py` | Additive (`open_gaps` text change) |
| S5: orchard audit | New `cmd_audit` | New tool schema + handler | New pipe dispatch | New handler file | New command |

### ISSUE — open_gaps text change could break string comparisons

Current `find_callers` produces `open_gaps=["no callers found"]` when data is empty. Solution 4 changes this to a multi-line descriptive string. If any consumer does `if "no callers found" in resp.open_gaps`, it would break. The spec should note that the existing open_gaps text should be preserved alongside the framework annotation, not replaced.

---

## 5. orchard audit Testability Without a Real Xcode Project

### ISSUE — Depends on xcodebuild -list for target enumeration

The spec (line 249) says: "Get Xcode workspace targets (via xcodebuild -list 或 .xcworkspace)". This requires an actual `.xcworkspace` or `.xcodeproj` on disk.

**Mock data needed for unit testing**:

1. **Mock graph.db state**: A seeded Ladybug DB with known module-level symbol counts (e.g. Module A: 1000 symbols, Module B: 500 symbols, Module C: 0 symbols).
2. **Mock Xcode workspace/project info**: Either:
   - A fake `.xcworkspace` directory structure, or
   - A mocked `subprocess.run(["xcodebuild", "-list", ...])` that returns a known target list.
3. **Expected coverage report**: A known mapping of targets to symbol counts, with some targets showing zero coverage.

### SUGGESTION — Add a --targets-file option for testing

To make `orchard audit` testable without xcodebuild, add a `--targets-file <path>` option that reads a simple JSON list of expected target names instead of running xcodebuild:

```json
["Zoom", "iOSServiceManager", "iOSLogin", "ZoomShared"]
```

This allows the audit command to be tested with a seeded DB and a fixed targets file, comparing counts and flagging modules as uncovered when their symbol counts are anomalous.

### ISSUE — "Anomalous" threshold undefined

The spec references "anomalously low symbol count" (line 277) but provides no threshold. Without a defined heuristic (e.g. "fewer than 1% of the average module symbol count", "absolute < 100", "z-score < -2"), the test cannot validate that the audit correctly flags a gap.

---

## 6. Integration Test Concerns

### ISSUE — Three code paths share no common integration test

The orchard system has three surface areas for each query:

| Surface | Entry point | Handler call |
|---------|------------|--------------|
| CLI | `cmd_find_callers()` → builds `CallerRequest` → `find_callers()` | Direct function call |
| MCP server | `orchard_find_callers` tool → `_do_handler("callers", ...)` → `find_callers()` | Via importlib + `_get_conn()` |
| Pipe mode | `_execute_pipe_cmd()` → builds `CallerRequest` → `find_callers()` | Via function call |

While the handler function is shared, the request construction, argument parsing, and response serialization differ in each path. Current tests only exercise the handler path (e.g. `tests/test_mcp/test_callers.py` calls `find_callers()` directly with a manually constructed `CallerRequest`).

**Integration-test gaps per new feature**:

| Feature | CLI path tested? | MCP path tested? | Pipe path tested? |
|---------|-----------------|-----------------|------------------|
| auto-expand | No | No | No |
| search --class | No | No | No |
| noise filter | No | No | No |
| framework boundary | No | No | No |
| orchard audit | N/A | N/A | N/A |

### SUGGESTION — Add at least one integration test per surface

For each new feature, add one test that exercises the full CLI path (via `subprocess.run(["orchard", "cmd", ...])` against a temp DB), one that exercise the MCP server handler dispatch, and one that exercises the pipe dispatch. The existing `test_acceptance.py` demonstrates a partial CLI integration pattern (calling `cmd_find_callers` with args but relying on `--db` auto-discovery to find a seeded DB).

### ISSUE — Auto-discovered DB path complicates CLI testing

The `_conn()` function in `cli.py` auto-discovers `.orchard/graph.db` by walking up from cwd. CLI-level tests need to either:
- Set `ORCHARD_DB_PATH` env var, or
- Create a temp `.orchard/graph.db` at the right location, or
- Pass `--db` explicitly.

The acceptance tests in `test_acceptance.py` use `cmd_find_callers` but do not appear to test with `--db` — they may be relying on side effects. This should be formalized.

---

## Summary of Findings

### PASS

None — all areas have at least one finding.

### ISSUES (blocking or high-risk)

1. **All 5 solutions lack mock-data-based unit-level acceptance criteria** — cannot run in CI without external Xcode project.
2. **Solution 4 has no verification command at all** — no way to confirm it works.
3. **Noise filter substring matching risks false positives** (`stream`, `str`, `operator` as substrings).
4. **Default noise filtering is a breaking behavior change** — automated consumers relying on full callee lists will silently break.
5. **MCP server tool schemas not updated** in the spec for new flags (`--include-noise`, `--class`).
6. **Pipe mode not addressed** for any new feature.
7. **Auto-expand does not include `protocol`** in the kind check — protocols with requirements would not auto-expand.
8. **`orchard audit` has no defined anomaly threshold** — test cannot validate correctness of gap detection.
9. **`open_gaps` text change** in Solution 4 could break string-comparison consumers.

### SUGGESTIONS (improvements)

1. Add `--targets-file` option to `orchard audit` for mock-based testing.
2. Use `name.startswith("operator")` instead of `"operator" in name` for noise matching.
3. Add explicit deduplication test cases for auto-expand in the callee direction.
4. Add an integration test per CLI surface (CLI/MCP/pipe) for each new feature.
5. Formalize `--db` flag usage in acceptance tests to avoid auto-discovery side effects.
6. Consolidate the duplicate search logic between `cmd_search`, `_pipe_search`, and `_do_search` — these are three implementations of the same Cypher query.
7. Consider adding a `--noise-patterns-file` option to make the noise filter configurable per project.
