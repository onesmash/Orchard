# Development Review: Orchard Wiring Completion Spec

**Spec under review:** `docs/superpowers/specs/2026-06-26-orchard-wiring-completion.md`
**Reviewer perspective:** Development / implementation feasibility
**Date:** 2026-06-26
**Verdict:** Spec is directionally correct — all 6 tasks describe real, verified gaps — but **4 of the 6 tasks have a missing or incorrect integration point** that will block or misdirect implementation. Spec needs revision before execution.

---

## Verification summary

I read the spec's claims against the actual source. Every "implemented but unwired" claim checks out:

| Claim in spec | Verified against code | Status |
|---|---|---|
| `IndexOutOfDateChecker` has no caller | `grep` confirms only definition in `validation/freshness.py`, no callers | CONFIRMED unwired |
| `_subtype_closure()` not called by `impact_analysis` | Defined at `handlers/impact.py:56`, never invoked | CONFIRMED unwired |
| `rrf_fuse`/`hybrid_search` not used by `semantic_search` | `handlers/semantic_search.py` never imports `search/hybrid_search.py` | CONFIRMED unwired |
| `runner.py` ignores `kahn_sort`/registry | `pipeline/runner.py` has 11 hardcoded sequential phases, never imports `pipeline.registry` or `pipeline.kahn` | CONFIRMED unwired |
| `CrossLanguageName` not populated by `run_bridge_recovery` | `derive/bridge.py:34` defines dataclass but `run_bridge_recovery` never instantiates it | CONFIRMED unwired |
| MRO/community/process modules uncalled by runner | `run_mro`, `run_community_detection`, `run_process_detection` exist but `runner.py` never calls them | CONFIRMED unwired |

---

## Task-by-task assessment

### Task 1 — freshness filter in `impact_analysis` — Integration point PARTIALLY correct, blocking schema gap

**What the spec says:** "接入：impact_analysis BFS 遍历到的每个 dependent，调用 `is_up_to_date(SymbolLocation(path, timestamp))` 过滤" and notes "当前 Symbol 节点无 timestamp 字段，用 file_path mtime 作为近似".

**Reality check:**
- `IndexOutOfDateChecker.is_up_to_date(SymbolLocation)` (`validation/freshness.py:46`) compares `source_mtime <= location.timestamp`. With `timestamp` missing, you cannot construct a meaningful `SymbolLocation(path, ???)`.
- The Symbol node schema (`graph/schema.py:40-55`) has **no `timestamp` column** — only `file_path`. So the spec's "用 file_path mtime 作为近似" is not implementable as written: there is no stored timestamp to compare against.
- The spec's fallback ("若文件存在且未改→fresh") is only achievable by forcing `IndexCheckLevel.DELETED_FILES` (file-exists check) or by storing `BuildSnapshot.created_at` as the comparison baseline — neither is mentioned.

**Correct integration point:** Either (a) add a `indexed_at`/`timestamp` column to the Symbol schema (schema migration — not mentioned anywhere in the spec), or (b) compare each dependent's `file_path` mtime against `BuildSnapshot.created_at` from `freshness_for()`, constructing `SymbolLocation(path, snapshot_created_at_epoch)`. Option (b) is cheaper and is what the spec *should* say.

**Additional issue:** The spec says "过滤" (filter out) stale dependents. But `impact_analysis` currently uses freshness only at the aggregate level (`_risk_level` returns "critical" if not fresh). Per-dependent filtering is a **behavior change to the response shape**: clients that today see N d1 dependents would see fewer. This is a backward-compat risk the spec does not flag.

### Task 2 — subtype closure into impact — Integration point INCORRECT (edge-type + placement bugs)

**What the spec says:** "BFS 后对查询 USR 调 `_subtype_closure()`，结果并入 d1，标记 `reached_via="subtype_closure"`" and "更新 risk_level 计算纳入 subtype 数量".

**Reality check — two concrete bugs in this task:**

1. **Edge-type mismatch.** `_subtype_closure` (`handlers/impact.py:56-83`) walks `Inherits`, `ConformsTo`, `Extends`. But `ImpactTraversalPolicy.effective_relation_types()` is what BFS uses, and `Implements` (the MRO override edge) is a separate type. The function docstring even says "Walks Inherits:FROM, ConformsTo:FROM, and ExtendsTo:FROM" — note it does **not** include `Implements`. The spec calling these "subtypes" is loose; conformers (via `ConformsTo`) are not strictly subtypes. If the intent is "anyone who could be virtually dispatched to," `Implements` should be included. Spec needs to clarify the semantic.

