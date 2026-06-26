# Design-Perspective Review: Orchard Wiring Completion

**Spec reviewed:** `docs/superpowers/specs/2026-06-26-orchard-wiring-completion.md`
**Reviewer role:** Architecture / design correctness
**Date:** 2026-06-26
**Verdict:** The revised spec is **architecturally sound** and materially better than the 6-task original. The two descopes are correct calls and align with the design intent of the underlying components. The three integration approaches are directionally right, but **two of them (BuildSnapshot.created_at for freshness, ObjC selector formatting) have correctness gaps that will produce subtly wrong results if implemented as written** — they are design-level concerns, not implementation nits. The remaining 4 tasks are acceptable in shape but under-specify one architectural decision each.

---

## Summary answers to the three questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Are the descopes (pipeline runner rewrite + hybrid search) architecturally sound? | **Yes — both are correct.** See §1. |
| 2 | Are the integration approaches (seed-before-BFS, BuildSnapshot.created_at, ObjC selector formatting) correct? | **Mixed.** Seed-before-BFS is correct. `created_at` is the right source but the comparison is underspecified. ObjC selector formatting is correct in form but unimplementable from available data. See §2. |
| 3 | Design concerns with the 4 remaining tasks? | **Four concerns**, one per task. None block the spec, all should be resolved before coding. See §3. |

---

## 1. Descoping assessment — architecturally sound

### Task 3 descope (pipeline runner kahn/registry rewrite) — **AGREE, strongly**

The descope is the right architectural call for three design-level reasons, not just the "it's a big rewrite" rationale the dev review gave:

1. **The registry/kahn abstraction does not match the runner's data-flow shape.** The current `run_ingest_pipeline` (`pipeline/runner.py:51`) threads rich, heterogeneous Python objects between phases — `is_result` (an IndexStore parse object with `.occurrences`, `.relations`, `.symbols`), `all_symbols`/`all_rels` (lists of dataclasses), `embed_chunks`. `PhaseConfig.execute(ctx: dict, deps: dict)` (per `pipeline/phase.py`) reduces this to a string-keyed dict. Encoding an IndexStore result through a dict and decoding it downstream is not "wiring" — it is designing a new inter-phase contract. That belongs in a dedicated spec, not a bullet. Keeping the sequential runner preserves the direct-object-passing model that the phases were written against.

2. **kahn topological sort would lose the one place parallelism exists.** The only `asyncio.gather` in the pipeline (`runner.py:94-95`) parallelizes the two independent I/O-heavy ingest phases (IndexStore subprocess + SymbolGraph file reads). kahn produces a linear order; same-rank concurrency is not implicit. Replacing the runner to gain "configurable phase ordering" at the cost of serializing the two phases that actually benefit from concurrency is a net-negative architectural trade unless the new runner explicitly schedules same-rank phases concurrently. The spec (correctly) does not attempt this in a wiring task.

