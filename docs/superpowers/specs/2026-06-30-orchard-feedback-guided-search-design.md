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
    "coverage": "partial"
  },
  "matches": [],
  "diag": [
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
    {"tool": "text_search", "args": {"pattern": "process_msg"}}
  ]
}
```

### Response Field Semantics

- `query`
  Records the raw input and how Orchard interpreted it, such as `symbol`, `owner`, `qualified_symbol`, `frame`, or `stack_text`
- `status`
  Provides the shortest reliable summary of the lookup result and the confidence boundary of the index coverage
- `matches`
  Holds actual resolved hits only
- `diag`
  Contains short diagnostic codes instead of verbose prose
- `candidates`
  Contains a small number of high-value fallbacks, grouped by how they should be used
- `next`
  Contains executable next actions instead of free-form suggestions

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

These codes are intentionally compact so agents can branch on them without paying for verbose text.

## Tool Behavior

### `orchard_search` Behavior

`orchard_search` should follow this rough decision path:

1. Classify the input query kind
2. Attempt exact or current direct symbol lookup
3. If exact hits exist, return `match` or `ambiguous`
4. If exact hits do not exist, generate a small set of high-value candidates
5. Evaluate coverage signals
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
6. If that fails, evaluate text fallback and coverage signals
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
- text fallback candidates should be included only when graph candidates are weak or absent

## Recommended Next-Step Routing

`next` should behave like a tiny routing program.

Priority order:

1. If exact matches exist, return graph-native follow-ups such as `orchard_find_callers`
2. If strong owner or qualified candidates exist, recommend another Orchard search step
3. If coverage appears partial or uncovered, recommend coverage-aware follow-up before concluding absence
4. Only recommend text fallback when graph-based next steps are exhausted

Example:

```json
[
  {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
  {"tool": "orchard_search", "args": {"name": "process_msg", "kind": "method"}},
  {"tool": "text_search", "args": {"pattern": "process_msg"}}
]
```

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

## Phase 1 Implementation Scope

Phase 1 should be intentionally narrow.

### Included

- Replace the current `orchard_search` response model with the new compact schema
- Add `orchard_lookup_frame`
- Add a shared decision layer for status, diagnostics, candidates, and next-step routing
- Reuse existing graph lookup, audit, freshness, and minimal text fallback signals to drive coverage judgments

### Deferred

- Full crashlog semantic analysis
- Rich multi-frame prioritization and business-frame scoring
- Deep C++ overload and template discoverability improvements
- Advanced fuzzy ranking infrastructure
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

## Risks

- Over-eager diagnostics could make miss explanations sound more certain than the evidence allows
- Weak fuzzy matching could lower information density by flooding candidates
- Coverage heuristics could be brittle if they depend on signals not consistently present in all databases
- A direct schema replacement means downstream users must update their expectations promptly

## Recommended Rollout

1. Refactor `orchard_search` to return the new response model
2. Add `orchard_lookup_frame` using shared diagnostics and routing logic
3. Wire in conservative coverage signals from audit, freshness, owner hits, and limited text fallback
4. Expand C++ and discoverability behavior only after the miss-path is measurably better

## Success Criteria

The design succeeds if Orchard:

- no longer returns opaque empty search results for common miss cases
- gives agents concise, reliable next actions
- lets crash investigators paste a frame and receive a useful guided path
- helps users distinguish between "not found in code" and "not confidently covered by the current index"

## Open Migration Note

This design intentionally chooses direct schema replacement for `orchard_search` rather than a compatibility-preserving `v2` path. That keeps the surface cleaner, but it should be treated as an explicit breaking change during implementation planning.