2. **`reached_via` injection is underspecified.** `_subtype_closure` returns a `set[str]` of USRs. To merge into d1 with a `reached_via` label, the wiring must re-fetch each USR's `name/module/language/kind` (the BFS loop currently gets these from the edge query). The spec says "并入 d1" but doesn't acknowledge that subtype-closure results bypass the BFS row fetcher and need a separate `MATCH (s:Symbol {usr: $u})` lookup. This is a real implementation step, not a detail.

3. **Duplicate-counting risk.** A subtype reached via `ConformsTo` may *also* be reached via the normal BFS if `ConformsTo` is in `effective_relation_types()`. The spec doesn't say whether subtype-closure results should be deduped against `visited_ids`. Without that, a conformer appears twice in d1 — once via `reached_via="ConformsTo"`, once via `reached_via="subtype_closure"`.

**Integration point correction:** Call `_subtype_closure` *before* the BFS loop (not "BFS 后"), seed `visited_ids` and `current_ids` with the closure so the BFS naturally dedupes, OR run it after and explicitly subtract `visited_ids`. The spec's ordering ("BFS 后") invites the duplicate bug.

### Task 3 — pipeline runner kahn + registry — Integration point correct, but HIGHEST RISK and under-specified

**What the spec says:** "保留 `run_ingest_pipeline()` 兼容入口，内部用 registry + kahn_sort 驱动" and "注册新阶段：MRO、社区、流程检测为可选阶段（enabledWhen 默认 True）".

**Reality check:**
- Integration point is correctly identified: `runner.py:51` `run_ingest_pipeline` is the single entry point, and the 11 inline phases (`indexstore_ingest`, `swift_symbolgraph_ingest`, `identity_normalization`, `swiftinterface_conformances`, `cross_language_bridge_recovery`, `embedding_projection`, `call_graph_derivation`, `architecture_derivation`, `swiftui_derivation`) plus 3 new ones must all become `PhaseConfig` entries.
- **This is a near-total rewrite of `runner.py`.** The current implementation uses `asyncio.gather` for the two independent ingest phases (`runner.py:94`) and inline CSV writes with temp-file lifecycle (`identity.py` upsert_symbols). The `PhaseConfig.execute(ctx, deps)` signature (`pipeline/phase.py:18`) passes a `dict` context — **not** the `BuildContext`, `conn`, and per-phase return values (`is_result`, `all_symbols`, `all_rels`) the current code threads through. Bridging this is non-trivial: the registry pattern expects each phase to be independent, but `call_graph_derivation` depends on `is_result` (the IndexStore parse result object), which is not a graph node.
- **Concurrency loss risk:** kahn produces a linear topological order. The current `asyncio.gather(_run_indexstore(), _run_symbolgraph())` parallelism would be lost unless the runner special-cases "phases with no deps at the same rank run concurrently." The spec doesn't address this — it will silently make ingestion slower.
- **`PhaseConfig.execute` is `Optional[Callable]`** (`phase.py:28`). The runner must handle `execute=None` (data-only registration). Spec doesn't mention.
- **enabledWhen default:** spec says "默认 True" but `registry.get_enabled_phases()` (`registry.py:20-25`) treats `enabled_when is None` as enabled. So "default True" should be "default None (= enabled)". Minor, but the spec wording could lead to `lambda: True` boilerplate everywhere.

**This is the riskiest task** (see Section 3 below). It needs its own design mini-spec, not a bullet point.

### Task 4 — CrossLanguageName population — Integration point INCORRECT (missing schema migration)

**What the spec says:** "写入 BridgesTo 的 clang_name/swift_name/definition_language 列" and "handlers/bridges.py 读取并返回".

**Reality check — blocking schema gap:**
- The `BridgesTo` rel table (`graph/schema.py:118-125`) is defined as:
  ```
  BridgesTo(FROM Symbol TO Symbol, bridge_kind, provenance, confidence, build_id, reason)
  ```
  **There are no `clang_name`, `swift_name`, or `definition_language` columns.** The spec instructs writing to columns that don't exist. Ladybug/Kùzo will reject the `MERGE ... SET r.clang_name=...`.
