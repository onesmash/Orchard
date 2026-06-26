# Wiring Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Execution mode: subagent-driven.

**Goal:** Wire 4 already-implemented-but-unwired components into orchard's active code paths: subtype closure + freshness into impact_analysis, CrossLanguageName into bridge recovery. Add tests for unwired derive phases.

**Architecture:** Minimal integration — annotate don't filter (freshness), seed-before-BFS (subtype dedup), schema migration first (CrossLanguageName).

**Tech Stack:** Python 3.12, Ladybug/KuzuDB, pytest.

## Global Constraints
- 179 existing tests must stay green
- Freshness: annotate via open_gaps, NOT silent filter (design review: filtering shrinks d1_count, lowers risk misleadingly)
- Subtype closure: seed visited_ids BEFORE BFS to avoid duplicate conformer counting
- Subtype closure: include Implements edge (MRO writes overrides as Implements)
- file_path="" : skip mtime check, default up-to-date
- BuildSnapshot.created_at: ISO string → parse to epoch for freshness comparison
- BridgesTo IF NOT EXISTS: column additions need DB rebuild to take effect

---

### Task 1: subtype closure wired into impact_analysis

**Files:** Modify `src/orchard/handlers/impact.py`
**Test:** `tests/test_handlers/test_impact_subtype_wiring.py`
**Risk:** Medium. Changes impact response (adds subtype dependents to d1).

- [ ] Step 1: Write test — seed protocol P + conformers A, B; impact(P) d1 must include A, B with reached_via="subtype_closure"; verify no duplicate when A also calls P
- [ ] Step 2: Run — expect FAIL (subtypes not in result)
- [ ] Step 3: In `impact_analysis()`: after computing closure via `_subtype_closure(conn, req.usr)`, seed those USRs into `visited_ids` BEFORE BFS loop; look up symbol metadata; append to depths["d1"] with reached_via="subtype_closure". Also add "Implements" to _subtype_closure edge list.
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Full suite — verify existing impact tests still pass (existing tests use no-inheritance graphs, so subtypes empty → no change)
- [ ] Step 6: Commit

---

### Task 2: freshness annotation wired into impact_analysis

**Files:** Modify `src/orchard/handlers/impact.py`
**Test:** `tests/test_handlers/test_impact_freshness_wiring.py`
**Risk:** Medium. Uses open_gaps annotation, not filtering.

- [ ] Step 1: Write test — impact on graph with stale file_path entries; verify open_gaps mentions stale entries (NOT that they're filtered out)
- [ ] Step 2: Run — expect FAIL
- [ ] Step 3: In `impact_analysis()`: after building depths, collect file_paths of d1 dependents; for each non-empty path, check `IndexOutOfDateChecker(MODIFIED_FILES).is_up_to_date()`; if stale, append to open_gaps as "dependent X in file Y may be stale". Skip empty file_path (default up-to-date).
- [ ] Step 4: Run — expect PASS
- [ ] Step 5: Full suite
- [ ] Step 6: Commit

---

### Task 3: BridgesTo schema migration + CrossLanguageName fill

**Files:** Modify `src/orchard/graph/schema.py`, `src/orchard/derive/bridge.py`, `src/orchard/handlers/bridges.py`
**Test:** `tests/test_derive/test_bridge_cross_language_fill.py`
**Risk:** Medium. Schema + fill logic.

- [ ] Step 1: Write test — seed objc method symbol + swift pair; run_bridge_recovery; verify BridgesTo edge has clang_name + swift_name + definition_language populated; get_cross_language_bridges returns them
- [ ] Step 2: Run — expect FAIL (columns missing)
- [ ] Step 3: Add `clang_name STRING, swift_name STRING, definition_language STRING` to BridgesTo in schema.py. Note: requires DB rebuild for existing DBs.
- [ ] Step 4: In `run_bridge_recovery()`: when writing BridgesTo edge, compute clang_name/swift_name from symbol language + name (ObjC: -[Cls method:]/+[Cls method:], need container lookup via Contains; Swift: Cls.method(_:)). definition_language = symbol's language.
- [ ] Step 5: Update handlers/bridges.py to read and return clang_name/swift_name/definition_language
- [ ] Step 6: Run — expect PASS; Full suite
- [ ] Step 7: Commit

---

### Task 4: Tests for unwired derive phases (community + process)

**Files:** Create `tests/test_derive/test_community_detection.py`, `tests/test_derive/test_process_detection.py`
**Risk:** Low. Test-only.

- [ ] Step 1: Write community_detection test — seed connected symbol cluster; run_community_detection; verify Community nodes created, MEMBER_OF edges for cluster members (size >= 3)
- [ ] Step 2: Write process_detection test — seed entry point (no callers) + callees; run_process_detection; verify Process node + STEP_IN_PROCESS edges
- [ ] Step 3: Run — expect PASS (functions already implemented)
- [ ] Step 4: Full suite
- [ ] Step 5: Commit

---

## Execution Handoff

Execution mode: **subagent-driven**. Each task: test → fail → implement → pass → full suite → commit.
