# Orchard vs GitNexus ios-client Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable first-pass comparison kit for evaluating orchard vs GitNexus on `ios-client`, including scenario cards, a scoring sheet, an execution workflow, and a report template that turns results into orchard roadmap guidance.

**Architecture:** Treat the comparison as a documentation-and-artifact project rather than a code feature. Create a dedicated evaluation folder under `docs/` with four focused assets: scenario definitions, scoring schema, runbook, and results template. Keep the first pass intentionally small: eight scenarios, single-maintainer review, and a minimal verifiable evidence baseline grounded in `knowledge-base/` and known `file:line` truth anchors.

**Tech Stack:** Markdown, YAML frontmatter, CSV, GitNexus MCP tooling, orchard MCP tooling, shell utilities (`rg`, `sed`, `git`).

## Global Constraints

- Reference repository is `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client`.
- Comparison must preserve the spec's two-layer model: `general code intelligence` and `Apple / iOS semantics`.
- First pass must use eight scenarios total across the five scenario families defined in the spec.
- Every scenario must include `task`, `target`, `expected_evidence`, `reference_truth`, and `expected_difficulty`.
- Every scored result must cover `task completion`, `semantic correctness`, `explainability`, and `interaction cost`.
- Score encoding is fixed: `success = 2`, `partial = 1`, `fail = 0`.
- Failure tags must be selected only from: `index_coverage`, `symbol_identity`, `cross_language_bridge`, `call_graph_precision`, `process_extraction`, `community_quality`, `retrieval_ranking`, `explanation_gap`, `interaction_cost`, `apple_specific_semantics`.
- First-pass truth sources should stay lightweight: `knowledge-base/`, known `file:line` anchors, and maintainer-accepted tracing paths.
- Result output must always be written in three layers: `scenario-level findings`, `capability gap model`, and `orchard roadmap guidance`.

---

## File Structure

- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md`
  Purpose: top-level index for the comparison kit, scope, artifact list, and rerun instructions.
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/scenarios.md`
  Purpose: the eight task cards with truth anchors and expected difficulty notes.
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/score-sheet.csv`
  Purpose: one row per tool per scenario with fixed scoring columns and failure tags.
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/runbook.md`
  Purpose: step-by-step execution workflow for running the comparison consistently.
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/report-template.md`
  Purpose: fill-in template for the first comparison write-up in the required three-layer structure.
- Modify: `docs/superpowers/specs/2026-06-26-orchard-vs-gitnexus-ios-client-design.md`
  Purpose: add a short "Implemented By" link to the evaluation kit after artifacts exist.

### Task 1: Create Evaluation Kit Skeleton

**Files:**
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md`
- Test: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-06-26-orchard-vs-gitnexus-ios-client-design.md`
- Produces: evaluation kit root directory and index document used by every later task

- [ ] **Step 1: Create the evaluation directory and write the README skeleton**

```markdown
---
title: "orchard vs GitNexus ios-client Evaluation Kit"
---

# orchard vs GitNexus ios-client Evaluation Kit

## Purpose

This folder contains the reusable artifacts for comparing orchard and GitNexus on `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client`.

## Artifacts

- `scenarios.md` — first-pass scenario cards
- `score-sheet.csv` — per-tool scoring log
- `runbook.md` — execution instructions
- `report-template.md` — final comparison write-up template

## Scope

- first pass only
- eight scenarios
- single-maintainer scoring
- two-layer comparison model: general code intelligence plus Apple / iOS semantics
```

- [ ] **Step 2: Verify the README file exists**

Run: `test -f docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md && echo OK`
Expected: `OK`

- [ ] **Step 3: Review the README for spec alignment**

Run: `sed -n '1,120p' docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md`
Expected: mentions `ios-client`, `eight scenarios`, and the four artifact files

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md
git commit -m "docs: add orchard vs gitnexus eval kit skeleton"
```

### Task 2: Author the Eight Scenario Cards

**Files:**
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/scenarios.md`
- Test: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/scenarios.md`

**Interfaces:**
- Consumes: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/README.md`
- Produces: eight scenario cards with `task`, `target`, `expected_evidence`, `reference_truth`, `expected_difficulty`

- [ ] **Step 1: Draft the scenario page header and family table**

```markdown
# Scenario Cards

## Coverage Summary

| Family | Scenario IDs |
|--------|--------------|
| Code Location | S1, S2 |
| Impact Analysis | S3, S4 |
| Apple Semantic Accuracy | S5, S6 |
| Flow Understanding | S7, S8 |

