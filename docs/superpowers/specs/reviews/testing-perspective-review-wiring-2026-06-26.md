# Testing-Perspective Review: Orchard Wiring Completion

**Spec reviewed:** `docs/superpowers/specs/2026-06-26-orchard-wiring-completion.md`
**Reviewer role:** Test engineering / QA
**Date:** 2026-06-26
**Verdict:** Spec is implementable but **under-specified for testability**. Two of the six tasks (4 and 5) have blocking schema gaps that will cause silent failures if not caught. The existing unit-level coverage of the *implementations* is strong, but coverage of the *wiring* (the act of calling them) is zero — and that gap is exactly what this spec is supposed to close. Task 6 ("补全测试") is a list of nouns, not test designs.

---

## 1. Test cases needed per wiring task

### Task 1 — freshness filter wired into `impact_analysis` (`handlers/impact.py`)

The function `IndexOutOfDateChecker.is_up_to_date(SymbolLocation(path, timestamp))` is tested in isolation (`tests/test_validation/test_freshness_checker.py`). Wiring = calling it per dependent inside the BFS loop.

| # | Test case | Why it matters |
|---|-----------|----------------|
| 1.1 | **Stale dependent is filtered out of `by_depth`** — seed a caller whose `file_path` mtime > indexed timestamp, assert it is absent from `d1`. | Core wiring behavior. |
| 1.2 | **Fresh dependent is retained** — same fixture, mtime ≤ timestamp, assert present. | Positive control so 1.1 isn't a false pass. |
| 1.3 | **`reached_via`/depth grouping survives filtering** — mixed fresh+stale callers at d1 and d2; verify depth assignment is unchanged for survivors. | Filtering must not corrupt the depth dict. |
| 1.4 | **`risk_level` reflects post-filter d1 count** — spec §1 says freshness affects risk; the existing `_risk_level` already takes `freshness_ok`, but now the *count* also changes. Assert risk recomputed on the filtered set, not the raw BFS set. | Easy regression: compute risk before filtering. |
| 1.5 | **Missing `file_path` / empty string** — many seeded Symbols in tests use `file_path=""`. Decide and assert the behavior (treat as fresh? stale? skip check?). | Spec §1 says "use file_path mtime as approximation" — empty path is the default in half the fixtures. **This is an under-specified edge that will surface in every existing test.** |
| 1.6 | **All dependents filtered → `open_gaps` surfaces it** — when every dependent is stale, the result should not silently look like "no dependents". Assert an open_gap distinguishes "no edges" from "all edges stale". | Prevents a misleading empty result. |
| 1.7 | **Performance: checker instantiated once, not per-row** — the class caches mtimes; verify the cache is reused across the BFS (the existing `test_modtime_cache_reuse` only tests the checker alone, not the wiring). | A naive wiring re-creating the checker per dependent defeats the cache. |

### Task 2 — `_subtype_closure` wired into `impact_analysis`

`_subtype_closure` has its own test file (`tests/test_handlers/test_impact_subtype.py`) — 4 cases. None test that `impact_analysis` actually calls it.

| # | Test case | Why |
|---|-----------|-----|
| 2.1 | **Subtypes appear in `d1` with `reached_via="subtype_closure"`** — seed a protocol P with conformers A, B; query P; assert A and B in d1 with the new tag. | The spec's explicit acceptance criterion. |
| 2.2 | **Subtypes are merged with, not duplicating, BFS results** — if A is reachable both via a Calls edge and via subtype closure, assert one entry (decide which `reached_via` wins). | Dedup semantics are unspecified. |
| 2.3 | **`risk_level` incorporates subtype count** — spec §2 says "更新 risk_level 计算纳入 subtype 数量". The current `_risk_level(d1_count, has_bridge, freshness_ok)` has **no subtype parameter**. Test must pin the new signature/behavior. | Signature change = contract change. |
| 2.4 | **Depth semantics** — are subtypes always d1, or do they inherit the depth of the symbol whose closure was expanded? Assert the documented choice. | Unspecified in the spec. |
| 2.5 | **`max_depth` on the closure is respected** — the helper has `max_depth=20`; verify the wiring doesn't hardcode something different. | Parameter pass-through. |
| 2.6 | **No infinite loop on diamond conforms** — A conforms P, B conforms P, C inherits both A and B. | The helper has a visited guard; the wiring must not bypass it by calling repeatedly. |

