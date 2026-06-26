# Testing Perspective Review: Orchard GitNexus Optimizations

> Reviewed spec: `docs/superpowers/specs/2026-06-25-orchard-gitnexus-optimizations.md`
> Date: 2026-06-25
> Focus: Testability, regressions, edge cases, isolation, and organization

---

## Overview: Test Landscape Before Changes

The codebase already has solid test infrastructure:

| Area | Pattern | Example |
|------|---------|--------|
| Unit (DB seeding) | Create temp DB, seed Symbols/edges with raw Cypher, call function under test, assert | `test_derive/test_architecture.py` |
| Phase-level integration | Mock input I/O (`read_index_store`, `parse_symbolgraph`), run `run_ingest_pipeline()`, assert phase results | `test_pipeline/test_runner.py` |
| Acceptance (E2E) | Full feature combinations: seed -> derive -> handler -> assert | `test_acceptance_m3.py` |
| Handler (MCP) | Seed DB, call handler, assert response shape | `test_mcp/test_impact.py` |
| Real integration | Skip-if-ollama-not-available pattern | `test_search/test_ollama_integration.py` |
| Fixtures | `tmp_db_path` in `conftest.py` | All tests |

Key fixture: `tmp_db_path` (defined in `tests/conftest.py`) provides a `str` path to a temp SQLite/DB file.

---

## 1. Test Cases per Optimization

### #1: In-Memory KnowledgeGraph + Dual Indexing (`src/orchard/graph/knowledge_graph.py`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_kg_add_node` | Unit | `add_node()` inserts into `node_map`, `node_ids_by_file` correctly |
| `test_kg_add_edge` | Unit | `add_edge()` inserts into `rel_map`, `rels_by_type`, `edge_ids_by_node` |
| `test_kg_rollback_on_error` | Unit | Modify KG then call `rollback()` - verify state restored; count before/after |
| `test_kg_flush_to_ladybug` | Integration | Add nodes + edges to KG, call `flush()`, verify DB has all data; verify COPY FROM count matches |
| `test_kg_idempotent_flush` | Integration | `flush()` twice - second call should not duplicate entries (MERGE semantics) |
| `test_kg_phase_data_isolation` | Integration | Phase A writes to KG, Phase B reads from KG, assert B sees A's data (inter-phase sharing) |
| `test_kg_add_node_with_missing_file` | Edge | `file_path=None` or empty on node -> should not crash `node_ids_by_file` indexing |
| `test_kg_add_duplicate_node` | Edge | Adding same node twice (same ID) -> second call is no-op or updates (document behavior) |
| `test_kg_flush_empty_kg` | Edge | Flush with zero nodes/edges -> no error |
| `test_kg_large_batch_flush` | Edge | 10k+ nodes/edges -> verify COPY FROM batching works, no OOM |
| `test_kg_concurrent_access` | Edge | If KG is shared across async phases, verify no race conditions (likely needs lock) |

**Existing tests at risk:** None. This is a new class. The runner (`test_pipeline/test_runner.py`) will need updates to pass KG instance to phases instead of raw `conn`.

**Can test independently:** Yes. The in-memory KG is a pure data structure -- no LadybugDB dependency for unit tests. Only `flush()` requires an integration test with a real DB.

---

### #2: Per-Edge Confidence + Reason (`src/orchard/graph/schema.py`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_all_rel_tables_have_confidence_and_reason` | Unit | After `init_schema()`, query each rel table schema; assert `confidence DOUBLE` and `reason STRING` columns exist |
| `test_calls_edge_has_confidence` | Unit | Insert a Calls edge with confidence=0.90, read back, assert value preserved |
| `test_bridges_to_edge_has_reason` | Unit | Insert BridgesTo with reason='derive/bridge', read back, assert value preserved |
| `test_indexstore_confidence_default` | Integration | Full pipeline with real mocks: Calls edges from indexstore should have confidence=0.90, reason='indexstore' |
| `test_bridge_confidence_varies` | Integration | Bridge edges: name_match=0.70, usr_correlate=0.85 -- verify in DB after bridge recovery |
| `test_swiftui_confidence` | Integration | ViewTree/NavigationFlow edges should have confidence=0.80, reason='derive/swiftui' |
| `test_backward_compat_no_confidence` | Edge | Query edges inserted before schema migration -- should return NULL or 0.0. Define behavior. |
| `test_confidence_range` | Edge | Insert confidence=1.5 or -0.5 -- should validation reject or DB clamp? |
| `test_reason_length_limit` | Edge | Very long reason strings (10k chars) -- does DB/store have a length limit? |
| `test_null_confidence_in_query` | Edge | Handler queries that filter on confidence (e.g. >= 0.70) must handle NULL gracefully |
| `test_read_existing_edges_with_new_columns` | Integration | Pipeline reads previously-inserted edges (without confidence/reason) and re-processes -- verify no crash |