## Scenario Card Format

- `task`
- `target`
- `expected_evidence`
- `reference_truth`
- `expected_difficulty`
```

- [ ] **Step 2: Write the first four scenario cards**

```markdown
### S1: Find login flow entry points
- `task`: Find the main login flow entry points in ios-client.
- `target`: Login and authentication startup path.
- `expected_evidence`: At least one concrete entry symbol plus the next two meaningful hops.
- `reference_truth`: `knowledge-base/components/login-and-authentication.md` and cited `file:line` anchors.
- `expected_difficulty`: Retrieval may find auth-adjacent helpers but miss the actual entry path.

### S2: Find meeting join / rejoin control points
- `task`: Locate the main control points for meeting join or rejoin.
- `target`: Meeting join and scene recovery entry logic.
- `expected_evidence`: A concrete control symbol and at least one related scene-recovery or transition symbol.
- `reference_truth`: `knowledge-base/components/meeting-join-and-scene-recovery.md`.
- `expected_difficulty`: Query terms overlap with generic meeting code and can produce noisy results.

### S3: Estimate impact of changing a login-state decision
- `task`: Estimate what breaks if a login-state decision point changes.
- `target`: A login-state branching symbol chosen from the knowledge-base truth anchors.
- `expected_evidence`: Direct callers or impacted flows plus evidence for why they are linked.
- `reference_truth`: `knowledge-base/components/login-and-authentication.md` and manual source inspection.
- `expected_difficulty`: False-positive callers and missing upstream explanation are both likely.

### S4: Estimate impact of changing a meeting lifecycle method
- `task`: Estimate what breaks if a meeting lifecycle method changes.
- `target`: A lifecycle method referenced by meeting-core or scene-recovery documentation.
- `expected_evidence`: Direct dependents, affected flows, and at least one meeting-related path explanation.
- `reference_truth`: `knowledge-base/components/meeting-core.md` and `knowledge-base/components/meeting-join-and-scene-recovery.md`.
- `expected_difficulty`: Framework callbacks and app lifecycle boundaries can distort impact results.
```

- [ ] **Step 3: Write the last four scenario cards**

```markdown
### S5: Resolve protocol implementation and override chains
- `task`: Resolve a protocol implementation path and any relevant override chain.
- `target`: One protocol-backed service or controller path in ios-client.
- `expected_evidence`: Protocol symbol, implementation symbol, and override or dispatch relation if present.
- `reference_truth`: Maintainer-verified source path plus any matching knowledge-base citations.
- `expected_difficulty`: Generic call graphs often flatten protocol dispatch semantics.

### S6: Resolve a Swift / Objective-C bridge identity
- `task`: Connect one Swift-facing symbol with its Objective-C or bridge-side identity.
- `target`: A cross-language symbol pair known to the maintainer.
- `expected_evidence`: Both symbol identities plus evidence that they refer to the same conceptual target.
- `reference_truth`: Maintainer-approved source anchors and bridging-related source files.
- `expected_difficulty`: Text matching may find both names without proving semantic identity.

### S7: Trace cold start to login screen
- `task`: Explain the flow from app cold start to login screen presentation.
- `target`: Startup and scene lifecycle path ending at login UI.
- `expected_evidence`: Entry point, at least three meaningful intermediate steps, and a recognizable terminal UI handoff.
- `reference_truth`: `knowledge-base/architecture/app-startup-and-scene-lifecycle.md` plus login page citations.
- `expected_difficulty`: Process extraction may stop too early or spill into framework internals.

### S8: Trace notification-driven meeting scene recovery
- `task`: Explain how a notification or external event restores a meeting scene.
- `target`: Notification-driven meeting recovery path.
- `expected_evidence`: Entry trigger, recovery control point, and at least one meeting-scene restoration handoff.
- `reference_truth`: `knowledge-base/components/meeting-join-and-scene-recovery.md`, `knowledge-base/components/apns-and-im-notifications.md`.
- `expected_difficulty`: Cross-subsystem transitions make process grouping and explanation harder.
```

- [ ] **Step 4: Verify all eight scenario IDs exist**

Run: `rg -n "^### S[1-8]:" docs/superpowers/evals/orchard-vs-gitnexus-ios-client/scenarios.md | wc -l`
Expected: `8`

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/evals/orchard-vs-gitnexus-ios-client/scenarios.md
git commit -m "docs: define orchard vs gitnexus scenario cards"
```