### Task 3 — `pipeline/runner.py` switched to kahn + registry

This is the **highest-risk** task (spec flags it last). The current runner is a ~190-line hardcoded sequence; `test_runner.py` asserts on phase *names* appearing in results, not ordering or dependency-driven execution.

| # | Test case | Why |
|---|-----------|-----|
| 3.1 | **Phase order respects declared deps** — declare `call_graph_derivation` depends on `identity_normalization`; assert the former never runs first. Use a fake clock or phase that records execution index. | This is the whole point of kahn; untested today. |
| 3.2 | **Backward-compat entry point preserved** — `run_ingest_pipeline()` signature and returned `list[PhaseResult]` shape unchanged; all 4 existing `test_runner.py` tests still pass unmodified. | Spec promises "保留兼容入口". The 4 existing tests are the regression suite. |
| 3.3 | **Optional phases (MRO/community/process) register with `enabledWhen=True` default** and appear in results. | Spec §3. |
| 3.4 | **`enabledWhen=False` phase is skipped** and produces no `PhaseResult`. | Mirrors `test_phase.py::test_enabled_when_false_excludes`. |
| 3.5 | **New phases produce non-zero output on a realistic fixture** — community/process currently return 0 because nothing calls them. Seed a Calls graph; assert `communities_found > 0` and `processes_found > 0`. | Spec §6 explicitly calls this out ("社区检测测试（之前为 0）"). |
| 3.6 | **Cycle in phase deps raises `ValueError`** from kahn (already tested in `test_kahn.py::test_kahn_cycle_detection`) — but add a runner-level test that a bad registry surfaces the error rather than silently dropping phases. | Unit test exists; integration test does not. |
| 3.7 | **`embedding_projection` degrades gracefully when Ollama down** — `test_pipeline_embedding_projection_handles_ollama_down` exists and MUST still pass after refactor. | The refactor must not change the warning/stats contract. |
| 3.8 | **Per-phase timing (`stats["elapsed_s"]`) still recorded** for every phase. | Current runner does this; a registry-driven loop could easily drop it. |
| 3.9 | **Concurrency preserved** — `_run_indexstore` and `_run_symbolgraph` run via `asyncio.gather`. A naive registry refactor could serialize them. Assert elapsed time or use a sentinel that detects serialization. | Real perf regression risk; the spec doesn't mention it. |

### Task 4 — `CrossLanguageName` populated in `run_bridge_recovery`

`tests/test_derive/test_bridge_cross_language.py` tests the **dataclass only** (5 cases). It does not test that `run_bridge_recovery` produces or persists these names. **Blocking issue: the `BridgesTo` REL TABLE schema (`schema.py:118`) has no `clang_name`/`swift_name`/`definition_language` columns.** Writing them will either error or silently no-op.

| # | Test case | Why |
|---|-----------|-----|
| 4.1 | **Schema migration test** — after wiring, `BridgesTo` rows carry `clang_name`, `swift_name`, `definition_language`. This requires a DDL change first; test the migration on an existing DB (init old schema, run new code, assert columns exist). | **Must precede any wiring test.** |
| 4.2 | **ObjC instance method → `clang_name="-[Cls method:]"`, `definition_language="objc"`** — seed an objc Symbol, run bridge recovery, read the edge back, assert both columns. | Mirrors `test_bridge_cross_language.py::test_objc_instance_method` but at the DB level. |
| 4.3 | **Swift definition → `swift_name="Cls.method(_:)"`** | Same. |
| 4.4 | **Handler surfaces the names** — `handlers/bridges.py::get_cross_language_bridges` currently returns `bridge_kind/confidence/provenance/target_*`. After wiring it must also return `clang_name`/`swift_name`. Update `tests/test_mcp/test_bridges.py`. | Spec §4 explicitly says "handlers/bridges.py 读取并返回". |
| 4.5 | **Idempotency with new columns** — `test_bridge_recovery_idempotent` must still pass; re-running must not duplicate edges or overwrite names with nulls. | The MERGE must set the new properties. |
| 4.6 | **Round-trip parity with sourcekit-lsp** — generate names for a known Zoom-style symbol (e.g. `-[ZMHomeViewController viewDidLoad]`) and assert the swift_name form matches what sourcekit-lsp would emit. | This is the reference implementation's contract. |