3. **The new derive phases (MRO/community/process) do not need the registry to be invoked.** They are already standalone functions with the `(conn, target_id) -> dict[str, int]` signature. Registering them as "callable derive functions" (the spec's revised Task 3 wording) and triggering via CLI/manual invocation is a strictly weaker integration than the full registry rewrite, and it captures ~90% of the value (the phases run and produce graph data) at ~5% of the risk. This is the correct minimum viable integration.

**Design caveat the spec should state:** the descoped phases (community/process/MRO) writing `MEMBER_OF`/`STEP_IN_PROCESS`/`Implements` edges means those edges will be **absent from any DB built by the default `run_ingest_pipeline` path** until the CLI/manual trigger is run. Any handler that reads those edges (impact's subtype closure reads `ConformsTo`/`Inherits`/`Extends`, but MRO writes `Implements`; community/process handlers read their own edges) will return partial results on a freshly-pipelined DB. The spec should call out that the CLI trigger is a **required post-ingest step**, not an optional one, for the graph to be query-complete. Right now it reads as "手动触发" (manual), which understates the dependency.

### Task 5 descope (hybrid search into semantic_search) — **AGREE, and the design reason is stronger than the spec states**

The spec's rationale ("semantic_search.py 已是混合搜索") is true but understates the case. The deeper design reason to descope:

- `hybrid_search()` (`search/hybrid_search.py:37`) is **architecturally a different layer** than `semantic_search` (`handlers/semantic_search.py`). The handler operates at **Chunk granularity** (embeddings live on `Chunk` nodes, the response returns `chunk_content`/`chunk_kind`). The helper operates at **Symbol granularity** (`RETURN s.id, s.usr, s.name`). Wiring the helper into the handler would either (a) discard the Chunk-level content the handler currently returns — a response-shape regression — or (b) require the helper to be rewritten to return Chunks, at which point it duplicates the handler. Neither is a "wiring" task; both are redesigns.
- The helper's "vector pass" (`hybrid_search.py:66-73`) does **no similarity computation** — it `LIMIT`s Chunks and returns them unordered. RRF-fusing an unordered list with a BM25 ranking is mathematically meaningless (RRF assumes ranked inputs). So the helper is not a drop-in upgrade; it is a downgrade that would silently weaken search quality. The descope avoids shipping that regression.

Keeping `rrf_fuse` as a standalone tool is the right architectural placement: it is a pure function on ranked lists, useful in its own right, and it does not belong inside a handler that already does correct cosine+FTS fusion at the right granularity.

**Net:** both descopes are architecturally justified, not just schedule-driven. The revised spec's instinct here is correct.

---

## 2. Integration approach correctness — mixed

### 2a. Seed-before-BFS for subtype closure — **CORRECT**

The revised Task 1 ("closure 结果先 seed 进 visited_ids 再 BFS") is the right design. It fixes the duplicate-counting bug the dev review flagged by making the BFS frontier's `visited_ids` guard do the dedup work natively, rather than adding a post-hoc set subtraction. This is the idiomatic way to integrate a closure into a BFS and avoids a class of bugs (a conformer appearing once as `reached_via="ConformsTo"` and again as `reached_via="subtype_closure"`).

**One design-level refinement the spec should make explicit:** after seeding `visited_ids` with closure USRs, the spec says closure results are "并入 d1，标记 `reached_via='subtype_closure'`". But if the closure USRs are seeded into `visited_ids` *and* into the d1 result set *before* BFS, then any closure member that is also reachable via a `Calls`/`References`/`Implements` edge from the root will be **frozen with `reached_via="subtype_closure"`** even though a Calls edge to it also exists. The `reached_via` label becomes "the reason we first saw it," not "the strongest relationship." This is a defensible semantic (subtype relationships are arguably the more important signal for impact), but it is a **labeling policy decision** the spec makes implicitly. It should be stated, because the `has_bridge` computation (`impact.py:159-161`) and downstream risk thresholds depend on `reached_via`. If a subtype is also a cross-language bridge, labeling it `subtype_closure` hides the bridge signal.

**Recommendation:** either (a) document that `reached_via` reflects first-contact order with subtype closure seeded first, or (b) when seeding, check for an existing `BridgesTo` edge and prefer `reached_via="BridgesTo"` for those closure members so `has_bridge` stays accurate. Option (b) is more correct for risk scoring.

### 2b. BuildSnapshot.created_at for freshness — **RIGHT SOURCE, UNDERSPECIFIED COMPARISON**

Choosing `BuildSnapshot.created_at` over adding a per-Symbol `indexed_at` column is the **architecturally correct** decision: it avoids a schema migration on the hot `Symbol` table (the highest-cardinality node table), it reuses a field that already exists and is already populated by `upsert_build_snapshot`, and it matches the semantic ("was this file modified after the index was built?"). The dev review's recommendation of option (b) is sound.

**The design gap:** `created_at` is an **ISO timestamp string** (`freshness.py:94`, `schema.py:18` — `STRING`), while `IndexOutOfDateChecker.is_up_to_date` compares `source_mtime <= location.timestamp` where `location.timestamp` is a **float Unix epoch** (`freshness.py:31,53`). The wiring must parse the ISO string to epoch before constructing the `SymbolLocation`. This is not mentioned in the spec and is a place where timezone/parse bugs silently make everything "fresh" or everything "stale." The spec should pin: (a) the parse format (assume UTC? local? `datetime.fromisoformat`?), (b) the fallback when `created_at` is empty (the `stale` branch in `freshness_for` returns `created_at=""` — what timestamp does that become?).

**Deeper design concern — per-dependent filtering vs aggregate freshness conflation.** The spec blends two distinct freshness concepts:
- *Aggregate freshness* (already wired at `impact.py:155,162`): "is this build snapshot current?" → drives `risk="critical"` when stale.
- *Per-dependent freshness* (Task 2): "has this specific dependent's source file changed since indexing?" → the spec says to **filter** such dependents out of `by_depth`.

These are different questions with different remedies. A build can be aggregate-fresh (correct toolchain/config) while individual files on disk have been hand-edited since indexing (per-dependent stale). The current `_risk_level` uses aggregate freshness; Task 2 adds per-dependent filtering. The spec does not say how the two interact:
- If per-dependent filtering removes rows, the `d1_count` passed to `_risk_level` shrinks, which can **lower** risk — the opposite of what staleness should signal. A query where 3 of 5 callers were hand-edited would compute risk on 2 callers and report "low," hiding the fact that the caller set is unreliable.
- Conversely, if aggregate freshness is "stale" (snapshot not found), every dependent is arguably suspect, but per-dependent filtering would still run on file mtimes.

**Design recommendation:** per-dependent staleness should **annotate, not filter**, OR filtering should surface an `open_gaps` entry ("N dependents filtered as stale") so the consumer knows the `by_depth` set is incomplete and the risk score is computed on a subset. Silently dropping rows is the wrong default for an impact-analysis tool whose entire purpose is "tell me what breaks." The test review (case 1.6) caught this from the QA side; from the design side it is a correctness issue — the response must not present a partial dependent set as the complete impact set.

### 2c. ObjC selector formatting (`-[Cls method:]`, `+[Cls method:]`) — **CORRECT FORM, UNIMPLEMENTABLE FROM AVAILABLE DATA**

The format strings are correct (they match sourcekit-lsp's `CrossLanguageName.clang_name` convention and LLVM selector syntax). The design problem is that **the data needed to produce them is not present at the point `run_bridge_recovery` runs.**

`run_bridge_recovery` (`derive/bridge.py:71-79`) matches symbols by `name` + `kind` across languages. For an ObjC method Symbol, the node carries `name` (e.g. `"method:withArg:"` or `"methodWithArg:"` — IndexStore/SymbolGraph are inconsistent about whether the selector label includes colons), `kind` (`"method"`), `usr`, `file_path`. To format `-[ClassName method:withArg:]` you additionally need:
1. **The owning class name** — not stored on the method Symbol; it lives on the parent via the `Contains` edge, or in `container_usr` (`schema.py:51`) which is a USR requiring a lookup.
2. **The instance (`-`) vs class (`+`) distinction** — `kind="method"` does not distinguish `instancemethod` from `classmethod`. The IndexStore kind mapping (`runner.py:42-44`) collapses both to `"method"`. So the `+`/`-` prefix cannot be determined from the graph as currently normalized.
3. **Whether the selector form has colons** — depends on whether the source is a SymbolGraph (tends to strip colons) or IndexStore (preserves them).

The spec treats "`-[Cls method:]`" as a formatting rule, but it is actually a **data-availability problem** that requires either (a) a query to resolve the container class name + method kind at bridge-recovery time (extra Cypher per pair, feasible), or (b) enriching the Symbol schema / IndexStore kind mapping to preserve the `-`/`+` distinction and the container name (a normalization change upstream of bridge recovery).

**Design recommendation:** the spec should state which path. Option (a) is the wiring-consistent choice (no schema change, resolve at derive time): for each matched ObjC symbol, `MATCH (container:Symbol)-[:Contains]->(m:Symbol {usr:$u}) RETURN container.name` and read the method kind from the original IndexStore kind before it was collapsed (or from `signature` if present). If `signature` is empty (common for SymbolGraph-only symbols), the `+`/`-` prefix is unknowable and the name must fall back to a best-effort form or be left `None`. The spec should define that fallback rather than implying every bridge gets a well-formed `clang_name`.

The Swift side (`Cls.method(_:)`) has the same container-resolution dependency but is more recoverable (Swift `name` from SymbolGraph is usually already fully-qualified, e.g. `MyClass.myMethod(_:)`).

---

## 3. Design concerns with the 4 remaining tasks

### Task 1 (subtype closure) — **edge-type coverage gap**

`_subtype_closure` (`impact.py:56-83`) walks `Inherits`, `ConformsTo`, `Extends`. It does **not** walk `Implements`. The dev review flagged this as a semantic question ("are conformers subtypes?"). The design-level concern is sharper: **MRO derivation (`derive/mro.py`) writes `Implements` edges to represent override relationships** (see `mro.py:44` — `MERGE (a)-[:Implements]->(b)`). So after MRO runs, a subclass method that overrides a parent method is connected by `Implements`, not `Inherits`. Impact analysis that omits `Implements` from the closure will **miss override-driven dispatch impact** — exactly the case virtual-dispatch-aware impact analysis is supposed to catch (this is the stated inspiration; the module docstring at `impact.py:1-5` claims to traverse `Implements`).

But the BFS loop (`impact.py:122`) *does* include `Implements` via `policy.effective_relation_types()` (it's in the default `relation_types`, `impact_policy.py:26-30`). So `Implements` is traversed in BFS but not in the closure. The asymmetry means: a direct `Implements` edge from root is caught; a two-hop path through `Implements` (root ← A implements ← B implements) is caught by BFS at depth 2; but a subtype reached only via a closure that should include `Implements` chains is not.

**Design decision the spec must make:** either add `Implements` to `_subtype_closure`'s edge list (making the closure consistent with the BFS edge set), or explicitly document that `Implements` is BFS-only and the closure is strictly for type-hierarchy (Inherits/ConformsTo/Extends). The current spec wording ("subtypes or conformers") does not match the code's edge set and does not resolve the MRO-interaction question.

### Task 2 (freshness) — **filter-vs-annotate semantics (see §2b)**

Covered above. The design concern is that silent filtering produces a misleading "complete impact set" that is actually partial. Resolve by annotating + surfacing in `open_gaps`, or by making filtering opt-in via the request.

### Task 3 (CrossLanguageName / BridgesTo migration) — **migration strategy for existing DBs**

The spec correctly adds the schema migration (3 columns on `BridgesTo`). The design concern is the **migration mechanics for already-built databases**, which `init_schema` (`schema.py`) does not handle: every DDL statement uses `CREATE ... IF NOT EXISTS`, so on an existing DB the new `BridgesTo` columns will **not be added** — the table already exists, the `CREATE REL TABLE IF NOT EXISTS` is a no-op, and the columns remain absent. Writing `clang_name` will then fail or silently drop.

This is not an implementation detail; it is an architectural property of the schema-init approach. The spec should state the migration strategy:
- **Option A:** document that existing DBs must be rebuilt (delete + re-ingest). Acceptable for a pre-production tool, but must be stated.
- **Option B:** add an `ALTER`/`drop-and-recreate` path for `BridgesTo` in `init_schema` (Ladybug/Kùzu `ALTER REL TABLE ADD` support varies by version — needs verification). The dev review flagged "affects init_schema and any existing DBs"; from the design side this is a **schema-evolution policy** decision that the project has not yet had to make (all prior changes were additive node tables). The spec is the right place to establish that policy.

**Recommendation:** state Option A explicitly (rebuild required for this release). It is the simplest correct choice and sets the precedent that schema changes are rebuild-grade until an incremental-migration story is added. Do not silently rely on `IF NOT EXISTS`.

### Task 4 (tests) — **the integration-test gap is the design contract**

The test review covered the test cases thoroughly. The design-level concern is narrower: the spec's Task 4 lists "补全测试" as a bucket, but several of the wiring tasks (especially subtype-into-impact and freshness filtering) **change the response contract of `impact_analysis`** — `by_depth` contents change, `risk` computation changes, `reached_via` gains a new value, and (per §2b) dependents may disappear. There is no consumer-side contract document for `impact_analysis`'s response shape.

**Design recommendation:** before Task 4, pin the post-wiring `impact_analysis` response schema explicitly (which fields are stable, which are new, what `reached_via` values are possible, what `open_gaps` values mean). The tests should assert against that pinned contract. Otherwise the tests codify whatever the implementation happens to produce, which defeats the purpose of a contract. This is a design artifact (a small response-schema spec), not a test artifact — and it is the missing prerequisite that makes Task 4 estimatable.

---

## 4. Cross-cutting design observations

1. **`reached_via` is becoming a poor man's enum.** It currently takes values from `{Calls, References, Implements, BridgesTo}` and will add `subtype_closure`. Downstream code switches on it (`has_bridge` checks `== "BridgesTo"`). As values grow, this stringly-typed field will accumulate bugs. Not a blocker, but worth a follow-up to make it an enum or at least a documented set.

2. **Two freshness concepts, one field.** The response `freshness` field carries aggregate status. Per-dependent staleness (Task 2) has nowhere to live in the response except by row removal. If the annotate-not-filter recommendation in §2b is adopted, the response needs a per-dependent `stale: bool` or similar — a response-shape change the spec does not mention.

3. **The descope leaves `derive/mro.py`, `derive/community_detection.py`, `derive/process_detection.py` as second-class citizens** (CLI-only invocation). This is acceptable, but the design should acknowledge that handlers consuming their edges (`type_hierarchy` reads `Inherits`/`Implements`; a future community handler reads `MEMBER_OF`) will return empty on default-built DBs. Either (a) add a one-line invocation of the three phases at the end of `run_ingest_pipeline` (cheap, no registry needed — just three function calls after `swiftui_derivation`), or (b) document the CLI step as mandatory. Option (a) captures the value the descope deferred without the registry rewrite risk, and is worth reconsidering as a small amendment to the descope.

---

## 5. Verdict and required spec revisions

The spec is approved **with required revisions** before implementation. The descopes are sound; the integration approaches need design-level tightening, not just the implementation detail the dev/test reviews already captured.

**Required revisions (design-blocking):**

1. **§2b / Task 2:** Decide filter-vs-annotate for per-dependent freshness. Recommend annotate + `open_gaps` surfacing. Pin the `created_at` ISO→epoch parse and the empty-`created_at` fallback.
2. **§2c / Task 3:** State how the ObjC container class name and `-`/`+` method kind are obtained at bridge-recovery time, and define the fallback when they are unavailable.
3. **Task 3 schema migration:** State the rebuild-vs-ALTER policy for existing DBs. Recommend rebuild-required for this release.
4. **Task 1 edge coverage:** Decide whether `_subtype_closure` includes `Implements`, or document it as type-hierarchy-only. Resolve the asymmetry with the BFS edge set.
5. **§2a labeling policy:** Document the `reached_via` first-contact-order semantic, or prefer `BridgesTo` for closure members that also have a bridge edge so `has_bridge` stays accurate.

**Recommended revisions (non-blocking):**

6. Pin the post-wiring `impact_analysis` response contract before Task 4.
7. Either add a one-line invocation of MRO/community/process to `run_ingest_pipeline` (preferred) or mark the CLI trigger as mandatory for a query-complete graph.
8. Consider an eventual follow-up to make `reached_via` an enum.

With revisions 1–5 addressed, the spec is ready for implementation. The descoping judgment is correct and the remaining work is well-scoped.
