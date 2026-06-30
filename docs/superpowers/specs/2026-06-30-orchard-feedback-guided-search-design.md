# Orchard Feedback Guided Search Design

## Context

This design responds to the issues captured in [orchard-feedback.md](/Users/hui.xu/SourceCode/orchard2/orchard-feedback.md), especially the weak miss-path during crash investigation:

- zero-result responses are too opaque
- failed symbol lookups do not guide the next step
- crash frames require manual translation into Orchard-friendly queries
- index coverage state is not visible enough
- default responses can waste agent context with low-signal output

The goal of this design is not to rebuild Orchard search from scratch. The goal is to make Orchard more useful when a query misses, while preserving the fast path for known indexed symbols.

## Goals

- Make zero-result responses diagnosable instead of opaque
- Improve agent usefulness by returning compact, high-density, machine-actionable output
- Support crash-debugging workflows directly from stack-frame text
- Distinguish between "not found", "not confidently covered", and "likely outside index coverage"
- Keep the first phase small enough to implement without reworking the full indexing pipeline

## Non-Goals

- Full crashlog semantic analysis in phase 1
- Rebuilding C++ symbol indexing quality end to end in phase 1
- Long natural-language explanations in default MCP responses
- Backward compatibility with the current `orchard_search` response shape

## Design Principles

- `orchard_search` is for symbol-intent lookup
- `orchard_lookup_frame` is for translating stack-frame text into an executable search path
- Default responses optimize for information density, not prose
- Miss-path behavior should route users and agents toward the next most likely successful action
- Coverage judgments must be evidence-based and conservative
- Freshness and degraded-mode signals should be surfaced explicitly instead of being hidden behind empty results

## Borrowed Patterns From GitNexus

This design intentionally borrows several product patterns from GitNexus because they improve agent reliability without requiring Orchard to copy GitNexus's full ingestion architecture.

- Treat stale index state as a first-class user-visible signal, not hidden implementation detail
- Treat degraded search capability as a diagnosable mode with a repair action
- Return the next most useful tool action directly, instead of assuming the agent will infer the workflow
- Prefer owner, qualified-name, and scope-aware candidates over raw fuzzy string dumps
- Use documentation, tool descriptions, and lightweight resources to reinforce the recommended tool sequence

Orchard should borrow these patterns at the workflow and response-shape level. It should not attempt to replicate GitNexus's full multi-stage scope-resolution system in phase 1.

## Tool Surface

### `orchard_search`

`orchard_search` remains the main search entry point and is upgraded to the new response model.

Supported query intent includes:

- bare symbol names such as `process_msg`
- owner or type names such as `thread_wrapper_t`
- namespace-qualified or partially qualified names such as `ssb::thread_wrapper_t`
- filtered symbol lookup using fields like `module`, `kind`, `language`, or `file`

It does not attempt full crash-frame parsing. If the input strongly resembles a stack frame, it should return a routing diagnosis and recommend `orchard_lookup_frame`.

### `orchard_lookup_frame`

`orchard_lookup_frame` is a new tool for crash and stack-driven debugging workflows.

Its responsibility is to accept stack-frame-like input and convert it into a structured lookup sequence. Typical inputs include:

- a single frame such as `ssb::thread_wrapper_t::process_msg(unsigned int)`
- lightweight multi-line stack text, where phase 1 extracts and evaluates the top parseable frames

The tool is not a general crashlog analyzer in phase 1. It is a frame-oriented routing and diagnostics layer.

## Unified Response Model

Both tools share the same compact top-level schema:

```json
{
  "query": {
    "raw": "process_msg",
    "kind": "symbol"
  },
  "status": {
    "outcome": "no_match",
    "coverage": "partial",
    "freshness": "stale"
  },
  "matches": [],
  "diag": [
    "index_stale",
    "owner_search_recommended",
    "target_coverage_incomplete"
  ],
  "candidates": {
    "symbols": [],
    "owners": ["thread_wrapper_t"],
    "text": ["process_msg"]
  },
  "next": [
    {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
    {"tool": "shell_text_search", "args": {"pattern": "process_msg"}}
  ]
}
```

### Response Field Semantics

- `query`
  Records the raw input and how Orchard interpreted it, such as `symbol`, `owner`, `qualified_symbol`, `frame`, or `stack_text`
- `status`
  Provides the shortest reliable summary of the lookup result, index coverage boundary, and freshness state
- `matches`
  Holds actual resolved hits only
- `diag`
  Contains short diagnostic codes instead of verbose prose
- `candidates`
  Contains a small number of high-value fallbacks, grouped by how they should be used
- `next`
  Contains executable next actions instead of free-form suggestions

### `next` Action Contract