**Existing tests at risk:**
- **`test_schema.py::test_all_rel_tables_created`** -- lists expected rel tables. No schema change to table names, so this test should still pass unless DDL format changes.
- **`test_schema.py::test_init_schema_is_idempotent`** -- must still pass. Adding columns to existing tables must not break idempotency (use `IF NOT EXISTS` for new columns if LadybugDB supports it; otherwise alter-then-create pattern needs careful handling).
- **`test_derive/test_bridge.py`** -- asserts `r.confidence` values; already works because BridgesTo already has confidence. No change needed but must still pass.
- **All tests that do `CREATE ... [:Calls {source:'test', confidence:1.0}]`** -- need to add `reason` field if the new schema makes it required. Check if `reason` defaults to empty string or NULL, or requires explicit value.

**Risk level:** LOW as stated, but **schema migration in a file-based DB needs idempotent ALTER TABLE handling** -- LadybugDB may not support `ALTER TABLE IF NOT EXISTS ADD COLUMN`. Test this first.

**Can test independently:** Schema tests are fully independent (unit). Integration confidence tests can reuse existing `run_bridge_recovery` and `run_swiftui_derivation` patterns.

---

### #3: Pipeline DAG + Kahn Topological Sort (`src/orchard/pipeline/`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_phase_protocol_compliance` | Unit | Define a class adhering to `PipelinePhase` protocol -- verify type checker acceptance |
| `test_kahn_simple_linear` | Unit | Phases: A->B->C. Kahn sort returns [A, B, C] |
| `test_kahn_diamond_deps` | Unit | A->B, A->C, B->D, C->D. Valid ordering: A before [B,C], both before D (any interleave of B/C is fine) |
| `test_kahn_parallel_branches` | Unit | A->B, C->D (two independent chains). Order must respect each chain internally; inter-chain order is unspecified |
| `test_kahn_cycle_detection` | Unit | A->B, B->A. Raises `CycleDetectedError` with cycle path info |
| `test_kahn_self_loop` | Edge | A->A. Detected as cycle or rejected early |
| `test_kahn_missing_dep` | Edge | Phase declares dep on non-existent phase name -- raises `UnknownDependencyError` |
| `test_kahn_empty_graph` | Edge | Registry with zero phases -- returns empty list |
| `test_kahn_single_phase` | Edge | One phase with no deps -- returns [phase] |
| `test_enabled_when_true` | Unit | Phase with `enabledWhen=lambda ctx: True` -- included |
| `test_enabled_when_false` | Unit | Phase with `enabledWhen=lambda ctx: False` -- excluded from DAG; downstream deps that depend on it also excluded (or raise) |
| `test_enabled_when_missing` | Edge | Phase has no `enabledWhen` -- treated as always enabled |
| `test_runner_execute_order` | Integration | Run a DAG with 3+ phases, verify execution order matches topological sort by checking side-effect order (e.g., logging or DB writes) |
| `test_runner_phase_failure_rollback` | Integration | Phase B fails; already-completed Phase A's KG writes should be rolled back |
| `test_runner_stats_aggregation` | Integration | Each phase returns `PhaseResult` with stats; runner aggregates all stats |
| `test_runner_warning_collection` | Integration | Phases produce warnings; runner collects all warnings |
| `test_registry_backward_compat` | Integration | The existing pipeline phases (indexstore, symbolgraph, identity, bridge, embedding, calls, architecture, swiftui) must all be registered and produce same results as before when run through DAG runner |