- `run_bridge_recovery` (`derive/bridge.py:106-118`) currently `MERGE`s edges with only `{bridge_kind, provenance, confidence, build_id}`. Wiring CrossLanguageName requires:
  1. Add 3 columns to the `BridgesTo` schema (migration — affects `init_schema` and any existing DBs).
  2. Compute the dual-language names. The spec says "根据语言生成 clang_name (objc `-[Cls method:]`) 和 swift_name" — but the current `run_bridge_recovery` only has `usr, name, file_path` for each symbol. **Constructing `-[Class method:]` from a USR/name is a non-trivial formatting step** (Objective-C selectors, method kind `+`/`-`). The spec treats this as trivial; it's the bulk of the work.
  3. `CrossLanguageName` is a return-shape helper; nothing in `run_bridge_recovery` produces one. The wiring must build instances per pair.

**Additional issue — `get_cross_language_bridges` response shape:** `handlers/bridges.py:33-43` currently returns `{bridge_kind, confidence, provenance, target_usr, target_name, target_language}`. Adding cross-language names changes the response dict — existing clients (MCP tool consumers) get new keys. Not necessarily breaking, but the spec should state whether `target_name` is superseded or complemented by `clang_name`/`swift_name`.

### Task 5 — hybrid search wiring — Integration point PARTIALLY correct, source module has bugs

**What the spec says:** "semantic_search 调用 hybrid_search()" and "验证 LadybugDB FTS 是否可用，不可用则降级到 name CONTAINS".

**Reality check:**
- The current `semantic_search` (`handlers/semantic_search.py:28-109`) is **already a hybrid implementation** — it does vector cosine similarity + substring FTS and dedupes by content. So "hybrid search 接入" is misleading: the handler already does fusion, just *not via* `hybrid_search()`/`rrf_fuse()`. The real task is "refactor to use the shared `hybrid_search` helper" — a dedup/consolidation task, not a capability addition.
- **`hybrid_search()` itself has bugs that make it a downgrade from the current handler:**
  - `search/hybrid_search.py:66-73`: the `embedding` parameter is accepted but **the vector pass does no similarity computation** — it just `LIMIT`s Chunks and returns their `owner_usr`. RRF then fuses BM25 ranks with an unordered Chunk list. This is worse than the current handler's real cosine scoring.
  - `search/hybrid_search.py:51-57`: "BM25 via LadybugDB FTS" is a comment, but the query is plain `CONTAINS` — there is no FTS index declaration in `schema.py`. So BM25 is fake; it's substring matching labeled as BM25.
  - Returns `Symbol`-level results (`s.id, s.usr, s.name`), while the current handler returns `Chunk`-level content. Different granularity.
- **The spec's "验证 LadybugDB FTS" step is the real blocker.** As written, wiring `hybrid_search` will *reduce* search quality because the helper ignores embeddings. The spec should either (a) fix `hybrid_search` to do real cosine similarity (like the current handler does) before wiring, or (b) drop this task as "not worth it — current handler is already hybrid."

### Task 6 — tests — Correct, but incomplete

**What the spec says:** Lists 5 test categories (subtype, freshness, community, process, CrossLanguageName).

**Reality check:**
- Existing tests already cover the *units* in isolation: `tests/test_handlers/test_impact_subtype.py` tests `_subtype_closure` directly; `tests/test_derive/test_bridge_cross_language.py` tests the `CrossLanguageName` dataclass; `tests/test_validation/test_freshness_checker.py` tests `IndexOutOfDateChecker`. These pass today.
- **What's missing is integration tests** — the spec's list is right conceptually but should explicitly require: "impact_analysis end-to-end returns subtypes" (not just `_subtype_closure` unit), "stale dependents are filtered in the response" (not just `is_up_to_date` unit), "pipeline runner with kahn produces identical graph to current sequential runner" (regression/golden test — critical for Task 3).
- No mention of a regression test that the refactored `run_ingest_pipeline` produces the same PhaseResult list / graph contents as today. For a full-runner-rewrite (Task 3), this is essential.

---

## Backward-compatibility risks

