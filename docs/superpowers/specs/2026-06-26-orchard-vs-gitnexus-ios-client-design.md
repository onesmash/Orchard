# Orchard vs GitNexus Comparison Design

## Summary

This design defines a scenario-driven comparison framework for evaluating `orchard` against `GitNexus`, using `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client` as the reference repository.

The goal is not to produce a shallow feature checklist or a single winner score. The goal is to answer a more useful maintainer question:

`On a real Apple-scale repository like ios-client, where is orchard already credible, where does it still fall short, and which gaps should drive the next orchard iterations?`

The framework therefore combines:

- a `general code intelligence layer`
- an `Apple / iOS semantic layer`
- `scenario-based evaluation`
- `lightweight but repeatable scoring`
- an output model that converts findings into an orchard roadmap

## Audience

Primary audience: orchard maintainer.

This document is intentionally optimized for architecture judgment, evaluation design, and prioritization. It does not attempt to serve as a cross-team presentation deck.

## Context

Two facts shape this design:

1. `GitNexus` is already proven on a large real repository and currently has a much richer graph at `ios-client` scale.
2. `orchard` should not be judged only by generic graph features, because its real opportunity is Apple-specific semantic correctness built from compiler artifacts such as IndexStore, SymbolGraph, and Swift interface data.

That means a fair comparison must avoid two traps:

- reducing the work to a feature matrix
- judging orchard only inside GitNexus's strongest problem frame

## Design Goals

This comparison design should:

- compare the two systems on real maintainer tasks
- separate generic capability gaps from Apple-specific semantic gaps
- produce evidence that is small enough to run in a first pass
- make repeated runs possible as orchard evolves
- end with prioritized orchard decisions, not just observations

## Non-Goals

This design does not aim to:

- produce a public benchmark
- create a full formal gold-standard graph for `ios-client`
- optimize for statistical significance in the first iteration
- require multiple reviewers before the first useful pass exists

## Comparison Model

The comparison uses a two-layer model.

### Layer 1: General Code Intelligence

This layer evaluates capabilities that should matter across most repositories:

- indexing inputs and structural coverage
- graph expressiveness
- symbol and relation grounding
- retrieval and navigation
- impact analysis
- process and community analysis
- result explainability
- interaction cost

### Layer 2: Apple / iOS Semantics

This layer isolates the dimensions where orchard may reasonably differentiate:

- Swift / Objective-C identity normalization
- extension ownership and type attachment
- protocol conformance and override chains
- compiler-artifact fusion from IndexStore, SymbolGraph, and Swift interfaces
- app lifecycle and framework-aware process tracing
- correctness at Apple semantic boundaries, not only graph density

## Evaluation Unit

The core evaluation unit is a `maintainer task scenario`.

We do not compare tools by menu items. We compare them by whether they help complete real work on `ios-client`.

Each scenario should be written in maintainer language rather than tool language. Example shapes:

- "Find the main login flow entry points."
- "Estimate what breaks if this meeting lifecycle method changes."
- "Trace how the app restores a meeting from a notification-driven entry."
- "Resolve a Swift / ObjC symbol relationship across bridging boundaries."

## Scenario Set

The first version should use five scenario families and a first-pass sample size of eight scenarios total.

### 1. Code Location

Purpose: evaluate whether a maintainer can find the right starting point quickly.

Candidate scenarios:

- find login flow entry points
- find meeting join / rejoin control points
- find APNS / PushKit related handlers

Primary capabilities under test:

- retrieval ranking
- symbol grounding
- first-hop navigation
- result organization

### 2. Impact Analysis

Purpose: evaluate whether changing a symbol can be reasoned about with confidence.

Candidate scenarios:

- change a login-state decision path
- change a meeting lifecycle method
- change a cross-layer service protocol

Primary capabilities under test:

- caller / callee graph quality
- upstream / downstream tracing
- dependency explainability
- confidence of results

### 3. Flow Understanding

Purpose: evaluate whether the system can explain how a feature runs end to end.

Candidate scenarios:

- cold start to login screen
- successful login to main UI
- notification or external event into meeting scene recovery

Primary capabilities under test:

- process extraction
- entry-point heuristics
- cross-module tracing
- framework boundary handling

### 4. Apple Semantic Accuracy

Purpose: evaluate whether Apple-specific semantics are modeled correctly.

Candidate scenarios:

- protocol implementation plus override resolution
- Swift / Objective-C bridge identity
- extension and conformance ownership
- SwiftUI / UIKit lifecycle linkage

Primary capabilities under test:

- symbol identity correctness
- cross-language bridge quality
- inheritance / conformance semantics
- lifecycle-aware semantic linking

### 5. Result Usability

Purpose: evaluate whether answers are directly consumable in engineering work.

This is recorded as a cross-cutting dimension inside every scenario rather than as a separate run queue.

Primary capabilities under test:

- jump-to-evidence quality
- explanation clarity
- noise level
- number of extra queries needed

## Scenario Card Format

Each scenario should be captured as a small task card with fixed fields:

- `task`: the maintainer-facing question
- `target`: the main object or subsystem in scope
- `expected_evidence`: what must be surfaced to count as success
- `reference_truth`: where the evaluator checks correctness
- `expected_difficulty`: why this scenario is likely to expose a gap

