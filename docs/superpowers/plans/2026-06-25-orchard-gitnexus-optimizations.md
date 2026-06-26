# GitNexus Patterns → orchard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execution mode: subagent-driven.

**Goal:** Implement 7 GitNexus-inspired patterns: confidence/reason on all edges, in-memory KG, pipeline DAG, RRF hybrid search, MRO stage, Leiden communities, process detection.

**Architecture:** Incremental refactoring — confidence/reason and KG self-contained, DAG touches runner, hybrid search extends existing embedder, MRO/communities/processes are new derive phases.

**Tech Stack:** Python 3.12, Ladybug/KuzuDB, pytest, dataclasses.

## Global Constraints

- 153 existing tests must stay green
- Follow orchard conventions: `ingest/` not `ingestion/`, `snake_case` filenames
- Schema changes use `IF NOT EXISTS` for idempotent `init_schema()`
- `pipeline/runner.py` backward compat: linear order when no `deps` declared
- Deferred: contract extractor (#8) — needs more Apple-specific design

---

### Task 1: confidence + reason on all rel tables

**Files:** Modify `src/orchard/graph/schema.py`, `src/orchard/normalize/identity.py`
**Test:** `tests/test_graph/test_confidence.py`
**Risk:** Low. Additive schema change.

- [ ] Step 1: Write test — verify all rel tables have confidence/reason columns after init_schema
- [ ] Step 2: Run — expect FAIL for tables missing columns
- [ ] Step 3: Add `confidence DOUBLE DEFAULT 0.90` + `reason STRING` to Contains, Inherits, Implements, Extends, ConformsTo, BridgesTo, ViewTree, NavigationFlow (Calls already has confidence)
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Run full suite `uv run pytest tests/ -x -q`, expect 158 passed
- [ ] Step 6: Commit

---

### Task 2: In-Memory KnowledgeGraph

**Files:** Create `src/orchard/graph/knowledge_graph.py`
**Test:** `tests/test_graph/test_knowledge_graph.py`
**Risk:** Medium. New module, no existing code touched.

- [ ] Step 1: Write tests — add_node, add_rel, remove_node, remove_nodes_by_file, iter_rels_by_type, freeze, flush draft
- [ ] Step 2: Run — expect FAIL
- [ ] Step 3: Implement `KnowledgeGraph` with `node_map: dict[str, dict]`, `rel_map: dict[str, dict]`, `rels_by_type: dict[str, dict[str, dict]]` (type→id→rel for O(1) delete), `edge_ids_by_node: dict[str, set]`, `node_ids_by_file: dict[str, set]`, `_frozen: bool`
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Full suite — 165 passed
- [ ] Step 6: Commit

---

### Task 3: Pipeline DAG + Kahn Topological Sort

**Files:** Create `src/orchard/pipeline/phase.py`, `src/orchard/pipeline/registry.py`; Modify `src/orchard/pipeline/runner.py`
**Test:** `tests/test_pipeline/test_phase.py`, `tests/test_pipeline/test_kahn.py`
**Risk:** HIGH — all runner tests affected.

- [ ] Step 1: Write tests — PipelinePhase protocol, Kahn sort (linear/diamond/parallel/cycle detection), enabledWhen predicate
- [ ] Step 2: Run new tests — expect FAIL
- [ ] Step 3: Implement phase protocol, registry with enabledWhen, Kahn runner in runner.py (preserve existing `run_ingest_pipeline()` as linear fallback when no deps declared)
- [ ] Step 4: Run new tests — expect PASS
- [ ] Step 5: Full suite — verify all existing pipeline tests still pass. If test_runner fails, fix backward compat.
- [ ] Step 6: Commit

---

### Task 4: RRF Hybrid Search

**Files:** Create `src/orchard/search/hybrid_search.py`; Modify `src/orchard/handlers/semantic_search.py`
**Test:** `tests/test_search/test_rrf.py`, `tests/test_search/test_hybrid_search.py`
**Risk:** Medium. FTS index may not be available.

- [ ] Step 1: Write tests — RRF algorithm correctness, FTS fallback, vector fallback, combined ranking
- [ ] Step 2: Run — expect FAIL
- [ ] Step 3: Implement `rrf_fuse(bm25_results, vector_results, k=60)`, `hybrid_search()`. Graceful degradation when FTS or embeddings unavailable (return available results).
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Full suite
- [ ] Step 6: Commit

---

### Task 5: MRO Stage

**Files:** Create `src/orchard/derive/mro.py`
**Test:** `tests/test_derive/test_mro.py`
**Risk:** Low. New derive phase.

- [ ] Step 1: Write tests — single inheritance chain, protocol conformance, diamond inheritance cycle guard
- [ ] Step 2: Run — expect FAIL
- [ ] Step 3: Implement `run_mro(conn, target_id)` — walk Inherits + Implements edges, write METHOD_OVERRIDES edges, detect diamond cycles
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Full suite
- [ ] Step 6: Commit

---

### Task 6: Leiden Communities

**Files:** Create `src/orchard/derive/community_detection.py`; Modify `src/orchard/graph/schema.py` (Community node + MEMBER_OF edge)
**Test:** `tests/test_derive/test_community_detection.py`
**Risk:** Medium. Schema change, external algorithm.

- [ ] Step 1: Write tests — clustering on seeded graph, weighted edges, deterministic with fixed seed, labeling
- [ ] Step 2: Run — expect FAIL
- [ ] Step 3: Add Community node table + MEMBER_OF rel table to schema. Implement Leiden clustering using igraph/leidenalg. Write results to graph.
- [ ] Step 4: Run — expect PASS (skip if leidenalg not installed — fallback to label propagation)
- [ ] Step 5: Full suite
- [ ] Step 6: Commit

---

### Task 7: Process Detection

**Files:** Create `src/orchard/derive/process_detection.py`; Modify `src/orchard/graph/schema.py` (Process node + STEP_IN_PROCESS edge)
**Test:** `tests/test_derive/test_process_detection.py`
**Risk:** Medium. Schema change, BFS traversal.

- [ ] Step 1: Write tests — entry point detection, BFS forward trace, cycle handling, deduplication, labeling
- [ ] Step 2: Run — expect FAIL
- [ ] Step 3: Add Process node table + STEP_IN_PROCESS rel table. Implement `run_process_detection(conn, target_id)`: find entry points → BFS forward → group → label.
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Full suite
- [ ] Step 6: Commit

---

### Task 8: Integration — Wire new phases into pipeline

**Files:** Modify `src/orchard/pipeline/runner.py`, `src/orchard/server.py`
**Test:** `tests/test_acceptance/test_pipeline_gitnexus.py`
**Risk:** Medium. Integration.

- [ ] Step 1: Register MRO, communities, process detection as optional phases in registry. Wire hybrid search into semantic_search handler. Register new tools in MCP server.
- [ ] Step 2: Write acceptance test — full pipeline with new phases enabled
- [ ] Step 3: Run full suite — verify all existing + new tests pass
- [ ] Step 4: Commit

---

## Execution Handoff

Execution mode: **subagent-driven**. Each task: write test → fail → implement → pass → verify full suite → commit.
