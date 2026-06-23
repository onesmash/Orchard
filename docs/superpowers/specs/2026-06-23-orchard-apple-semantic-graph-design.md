# Orchard Apple Semantic Graph Design

- Date: 2026-06-23
- Status: Approved design candidate pending review loop
- Repository: `/Users/hui.xu/SourceCode/orchard`
- Source architecture note: `/Users/hui.xu/SourceCode/orchard/apple-semantic-grapgh.md`
- Real integration target: `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/`

## Clarified Request

This design defines how Orchard should implement the architecture described in
`apple-semantic-grapgh.md` as a compiler-grade semantic graph system for Apple
platform codebases.

The confirmed constraints are:

- Orchard is not an IDE-assistant-first system. It is a compiler-artifact-first
  semantic graph system for agents.
- The implementation language should be Python-first.
- The graph storage layer should use the real
  [`LadybugDB/ladybug`](https://github.com/LadybugDB/ladybug) project rather
  than a placeholder graph abstraction.
- Orchard must integrate with real compiler-driven build artifacts rather than
  mock-only or grep-based inputs.
- The first concrete integration target is the Zoom iOS client repository at
  `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/`.
- This document should not break the work into phase planning. It should define
  the complete target architecture, the implementation boundaries for this
  change, and the acceptance criteria for completion.

## Design Goals

Orchard must provide a unified semantic graph for Apple-platform code that
supports:

- Swift
- Objective-C
- Objective-C++
- C
- C++

The resulting system must support agent-facing capabilities such as:

- symbol lookup
- references and call graph navigation
- dependency graph queries
- impact-analysis seeds
- cross-language relation traversal
- protocol and type graph queries
- architecture reasoning
- context retrieval for downstream agent workflows

The design intentionally separates three layers:

1. compiler and toolchain facts
2. unified semantic graph
3. derived higher-level agent capabilities

This separation protects correctness. Compiler artifacts remain the
authoritative truth. Higher-level analyses may derive from those artifacts, but
must not be confused with them.

## System Boundary

Orchard owns the pipeline from real build invocation through graph persistence
and agent query access:

```text
xcodebuild / build invocation
-> build artifact discovery
-> raw artifact collectors
-> semantic normalization
-> unified graph snapshot writer
-> Ladybug
-> query and MCP services
```

Orchard does not treat the following as the system of record:

- LSP hover or completion output
- grep-based source scraping
- tree-sitter-only parsing
- hand-authored mock graph data used in place of compiler facts

Those may be used as supplementary context or fallback diagnostics, but not as
the primary semantic truth for the graph.

## Authoritative Inputs

The authoritative source is the real compiler-driven build process for the
target repository. In practice, Orchard must support at least:

- `xcodebuild`
- `swift build`
- other build-system entrypoints that truly invoke the Swift or Clang toolchain

For the Zoom iOS client target, the primary path is real `xcodebuild`-driven
collection against the workspace and scheme selected for ingestion.

Orchard must ingest facts from these classes of artifacts:

- `IndexStore`
- Swift symbol graph output
- Clang ExtractAPI or equivalent C-family API extraction output
- on-demand AST and source context extraction

These sources map to Orchard's fact layer as follows:

- `IndexStore`: references, occurrences, symbol relations, high-confidence
  usage edges
- `swift-symbolgraph-extract`: Swift type graph, protocol hierarchy,
  inheritance, generic relationship metadata, public API structure
- `Clang ExtractAPI` or equivalent symbol graph output: C, Objective-C, and
  C++ API and declaration relationships
- `SourceKitten` or equivalent Swift AST tooling: declaration structure,
  syntax, comments, context chunking, SwiftUI-oriented static analysis inputs
- `libclang` or equivalent C-family AST access: include graph, inheritance,
  receiver-level drill-down, macro and preprocessing-aware context

## Build Context Contract

Every ingestion run must persist a reproducible build snapshot. Orchard should
carry a build context equivalent to:

```ts
interface BuildContext {
  build_id: string
  build_system: "xcodebuild" | "swift_build" | "other"
  workspace_root: string
  scheme?: string
  target: string
  configuration: string
  sdk: string
  triple: string
  toolchain_id: string
  derived_data_path?: string
  index_store_path?: string
  symbolgraph_output_path?: string
  commit_sha?: string
  build_config_hash: string
}
```

This is not optional metadata. Orchard uses it for:

- reproducibility
- target and module disambiguation
- freshness checks
- provenance and evidence tracing
- later rebuild and comparison workflows

## Repository Structure

The Orchard repository should be organized around the semantic pipeline rather
than around incidental script order:

```text
orchard/
  pyproject.toml
  src/orchard/
    cli/
    build/
    collect/
    normalize/
    graph/
    query/
    mcp/
    models/
    provenance/
    config/
  tests/
  docs/
  scripts/
```

### Module Responsibilities

#### `cli/`

Thin command entrypoints only. This layer parses parameters and dispatches to
services. It must not absorb build orchestration, parsing rules, or graph
logic.

#### `build/`

Owns real build execution and build snapshot capture. Responsibilities include:

- invoking `xcodebuild` or other supported build systems
- recording build logs and exit status
- resolving derived data and output paths
- populating `BuildContext`
- surfacing recoverable and non-recoverable build failures clearly

This layer should hide build-system complexity from the rest of Orchard.

#### `collect/`

Owns raw artifact collection from authoritative sources. Suggested modules:

- `indexstore_collector.py`
- `symbolgraph_collector.py`
- `extractapi_collector.py`
- `ast_context_collector.py`

Each collector converts raw tool output into Orchard fact records. Collectors do
not decide graph shape or query behavior.

#### `normalize/`

Owns semantic unification. This layer merges facts into a stable Orchard
semantic model by handling:

- symbol identity and de-duplication
- USR and precise ID alignment
- module and target attribution
- container and ownership relationships
- cross-language linkage recovery
- confidence assignment where inference is required

This is one of the deepest modules in the system and should absorb complexity
that would otherwise leak into graph storage or query consumers.

#### `graph/`

Owns Ladybug integration and persistence. Responsibilities include:

- schema initialization
- batch loading and idempotent writes
- graph snapshot association with build snapshots
- physical query adapter code
- persistence-oriented validation

This layer must not know how to run `xcodebuild`, parse source files, or infer
Apple semantic relationships from raw compiler output.

#### `query/`

Owns agent-facing graph queries and semantic retrieval APIs. This layer exposes
high-level operations such as:

- find symbol
- find callers
- find references
- inspect module dependencies
- inspect cross-language bridges
- inspect build snapshot metadata

This layer should package query intent in a way that minimizes caller
complexity.

#### `mcp/`

Owns external agent transport for Orchard query capabilities. It should remain
thin and translate between tool inputs and Orchard query APIs rather than
reimplement query logic.

#### `models/`

Owns stable internal types for build snapshots, artifacts, symbols, edges, and
query results.

#### `provenance/`

Owns evidence models, confidence rules, source tracking, and policies for
distinguishing direct compiler facts from derived relationships. This is
separate because provenance is a first-class system requirement, not a
secondary annotation.

## Unified Semantic Model

Orchard should not collapse the graph into symbols and edges alone. To match
the approved architecture source and avoid information leakage, the graph must
model the following first-class entities:

- `BuildSnapshot`
- `Target`
- `Module`
- `File`
- `SymbolNode`
- `Occurrence`
- `Chunk`
- `ArtifactRecord`
- `SemanticEdgeRecord`

These entities exist for different reasons:

- `BuildSnapshot` anchors freshness, toolchain identity, and reproducibility
- `Target` captures build-configuration-sensitive visibility and platform scope
- `Module` captures ownership, namespace, and high-level architecture
- `File` captures declaration location, imports, and retrieval jumps
- `SymbolNode` captures durable symbol identity
- `Occurrence` captures occurrence-level reference evidence
- `Chunk` captures retrieval and embedding entrypoints
- `ArtifactRecord` captures what was physically collected
- `SemanticEdgeRecord` captures evidence for logical relations

### `BuildSnapshot`

Represents one real build-backed ingestion run. It anchors all downstream facts
to a reproducible compilation context and is the source of truth for freshness.

### `Target`

Represents the Xcode or build target context in which files and symbols were
observed. Target must remain explicit because the same symbol may differ in
visibility or bridge availability across target, SDK, or configuration.

### `Module`

Represents a logical module boundary. A module may contain targets, files, and
symbols. This layer is required for architecture queries and for keeping
file-level imports from being flattened into symbol-only relations.

### `File`

Represents a source or generated file. File nodes are required so Orchard can
preserve:

- declaration ownership
- file-level imports
- file-to-target attribution
- retrieval and chunk back-links
- occurrence-level evidence

### `SymbolNode`

Represents a unified symbol in the graph. The design should retain at least the
following fields:

```ts
interface SymbolNode {
  usr: string
  precise_id?: string
  language: "swift" | "objc" | "cpp" | "c"
  kind:
    | "class"
    | "protocol"
    | "function"
    | "method"
    | "struct"
    | "enum"
    | "extension"
    | "property"
    | "typealias"
  name: string
  module: string
  file_path: string
  target?: string
  container_usr?: string
  signature?: string
  access_level?: "private" | "fileprivate" | "internal" | "public" | "open"
  origin?:
    | "indexstore"
    | "swift_symbolgraph"
    | "clang_extractapi"
    | "sourcekitten"
    | "libclang"
    | "derived"
  is_generated?: boolean
  availability?: string[]
}
```

These fields matter because Apple codebases routinely contain name collisions
that can only be resolved correctly when stable identifiers, module ownership,
file path, target, and origin are all preserved.

### `Occurrence`

Represents one observed usage or declaration occurrence anchored to a concrete
file location. Orchard needs occurrence-level modeling to support precise
reference evidence, file-level diagnostics, and future source-jump tooling.

Suggested shape:

```ts
interface Occurrence {
  id: string
  usr: string
  file_path: string
  line: number
  column: number
  role: "declaration" | "reference" | "call" | "implementation" | "import"
}
```

### `Chunk`

Represents the retrieval unit for full-text and vector search. Chunks should be
cut around semantically meaningful owners such as:

- type
- method
- extension
- SwiftUI view

Suggested shape:

```ts
interface Chunk {
  id: string
  owner_usr?: string
  file_path: string
  chunk_kind: "type" | "method" | "extension" | "view" | "file_context"
  content: string
  embedding?: number[]
}
```

### `ArtifactRecord`

Represents one collected artifact or fact set produced within a build snapshot,
such as:

- an `IndexStore` location
- a set of Swift symbol graph files
- a set of ExtractAPI outputs
- AST and source-context extraction output

Suggested shape:

```ts
interface ArtifactRecord {
  id: string
  build_id: string
  artifact_kind:
    | "indexstore"
    | "swift_symbolgraph"
    | "clang_extractapi"
    | "sourcekitten"
    | "libclang"
    | "build_log"
  path: string
  status: "collected" | "missing" | "partial" | "failed"
  toolchain_id?: string
  notes?: string[]
}
```

### `SemanticEdgeRecord`

Represents a logical relationship with evidence and provenance. Orchard should
distinguish between:

- the canonical logical relation that query consumers traverse
- the evidence records that justify that relation

Suggested shape:

```ts
interface SemanticEdgeRecord {
  id: string
  type:
    | "calls"
    | "references"
    | "inherits"
    | "implements"
    | "imports"
    | "contains"
    | "bridges_to"
  from: string
  to: string
  build_id: string
  source?:
    | "indexstore"
    | "swift_symbolgraph"
    | "clang_extractapi"
    | "sourcekitten"
    | "libclang"
    | "derived"
  confidence?: number
  provenance?: string
  authoritative: boolean
}
```

`normalize/` owns the generation of canonical logical edges. `provenance/`
owns the evidence model, confidence rules, and merge policy. `graph/` persists
the canonical edge plus its evidence records but does not decide semantics.

This ownership split avoids pass-through layering:

- `collect/` produces raw facts
- `normalize/` decides symbol identity and logical relation candidates
- `provenance/` decides evidence quality, merge policy, and direct vs derived
  classification
- `graph/` persists the decided graph shape and enforces storage constraints

## Provenance Rules

The following edge types must preserve explicit provenance and build snapshot
association:

- `calls`
- `references`
- `implements`
- `imports`
- `bridges_to`

This protects the system from conflating:

- direct compiler relations
- symbol graph declarations
- AST-derived inferences
- build-configuration recovery
- higher-level semantic derivations

Orchard must make it possible for an agent to inspect not only that a relation
exists, but why Orchard believes the relation exists.

### Cross-Language Bridge Taxonomy

Cross-language support must not stop at a generic `bridges_to` label. Orchard
must preserve at least these bridge kinds:

- `bridging_header`
- `generated_swift_interface`
- `objc_selector`
- `module_import`
- `cxx_interop`
- `swift_overlay`

Bridge recovery depends on real build configuration, module boundaries,
visibility, and toolchain behavior. All bridge results must therefore carry:

- `bridge_kind`
- `provenance`
- `confidence`
- `build_id`

If a bridge is inferred rather than directly observed from compiler artifacts,
the result must be marked as non-authoritative and surfaced as such to query
consumers.

## Ladybug Integration

The unified graph database layer is implemented using the real
`LadybugDB/ladybug` project.

Ladybug is a strong fit because it supports:

- property graph modeling
- Cypher query language
- embedded and serverless application integration
- full-text indexing
- vector index support

That aligns with Orchard's requirements for a unified graph DB plus
query-oriented and retrieval-oriented downstream capabilities.

Ladybug is used here as a local embedded backend, not as a centralized shared
service. The storage model should assume:

- local single-workspace persistence
- many readers and a single active writer
- FTS and vector search attached to node properties rather than externalized by
  default

### Canonical Ladybug Schema

The canonical Orchard schema in Ladybug should be equivalent to:

```cypher
CREATE GRAPH code_graph;
USE code_graph;

CREATE NODE TABLE Module(
  name STRING PRIMARY KEY,
  language STRING
);

CREATE NODE TABLE Target(
  id STRING PRIMARY KEY,
  name STRING,
  platform STRING,
  sdk STRING,
  triple STRING,
  configuration STRING
);

CREATE NODE TABLE BuildSnapshot(
  id STRING PRIMARY KEY,
  build_system STRING,
  workspace_root STRING,
  derived_data_path STRING,
  index_store_path STRING,
  toolchain_id STRING,
  commit_sha STRING,
  created_at STRING,
  build_config_hash STRING
);

CREATE NODE TABLE File(
  path STRING PRIMARY KEY,
  module STRING,
  language STRING,
  target_id STRING
);

CREATE NODE TABLE Symbol(
  usr STRING PRIMARY KEY,
  precise_id STRING,
  name STRING,
  language STRING,
  kind STRING,
  module STRING,
  target_id STRING,
  file_path STRING,
  signature STRING,
  container_usr STRING,
  access_level STRING,
  origin STRING,
  is_generated BOOL
);

CREATE NODE TABLE Chunk(
  id STRING PRIMARY KEY,
  owner_usr STRING,
  chunk_kind STRING,
  content STRING,
  embedding FLOAT[1536]
);

CREATE NODE TABLE Occurrence(
  id STRING PRIMARY KEY,
  usr STRING,
  file_path STRING,
  line INT64,
  column INT64,
  role STRING
);

CREATE REL TABLE ContainsFile(FROM Module TO File);
CREATE REL TABLE ContainsTarget(FROM Module TO Target);
CREATE REL TABLE BuiltTarget(FROM BuildSnapshot TO Target);
CREATE REL TABLE ObservedFile(FROM BuildSnapshot TO File);
CREATE REL TABLE Declares(FROM File TO Symbol);
CREATE REL TABLE ContainsChunk(FROM Symbol TO Chunk);
CREATE REL TABLE ContainsOccurrence(FROM File TO Occurrence);
CREATE REL TABLE RefersTo(FROM Occurrence TO Symbol, role STRING);
CREATE REL TABLE Calls(FROM Symbol TO Symbol, source STRING, confidence DOUBLE);
CREATE REL TABLE References(FROM Symbol TO Symbol, source STRING, confidence DOUBLE);
CREATE REL TABLE Inherits(FROM Symbol TO Symbol);
CREATE REL TABLE Implements(FROM Symbol TO Symbol);
CREATE REL TABLE Imports(FROM File TO File, kind STRING);
CREATE REL TABLE BridgesTo(
  FROM Symbol TO Symbol,
  bridge_kind STRING,
  provenance STRING,
  confidence DOUBLE
);
```

This schema is the baseline contract. Orchard may add supporting evidence
tables, but it should not silently replace these first-class entities with a
symbol-only graph.

Within Ladybug, Orchard should model both durable graph relationships and their
supporting evidence. A recommended graph shape is:

```text
(:BuildSnapshot)-[:BUILT_TARGET]->(:Target)
(:Module)-[:CONTAINS_TARGET]->(:Target)
(:Module)-[:CONTAINS_FILE]->(:File)
(:BuildSnapshot)-[:OBSERVED_FILE]->(:File)
(:File)-[:DECLARES]->(:SymbolNode)
(:File)-[:CONTAINS_OCCURRENCE]->(:Occurrence)
(:Occurrence)-[:REFERS_TO]->(:SymbolNode)
(:SymbolNode)-[:CONTAINS_CHUNK]->(:Chunk)
(:SymbolNode)-[:CALLS|REFERENCES|INHERITS|IMPLEMENTS|BRIDGES_TO]->(:SymbolNode)
(:ArtifactRecord)-[:EMITS]->(:File|:SymbolNode|:Occurrence|:Chunk|:SemanticEdgeRecord)
```

The preferred persistence approach is to keep edge evidence explicitly
addressable, rather than relying only on properties stored on the relationship.
That design supports:

- multiple evidence records for the same logical relation
- build-to-build comparison
- mixed direct and derived evidence
- later confidence and explanation queries

### Load Ownership

- `build/` writes `BuildSnapshot`
- `collect/` writes `ArtifactRecord` payloads into Orchard staging data
- `normalize/` produces canonical node and relation payloads
- `graph/` performs the actual Ladybug DDL check, bulk load, and idempotent
  merge semantics

### Index Placement

FTS and vector indexing should prioritize node properties on:

- `Chunk.content`
- `Symbol.name`
- `File.path`

This keeps semantic retrieval aligned with the graph model instead of splitting
retrieval into a disconnected side store.

## Query Model

Orchard's first query surface should be oriented around semantic questions that
agents actually ask, not around low-level table dumps. Required query families
are:

- `find_symbol`
- `semantic_search`
- `get_symbol_context`
- `find_references`
- `find_callers`
- `find_callees`
- `impact_analysis`
- `get_type_hierarchy`
- `module_dependencies`
- `get_cross_language_bridges`
- `get_module_graph`
- `build_snapshot_info`

The query layer must surface provenance with results whenever the relation is
not self-evident from the returned symbol itself.

### Base Request and Response Contract

All Orchard query and MCP tools should share a stable base contract:

```ts
interface BaseToolRequest {
  repo_root?: string
  build_id?: string
  target?: string
  module?: string
  include_derived?: boolean
  max_depth?: number
}

interface BaseToolResponse<T> {
  data: T
  freshness:
    | "fresh"
    | "stale"
    | "partially_stale"
    | "build_mismatch"
    | "toolchain_mismatch"
  build_id?: string
  target?: string
  module?: string
  toolchain_id?: string
  evidence_sources: string[]
  confidence?: number
  open_gaps: string[]
}
```

This contract is required so callers do not have to infer freshness or missing
coverage from free-form prose.

### Required Query Contracts

#### `find_symbol`

```ts
interface FindSymbolRequest extends BaseToolRequest {
  usr?: string
  precise_id?: string
  symbol_name?: string
  file_path?: string
  limit?: number
}
```

Resolution rules:

- if `usr` is present, resolve by `usr`
- else if `precise_id` is present, resolve by `precise_id`
- else symbol-name lookup must return either a unique match or an explicit
  disambiguation set
- ambiguous name-only requests must never silently pick one symbol

The response must return either:

- one resolved symbol record, or
- a candidate set containing `usr`, `precise_id`, `module`, `target`,
  `file_path`, and `language`

#### `semantic_search`

```ts
interface SemanticSearchRequest extends BaseToolRequest {
  query: string
  limit?: number
  language?: "swift" | "objc" | "cpp" | "c"
  symbol_kinds?: string[]
}
```

Returns chunk hits with at least:

- `chunk_id`
- `owner_usr`
- `owner_name`
- `file_path`
- `module`
- `score`
- `excerpt`

#### `get_symbol_context`

```ts
interface GetSymbolContextRequest extends BaseToolRequest {
  usr?: string
  precise_id?: string
  symbol_name?: string
  file_path?: string
}
```

The response must include:

- symbol metadata
- declaration locations
- references
- callers
- callees
- inheritance
- implementation edges
- bridge edges

#### `find_callers` and `find_callees`

Both queries must support:

- direct-only traversal by default
- optional indirect traversal
- explicit provenance payload on each returned edge
- duplicate suppression by logical edge identity

#### `impact_analysis`

This query is required and must not be left implicit in downstream planning.

```ts
interface ImpactAnalysisRequest extends BaseToolRequest {
  usr: string
  max_depth?: number
  include_bridge_edges?: boolean
}
```

Traversal policy:

- depth 1 must prioritize `calls`, `references`, and `implements`
- deeper traversal may include `inherits`, `imports`, and `bridges_to`
- low-confidence edges are excluded from direct-caller counts by default
- bridge edges must be counted separately in the summary

The response summary must include:

- `direct_callers`
- `indirect_dependents`
- `bridge_dependents`
- `affected_modules`
- `affected_targets`
- `risk`

Risk defaults:

- `low`: low fan-out, no cross-target or bridge spread
- `medium`: multiple direct dependents or broad module-local references
- `high`: cross-module or cross-target spread, or bridge dependents present
- `critical`: cross-target and cross-bridge spread with high fan-out, or a
  freshness mismatch under a high-confidence request

If `freshness != "fresh"`, risk must be raised by at least one level and the
reason must appear in `open_gaps`.

#### `get_cross_language_bridges`

Must return:

- `from_usr`
- `to_usr`
- `bridge_kind`
- `provenance`
- `confidence`

#### Derived Queries

Derived queries such as `get_view_tree` or `find_navigation_flow` are allowed,
but must explicitly include:

- `confidence`
- `evidence_sources`
- `derived_from`
- `open_gaps`

They must not present derived results as compiler-direct truth.

## Real Integration Target

The concrete integration target is:

`/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/`

This repository is a suitable real-world validation target because it contains:

- multiple modules
- mixed Swift and Objective-C-family code
- module maps and prefix headers
- Xcode projects and shared schemes
- cross-module service interfaces
- Objective-C++ files in active use

Orchard's implementation must assume this is a large and evolving repository.
That means the design should optimize for correctness of identity and
provenance, not for the shortest possible prototype path.

### Deterministic Validation Baseline

The initial deterministic ingestion baseline should be:

- workspace: `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/Zoom.xcworkspace`
- scheme: `iOSAdditions`
- configuration: `Debug`

This baseline is chosen because the repository exposes a shared
`iOSAdditions.xcscheme`, and the target contains mixed Swift, Objective-C, and
Objective-C++ source, making it a practical first deterministic integration
surface for Orchard.

## Execution Flow

Orchard should expose a direct ingestion path for the target iOS repository.
A representative command shape is:

```bash
orchard ingest-ios-client \
  --workspace /Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/Zoom.xcworkspace \
  --scheme iOSAdditions \
  --configuration Debug \
  --derived-data <path> \
  --repo-root /Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client
```

Internally, the execution flow is:

1. Trigger real `xcodebuild`.
2. Capture and persist a `BuildSnapshot`.
3. Discover and register produced authoritative artifacts.
4. Run artifact collectors against those outputs.
5. Normalize the collected facts into Orchard's semantic model.
6. Persist the unified graph and evidence into Ladybug.
7. Expose the resulting graph through query and MCP interfaces.

### Minimum Operational Commands

The minimum real ingestion path should be concrete enough for implementation:

1. Build capture:

```bash
xcodebuild build \
  -workspace /Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/Zoom.xcworkspace \
  -scheme iOSAdditions \
  -configuration Debug \
  -derivedDataPath <derived-data-path> \
  COMPILER_INDEX_STORE_ENABLE=YES
```

2. IndexStore discovery:

- read from `<derived-data-path>/Index.noindex/DataStore`

3. Swift symbol graph collection:

```bash
swift-symbolgraph-extract \
  -module-name iOSAdditions \
  -output-dir <symbolgraph-output-dir> \
  -target <triple> \
  -I <built-products-dir>
```

4. Swift AST and source context extraction:

```bash
sourcekitten structure --file <swift-file>
```

5. C-family AST drill-down:

- use `libclang` or equivalent compile-command-backed parsing from the build
  environment for targeted files when additional AST detail is needed

These commands define the operational baseline. Implementation may wrap them,
but should not replace them with guess-based shortcuts.

Orchard may also expose lower-level commands for debugging or selective use,
such as:

- `orchard build capture`
- `orchard collect indexstore`
- `orchard collect symbolgraph`
- `orchard normalize snapshot`
- `orchard graph load`
- `orchard query ...`

These commands are debugging and operational seams around the same core model,
not separate competing workflows.

## Error Handling and Operational Semantics

Orchard should absorb operational complexity inside deep modules instead of
forcing every caller to understand build artifacts and partial collection
states.

Required behaviors:

- If `xcodebuild` fails, Orchard must fail the ingestion with a build-scoped
  error that preserves logs and build context.
- If an artifact class is missing, Orchard must explicitly record degraded
  collection status instead of silently claiming full success.
- If an artifact collector succeeds partially, the resulting graph must preserve
  which facts are authoritative and which relationships are unavailable.
- Query consumers should not need to interpret raw compiler output paths or
  individual tool error formats.

This follows the design principle that complexity should be pulled downward into
the owning module.

### Failure and Degraded-State Matrix

The system must expose machine-checkable status for at least these cases:

| Condition | Ingestion Status | Graph Write | Query Freshness | Required Surface |
| --- | --- | --- | --- | --- |
| `xcodebuild` failed | `failed` | no write | `build_mismatch` | build log path, exit code, build context |
| `IndexStore` missing | `partial` | allowed | `partially_stale` | missing artifact note, open gap |
| symbol graph extraction failed | `partial` | allowed | `partially_stale` | failed artifact note, open gap |
| provenance incomplete | `partial` | allowed | `partially_stale` | edge-level warning, open gap |
| Ladybug load failed | `failed` | no partial success claim | `build_mismatch` | storage error, failed stage |
| toolchain mismatch detected | `partial` or `failed` depending on severity | guarded | `toolchain_mismatch` | expected vs observed toolchain |

Required semantics:

- Orchard must not claim full success when any authoritative artifact class is
  missing.
- Partial ingestion may write graph data, but every query result must surface
  freshness and open gaps accordingly.
- A failed Ladybug load must not leave the run presented as completed.
- Query responses over degraded data must still be well-formed under
  `BaseToolResponse`.

## Out of Scope

This design does not define the internal algorithms for:

- ranking or summarizing semantic search results
- full agent UX over the query surface
- repository-wide task planning after this design is approved

This document also does not substitute future implementation planning. It
defines the design contract and completion expectations for this change only.

## Acceptance Criteria

The implementation is complete when Orchard satisfies all of the following:

- Orchard can execute a real build-backed ingestion against the Zoom iOS client
  repository.
- Orchard persists a reproducible `BuildSnapshot` for that ingestion.
- Orchard persists first-class `Target`, `Module`, `File`, `Symbol`, `Chunk`,
  and `Occurrence` records into Ladybug or an equivalent directly mappable
  internal load model.
- Orchard discovers and records the real `IndexStore` path for the run.
- Orchard ingests real symbol data from Swift, Objective-C, and Objective-C++
  sources present in the deterministic validation baseline.
- C and C++ are in architectural scope for Orchard, but are not part of the
  deterministic acceptance baseline for this specific validation target unless
  the selected baseline scheme emits corresponding authoritative artifacts in
  the local environment.
- Orchard persists stable `SymbolNode` identity with module, file, origin, and
  language information.
- Orchard persists semantic relations with explicit provenance and build
  association.
- Re-running ingestion against the same baseline input does not create duplicate
  logical nodes or edges, and the resulting build snapshot differences are
  explainable.
- Orchard can answer the required query families listed in this document using
  the shared `BaseToolResponse` freshness and evidence contract.
- `find_symbol` returns either a unique resolved symbol or an explicit
  disambiguation set for ambiguous name-only requests.
- `find_callers` and `find_callees` return edge-level provenance and suppress
  duplicate logical edges.
- `impact_analysis` returns summary counts, risk classification, affected
  modules, affected targets, and freshness-aware open gaps.
- `get_cross_language_bridges` returns bridge kind and provenance for each
  bridge.
- Returned relationship results can be traced to evidence and build context.
- The following high-risk regression assertions hold:
  - duplicate symbol names across modules or targets must not silently collapse
    into one symbol identity
  - same-name symbols under different targets must preserve distinct target
    attribution
  - Objective-C++ bridge edges must surface `bridge_kind`, `provenance`, and
    `confidence`
  - generated symbols must retain `origin` and generated-state metadata
  - macro or preprocessing-influenced C-family parsing must either yield
    authoritative evidence or emit an explicit open gap
  - multiple evidence records for one logical relation must not produce
    duplicate logical edges in query results
- Missing or degraded artifact conditions are reported explicitly rather than
  hidden behind false success.
- Query responses over degraded data preserve machine-readable freshness and
  open-gap reporting.
- Minimum non-functional gates are defined and met on the deterministic
  baseline:
  - one baseline ingestion completes within the configured ingestion timeout
    budget and reports its duration
  - one single-symbol query completes within the configured query timeout budget
    and reports degraded status instead of hanging on partial data
  - repeated ingestion does not cause unbounded logical graph growth for the
    same baseline input

Default non-functional budgets:

- `orchard.config.timeouts.ingestion_seconds` defaults to `1800`
- `orchard.config.timeouts.query_seconds` defaults to `5`

If a team overrides these defaults, the overridden values become part of the
test preconditions for that environment and must be reported with the run.

## Impact Scope

This design implies Orchard will add or touch the following areas in this
repository:

- new Python package and CLI entrypoints under `src/orchard/`
- Ladybug integration and schema bootstrap code
- build orchestration for Apple toolchain-backed ingestion
- artifact collectors for IndexStore, symbol graph, and AST-context inputs
- query and MCP surface contracts
- test coverage for ingestion, graph persistence, and query semantics
- local-environment assumptions for Xcode, Swift toolchain, and Ladybug

The implementation environment therefore depends on:

- local Xcode toolchain availability
- `xcodebuild`
- `swift-symbolgraph-extract`
- `sourcekitten`
- Ladybug runtime availability for Python
- access to the local Zoom iOS client workspace

## Key Design Decisions

1. Python is the primary implementation language for Orchard orchestration and
   integration logic.
2. Real compiler-driven artifacts are the authoritative source of truth.
3. Ladybug is the actual unified graph database, not a placeholder.
4. Provenance is a first-class concern and therefore has a dedicated modeling
   boundary.
5. Build capture, collection, normalization, graph persistence, and agent query
   access are separate layers with different abstractions.
6. This design does not plan future phases. It defines the complete target
   architecture and current implementation boundaries without phase-roadmapping.

## Open Questions

The remaining questions are intentionally narrowed to implementation-time
environment specifics rather than design-shaping ambiguity:

- the exact derived-data root Orchard should default to on the local machine
- the exact `swift-symbolgraph-extract` include path arguments required by the
  selected Xcode toolchain on this workstation
- whether additional Zoom iOS schemes should be added as regression fixtures
