# Orchard GitNexus Pattern Optimizations

> Revised after 3-perspective review. Original 8 patterns → 7 (removed incremental indexing: GitNexus does full rebuilds, not incremental). Added MRO stage. Fixed naming (ingest/ not ingestion/, underscores not hyphens). Reordered communities before processes.

## P0 — Architecture Foundation

### 1. Per-Edge confidence + reason (`src/orchard/graph/schema.py`)
**Review:** Dev ✅ | Design ✅ | Test ✅ (11 cases)
- Add `confidence DOUBLE DEFAULT 0.90` + `reason STRING` to remaining rel tables lacking them
- IndexStore edges: confidence=0.90, reason="indexstore"
- Bridge edges: confidence=0.70/0.85, reason="derive/bridge"
- SwiftUI-derived: confidence=0.80, reason="derive/swiftui"
- Backward compat: existing queries without confidence filter still work

### 2. Pipeline DAG + Kahn Topological Sort (`src/orchard/pipeline/`)
**Review:** Dev ⚠️ (runner name conflict) | Design ⚠️ | Test ⚠️ (17 cases, HIGH risk to test_runner.py)
- **New file `pipeline/phase.py`**: `PipelinePhase` protocol with `name`, `deps`, `execute(ctx, deps) -> T`
- **New file `pipeline/registry.py`**: `enabledWhen` predicate for conditional phases
- **Modify `pipeline/runner.py`**: Kahn topological sort replacing fixed order
- Fallback: linear order preserved when no `deps` declared (backward compat)

### 3. In-Memory KnowledgeGraph (`src/orchard/graph/knowledge_graph.py`)
**Review:** Dev ⚠️ (touches all write paths) | Design ⚠️ (missing public API) | Test ✅ (11 cases)
- `KnowledgeGraph` class: `node_map`, `rel_map`, `rels_by_type` (dict of id→Rel for O(1) delete), `edge_ids_by_node`, `node_ids_by_file`
- Methods: `add_node()`, `add_rel()`, `remove_node()`, `remove_nodes_by_file()`, `iter_rels_by_type()`
- SemanticModel: mutable → `freeze()` → read-only (inspired by GitNexus)
- Flush to LadybugDB via COPY FROM in final phase

## P1 — Query & Analysis

### 4. RRF Hybrid Search (`src/orchard/search/hybrid_search.py`)
**Review:** Dev ⚠️ (verify LadybugDB FTS) | Design ✅ | Test ✅ (14 cases)
- BM25 via LadybugDB FTS + embedding vector cosine
- Reciprocal Rank Fusion K=60: `score = sum(1 / (K + rank + 1))`
- Falls back gracefully when FTS or embeddings unavailable
- Integrate into `semantic_search` handler

### 5. MRO Stage (`src/orchard/derive/mro.py`)
**Review:** Design (recommended as prerequisite) | Test ✅
- **Prerequisite for communities and processes**
- Compute method override chains: walk Inherits + Implements edges
- Write `METHOD_OVERRIDES` edges for correct dispatch resolution
- Guard against diamond inheritance cycles

### 6. Leiden Communities (`src/orchard/derive/community_detection.py`)
**Review:** Dev ⚠️ (must precede processes) | Design ✅ | Test ✅ (12 cases)
- **Moved before processes** (communities feed process labeling)
- Leiden algorithm on symbol co-occurrence graph
- Write `Community` nodes + `MEMBER_OF` edges
- Auto-discover functional domains (Auth, Meeting, Chat, etc.)

### 7. Process Detection (`src/orchard/derive/process_detection.py`)
**Review:** Dev ✅ | Design ✅ | Test ✅ (15 cases)
- Find entry points (functions with no internal callers)
- BFS forward through Calls edges
- Group similar paths, deduplicate
- Write `Process` nodes + `STEP_IN_PROCESS` edges
- Consumes Community nodes for cross-community labeling

## P2 — Polish

### 8. Contract Extractor (`src/orchard/derive/contract_extractor.py`)
**Review:** Dev ⚠️ (underspecified, Apple-adapt) | Design ⚠️ (reinterpretation) | Test ✅ (11 cases)
- **Scope reduced**: extract public API method signatures + protocol conformances from IndexStore
- Normalize contract IDs for cross-target matching (within same orchard DB)
- Deferred: cross-repo bridging (needs multi-DB infrastructure)

## Implementation Order (revised)

| Phase | Items | Risk | Schema Change |
|-------|-------|------|---------------|
| 1 | #1 confidence/reason columns | Low | Yes (additive) |
| 2 | #3 In-memory KG | High | No (refactor) |
| 3 | #2 Pipeline DAG | High (test_runner) | No |
| 4 | #4 RRF hybrid search | Medium | No (FTS index) |
| 5 | #5 MRO stage | Low | Yes (METHOD_OVERRIDES) |
| 6 | #6 Leiden communities | Medium | Yes (Community nodes) |
| 7 | #7 Process detection | Medium | Yes (Process nodes) |
| 8 | #8 Contract extractor | High | No |

## Files Changed
| File | Changes |
|------|---------|
| `src/orchard/graph/schema.py` | #1 confidence/reason columns |
| `src/orchard/graph/knowledge_graph.py` | #3 In-memory KG |
| `src/orchard/pipeline/phase.py` | #2 PipelinePhase protocol |
| `src/orchard/pipeline/registry.py` | #2 enabledWhen + phase registry |
| `src/orchard/pipeline/runner.py` | #2 Kahn topological sort |
| `src/orchard/search/hybrid_search.py` | #4 RRF fusion |
| `src/orchard/derive/mro.py` | #5 MRO computation |
| `src/orchard/derive/community_detection.py` | #6 Leiden communities |
| `src/orchard/derive/process_detection.py` | #7 Process detection |
| `src/orchard/derive/contract_extractor.py` | #8 Contract extraction |
| `src/orchard/handlers/semantic_search.py` | #4 hybrid search integration |
| `src/orchard/server.py` | #4, #7 tool registration |
