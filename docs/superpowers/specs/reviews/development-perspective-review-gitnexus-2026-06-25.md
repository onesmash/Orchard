# Development Perspective Review: Orchard GitNexus Optimizations

**Review date**: 2026-06-25
**Spec under review**: `docs/superpowers/specs/2026-06-25-orchard-gitnexus-optimizations.md`
**Reference codebase**: GitNexus (TypeScript) + Orchard (Python, current `src/orchard/`)
**Reviewer perspective**: Development feasibility, file scope correctness, implementation ordering, risk analysis

---

## 1. Are the file scopes correct? Any missing files?

### 1.1 Directory naming mismatch (BLOCKING)

The spec uses `src/orchard/ingestion/` throughout (items #5, #6, #8), but the actual orchard codebase uses `src/orchard/ingest/` (containing `indexstore.py`, `symbolgraph.py`, `swiftinterface.py`). There are two options:

- **Option A**: Create a new `src/orchard/ingestion/` directory for these new processors. This creates an inconsistent naming convention (`ingest/` vs `ingestion/`).
- **Option B (recommended)**: Place the new files in the existing `src/orchard/ingest/` directory, or rename the old `ingest/` to `ingestion/` for consistency.

Either way, the spec's file paths will not resolve without a conscious directory decision.

### 1.2 Missing files in the "Files Changed" table

The following files are **missing** from the spec's Files Changed table but are touched by the changes described:

| Missing File | Why It Is Needed |
|---|---|
| `src/orchard/pipeline/runner.py` (modified) | Already exists. Item #3 changes this to use PipelinePhase protocol + Kahn sort. The spec only lists `phase.py`, `runner.py`, and `registry.py` as new/separate files, but the existing `runner.py` at `src/orchard/pipeline/runner.py` MUST be refactored. |
| `src/orchard/handlers/semantic_search.py` (modified) | Already exists at `src/orchard/handlers/semantic_search.py`. Item #4 explicitly says "Integrate into `semantic_search` handler", but this file is not in the Files Changed table. |
| `src/orchard/query/lookup.py` (modified) | Already exists. The `GraphLookup` class needs new methods for process queries, community queries, and hybrid search queries. |
| `src/orchard/server.py` (modified) | New MCP tools for process queries, community queries, contract queries need registration here. |
| `src/orchard/pipeline/__init__.py` | Needs to export `PipelinePhase`, `PhaseRegistry`, Kahn runner. |

### 1.3 File name discrepancies with GitNexus references

| Spec File Name | GitNexus Reference | Issue |
|---|---|---|
| `src/orchard/incremental/shadow.py` | `shadow-candidates.ts` | Name differs. The spec says "shadow.py" but the reference is "shadow-candidates.ts". The subgraph extract logic (`subgraph.py`) is listed separately but in GitNexus the subgraph extraction is part of the incremental writeback pipeline, not a standalone module. |

### 1.4 Missing infrastructure files

- **`src/orchard/ingestion/__init__.py`** (or `src/orchard/ingest/__init__.py`): New module needs package init.
- **`src/orchard/incremental/__init__.py`**: New module.
- **`src/orchard/graph/knowledge_graph.py`**: The spec says this is file #1. No `__init__.py` modification mentioned.
- **Cross-target DB for contract extractor**: Item #6 says "Write Contract entries to a shared cross-target DB". Orchard currently uses a single `graph.db` Ladybug database per build (opened at `~/.orchard/graph.db` default). There is **no cross-target database infrastructure** in the current architecture. This requires either a new `Contract` node table in the existing DB, or a completely separate database. The spec does not specify which.

---

## 2. Is the implementation order feasible given orchard's current architecture?

### 2.1 Overall assessment: The order is roughly correct for items #1-#4, but #5-#8 have issues.

### 2.2 Phase 1: Item #2 confidence/reason (schema only) -- FEASIBLE

The current `schema.py` already has `confidence DOUBLE` on these rel tables:
- `Calls` (already has `confidence DOUBLE`)
- `BridgesTo` (already has `confidence DOUBLE`)
- `ViewTree` (already has `confidence DOUBLE`)
- `NavigationFlow` (already has `confidence DOUBLE`)
- `References` (already has `confidence DOUBLE`)

Tables that currently **lack** `confidence`:
- `Contains`
- `Extends`
- `Inherits`
- `Implements`

Tables that currently **lack** `reason` (which the spec calls `reason STRING`):
- All rel tables. No table has a `reason` column.

**Risk**: Ladybug uses `CREATE ... IF NOT EXISTS` (idempotent DDL), but adding a column to an existing table may require `ALTER TABLE` depending on Ladybug's DDL semantics. If Ladybug does not support `ALTER TABLE ADD COLUMN`, this would need a schema migration strategy (drop and recreate, which loses data). This needs to be verified against Ladybug's DDL documentation.

**Also note**: The spec says `confidence DOUBLE` but the current schema already uses `DOUBLE` for some tables. Consistency is fine.

### 2.3 Phase 2: Item #3 Pipeline DAG -- FEASIBLE, but more invasive than spec implies

The current `runner.py` is a single 244-line function (`run_ingest_pipeline`) with hardcoded sequential phases and some concurrent sub-phases (indexstore + symbolgraph via `asyncio.gather`). Refactoring to a PipelinePhase protocol + Kahn topological sort requires:

1. Extracting each logical phase block into a standalone `PipelinePhase` object.
2. Adding dependency declarations.
3. Running them in topological order.

The current code's phases are:
```
[build_artifacts (external)] → [indexstore_ingest || swift_symbolgraph_ingest] → identity_normalization → swiftinterface_conformances → cross_language_bridge_recovery → embedding_projection → call_graph_derivation → architecture_derivation → swiftui_derivation
```

This maps naturally to a DAG. The concurrent pair (indexstore + symbolgraph) has no inter-dependencies, which fits the Kahn model. **Feasible**.

### 2.4 Phase 3: Item #1 In-memory KG -- FEASIBLE but HIGH IMPACT

The current pipeline writes directly to LadybugDB via `COPY FROM` CSV bulk import after each phase. The spec proposes adding an in-memory `KnowledgeGraph` that accumulates all nodes/edges, then flushes to Ladybug at the end.

**Impact on existing code**: Every phase's data output path changes. Currently:
- `upsert_symbols` writes to Ladybug via COPY FROM CSV.
- `upsert_calls` writes to Ladybug via COPY FROM CSV.
- `upsert_indexstore_rels` writes to Ladybug via COPY FROM CSV.
- `run_bridge_recovery` writes BridgesTo edges directly via conn.execute().
- `chunk_symbols` + embedder writes Chunk nodes directly.
- `run_architecture_derivation` writes DependsOn edges directly.

All of these would need to write to the in-memory KG instead, then a final flush phase copies everything to Ladybug via COPY FROM.

**Performance concern**: COPY FROM is Ladybug's fastest import path. The current approach uses it aggressively. The KG approach means holding the entire graph in memory before a single bulk flush. For large projects (500K+ symbols, millions of edges), this could cause memory pressure. Mitigation: the KG can flush incrementally per-phase, but then the "efficient rollback" benefit is lost.

### 2.5 Phase 4: Item #4 RRF Hybrid Search -- FEASIBLE with caveats

Current `semantic_search.py` does:
1. Try embedding (via Ollama).
2. Vector path: load ALL Chunks with embeddings, compute cosine similarity in Python.
3. FTS path: `CONTAINS` substring match (NOT an FTS index).
4. Sort by score, return top-k.

This is already a form of "hybrid" search, but without RRF fusion and without a proper FTS index. The spec says "BM25 via LadybugDB FTS extension".

**Critical question**: Does LadybugDB have an FTS extension? Ladybug is a fork/evolution of KuzuDB. KuzuDB does not have native FTS; it relies on `CONTAINS`/`STARTS_WITH` string functions. If Ladybug doesn't have FTS, the BM25 path cannot be implemented as described. The current `CONTAINS` approach would need to be used instead, which is substring matching, not BM25.

**Additionally**: The current approach loads ALL embeddings into Python for cosine similarity. This is O(N) per query. The spec's RRF approach doesn't change this -- it still needs the vector search layer. An efficient KNN vector search would require Ladybug's vector index support (HNSW or similar). The spec doesn't address this.

### 2.6 Phases 5-8 ordering issues

**Critical ordering bug: Item #8 (Leiden communities) runs AFTER #5 (process detection), but it should run BEFORE.**

Proof from GitNexus `pipeline.ts`:
```typescript
.register(mroPhase, ...)
.register(communitiesPhase, ...)  // ← communities BEFORE processes
.register(processesPhase, ...)
```

Proof from GitNexus `process-processor.ts`:
```typescript
export const processProcesses = async (
  knowledgeGraph: KnowledgeGraph,
  memberships: CommunityMembership[],  // ← depends on community output
  ...
)
```

The process detector uses community memberships to tag processes as `intra_community` vs `cross_community`. If communities haven't been computed yet, processes can't be classified.

**Correction**: Move #8 (communities) to phase 5, and #5 (processes) to phase 7 or 8.

### 2.7 Item #6 contract extractor feasibility

This is the least specified item. "Cross-target DB" does not exist in Orchard's architecture. The current design uses a single `graph.db` for one build/target. Contract extraction for Apple code would mean extracting public API surface from Swift protocols, ObjC headers, and module interfaces. The GitNexus contract extractor (`contract-extractor.ts`) is a TypeScript interface definition -- it has no implementation in the referenced code, just a type:
```typescript
export interface ContractExtractor {
  type: ContractType;
  canExtract(repo: RepoHandle): Promise<boolean>;
  extract(...): Promise<ExtractedContract[]>;
}
```

This item is effectively a **research task**, not an implementation task. It needs a detailed sub-spec before it can be estimated.

---

## 3. Which items are riskiest and why?

### Risk ranking (highest to lowest):

| Rank | Item | Risk | Why |
|------|------|------|-----|
| 1 | **#6 Contract Extractor** | HIGH | Underspecified. No cross-target DB. GitNexus reference is just a type interface with no implementation. Apple contract semantics (protocols, headers, modules, availability) are very different from HTTP API contracts. Needs a sub-design. |
| 2 | **#7 Incremental Indexing** | HIGH | The GitNexus shadow-candidate algorithm is **JS/TS-specific**. It handles `.ts`/`.tsx`/`.js` extension priority and directory-index resolution -- patterns that don't exist in Swift/ObjC. Apple module resolution uses frameworks, modules, bridging headers, and `@import` -- completely different. This item would need a ground-up redesign of the shadow-candidate logic. Additionally, detecting "changed files" for an Xcode project means diffing IndexStore directories, not source files directly (or requires expensive recompilation to detect changes). |
| 3 | **#1 In-memory KG** | HIGH (classified correctly) | Touches every phase's write path. Risk of performance regression (losing per-phase COPY FROM). Memory pressure for large graphs. Need careful dual-index maintenance invariant (like GitNexus does with `writeRel`/`deleteRel` helpers). |
| 4 | **#5 Process Detection** | MEDIUM | Apple ecosystem challenge: any BFS from a user function will immediately hit UIKit/SwiftUI system framework calls. Without a framework-boundary detection mechanism, processes will either be truncated to 1-2 steps (useless) or explode into system calls (meaningless). GitNexus has `MIN_TRACE_CONFIDENCE` filtering but doesn't have a framework boundary concept. This needs an Apple-specific adaptation: detect when the trace enters a system framework and stop. |
| 5 | **#3 Pipeline DAG** | MEDIUM | Refactoring a working pipeline always carries regression risk. The current runner is simple and correct. The DAG is a net improvement but the transition needs careful testing. |
| 6 | **#4 RRF Hybrid Search** | MEDIUM | Depends on LadybugDB FTS support (unconfirmed). The current approach "works" -- replacing it with a framework-dependent FTS path could regress if FTS is not available. |
| 7 | **#2 confidence/reason** | LOW (classified correctly) | Schema-only change. However, Ladybug's ALTER TABLE support needs to be verified. |
| 8 | **#8 Leiden Communities** | LOW (classified correctly) | Algorithm is well-understood. Main risk is Python dependency: needs `leidenalg` (pip) or `python-igraph` + `leidenalg`. The GitNexus version vendored `graphology-communities-leiden` because the npm package was never published. The Python ecosystem has `leidenalg` on PyPI, making this simpler. |

---

## 4. Dependency issues between items

### 4.1 Dependency graph (as spec'd):

```
#2 (schema) → #3 (DAG) → #1 (KG) → #4 (search)
                                   → #5 (processes)
                                   → #6 (contracts)
                                   → #8 (communities)
                                   → #7 (incremental)
```

### 4.2 Issues found:

**Issue A: #8 (communities) must precede #5 (processes).**

As shown in section 2.6, the process detector consumes `CommunityMembership[]` from the community detector. The dependency should be:
```
#8 (communities) → #5 (processes)
```

**Fix**: Swap the order of #5 and #8 in the implementation plan.

**Issue B: #1 (KG) is an implicit hard dependency for #4 (hybrid search).**

The spec's RRF hybrid search item says "BM25 via LadybugDB FTS extension". If the in-memory KG holds the data, the FTS index needs to be built from KG data, not from Ladybug directly. However, FTS is a database-level index, not an in-memory construct. This means the hybrid search layer would need to query Ladybug directly (for FTS) AND potentially the KG (for structural queries). This dual-source pattern needs clarification.

**Issue C: #6 (contract extractor) needs a cross-target DB that doesn't exist.**

This is a prerequisite development task that is not listed. Before #6 can be implemented, either:
- Add a `Contract` node table to the existing schema (simpler, single-target only).
- Design and build a cross-target database layer (complex, what the spec implies).

**Issue D: Missing item: MCP tool registration.**

The spec describes 4 new capabilities (process detection, contract extraction, hybrid search upgrade, community detection) but none of the existing MCP handlers know about them. New tools need to be registered in the MCP server (`src/orchard/server.py`). This is an implicit dependency for each of items #4, #5, #6, #8.

**Issue E: The KG refactor (#1) gates #5, #6, #7, #8.**

All of these items depend on the in-memory KG being the source of truth. If #1 slips, everything downstream is blocked. The spec's parallel structure (P1 and P2 items all depending on P0 KG) is correct in recognizing this, but the risk of a long critical path through #1 should be explicitly called out.

**Issue F: #4 (hybrid search) has a hidden dependency on LadybugDB features.**

Before implementing #4, verify:
1. LadybugDB's FTS extension exists and is loadable.
2. The FTS index has BM25 scoring (not just boolean match).
3. LadybugDB's vector index supports KNN queries (needed for efficient semantic search, separate from the brute-force cosine scan currently used).

If any of these are false, the implementation plan for #4 needs to change.

---

## 5. Additional observations

### 5.1 Provenance field naming

The current schema uses `provenance` (on Calls, BridgesTo), while the spec introduces `reason` as a new field. In GitNexus, edges have `confidence` and `reason` (not `provenance`). Orchard already has `provenance`. The spec should clarify whether `reason` replaces `provenance` or supplements it. Using both `provenance` and `reason` on the same edge is redundant -- they serve the same purpose.

### 5.2 Embedding dimension mismatch

The 6-24 spec and the current `schema.py` say `FLOAT[768]` (qwen3-embedding:0.6b). The 6-25 spec uses `FLOAT[1024]` in its Chunk definition (see the schema.py snippet in the review input). These must be consistent. Currently `schema.py` uses `FLOAT[1024]` while the 6-24 spec says 768. The actual embedding dimension depends on the Ollama model -- either is fine, but the schema and embedder must agree.

### 5.3 Test coverage gap

The spec does not mention test files. The current orchard codebase has no `tests/` directory populated (though the 6-24 spec reserves space for it). Each new item should include corresponding test files:
- `tests/test_graph/test_knowledge_graph.py`
- `tests/test_pipeline/test_runner_dag.py`
- `tests/test_search/test_hybrid_search.py`
- `tests/test_ingest/test_process_processor.py`
- `tests/test_ingest/test_community_processor.py`
- `tests/test_incremental/test_shadow.py`

### 5.4 Apple-specific algorithm gaps

The spec borrows 8 patterns from a JS/TS code knowledge graph system. Two patterns need significant Apple-specific redesign:

1. **Shadow candidates** (#7): JS module resolution (extension priorities, directory-index patterns) does not map to Apple's module system. Apple uses frameworks, modules (`@import`), bridging headers, and umbrella headers. The shadow logic needs a complete redesign for `.swift`/`.m`/`.h` files in an Xcode build context.

2. **Process detection boundary** (#5): JS code typically stays within the project boundary. Apple code invariably calls into massive system frameworks (UIKit, SwiftUI, Foundation, AppKit). BFS without framework boundaries will produce useless traces. Need to add: (a) detect framework boundary symbols, (b) stop tracing at framework boundaries, (c) label processes by the framework entry points they reach.

---

## Summary

| Question | Answer |
|---|---|
| File scopes correct? | **No**. Directory name mismatch (`ingestion/` vs `ingest/`). 5 files missing from the Changed table. File names don't match GitNexus references. Cross-target DB undefined. |
| Implementation order feasible? | **Partially**. Items #1-#4 order is OK. Item #8 must come before #5. Item #6 needs prerequisite infrastructure. |
| Riskiest items? | **#6 Contract Extractor** (underspecified, missing infra), **#7 Incremental Indexing** (JS-specific algorithm must be redesigned for Apple), **#1 In-memory KG** (touches every write path, performance risk). |
| Dependency issues? | **Yes**. Communities must precede processes. Contract extractor needs cross-target DB. KG refactor gates 4 downstream items. Hybrid search needs LadybugDB feature verification. MCP tool registration is an implicit dependency for all P1/P2 items. |

**Overall development readiness**: 2/5. The spec identifies the right patterns to borrow, but needs: (a) directory naming resolution, (b) item #5/#8 reordering, (c) a sub-spec for contract extraction, (d) Apple-specific algorithm adaptations for incremental indexing and process detection, and (e) LadybugDB FTS/vector index capability verification before committing to #4.