### Task 3: Build the Scoring Sheet

**Files:**
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/score-sheet.csv`
- Test: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/score-sheet.csv`

**Interfaces:**
- Consumes: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/scenarios.md`
- Produces: reusable row-based scoring log for both tools across eight scenarios

- [ ] **Step 1: Create the CSV header with fixed scoring columns**

```csv
scenario_id,tool,task_completion_score,task_completion_notes,semantic_correctness_score,semantic_correctness_notes,explainability_score,explainability_notes,interaction_cost_score,interaction_cost_notes,failure_tags,evidence_links,overall_verdict
```

- [ ] **Step 2: Pre-populate one blank row per tool per scenario**

```csv
S1,orchard,,,,,,,,,,
S1,gitnexus,,,,,,,,,,
S2,orchard,,,,,,,,,,
S2,gitnexus,,,,,,,,,,
S3,orchard,,,,,,,,,,
S3,gitnexus,,,,,,,,,,
S4,orchard,,,,,,,,,,
S4,gitnexus,,,,,,,,,,
S5,orchard,,,,,,,,,,
S5,gitnexus,,,,,,,,,,
S6,orchard,,,,,,,,,,
S6,gitnexus,,,,,,,,,,
S7,orchard,,,,,,,,,,
S7,gitnexus,,,,,,,,,,
S8,orchard,,,,,,,,,,
S8,gitnexus,,,,,,,,,,
```

- [ ] **Step 3: Verify row count and header shape**

Run: `python3 - <<'PY'\nimport csv\nfrom pathlib import Path\npath = Path('docs/superpowers/evals/orchard-vs-gitnexus-ios-client/score-sheet.csv')\nrows = list(csv.reader(path.open()))\nprint(len(rows), len(rows[0]))\nPY`
Expected: `17 13`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/evals/orchard-vs-gitnexus-ios-client/score-sheet.csv
git commit -m "docs: add orchard vs gitnexus scoring sheet"
```

### Task 4: Write the Execution Runbook

**Files:**
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/runbook.md`
- Test: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/runbook.md`

**Interfaces:**
- Consumes: `scenarios.md`, `score-sheet.csv`
- Produces: exact run order, query discipline, evidence logging rules, and aggregation workflow

- [ ] **Step 1: Write the runbook header and setup section**

```markdown
# Runbook

## Before You Start

1. Confirm the `ios-client` knowledge base exists and the reference pages still match the chosen scenarios.
2. Confirm `GitNexus` index metadata is readable in `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/.gitnexus/meta.json`.
3. Confirm orchard can query the same repository or an intentionally comparable graph snapshot.

## Run Order

1. S1-S2 code location
2. S3-S4 impact analysis
3. S5-S6 Apple semantic accuracy
4. S7-S8 flow understanding
```

- [ ] **Step 2: Add per-scenario execution rules**

```markdown
## Per-Scenario Rules

- Run both tools on the same task wording before editing the score sheet.
- Record raw query phrases used for each tool in the notes fields.
- Do not mark `success` unless the result surfaces the scenario's expected evidence.
- If a result is partially correct but missing grounding, tag both the score notes and the failure taxonomy.
- Prefer `knowledge-base/` citations, known `file:line` anchors, and maintainer-accepted traces as truth checks.
```

- [ ] **Step 3: Add aggregation and report handoff steps**

```markdown
## After All Scenarios

1. Group failures by taxonomy tag.
2. Summarize which scenario families orchard loses, ties, or wins.
3. Convert repeated failure tags into capability gaps.
4. Convert capability gaps into `directly borrow`, `Apple-specific rebuild`, or `explicitly not chased`.
5. Write the final results into `report-template.md`.
```

- [ ] **Step 4: Verify the runbook includes setup, run order, and aggregation**

Run: `rg -n "Before You Start|Run Order|Per-Scenario Rules|After All Scenarios" docs/superpowers/evals/orchard-vs-gitnexus-ios-client/runbook.md`
Expected: four matching section headings

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/evals/orchard-vs-gitnexus-ios-client/runbook.md
git commit -m "docs: add orchard vs gitnexus evaluation runbook"
```

### Task 5: Create the Comparison Report Template

**Files:**
- Create: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/report-template.md`
- Test: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/report-template.md`

**Interfaces:**
- Consumes: `score-sheet.csv`, `runbook.md`
- Produces: reusable write-up shell with scenario findings, capability model, and roadmap sections

- [ ] **Step 1: Write the report template header**

```markdown
# orchard vs GitNexus on ios-client: First-Pass Comparison