### Task 5 — `hybrid_search` wired into `semantic_search`

`tests/test_search/test_rrf.py` tests `rrf_fuse` (4 cases) on plain dicts. `tests/test_mcp/test_semantic_search.py` (15+ cases) tests the **current** `semantic_search` which hand-rolls vector + FTS merge. Wiring in `hybrid_search` will change that merge path.

| # | Test case | Why |
|---|-----------|-----|
| 5.1 | **All 15 existing `TestSemanticSearch*` tests pass unchanged** — these are the regression contract. Run them; they encode FTS-fallback, vector, shape, freshness, and top_k. | Highest existing-coverage area; must not regress. |
| 5.2 | **RRF fusion actually engaged when both paths return results** — seed BM25-only and vector-only matches for different symbols; assert the symbol appearing in *both* outranks either alone. | This is the one behavior the current hand-rolled merge does NOT guarantee. |
| 5.3 | **LadybugDB FTS unavailability → graceful degradation to `CONTAINS`** — spec §5 explicitly says "不可用则降级". The `hybrid_search` impl already wraps BM25 in try/except returning `[]`; add a test forcing the exception and asserting the FTS path still returns results. | Spec calls this out; `hybrid_search` has a bare except that is currently untested. |
| 5.4 | **Score semantics change** — current FTS fallback assigns `score=0.5`; RRF assigns `1/(k+rank+1)`. `test_response_fields` only checks `score` is a float. Add a test that pins or documents the new score range. | Scores will silently shift; downstream consumers may rank-break. |
| 5.5 | **`top_k` applied after fusion, not per-list** — the current `semantic_search` slices after merge; `hybrid_search` takes a `limit` defaulting to 20 that is applied inside BM25/vector passes. Assert `req.top_k` still bounds the final result count. | `test_fts_respects_top_k` may fail if limit semantics differ. |
| 5.6 | **Empty query / whitespace query** — no test currently covers `query=""`. RRF on empty BM25 is fine, but `MATCH ... WHERE s.name CONTAINS ""` returns everything. Assert bounded result. | New edge introduced by wider net. |

### Task 6 — "补全测试"

