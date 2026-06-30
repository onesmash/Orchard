# Orchard Crash Boundary Refactor Design

## Context

The existing crash-thread direction for Orchard drifted beyond Orchard's real
capability boundary. Orchard is a compiler-indexed semantic graph for Apple
codebases. It can enrich known symbol identity evidence with graph context, but
it should not parse full crashlogs, interpret exception sections, infer UIKit
delegate selectors, rank likely crash causes, or select the first business frame
from a crashed thread.

The feedback in `orchard-feedback.md` showed this clearly: the useful Orchard
parts were index freshness, exact symbol lookup, references, and caller/callee
context. The misleading part was treating a crashed-thread block as something
Orchard could summarize into a root-cause-oriented path.

This refactor removes the crash-thread abstraction and keeps only deterministic
single-frame symbol resolution.

## Goals

- Delete `orchard_lookup_crash_thread` as a public capability.
- Preserve `orchard_lookup_frame` for one frame-like symbol text only.
- Make Orchard guidance describe graph enrichment, not crash analysis.
- Remove root-cause-ish fields and language from code, tests, README, setup
  guidance, and the Orchard skill.
- Add tests that prevent the crash-thread workflow from reappearing.

## Non-Goals

- No full crashlog parser.
- No `Application Specific Information` parser.
- No exception-reason entity extraction.
- No ARM64 register interpretation.
- No UIKit delegate selector inference.
- No likelihood ranking or root-cause candidate selection.
- No compatibility wrapper for `orchard_lookup_crash_thread`.

## Core Boundary

Orchard accepts explicit symbol identity evidence and returns compiler-indexed
graph context.

Accepted inputs:

- USR
- symbol name
- owner-qualified symbol name
- a single frame-like symbol string

Rejected inputs:

- full crashlog
- crashed-thread block
- multi-line stack text requiring frame selection
- exception reason text requiring semantic interpretation

If a user has a full crashlog, the caller or outer agent must choose a concrete
frame, symbol name, qualified name, or USR before calling Orchard.

## Public Tool Surface

### Remove `orchard_lookup_crash_thread`

Remove:

- MCP tool registration from `src/orchard/server.py`
- dispatcher entry from `src/orchard/server.py`
- implementation function from `src/orchard/query/frame_lookup.py`
- any CLI or MCP tests that assert the tool exists
- README / setup / skill guidance that recommends pasted crashed-thread blocks

No replacement crash-level Orchard tool should be added.

### Keep `orchard_lookup_frame`

`orchard_lookup_frame` remains, but its contract is narrower:

- input is one frame-like symbol text
- output is parsed frame fields plus graph matches
- no multi-line frame selection
- no thread boundary summary
- no first business frame
- no register semantics
- no root-cause or likelihood language

If input contains multiple non-empty lines, `lookup_frame` should reject it with
a compact parse failure such as `diag: ["input_too_broad"]`.

## Response Shape

`lookup_frame` should keep compact, graph-oriented output. Exact field names may
follow the existing response contract, but fields should express evidence and
matching, not diagnosis.

Allowed concepts:

- `query`
- `status`
- `matches`
- `candidates`
- `caller_summary`
- `match_basis`
- `source_scope`
- `freshness`
- `diag`

Forbidden concepts in frame lookup output:

- `first_indexed_symbol`
- `business_first_frame`
- `thread_boundaries`
- `dispatch_boundaries`
- `likely_fault`
- `root_cause`
- `register_semantics`
- frame selection across a multi-line thread

`match_basis` may describe how a graph match was found, for example:

- `exact_owner_symbol`
- `bare_symbol_match`
- `owner_only`
- `no_match`

It must not imply crash causality.

## Data Flow

1. The outer caller owns crashlog understanding.
   If the source input is a full crashlog, the caller extracts a specific frame,
   symbol name, qualified name, or USR outside Orchard.

2. `orchard_lookup_frame` receives one frame-like string.
   It mechanically parses owner, symbol, qualified name, signature, and optional
   source-file hint when those fields are present in the input.

3. Orchard enriches the parsed identity with graph evidence.
   It performs owner and method lookup, source-scope annotation, direct caller
   lookup, and freshness reporting.

4. The caller decides the debugging path.
   Orchard may return graph-native follow-up identifiers, such as matched USRs.
   It must not rank crash hypotheses or choose a root-cause path.

## Error Handling

- Multi-line input: return parse failure with `input_too_broad`; do not choose a
  line.
- Full crashlog-looking input: same as multi-line input. Do not parse sections.
- Ambiguous method matches: return candidates and `match_basis`; do not select a
  business frame.
- No graph match: return parsed fields, empty matches, freshness, and coverage.
- Stale or unknown freshness: keep existing freshness reporting.

## Documentation And Guidance Updates

Update `_ORCHARD_BLOCK` in `src/orchard/setup.py`:

- remove `crash triage` as a project-level Orchard capability
- remove `orchard_lookup_crash_thread`
- remove guidance about pasted crashed-thread blocks
- remove ARM64 register clue / `likely_fault` language
- add a boundary statement: full crashlogs are handled outside Orchard; pass a
  single frame or explicit symbol identity to Orchard

Update `skills/orchard/SKILL.md`:

- remove crashed-thread triage from the skill description
- remove `orchard_lookup_crash_thread` from MCP tool lists
- remove ARM64 register-clue guidance
- describe `orchard_lookup_frame` as single-frame deterministic symbol
  resolution
- tell agents to extract a concrete frame or symbol outside Orchard before using
  Orchard

Update README and any generated guidance similarly.

## Testing Strategy

Remove tests:

- unit tests for `lookup_crash_thread`
- MCP registration tests for `orchard_lookup_crash_thread`
- freshness tests that call `_do_lookup_crash_thread`

Add or update tests:

- `orchard_lookup_crash_thread` is absent from the MCP tool list.
- `lookup_frame` rejects multi-line input instead of selecting a frame.
- `_ORCHARD_BLOCK` does not contain `orchard_lookup_crash_thread`,
  `crashed-thread`, `business_first_frame`, `likely_fault`, or ARM64 register
  clue language.
- `_ORCHARD_BLOCK` contains a clear single-frame boundary statement.
- `skills/orchard/SKILL.md` does not contain `orchard_lookup_crash_thread`,
  `crashed-thread triage`, `likely_fault`, or ARM64 register clue language.
- existing single-frame C++, Objective-C, and Swift parsing tests still pass.
- existing graph enrichment tests for direct callers and source scope still pass.

## Migration

This is a breaking cleanup. Existing callers of `orchard_lookup_crash_thread`
must move crashlog parsing outside Orchard.

Migration pattern:

1. Caller receives crashlog.
2. Caller chooses one concrete frame, symbol name, qualified name, or USR.
3. Caller invokes `orchard_lookup_frame`, `orchard_search`,
   `orchard_find_references`, `orchard_find_callers`, or
   `orchard_find_callees`.

There should be no compatibility wrapper because preserving the old tool name
would keep attracting agents back to the wrong abstraction.

## Acceptance Criteria

- `orchard_lookup_crash_thread` is no longer exposed through MCP.
- `lookup_crash_thread` implementation is removed.
- `lookup_frame` rejects multi-line input.
- Documentation and skill guidance no longer describe Orchard as a crashlog or
  crashed-thread analyzer.
- Guard tests prevent the removed crash-thread workflow from returning.
- Single-frame lookup remains available and tested.