**Existing tests at risk:**
- **`test_pipeline/test_runner.py::test_run_ingest_pipeline_returns_results`** -- asserts specific phase names exist. If phases are renamed or re-registered in `registry.py`, this test needs updating.
- **`test_pipeline/test_runner.py::test_pipeline_writes_calls_then_handlers_return_data`** -- asserts `calls_written == 1` and caller/callee queries work. Must still pass -- if DAG reordering changes the order of writes, the final DB state must be identical.
- **`test_pipeline/test_runner.py::test_pipeline_includes_bridge_recovery_phase`** -- asserts phase name `cross_language_bridge_recovery` exists. Must update if registry names change.
- **`test_pipeline/test_runner.py::test_pipeline_embedding_projection_handles_ollama_down`** -- asserts `embedding_projection` phase and `embedded == 0` with warning. Must still pass.
- **All acceptance tests (M0-M5)** -- run `run_ingest_pipeline()`. If the runner refactor changes the entry point signature or return type, these must be updated.
- **`test_ollama_integration.py::test_real_ollama_embedding_projection_pipeline_integration`** -- Same, depends on `run_ingest_pipeline`.

**Risk level:** MEDIUM as stated. This is the biggest refactor risk -- it changes how all phases are orchestrated.

**Can test independently:** Kahn sort is pure algorithm -- unit testable with no DB. The runner and registry need integration tests with mocks.

---

### #4: RRF Hybrid Search (`src/orchard/search/hybrid_search.py`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_rrf_two_lists_equal_length` | Unit | BM25 ranks [A, B, C], vector ranks [A, C, B]. RRF score(A) = 1/(60+1) + 1/(60+1) = 2/61. Verify ranking. |
| `test_rrf_one_list_empty` | Unit | BM25 has results, vector is empty. RRF uses only BM25 scores (vector contributes 0). Results = BM25 order. |
| `test_rrf_both_empty` | Edge | Both empty -> returns empty list |
| `test_rrf_different_lengths` | Unit | BM25 has 10 results, vector has 3. Missing items in shorter list get rank=infinity (score=0 from that list) |
| `test_rrf_k_value_sensitivity` | Edge | K=60 (default) vs K=0 vs K=1000. Document behavior for extreme K values |
| `test_rrf_rank_stability` | Edge | Repeat with same input -> same output (deterministic) |
| `test_hybrid_search_fts_only` | Integration | No embeddings available (FTS only) -> returns FTS results |
| `test_hybrid_search_vector_only` | Integration | FTS index unavailable, embeddings exist -> returns vector results |
| `test_hybrid_search_combined` | Integration | Both available -> merged ranking; verify top result is the intersection of both methods |
| `test_hybrid_search_fallback_on_fts_error` | Edge | FTS query fails (malformed query, index corruption) -> fallback to vector-only |
| `test_hybrid_search_fallback_on_vector_error` | Edge | Ollama unreachable -> fallback to FTS-only with warning |
| `test_semantic_search_uses_hybrid` | Integration | `semantic_search()` handler integrates with hybrid search -- existing handler tests should still pass with improved results |
| `test_hybrid_search_top_k_truncation` | Edge | Request top_k=3, get exactly 3 results even if combined list has more |
| `test_hybrid_search_fts_index_creation` | Integration | Verify FTS index is created on Chunk.content during init_schema() or first search |
| `test_hybrid_search_idempotent_index` | Edge | Create FTS index twice -> no error |

**Existing tests at risk:**
- **`test_mcp/test_semantic_search.py`** -- MCP handler test for semantic_search. If `semantic_search()` now uses hybrid search internally, the response format must not change (backward compatible). Scores may change (RRF vs pure FTS) -- if test asserts specific score ordering, may need updating.
- **`test_acceptance_m4.py::test_m4_semantic_search_fts_fallback`** -- explicitly tests FTS fallback path. Must still work, but may now go through hybrid search which delegates to FTS when embeddings unavailable. Assertions on results should still hold.
- **`test_search/test_ollama_integration.py::test_real_ollama_full_pipeline_embed_to_search`** -- assert `resp.data[0]['usr'] == 's:loadData'` and `score > 0.5`. RRF scores may differ in magnitude -- score threshold may need adjustment.

**Risk level:** MEDIUM as stated. Backward compat of `semantic_search` handler is the key risk.

**Can test independently:** RRF algorithm is pure math -- unit test with no DB. FTS + vector integration needs a real DB with FTS extension.

---