`next` must only contain actions from a small, explicit set so downstream agents do not have to guess what is executable.

Phase 1 action kinds:

- Orchard MCP tool call
  Example: `{"tool": "orchard_search", "args": {...}}`
- Orchard maintenance action
  Example: `{"tool": "orchard_refresh_index", "args": {}}`
- Explicit shell fallback action
  Example: `{"tool": "shell_text_search", "args": {"pattern": "process_msg"}}`

Phase 1 should not emit vague action names that have no defined executor.
If Orchard does not own a refresh tool in phase 1, the spec or implementation plan must define the exact maintenance action shape and execution owner instead of implying one.

### Phase 1 Maintenance Action Decision

For phase 1, `orchard_refresh_index` should be treated as a maintenance action contract, not as a new Orchard MCP tool.

That means:

- the response model may recommend `{"tool": "orchard_refresh_index", "args": {}}`
- the execution owner is the agent or client integration layer
- the implementation plan must map this action to the concrete Orchard refresh command already supported by the project

This keeps phase 1 narrow. Orchard can add a first-class MCP maintenance tool later if refresh orchestration needs to move into the server boundary.

## High-Density Response Rules

The response model is intentionally optimized for agent context efficiency.

- Prefer short stable codes over repeated natural-language explanations
- Limit each candidate bucket to 3 items by default and 5 at most
- Limit `next` to 1 to 3 actions
- Return no long explanatory paragraph by default
- Use richer prose only in optional debug or human-facing modes in the future

This keeps Orchard useful inside MCP agent loops where every extra token has opportunity cost.

## Status Model

### `status.outcome`

Allowed values:

- `match`
- `ambiguous`
- `near_match`
- `no_match`
- `parse_failed`

Meaning:

- `match` means Orchard found a sufficiently direct result
- `ambiguous` means Orchard found plausible matches but the user or agent should disambiguate
- `near_match` means there was no exact hit, but there are high-value candidates
- `no_match` means no acceptable match was found
- `parse_failed` means the frame or stack input could not be reliably interpreted

### `status.coverage`

Allowed values:

- `covered`
- `partial`
- `uncovered`
- `unknown`

Meaning:

- `covered` means the result or miss is supported by enough graph evidence to trust the lookup boundary
- `partial` means the graph contains relevant evidence, but the query boundary may extend beyond confidently indexed scope
- `uncovered` means there is strong evidence the query likely falls outside current indexed targets or modules
- `unknown` means Orchard cannot make a reliable coverage judgment

### `status.freshness`

Allowed values:

- `fresh`
- `stale`
- `partially_stale`
- `unknown`

Meaning:

- `fresh` means the current build snapshot is suitable for trusting lookup results
- `stale` means the indexed snapshot is known to lag the working tree or build context and the miss-path should not be over-trusted
- `partially_stale` means some supporting data is present but freshness is not consistent enough to treat the result as hard evidence
- `unknown` means Orchard cannot confidently determine freshness

This field is separate from `coverage`. A query may be well-covered but stale, or fresh but uncovered.

### Freshness Decision Order

Phase 1 should determine `status.freshness` from a fixed evidence order:

1. If the active build or snapshot metadata explicitly says the queried view is stale, return `stale`
2. If freshness metadata is present but indicates mixed or incomplete validity, return `partially_stale`
3. If freshness metadata is present and valid for the current queried view, return `fresh`
4. If Orchard cannot map the query to reliable freshness metadata, return `unknown`

This is intentionally narrower than GitNexus-style git `HEAD` drift. Orchard should use only Orchard-owned freshness/build evidence in phase 1.

## Diagnostic Codes

Phase 1 should use a small, stable diagnostic vocabulary:

- `case_mismatch_possible`
- `owner_search_recommended`
- `namespace_search_recommended`
- `text_fallback_recommended`
- `overload_disambiguation_needed`
- `module_filter_recommended`
- `symbol_may_be_unindexed`
- `frame_lookup_recommended`
- `frame_outside_index_scope`
- `cpp_symbol_lookup_weak`
- `target_coverage_incomplete`
- `index_stale`
- `freshness_unknown`
- `repair_index_recommended`

These codes are intentionally compact so agents can branch on them without paying for verbose text.

## Tool Behavior

### `orchard_search` Behavior

`orchard_search` should follow this rough decision path:

1. Classify the input query kind
2. Attempt exact or current direct symbol lookup
3. If exact hits exist, return `match` or `ambiguous`
4. If exact hits do not exist, generate a small set of high-value candidates
5. Evaluate coverage and freshness signals
6. Return `near_match` or `no_match` with diagnostics and executable next steps

If the input looks like a full frame signature, the tool should avoid overly clever partial parsing and instead return:

- `diag=["frame_lookup_recommended"]`
- `next=[{"tool": "orchard_lookup_frame", ...}]`

### `orchard_lookup_frame` Behavior

`orchard_lookup_frame` should follow a frame-aware fallback chain:

1. Parse stack text into one or more candidate frames
2. Extract namespace, owner, symbol, and parameter text when possible
3. Attempt exact qualified-symbol lookup
4. If that fails, attempt owner lookup
5. If that fails, attempt bare symbol lookup
6. If that fails, evaluate text fallback, coverage, and freshness signals
7. Return compact diagnostics and the next highest-value action

This turns crash frames into a guided path instead of forcing users to manually translate stack text into several separate Orchard queries.

## Candidate Generation Strategy

Candidates should be grouped by intended next action, not by a generic similarity list.

### Candidate Buckets

- `symbols`
  Near symbol matches that are worth trying directly
- `owners`
  Types, classes, containers, or namespaces likely to narrow the search
- `text`
  Raw fallback patterns suitable for grep-like search outside the symbol graph
- `frames`
  Only for `orchard_lookup_frame`, representing parseable frame candidates from stack text

### Ranking Rules

Phase 1 ranking should prioritize actionability over theoretical similarity:

- owner or type candidates are often more valuable than weak fuzzy symbol matches
- exact-prefix or qualified candidates outrank loose substring matches
- scope-aware or container-backed candidates outrank isolated bare-name similarity
- qualified-name candidates should preserve deterministic tie-breaks so the same input yields the same top suggestion
- text fallback candidates should be included only when graph candidates are weak or absent

Inspiration: GitNexus gets strong results from a dedicated qualified-name index and deterministic tie-break logic. Orchard does not need that full subsystem in phase 1, but it should adopt the same principle: candidate ordering should reflect ownership, qualification, and stable ranking evidence rather than simple substring order.

### Phase 1 Deterministic Tie-Breaks

To keep implementation bounded, phase 1 tie-breaks should use only evidence Orchard already has or can derive cheaply from the current lookup flow.

Recommended order:

1. exact qualified-name match
2. exact bare-name match
3. owner or container match
4. exact case-preserving match over case-insensitive match
5. explicit module or language filter match
6. prefix match over substring match
7. stable lexical fallback such as `(module, kind, name, usr)`

Phase 1 must not require a new general-purpose resolver or a GitNexus-style multi-stage scope engine. If these signals are insufficient, Orchard should return fewer candidates rather than invent weaker ranking evidence.

## Recommended Next-Step Routing

`next` should behave like a tiny routing program.

Priority order:

1. If exact matches exist, return graph-native follow-ups such as `orchard_find_callers`
2. If strong owner or qualified candidates exist, recommend another Orchard search step
3. If freshness is stale or unknown, recommend re-index or freshness validation before concluding absence
4. If coverage appears partial or uncovered, recommend coverage-aware follow-up before concluding absence
5. Only recommend text fallback when graph-based next steps are exhausted

Example:

```json
[
  {"tool": "orchard_search", "args": {"name": "process_msg", "kind": "method"}},
  {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
  {"tool": "shell_text_search", "args": {"pattern": "process_msg"}}
]
```

When freshness is the dominant risk, `next` should prefer an Orchard-native repair or refresh action over deeper fallback search. This mirrors the GitNexus pattern of treating stale or degraded index state as a first-order explanation for confusing misses.

In phase 1, that repair or refresh action should resolve to the maintenance action contract above rather than forcing Orchard to introduce a new MCP tool immediately.

## Coverage Determination

Coverage must be conservative and evidence-based. Phase 1 should use only a small number of explicit signals.

### `covered`

Use when graph evidence strongly supports that Orchard searched the relevant indexed scope.

Examples:

- exact symbol or owner hits exist in the relevant namespace or module
- related symbols are clearly present in the graph even if this exact variant is not

### `partial`

Use when the graph contains related evidence but the query boundary may extend beyond confidently indexed scope.

Examples:

- owner exists but method does not
- namespace exists but the specific overload does not
- relevant modules exist but target boundaries are unclear

### `uncovered`

Use only when there is strong structured evidence the query likely points outside indexed coverage.

Examples:

- coverage or audit data show the relevant target or module is absent
- Orchard graph lacks the relevant target family while external signals indicate the code likely exists

### `unknown`

Use when Orchard cannot make a reliable judgment.

Examples:

- input is too short or too generic
- frame parsing failed
- index coverage metadata is insufficient

`not_indexed` should not be a top-level coverage state in phase 1. It is better represented as a diagnostic code such as `symbol_may_be_unindexed`.

## Freshness And Degraded Modes

In addition to coverage, Orchard should surface whether the current lookup is being performed against trustworthy index state.