This keeps the first run focused and makes later reruns comparable.

## Scoring Model

Each scenario should be scored along four dimensions instead of one overall impression.

### 1. Task Completion

- `success`: enough signal to continue work without major manual reconstruction
- `partial`: key elements found, but significant manual stitching still required
- `fail`: misses the core target or produces misleading output

### 2. Semantic Correctness

Checks whether the answer is correct in engineering meaning, not only text relevance.

Key questions:

- did it land on the right symbol or relation
- did it model inheritance / conformance / bridge semantics correctly
- did it trace flows across meaningful boundaries without collapsing or exploding

### 3. Explainability

Checks whether a maintainer can understand why the answer was produced.

Key questions:

- can the result be traced back to specific symbols, files, or relations
- are conclusions auditable
- are process / community / impact outputs interpretable in engineering language

### 4. Interaction Cost

Checks how much effort it takes to get to a usable answer.

Key questions:

- how many queries were needed
- how much manual filtering was required
- how much prior repo knowledge was needed to ask the right question

### Score Encoding

Use a simple three-level encoding:

- `success = 2`
- `partial = 1`
- `fail = 0`

The purpose of the numeric mapping is aggregation, not leaderboard theater.

## Failure Taxonomy

Every partial or failed result should be tagged with one or more normalized causes:

- `index_coverage`
- `symbol_identity`
- `cross_language_bridge`
- `call_graph_precision`
- `process_extraction`
- `community_quality`
- `retrieval_ranking`
- `explanation_gap`
- `interaction_cost`
- `apple_specific_semantics`

This taxonomy is critical because orchard roadmap decisions should be driven by clustered failure causes rather than anecdotal examples.

## Evidence Baseline

The first pass should use a `minimal verifiable evidence set`, not a full gold-standard graph.

Recommended truth sources:

- existing `knowledge-base/` pages in `ios-client`
- known source `file:line` references
- trusted symbolic entry points already familiar to the maintainer
- manually accepted tracing paths for a small set of scenarios

This is sufficient for a useful first pass because the purpose is comparative diagnosis, not formal benchmark publication.

## First-Pass Execution Plan

To keep cost reasonable, the first run should use eight scenarios total.

Recommended order:

1. `code location`
2. `impact analysis`
3. `Apple semantic accuracy`
4. `flow understanding`

`result usability` should be scored inside each scenario rather than run separately.

The first pass should use single-maintainer judgment. Multi-reviewer validation can be added later only after the scenario definitions and score sheets prove stable.

## Output Structure

The comparison output should always be written in three layers.

### 1. Scenario-Level Findings

For each scenario:

- what each tool returned
- whether the task succeeded
- what evidence supported the judgment
- where the answer broke down

### 2. Capability Gap Model

Roll scenario findings upward into capability themes, such as:

- retrieval ranking quality
- process extraction reliability
- Apple semantic correctness
- explainability quality

This is where the comparison stops being anecdotal.

### 3. Orchard Roadmap Guidance

Route findings into three decision buckets.

#### Directly Borrow

Patterns GitNexus handles well and orchard should likely adopt with limited reinterpretation, such as:

- output organization for analytical results
- explainability conventions
- evaluation and failure categorization discipline

#### Apple-Specific Rebuild

Patterns whose intent is valid but whose implementation must be adapted to Apple semantics, such as:

- cross-language identity
- protocol / extension / override modeling
- lifecycle-aware process tracing
- compiler-artifact evidence fusion

#### Explicitly Not Chased

Capabilities that may be strong in GitNexus but are not worth near-term parity for orchard if they do not improve important Apple maintainer tasks.

This bucket prevents roadmap drift into feature-for-feature imitation.

## Decision Framework

The final comparison should not conclude with "which tool wins."

It should conclude with:

- where GitNexus remains the stronger general baseline on `ios-client`
- where orchard is already directionally promising
- which orchard gaps block task completion today
- which Apple-specific strengths could make orchard meaningfully better rather than merely similar

The implied roadmap should be grouped as:

- `must close now`
- `differentiate next`
- `observe and defer`

## Recommended First-Round Deliverables

The first execution of this design should produce:

1. a scenario list with eight task cards
2. a reusable score sheet
3. a comparison write-up in the three-layer output structure
4. an orchard roadmap summary grouped by failure clusters

## Why This Design

This design is recommended because it balances rigor and practicality:

- it is grounded in a real Apple repository
- it is fair to both tools without pretending they are identical systems
- it keeps first-pass evaluation lightweight
- it produces roadmap-grade conclusions rather than a decorative benchmark

## Open Follow-On

If the first pass is useful, the next design step should be a dedicated implementation plan for:

- scenario card authoring
- score sheet format
- execution workflow
- artifact storage for reruns

That next step should be handled separately from this design.

## Implemented By

- [Evaluation Kit](../evals/orchard-vs-gitnexus-ios-client/README.md)
- [Scenario Cards](../evals/orchard-vs-gitnexus-ios-client/scenarios.md)
- [Score Sheet](../evals/orchard-vs-gitnexus-ios-client/score-sheet.csv)
- [Runbook](../evals/orchard-vs-gitnexus-ios-client/runbook.md)
- [Report Template](../evals/orchard-vs-gitnexus-ios-client/report-template.md)