### #5: Process Detection (`src/orchard/ingestion/process-processor.py`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_find_entry_points_no_callers` | Unit | Seed symbols A, B, C. A calls B, C calls B. B has no internal callers. Entry points = [A, C] |
| `test_find_entry_points_all_called` | Edge | Everyone is called by someone -- no entry points (or external entry is the entry point) |
| `test_bfs_forward_linear` | Unit | A->B->C. BFS from A produces path [A, B, C] with STEP_IN_PROCESS edges |
| `test_bfs_forward_branching` | Unit | A calls both B and C. BFS produces two paths: A->B and A->C |
| `test_bfs_forward_merge` | Unit | A calls B, A calls C, both B and C call D. Paths merge at D |
| `test_bfs_max_depth` | Edge | Very deep call chain (100 levels) -- BFS must terminate or hit configured max_depth |
| `test_bfs_cycle_handling` | Edge | A->B->C->A (cycle). BFS must detect visited nodes and not loop infinitely |
| `test_bfs_cross_target_reference` | Edge | Entry point calls into external symbols (different target_id). Should external calls be followed or stop at target boundary? |
| `test_process_node_written` | Integration | After process detection, verify Process nodes exist in DB with correct label |
| `test_step_in_process_edge_written` | Integration | Verify STEP_IN_PROCESS edges exist with correct step numbers |
| `test_deduplicate_similar_paths` | Unit | Two entry points produce nearly identical call chains (differ by 1 leaf). Verify they are grouped as same Process. |
| `test_heuristic_label_generation` | Unit | Function names like 'authenticateUser', 'loginHandler' -> label 'Auth'. Verify naming heuristic. |
| `test_empty_graph` | Edge | No Calls edges -> zero processes written |
| `test_process_detection_idempotent` | Integration | Run twice -> second run writes zero new processes |
| `test_process_detection_with_bridge_edges` | Integration | Calls traverse through BridgesTo edges? If yes, verify cross-language process detection. If no, verify BridgesTo are not traversed. |

**Existing tests at risk:** Minimal -- this is a new feature. However:
- **Pipeline tests** that assert a fixed set of phase names will need to add `process_detection` phase.
- **Schema tests** (`test_all_node_tables_created`) will need Process nodes added to expected tables.

**Risk level:** MEDIUM as stated. New node type and edge type, schema change.

**Can test independently:** BFS + entry point detection is pure algorithm -- unit testable. Process node writing needs integration test with DB.

---

### #6: Contract Extractor (`src/orchard/ingestion/contract-extractor.py`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_extract_public_methods` | Unit | Symbol with kind='function', access_level='public' -> contract entry with method signature |
| `test_extract_protocol_conformance` | Unit | Class conforms to protocol -> contract for protocol methods |
| `test_extract_private_symbols_ignored` | Edge | access_level='private' or 'internal' -> NOT extracted |
| `test_normalize_contract_id` | Unit | Same method signature across targets -> same normalized contract ID |
| `test_normalize_contract_id_swift_objc` | Edge | Swift `func loadData() -> Data` and ObjC `-(NSData*)loadData` -> same normalized ID? Define behavior. |
| `test_cross_target_contract_db` | Integration | Two targets produce contracts; write to shared cross-target DB; verify both exist and normalized IDs match where expected |
| `test_contract_written_to_shared_db` | Integration | Contract entries written to shared DB (not per-target DB) |
| `test_contract_extraction_empty_target` | Edge | Target has zero public symbols -> zero contracts |
| `test_contract_extraction_generated_code` | Edge | is_generated=true symbols -- include or exclude? Document behavior. |
| `test_contract_extraction_idempotent` | Integration | Run twice -> second run zero new contracts |
| `test_contract_db_concurrent_write` | Edge | Two pipeline runs (different targets) write to same cross-target DB concurrently -> no corruption |

**Existing tests at risk:**
- Pipeline phase list tests need `contract_extraction` phase added.
- Cross-target DB is a new concept -- may need new fixture (`tmp_shared_db_path`).

**Risk level:** HIGH as stated. Cross-target shared DB introduces concurrency and file-locking concerns.

**Can test independently:** Contract normalization (ID generation) is pure unit testable. Extraction needs DB-seeded symbols (integration). Cross-target DB needs multiple pipeline run simulation (integration).

---