### Freshness signals

Phase 1 should reuse existing freshness/build metadata and collapse it into compact response state:

- known current snapshot matches the active build context
- snapshot is older than the current working tree or active build
- freshness cannot be established reliably

The default response should not dump raw freshness internals. It should emit a short `status.freshness` value plus one or more diagnostics such as `index_stale` or `repair_index_recommended`.

Phase 1 should define freshness using Orchard's existing snapshot/build metadata only. It should not infer freshness from unrelated git state unless a later design explicitly adds that contract.

### Degraded modes

Orchard should also make degraded search capabilities explicit when detected. Examples include:

- index exists but freshness is stale
- graph lookup path is available but supporting search indexes are incomplete
- symbol-level lookup is weak for the current language pattern, especially C++ or template-heavy input

The key behavior is borrowed from GitNexus: degraded capability should become part of the response contract, not an invisible implementation footnote.

## Agent-Facing Workflow Surface

The response schema alone is not enough. Orchard should also reinforce the intended workflow in the same places agents actually read:

- MCP tool descriptions should describe when to use `orchard_search` versus `orchard_lookup_frame`
- lightweight resources or setup text should point agents to check freshness before over-trusting misses
- AGENTS guidance should recommend the standard sequence for crash investigation and miss-path follow-up

This is a direct lesson from GitNexus, whose tool descriptions, skills, and context resources all reinforce the same graph-assisted workflow.

## Phase 1 Implementation Scope

Phase 1 should be intentionally narrow.

### Included

- Replace the current `orchard_search` response model with the new compact schema
- Add `orchard_lookup_frame`
- Add a shared decision layer for status, diagnostics, candidates, and next-step routing
- Reuse existing graph lookup, audit, freshness, and minimal text fallback signals to drive coverage judgments
- Add explicit freshness and degraded-mode signaling to the default response contract
- Define a bounded `next` action contract so executable follow-ups are unambiguous
- Use a minimal deterministic tie-break set for candidate ordering
- Update tool descriptions and setup guidance so agents are nudged toward the intended miss-path workflow
- Keep refresh as an agent-executed maintenance action in phase 1 rather than expanding the MCP surface

### Deferred

- Full crashlog semantic analysis
- Rich multi-frame prioritization and business-frame scoring
- Deep C++ overload and template discoverability improvements
- Advanced fuzzy ranking infrastructure
- Automatic hook-based context augmentation similar to GitNexus
- A first-class Orchard MCP refresh or repair tool
- Backward compatibility adapters for the old search schema

## Testing Strategy

The tests should validate usefulness, not just structure.

### Core Scenarios

1. Zero-result diagnostics
   A failed lookup must return stable `status`, `diag`, and `next`, not just an empty match list
2. Owner fallback
   A miss like `process_msg` should prefer owner or container suggestions when available
3. Frame lookup
   A frame such as `ssb::thread_wrapper_t::process_msg(unsigned int)` should parse into a layered lookup path
4. Coverage states
   The test suite should exercise `covered`, `partial`, `uncovered`, and `unknown`
5. Information-density guardrails
   Default responses should remain compact, with bounded candidate lists and no long prose
6. Executable next steps
   Every `next` action should map to a concrete tool invocation pattern
7. Freshness and degraded diagnostics
   Stale or degraded states must produce explicit compact diagnostics instead of being folded into generic misses
8. Deterministic candidate ranking
   The same qualified or owner-backed query should produce a stable top-ranked candidate ordering

## Risks

- Over-eager diagnostics could make miss explanations sound more certain than the evidence allows
- Weak fuzzy matching could lower information density by flooding candidates
- Coverage heuristics could be brittle if they depend on signals not consistently present in all databases
- Freshness heuristics could be conflated with coverage unless the schema keeps them separate
- A direct schema replacement means downstream users must update their expectations promptly

## Recommended Rollout

1. Refactor `orchard_search` to return the new response model
2. Add explicit freshness and degraded-mode signaling
3. Add `orchard_lookup_frame` using shared diagnostics and routing logic
4. Wire in conservative coverage signals from audit, freshness, owner hits, and limited text fallback
5. Expand C++ and discoverability behavior only after the miss-path is measurably better

## Success Criteria

The design succeeds if Orchard:

- no longer returns opaque empty search results for common miss cases
- gives agents concise, reliable next actions
- lets crash investigators paste a frame and receive a useful guided path
- helps users distinguish between "not found in code" and "not confidently covered by the current index"

## Open Migration Note

This design intentionally chooses direct schema replacement for `orchard_search` rather than a compatibility-preserving `v2` path. That keeps the surface cleaner, but it should be treated as an explicit breaking change during implementation planning.