This task is circular (it's the task of writing the tasks above). Treat it as a tracking bucket, not a testable unit. Map its bullet points to the cases in 2.1/2.2 (subtype), 1.1/1.2 (freshness), 3.5 (community), 3.5 (process), 4.2–4.4 (CrossLanguageName). See section 3 for the community/process edge cases that have **zero** tests today.

---

## 2. Existing tests at risk

Ranked by likelihood of breaking:

| Test file | Risk | Reason |
|-----------|------|--------|
| `tests/test_mcp/test_impact.py` (5 cases) | **HIGH** | Task 2 changes `_risk_level` signature and adds subtype rows to `d1`. `test_impact_response_has_risk` only checks the value is in a set, so it survives — but `test_impact_returns_callers_by_depth` may pick up extra d1 entries if any fixture symbol happens to form a conforms closure. The `bridges_graph` fixture could add subtype rows unexpectedly. |
| `tests/test_mcp/test_semantic_search.py` (15 cases) | **HIGH** | Task 5 swaps the merge algorithm. `test_fts_respects_top_k` (top_k=1 expects exactly 1) and `test_vector_search_with_mocked_embedder` (expects cosine≈1.0 to win) both depend on the current scoring. RRF will reorder. |
| `tests/test_pipeline/test_runner.py` (5 cases) | **HIGH** | Task 3 rewrites the runner. All 5 assert phase *names*; if registry-driven phases are renamed or if optional phases are appended, `test_run_ingest_pipeline_returns_results` and `test_pipeline_includes_bridge_recovery_phase` may pass trivially while hiding real breakage. `test_pipeline_embedding_projection_handles_ollama_down` is the canary. |
| `tests/test_derive/test_bridge_cross_language.py` (5 cases) | **MEDIUM** | The dataclass itself is stable, but if task 4 adds validation/coercion in `__post_init__` (e.g. requiring both names present), `test_optional_names` could break. |
| `tests/test_derive/test_bridge.py` (2 cases) | **MEDIUM** | `test_bridge_recovery_name_match` asserts `r.bridge_kind` and `r.confidence` on the edge — task 4 adding new columns via a different MERGE path could change the edge set or its properties. Re-run mandatory. |
| `tests/test_mcp/test_bridges.py` | **MEDIUM** | Handler response shape changes (new `clang_name`/`swift_name` fields). Any test doing strict equality on the dict will fail; field-subset assertions survive. |
| `tests/test_handlers/test_impact_subtype.py` (4 cases) | **LOW** | Tests the helper directly; unaffected unless the helper's signature changes (it shouldn't). |
| `tests/test_acceptance*.py` (4 files) | **LOW–MEDIUM** | `test_acceptance_m5.py` exercises `run_swiftui_derivation` end-to-end; if task 3 changes how derive phases are invoked, this is the integration canary. |
| `tests/test_pipeline/test_kahn.py`, `test_phase.py` | **NONE** | Pure unit tests of the sorting/registry primitives. |

**Action:** Before any code lands, run the full suite to get a green baseline. After tasks 2, 3, and 5, re-run in full — these are the breakage hotspots.

---

## 3. Edge cases the spec does not address

1. **Freshness approximation on empty/None `file_path`.** Spec §1 says "use file_path mtime as approximation". Half the test fixtures seed `file_path=""`. `os.path.getmtime("")` raises. The wiring MUST decide: skip the check, treat as stale, or treat as fresh. This decision propagates into `risk_level`. **Highest-value unspecified behavior.**
2. **Symbol node has no `timestamp` column** (schema.py:40–55). The spec admits this ("当前 Symbol 节点无 timestamp 字段") and proposes mtime approximation, but doesn't say *what timestamp to compare against*. Options: (a) the build snapshot `created_at`, (b) a per-symbol indexed-at time that doesn't exist yet, (c) always use "now". Each produces different filtering behavior. A test must pin this.
3. **Subtype closure + bridge edges interaction.** If a subtype is reached via `ConformsTo` and also via `BridgesTo`, which `reached_via` wins? Affects `has_bridge` and thus risk.
4. **RRF `k=60` constant is hardcoded** (`hybrid_search.py:14`). No test varies it; downstream score magnitudes are unspecified.
5. **LadybugDB FTS availability detection** — spec §5 says "验证 LadybugDB FTS 是否可用". `hybrid_search` uses a bare `except Exception` → `bm25=[]`, which masks the difference between "FTS not supported" and "query syntax error". A test should inject each failure mode separately.
6. **Community/process detection on empty or single-node graphs** — both modules early-return zeros, but the wiring through the registry means they now run on every pipeline. Assert they don't crash on an empty DB (the `conn` could have no Symbols yet if an earlier phase failed silently).
7. **`enabledWhen` predicates with side effects / ordering** — `get_enabled_phases` evaluates predicates in dict-insertion order. If a predicate reads module-level state mutated by a previous phase, order matters. Unspecified.
8. **`CrossLanguageName` for C/C++ symbols** — `definition_language` accepts "c"/"cpp" but `run_bridge_recovery` only scans `['swift','objc']`. Bridging a C function to its Swift wrapper won't happen. Test or document the gap.
9. **Concurrent `run_ingest_pipeline` calls sharing one DB** — the current runner opens/closes its own connection; a registry refactor that shares a connection across phases could introduce transaction contention. No test covers parallel pipelines.
10. **`risk_level="critical"` when freshness is not "fresh"** — task 1 makes this more likely to trigger because per-dependent staleness now feeds in. Verify this doesn't make every real-world query return "critical".