### #7: Incremental Indexing (`src/orchard/incremental/`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_shadow_candidates_git_diff` | Integration | Create git repo, touch 2 files, commit. Shadow_candidates returns those 2 files. |
| `test_shadow_candidates_no_changes` | Edge | No diff since last index -> empty candidate list |
| `test_shadow_candidates_deleted_files` | Edge | File deleted since last index -> included as candidate (symbols to remove) |
| `test_shadow_candidates_renamed_files` | Edge | File renamed -> both old path (removal) and new path (re-index) as candidates? |
| `test_shadow_candidates_new_untracked` | Edge | New untracked file -> should it be a candidate? |
| `test_subgraph_extract_affected_symbols` | Unit | Given changed file paths, extract all Symbol nodes with `file_path` matching, plus their 1-hop neighbors via Calls/References edges |
| `test_subgraph_extract_transitive_deps` | Unit | Option: extract N-hop neighbors. Verify depth parameter works. |
| `test_subgraph_extract_empty_changes` | Edge | No changed files -> empty subgraph |
| `test_incremental_remove_stale` | Integration | Remove old symbols + edges for changed files, keep rest of graph intact |
| `test_incremental_preserve_unchanged` | Integration | After incremental update, symbols from unchanged files still exist with same edges |
| `test_incremental_output_matches_full` | Integration | Run full pipeline, then incremental update on changed file. Resulting graph = running full pipeline from scratch on all files. |
| `test_subgraph_extract_cross_file_edges` | Edge | Symbol in changed file has Calls edge to symbol in unchanged file. That unchanged symbol should be in subgraph (or not?). Define behavior. |

**Existing tests at risk:**
- Pipeline runner tests -- incremental mode may need separate entry point or flag.
- Acceptance tests -- may need `incremental=True` variants.

**Risk level:** HIGH as stated. Correctness of incremental vs. full rebuild equivalence is hard to test and very important.

**Can test independently:** `shadow_candidates` needs a real git repo (can create with `git init` in tmp_path). `subgraph_extract` is algorithm unit-testable. Full incremental integration test needs a complete indexed DB as baseline.

---

### #8: Leiden Community Detection (`src/orchard/ingestion/community-processor.py`)

**What must be tested:**

| Test case | Type | Description |
|-----------|------|-------------|
| `test_leiden_simple_two_clusters` | Unit | Graph: {A,B,C} fully connected internally, {D,E,F} fully connected, one cross-edge C->D. Leiden produces 2 communities. |
| `test_leiden_single_component` | Edge | Fully connected graph -> 1 community |
| `test_leiden_isolated_nodes` | Edge | No edges -> each node in its own community, or all in one? |
| `test_leiden_weighted_edges` | Unit | Higher call count = stronger edge weight -> affects community assignment |
| `test_community_nodes_written` | Integration | After detection, verify Community nodes exist with heuristicLabel |
| `test_member_of_edges_written` | Integration | Verify MEMBER_OF edges from Symbol to Community |
| `test_community_labels_meaningful` | Integration | Community containing symbols from module 'AuthService', 'LoginManager' -> label contains 'Auth' |
| `test_leiden_deterministic` | Edge | Run twice on same graph -> same communities (Leiden has randomness but should be deterministic with fixed seed) |
| `test_leiden_on_empty_graph` | Edge | No Calls edges -> zero communities |
| `test_leiden_performance_large_graph` | Edge | 10k symbols, 50k edges -- should complete in reasonable time (< 30s) |
| `test_community_idempotent` | Integration | Run twice -> second run zero new communities |

**Existing tests at risk:**
- Pipeline phase list tests -- need `community_detection` phase added.
- Schema tests -- need Community node table added to expected list.
- MEMBER_OF must be added to expected rel tables in `test_all_rel_tables_created`.

**Risk level:** LOW as stated. Pure addition, no mutation of existing edges.

**Can test independently:** Leiden algorithm can be unit-tested with a mock graph. Community node writing needs integration test.

---

## 2. Existing Tests at Risk (Summary)