## Scope

- repository: `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client`
- pass: first-pass
- scenario count: 8
- reviewer model: single maintainer
```

- [ ] **Step 2: Add the scenario findings section**

```markdown
## 1. Scenario-Level Findings

| Scenario | orchard verdict | GitNexus verdict | Key evidence | Notes |
|----------|-----------------|------------------|--------------|-------|
| S1 |  |  |  |  |
| S2 |  |  |  |  |
| S3 |  |  |  |  |
| S4 |  |  |  |  |
| S5 |  |  |  |  |
| S6 |  |  |  |  |
| S7 |  |  |  |  |
| S8 |  |  |  |  |
```

- [ ] **Step 3: Add the capability and roadmap sections**

```markdown
## 2. Capability Gap Model

- strongest orchard scenarios:
- weakest orchard scenarios:
- repeated failure tags:
- capability-level interpretation:

## 3. Orchard Roadmap Guidance

### Directly Borrow

-

### Apple-Specific Rebuild

-

### Explicitly Not Chased

-
```

- [ ] **Step 4: Verify the template contains the required three-layer structure**

Run: `rg -n "^## 1\\. Scenario-Level Findings|^## 2\\. Capability Gap Model|^## 3\\. Orchard Roadmap Guidance" docs/superpowers/evals/orchard-vs-gitnexus-ios-client/report-template.md`
Expected: three matching headings

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/evals/orchard-vs-gitnexus-ios-client/report-template.md
git commit -m "docs: add orchard vs gitnexus report template"
```

### Task 6: Link the Artifacts Back to the Spec

**Files:**
- Modify: `docs/superpowers/specs/2026-06-26-orchard-vs-gitnexus-ios-client-design.md`
- Test: `docs/superpowers/specs/2026-06-26-orchard-vs-gitnexus-ios-client-design.md`

**Interfaces:**
- Consumes: all files created in Tasks 1-5
- Produces: discoverable handoff from approved design to executable evaluation kit

- [ ] **Step 1: Add an implementation link block near the end of the spec**

```markdown
## Implemented By

- [Evaluation Kit](../evals/orchard-vs-gitnexus-ios-client/README.md)
- [Scenario Cards](../evals/orchard-vs-gitnexus-ios-client/scenarios.md)
- [Score Sheet](../evals/orchard-vs-gitnexus-ios-client/score-sheet.csv)
- [Runbook](../evals/orchard-vs-gitnexus-ios-client/runbook.md)
- [Report Template](../evals/orchard-vs-gitnexus-ios-client/report-template.md)
```

- [ ] **Step 2: Verify every linked file exists**

Run: `for f in README.md scenarios.md score-sheet.csv runbook.md report-template.md; do test -f "docs/superpowers/evals/orchard-vs-gitnexus-ios-client/$f" || exit 1; done && echo OK`
Expected: `OK`

- [ ] **Step 3: Run a final grep-based completeness check**

Run: `rg -n "task completion|semantic correctness|explainability|interaction cost|Directly Borrow|Apple-Specific Rebuild|Explicitly Not Chased" docs/superpowers/evals/orchard-vs-gitnexus-ios-client docs/superpowers/specs/2026-06-26-orchard-vs-gitnexus-ios-client-design.md`
Expected: matches in the spec plus the evaluation artifacts

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-26-orchard-vs-gitnexus-ios-client-design.md \
        docs/superpowers/evals/orchard-vs-gitnexus-ios-client
git commit -m "docs: wire orchard vs gitnexus evaluation artifacts"
```

## Self-Review Checklist

- Spec coverage:
  - comparison scope is implemented by `README.md`
  - eight scenarios are implemented by `scenarios.md`
  - four scoring dimensions and failure taxonomy are implemented by `score-sheet.csv` and `runbook.md`
  - three-layer result output is implemented by `report-template.md`
  - design-to-artifact traceability is implemented by the final spec link block
- Placeholder scan:
  - no `TBD`, `TODO`, or "implement later" language is allowed in any created artifact
  - report template may contain blank bullets for later findings, but headings and structure must be complete
- Type consistency:
  - scenario IDs must stay `S1` through `S8` in both `scenarios.md` and `score-sheet.csv`
  - roadmap bucket names must stay exactly `Directly Borrow`, `Apple-Specific Rebuild`, and `Explicitly Not Chased`

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-orchard-vs-gitnexus-ios-client.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