---

## 4. Unit-testable vs integration-required

| Task | Unit-testable? | Integration needed? |
|------|----------------|---------------------|
| **1. Freshness in impact** | The checker is already unit-tested. The **wiring** (does `impact_analysis` call it?) needs a **handler-level integration test** with a real (in-memory) Ladybug DB and seeded Symbols whose `file_path` points at real temp files. Pure mocks won't exercise the mtime comparison. | Integration (handler + DB + filesystem). |
| **2. Subtype in impact** | The closure is unit-tested. The **wiring + risk recompute** is a **handler-level integration test** — same in-memory DB pattern as `test_impact.py`. No external services. | Integration (handler + DB), cheap. |
| **3. Runner → kahn/registry** | kahn and registry are unit-tested. The **rewrite** is inherently **integration**: you must run `run_ingest_pipeline` end-to-end with mocked I/O (as `test_runner.py` already does) and assert ordering, optional-phase inclusion, and backward-compat. Mocking the phases themselves would defeat the purpose. | Integration (full pipeline with mocked I/O). Highest effort. |
| **4. CrossLanguageName** | The dataclass is unit-tested. The **generation + persistence + handler readback** is a **3-layer integration test**: derive phase writes edge columns → DB stores them → handler returns them. Plus a **schema migration test**. All can run on in-memory DB. | Integration (derive + DB + handler), but self-contained. |
| **5. Hybrid search** | `rrf_fuse` is unit-tested. `hybrid_search` is integration (DB queries). The **wiring into `semantic_search`** is a **handler integration test** — the existing 15-case suite is the right level. Mocking the Embedder is acceptable (already done). | Integration (handler + DB + mocked Embedder). |
| **6. 补全测试** | N/A — this is the test-writing task itself. | N/A |

**Summary:** Every wiring task is **integration-test territory**, not unit-testable in isolation, because the bug class is "the implementation exists but is never called". A unit test of the implementation cannot detect a missing call site. The existing test patterns (in-memory Ladybug DB via `tmp_db_path` fixture, seeded Cypher, mocked I/O for the pipeline) are the right scaffolding and should be reused.

**One caveat on "integration":** none of these require a live IndexStore, real Xcode build, or running Ollama — the existing tests prove you can fake all three. So "integration" here means *multiple Orchard layers + a real (in-memory) DB*, not *end-to-end system*. Keep the fake-IO boundary; it keeps the suite fast.

---

## 5. Recommendations to the spec author

1. **Add a "Testability" section per task** specifying the fixture shape, the in-memory DB pattern, and which existing tests are the regression contract. Right now Task 6 is a wishlist, not a plan.
2. **Resolve the `timestamp` and empty-`file_path` questions in Task 1 before implementation.** These are not test details; they are product behavior.
3. **Sequence Task 4 after a schema-migration sub-task.** The `BridgesTo` table literally cannot hold the new columns today; the spec lists one bullet for it but doesn't flag it as a prerequisite.
4. **For Task 3, list the 4 existing `test_runner.py` cases and the `embedding_projection` test as the "do not break" contract.** They are the only thing preventing a silent rewrite regression.
5. **Add an acceptance test** (alongside `test_acceptance_m3/m4/m5.py`) that runs the full pipeline on a small seeded project and asserts: subtype appears in impact, stale-dependent filtered, community/process counts > 0, CrossLanguageName on a bridge edge, RRF-merged search result. This is the single highest-value deliverable and the natural home for Task 6.
6. **Pin the `_risk_level` new signature in the spec** so the test can assert against it; otherwise the test author is reverse-engineering the contract from the implementation.