| Test file | At risk from | Severity | What to do |
|-----------|-------------|----------|------------|
| `test_graph/test_schema.py` | #2 (confidence/reason), #5 (Process nodes), #8 (Community nodes) | MEDIUM | Update expected table lists; add ALTER TABLE idempotency test |
| `test_pipeline/test_runner.py` | #3 (DAG runner) | HIGH | Most affected: runner signature changes, phase ordering, registry |
| `test_acceptance.py` (M0-M2) | #3 (DAG runner), #2 (confidence) | MEDIUM | Verify results are unchanged after DAG refactor |
| `test_acceptance_m3.py` | #2 (confidence in BridgesTo), #3 (DAG) | LOW | Already tests confidence; DAG refactor may change phase names |
| `test_acceptance_m4.py` | #3 (DAG), #4 (hybrid search in semantic_search) | MEDIUM | RRF scores may differ; assert counts not exact scores |
| `test_acceptance_m5.py` | #2 (swiftui confidence=0.80), #3 (DAG) | LOW | May need confidence assertions added |
| `test_mcp/test_semantic_search.py` | #4 (hybrid search) | MEDIUM | Response shape must stay same; scores may change |
| `test_search/test_ollama_integration.py` | #3 (DAG), #4 (hybrid) | LOW | Skip-if pattern protects against env changes; score thresholds may drift |
| `test_derive/test_bridge.py` | #2 (confidence on BridgesTo) | LOW | Already asserts confidence -- must still pass |
| `test_derive/test_swiftui.py` | #2 (confidence=0.80) | LOW | May need new assertions for confidence/reason |
| `test_derive/test_architecture.py` | None directly | NONE | No DependsOn schema changes in this spec |
| `test_validation/test_freshness*.py` | None directly | NONE | No freshness changes |

---

## 3. Edge Cases per Pattern

### Cross-cutting Edge Cases (apply to multiple optimizations)

| Edge case | Affected optimizations | Why it matters |
|-----------|----------------------|----------------|
| **Empty input** (zero symbols, zero edges, zero phases) | #1, #3, #4, #5, #6, #7, #8 | Every component must handle "nothing to do" gracefully |
| **Idempotency** (run twice, same result) | #1, #2, #5, #6, #7, #8 | Critical for re-indexing and pipeline re-runs |
| **Concurrent access** (two pipelines, shared DB/KG) | #1, #6, #7 | KG is in-memory (per-run safe), but cross-target DB (#6) and file-based DB (#7) need locking |
| **Large scale** (10k+ symbols, deep call chains, many files) | #1, #3, #4, #5, #7, #8 | Performance must not degrade to O(n^2) |
| **Schema evolution** (old DB with new code, new DB with old code) | #2, #5, #8 | Backward compat of DB files is essential -- users won't delete their graph DB |
| **Ollama unavailable** | #4 | FTS fallback must cover 100% of functionality |
| **Cross-target boundaries** (symbols in target A, edges crossing to target B) | #5, #6, #7 | Process detection and contract extraction must decide: follow cross-target or stop? |
| **Generated code** (is_generated=true symbols) | #5, #6, #7, #8 | Should generated symbols participate in process detection, contracts, communities? |
| **Error in one phase should not corrupt graph** | #1, #3 | KG rollback (#1) and DAG error handling (#3) are the safety net |

### Optimization-Specific Edge Cases

| Optimization | Edge case | Why tricky |
|-------------|-----------|------------|
| #1 KG | `file_path` collision: two modules have `/src/Util.swift` | `node_ids_by_file` indexing -- which wins? |
| #1 KG | COPY FROM vs. MERGE semantics | COPY FROM is fast but may not deduplicate; MERGE is slow but idempotent. Which to use? |
| #2 confidence | `confidence` on Contains (structural) edges | Contains edges (class contains method) are 100% certain from IndexStore. Should they have confidence=1.0? |
| #3 DAG | Phase declared but not registered | Should the runner warn, error, or silently skip? |
| #3 DAG | Phase A depends on Phase B, but B is disabled by `enabledWhen` | Should A also be disabled? Or should it error? |
| #4 RRF | FTS query syntax errors | User query contains FTS special characters (AND, OR, NOT, quotes) -- must sanitize or quote |
| #4 RRF | Embedding dimension mismatch | Model upgraded from 1024d to 2048d; old Chunks have 1024d. How to handle? |
| #5 Process | Recursive functions (A calls A) | Entry point detection: A has caller = A itself. Is A an entry point? |
| #5 Process | Override chains (subclass method overrides parent) | Should BFS follow method override hierarchy through Inherits edges? |
| #6 Contract | ObjC category methods | ObjC categories add methods to existing classes -- how to normalize contract IDs? |
| #6 Contract | Protocol extensions with default implementations | Swift protocol extensions provide default impls -- is the extension a contract or the protocol? |
| #7 Incremental | Rebase/merge changes many files | `shadow_candidates` from git diff may return hundreds of files -- should it fall back to full rebuild? |
| #7 Incremental | DB schema change between increments | New code expects `reason` column but old DB lacks it. Must handle migration. |
| #8 Leiden | Symbols with no module | Community labeling heuristic depends on module names -- what if module is empty/missing? |
| #8 Leiden | Cross-language communities | Swift and ObjC symbols should end up in same community if bridged -- verify community detection uses BridgesTo edges |