| Task | Backward-compat risk | Severity |
|---|---|---|
| 1 (freshness filter) | Response shape change: d1/d2 dependent counts shrink when files are stale. Clients expecting today's counts break. Also, `risk_level` becomes "critical" more often (already true today, but per-dependent filtering compounds it). | MEDIUM |
| 2 (subtype closure) | d1 count grows (subtypes added), pushing more queries into "medium"/"high" risk. Downstream tooling that thresholds on risk level will change behavior. | MEDIUM |
| 3 (kahn runner) | Phase ordering may change if dependency declarations are imperfect. Current implicit ordering (e.g. `identity_normalization` before `bridge_recovery`) must be preserved explicitly via `deps`. Loss of `asyncio.gather` parallelism slows ingestion. | HIGH |
| 4 (CrossLanguageName) | Schema migration on `BridgesTo` — existing DBs need ALTER or rebuild. `init_schema` uses `IF NOT EXISTS`, so adding columns requires either a new `ALTER TABLE` path or a full re-ingest. | HIGH |
| 5 (hybrid search) | If wired naively, search quality regresses (helper ignores embeddings). Response granularity may change from Chunk-level to Symbol-level. | MEDIUM |
| 6 (tests) | None — additive. | NONE |

---

## Riskiest wiring

**Task 3 (pipeline runner kahn + registry rewrite)** is the riskiest, for three reasons:

1. **Scope creep disguised as a bullet.** It's a near-complete rewrite of the most important file in the ingest path (`runner.py`, 244 lines, 11 interdependent phases). The spec allots it one line: "改造：保留入口，内部用 registry + kahn 驱动."
2. **Abstraction mismatch.** The current runner threads rich Python objects between phases (`is_result`, `all_symbols`, `all_rels`, `embed_chunks`). `PhaseConfig.execute(ctx: dict, deps: dict)` reduces this to a string-keyed dict. Encoding/decoding the IndexStore parse result through a dict is fiddly and error-prone.
3. **Silent performance regression.** kahn produces a linear order; the current `asyncio.gather` for the two independent ingest phases parallelizes real I/O. Unless the new runner explicitly runs same-rank phases concurrently, ingestion wall-time doubles for the indexstore+symbolgraph phases. The spec doesn't mention this.

The spec's own implementation ordering ("3. pipeline runner 改造（最高风险，最后做）") acknowledges this is the riskiest, but the task description doesn't reflect that acknowledgment — it needs a dedicated design section covering: (a) how rich inter-phase objects pass through `ctx`/`deps`, (b) whether concurrency is preserved, (c) a golden regression test against current output.

---

## Tasks needing more detail

1. **Task 3 (runner rewrite)** — needs its own mini-design: context-object passing, concurrency preservation, golden regression test. Currently under-specified to the point of being unestimatable. (See above.)

2. **Task 4 (CrossLanguageName)** — must add: (a) the `BridgesTo` schema migration (3 new columns), (b) the ObjC selector formatting algorithm (`-[Class selector:withArg:]` construction from a USR/name — this is the actual work), (c) whether `target_name` in the bridges handler is replaced or augmented.

3. **Task 1 (freshness filter)** — must pick a concrete timestamp source: add Symbol.indexed_at column, or reuse BuildSnapshot.created_at. The current "用 file_path mtime 作为近似" is unimplementable as written. Also must decide filter-vs-annotate behavior (does a stale dependent disappear from the response, or get flagged?).

4. **Task 5 (hybrid search)** — should be reframed as "refactor to share `hybrid_search` helper" and must first fix the helper's vector pass (it currently ignores the embedding). Otherwise this task actively regresses search quality. Consider dropping the task if the consolidation isn't valuable — the current handler is already hybrid.

**Tasks 2 and 6 are sufficiently specified** (modulo the duplicate-counting and edge-type clarifications noted above for Task 2).

---

## Recommended spec revisions before execution

1. Add a "Schema migrations" section: Task 4 requires `ALTER BridgesTo ADD clang_name/swift_name/definition_language` (or a rebuild strategy).
2. Add a "Backward-compat" section per task (table above).
3. Split Task 3 into its own design doc, or expand it to ~half the spec.
4. For Task 1, commit to a timestamp source (recommend `BuildSnapshot.created_at`) and decide filter-vs-annotate semantics.
5. For Task 5, either pre-fix `hybrid_search` (real cosine in vector pass, real FTS or drop the BM25 claim) or descope.
6. For Task 2, specify dedup against `visited_ids` and clarify whether `Implements` edges are in scope.