---

## 4. Independent vs. Integration Testing

### Can Be Tested Independently (Unit Tests, No DB Required)

| Component | Unit-testable? | Dependency |
|-----------|---------------|------------|
| `KnowledgeGraph` (in-memory data structure) | Yes | None (pure Python dicts) |
| `Kahn topological sort` | Yes | None (pure algorithm) |
| `PipelinePhase` protocol | Yes | None (type check) |
| `RRF score calculation` | Yes | None (pure math) |
| `Contract ID normalization` | Yes | None (string manipulation) |
| `BFS forward traversal (process detection)` | Yes | Mock graph (dict of lists) |
| `Process deduplication` | Yes | None (set comparison) |
| `Heuristic label generation` | Yes | None (string parsing) |
| `Leiden clustering` | Yes (with fixed seed) | NetworkX or igraph library |
| `Shadow candidates (with git repo)` | Yes (git init in tmp_path) | git CLI |
| `Subgraph extraction` | Yes | Mock graph |

### Require Integration Tests (Need Real DB or Multiple Components)

| Component | Why integration needed |
|-----------|----------------------|
| `KG.flush()` -> LadybugDB | Verifies COPY FROM writes correctly to real DB |
| Schema migration (ALTER TABLE add confidence/reason) | Must verify on real LadybugDB file |
| `run_ingest_pipeline()` with DAG runner | End-to-end phase execution |
| `semantic_search` with hybrid search | FTS index + vector search combined |
| Process node + STEP_IN_PROCESS edge writing | Cypher writes to real DB |
| Contract cross-target shared DB | Multiple pipeline writes to same DB |
| Incremental indexing: diff -> subgraph -> re-index -> verify = full | Must compare DB state against full rebuild |
| Community node + MEMBER_OF writing | Cypher writes to real DB |
| All existing acceptance tests (M0-M5) | Ensure no regression |

### Recommended Test Mix

For **low-risk** changes (#2 schema, #8 communities): 70% unit / 30% integration.

For **medium-risk** changes (#3 DAG, #4 RRF, #5 processes): 50% unit / 50% integration.

For **high-risk** changes (#1 KG, #6 contracts, #7 incremental): 30% unit / 70% integration.

---

## 5. Test File Organization Recommendations

### Proposed Directory Structure

```
tests/
├── conftest.py                          # Existing: tmp_db_path fixture
│                                        # ADD: tmp_git_repo fixture for #7
│                                        # ADD: tmp_shared_db fixture for #6
├── test_graph/
│   ├── test_schema.py                   # EXISTING: update for #2, #5, #8
│   └── test_knowledge_graph.py          # NEW: #1 unit tests
├── test_pipeline/
│   ├── test_runner.py                   # EXISTING: update for #3 DAG
│   ├── test_phase.py                    # NEW: #3 PipelinePhase protocol tests
│   ├── test_registry.py                 # NEW: #3 registry + enabledWhen tests
│   └── test_kahn.py                     # NEW: #3 Kahn topological sort tests
├── test_search/
│   ├── test_chunker.py                  # EXISTING
│   ├── test_embedder.py                 # EXISTING
│   ├── test_ollama_integration.py       # EXISTING: update for #4
│   ├── test_rrf.py                      # NEW: #4 RRF algorithm unit tests
│   └── test_hybrid_search.py            # NEW: #4 FTS + vector integration
├── test_derive/
│   ├── test_architecture.py             # EXISTING
│   ├── test_bridge.py                   # EXISTING: may add confidence assertions
│   ├── test_swiftui.py                  # EXISTING: may add confidence assertions
│   ├── test_bridge_cross_language.py    # EXISTING
│   ├── test_process_detection.py        # NEW: #5 unit + integration tests
│   ├── test_contract_extractor.py       # NEW: #6 unit + integration tests
│   └── test_community_detection.py      # NEW: #8 unit + integration tests
├── test_incremental/                    # NEW directory
│   ├── test_shadow_candidates.py        # NEW: #7 git diff detection
│   ├── test_subgraph.py                 # NEW: #7 subgraph extraction
│   └── test_incremental_pipeline.py     # NEW: #7 full incremental flow
├── test_mcp/                            # EXISTING: update semantic_search for #4
├── test_acceptance.py                   # EXISTING: M0-M2, verify no regression
├── test_acceptance_m3.py               # EXISTING: verify no regression
├── test_acceptance_m4.py               # EXISTING: update for hybrid search
├── test_acceptance_m5.py               # EXISTING: verify no regression
└── test_acceptance_m6.py               # NEW: acceptance for #5 + #8 combined
```

### Key Recommendations

1. **New fixtures in conftest.py:**
   - `tmp_git_repo` -- creates a git repo in tmp_path with a few committed files, returns path. Used by #7 tests.
   - `tmp_shared_db` -- creates a shared cross-target DB path. Used by #6 tests.
   - `seeded_kg` -- creates a KnowledgeGraph with known nodes + edges. Used by #1 unit tests.

2. **Test the Kahn sort before the runner.** Write `test_kahn.py` first. Once the topological sort is verified independently, update `test_runner.py` to use the new DAG runner, ensuring all existing assertions still pass. This is the safest refactor path.

3. **Schema migration tests go in `test_graph/test_schema.py`.** Add a test that creates a DB with the OLD schema (no confidence/reason columns in some rel tables), then runs `init_schema()` upgrade, then verifies the new columns exist. This catches migration bugs.

4. **Acceptance test for M6:** Combine process detection (#5) + community detection (#8) in one E2E test: seed calls, run both phases, query Process nodes, query Community nodes, verify processes and communities align (processes should stay within communities).

5. **Do NOT put RRF tests in `test_mcp/`.** RRF is algorithm logic, not an MCP handler. Keep in `test_search/test_rrf.py`. Only test the handler integration in `test_mcp/test_semantic_search.py`.

6. **Incremental indexing needs a dedicated acceptance test.** The equivalence property (incremental result == full rebuild result) is the single most important correctness property for #7. Write `test_incremental_pipeline.py::test_incremental_equals_full_rebuild`.

---

## Summary: Risk Prioritization for Testing

| Priority | Optimization | Why |
|----------|-------------|-----|
| **Critical** | #3 DAG Runner | Touches every phase; breaks all pipeline tests if wrong |
| **High** | #1 In-Memory KG | New data path; must not corrupt DB |
| **High** | #7 Incremental | Correctness equivalence hard to verify; git-dependent |
| **Medium** | #2 Confidence/Reason | Schema migration risks; backward compat |
| **Medium** | #4 RRF Hybrid Search | Changes search semantics; scores shift |
| **Medium** | #5 Process Detection | New node/edge types; algorithm correctness |
| **Medium** | #6 Contract Extractor | Cross-target DB concurrency; normalization |
| **Low** | #8 Leiden Communities | Pure addition; isolated new code path |

**Pre-implementation testing checklist:**
1. [ ] Run existing full test suite, record baseline (all pass/fail counts)
2. [ ] Write Kahn sort unit tests first (#3)
3. [ ] Add schema migration test for confidence/reason (#2) before altering DDL
4. [ ] Write KG unit tests (#1) before integrating KG into pipeline
5. [ ] Update pipeline runner tests (#3) with DAG runner, ensure same assertions pass
6. [ ] Add RRF unit tests (#4) independent of DB
7. [ ] Write process detection unit tests (#5) with mock graph
8. [ ] Write community detection unit tests (#8) with mock graph
9. [ ] Write incremental shadow_candidates tests (#7) with git repo fixture
10. [ ] After all unit tests pass, write integration tests for each optimization
11. [ ] Final: run full suite, compare against baseline, investigate any new failures
