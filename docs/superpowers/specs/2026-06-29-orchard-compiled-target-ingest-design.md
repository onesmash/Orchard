# Orchard Compiled-Target Ingest Design

Date: 2026-06-29

## Goal

Make `orchard ingest` index all targets that were actually compiled into the current Xcode build result, without requiring users to manually enumerate dependency targets or widen `source-root`.

This design replaces path-prefix filtering with build-scope discovery driven by Xcode `DerivedData`.

## Problem

Today `orchard ingest` can miss valid project code even when that code is present in the `IndexStore`.

The concrete failure mode is:

- a main Xcode project builds dependency targets such as `zPSApp`
- those dependency targets produce compilation units in the same `IndexStore`
- Orchard narrows ingest using `project-dir` / `source-root`
- sources outside that directory tree are filtered out

This makes ingest behavior depend on repository layout rather than on what Xcode actually compiled.

## Desired Behavior

`orchard ingest` should default to indexing the set of targets that were actually compiled for the current build.

Example:

```bash
orchard ingest --project-dir /path/to/ios-client --target Zoom
```

If the matching `DerivedData` contains:

- `Zoom.build`
- `zPSApp.build`
- `zClipsApp.build`

then Orchard should index those three targets by default.

If only `Zoom.build` exists, Orchard should index only `Zoom`.

## Non-Goals

- No new CLI flag for this behavior
- No `--deps` feature in this iteration
- No transitive dependency resolution from `project.pbxproj`
- No automatic per-target build invocation
- No per-target `DerivedData` or `IndexStore` discovery

## User-Facing CLI Changes

### Remove `--source-root`

`--source-root` should be removed from `orchard ingest`.

Reasons:

- it is the main source of false negatives for valid compiled modules
- it encodes a directory heuristic instead of build truth
- it complicates the ingest mental model

After this change, users describe what to ingest through:

- `--project-dir`
- `--target`
- optional `--index-store`

The actual ingest scope is derived from compiled targets in `DerivedData`.

### Keep `--target`

`--target` remains required behaviorally, but its meaning narrows:

- it is the entry target requested by the user
- it anchors project discovery and validation
- it is expected to be present in the compiled target set

It no longer implies "only ingest this one target".

## Build-Scope Discovery

### Primary source: `Intermediates.noindex`

For Xcode-based ingest, Orchard should inspect the matching `DerivedData` entry and discover compiled targets from:

`DerivedData/Build/Intermediates.noindex`

The discovery rule is:

- find directories matching `*.build`
- extract target names from directory names
- keep only real project target build directories

Example:

- `Zoom.build`
- `zPSApp.build`
- `zClipsApp.build`

These become the compiled target set for this ingest run.

### Why `Intermediates.noindex`

This reflects the build products that Xcode actually emitted for this run. It matches the user's real intent better than:

- `project-dir` path filtering
- hand-written dependency lists
- static project dependency graphs

It is also a better fit for multi-root repositories where compiled dependency targets live outside the main project subtree.

## IndexStore Usage

The `IndexStore` remains the source of symbols, occurrences, and relationships.

The new rule is:

- `Intermediates.noindex` determines which targets are in scope
- `IndexStore` supplies the data to ingest for those targets

This means Orchard should not attempt to ingest every record visible in the `IndexStore` blindly.

Instead it should:

1. discover compiled target names from `Intermediates.noindex`
2. validate that the requested entry `--target` is present
3. ingest those compiled targets into the database

### Replace `source-root` with compiled-file filtering

Removing `--source-root` means Orchard needs a new way to restrict ingest to
project code without promoting SDK internals to first-class targets.

The filtering model should become:

1. discover compiled targets from `Intermediates.noindex`
2. derive the compiled source file set for those targets
3. accept IndexStore symbol / occurrence / relation records only when their
   associated source file belongs to that compiled file set

This keeps ingest aligned with what Xcode actually built while avoiding broad
directory-prefix heuristics.

The key design constraint is that target discovery comes from
`Intermediates.noindex`, while record admission comes from compiled file
membership rather than a path prefix.

## Target Set Resolution

The resolved target set for a run should be:

1. discover matching Xcode project / workspace
2. discover matching `DerivedData`
3. inspect `Intermediates.noindex`
4. compute compiled target names
5. ensure `--target` is included in that set

If `--target` is not present in the compiled target set, Orchard should fail with a clear error rather than silently ingesting an unrelated build.

### Manual `--index-store` mode

When users pass `--index-store` explicitly, Orchard should still try to locate
the matching `DerivedData` root so it can inspect `Intermediates.noindex`.

Resolution order:

1. infer the `DerivedData` root from the supplied `--index-store` path
2. if successful, use that root for compiled-target discovery
3. if not successful, fail with a clear error explaining that compiled-target
   ingest requires a sibling `Intermediates.noindex` tree

This keeps behavior consistent across auto-detected and manually supplied
IndexStore paths.

## Database Behavior

### Symbols and relationships

The ingest loop should iterate over the compiled target set rather than only the CLI-provided target.

This preserves the current multi-target write pattern already used by Orchard:

- symbols upserted per target
- calls upserted per target
- structural relations upserted per target

The current `read_index_store(...)` interface is path-filter based. This design
requires extending it so the ingest pipeline can filter by compiled file set
instead of `source-root`.

Acceptable implementations:

- add a compiled-file allowlist parameter to the reader
- add a post-read filtering phase before upsert

The implementation should choose one path and make it the single filtering
mechanism for Xcode ingest.

### State persistence

`ingest-state.json` should record the actual compiled target set for the run.

Example:

```json
{
  "targets": ["Zoom", "zPSApp", "zClipsApp"],
  "index_store_paths": {
    "Zoom": "/path/to/DataStore",
    "zPSApp": "/path/to/DataStore",
    "zClipsApp": "/path/to/DataStore"
  }
}
```

This keeps state aligned with the true ingest scope rather than the narrow CLI input.

### Incremental / fast path

Fast-path eligibility should continue to depend on:

- `unit_ts <= last_ingest_ts`
- requested ingest scope already being present in state

With this design, "requested ingest scope" becomes the compiled target set discovered from `Intermediates.noindex`, not just the single `--target` argument.

If a new compiled target appears in `Intermediates.noindex`, Orchard must perform a real ingest instead of skipping.

Compiled-target discovery must be scoped narrowly enough to avoid stale build
directories from unrelated configurations or historical builds under the same
DerivedData root. The implementation should only consider build directories
associated with the currently matched project/build context, not every
`*.build` directory that happens to exist anywhere under `Intermediates.noindex`.

## Filtering and system libraries

This design should not treat every `IndexStore` record as a full ingest candidate.

Expected policy:

- compiled project targets from `Intermediates.noindex` become first-class ingest targets
- system SDK modules, `pcm`, and framework internals are not promoted to ingest targets
- those external symbols may still appear as relationship endpoints through placeholder nodes

This preserves useful graph connectivity without turning SDK internals into project modules.

## Errors

Orchard should fail early in these cases:

### No matching `DerivedData`

Current behavior remains: explain the project searched and advise running an Xcode build first.

### No compiled targets discovered

New error:

- explain that `Intermediates.noindex` did not contain any `*.build` directories for this build
- recommend rebuilding the target in Xcode

### Requested target not compiled

New error:

- show requested `--target`
- show discovered compiled targets
- explain that the current `DerivedData` does not correspond to a build containing that target

### Cannot derive compiled-file scope

New error:

- explain that Orchard found compiled target names but could not derive the
  compiled source file set needed for safe filtering
- recommend rebuilding in Xcode or using a fresher DerivedData

## Testing Strategy

### Acceptance tests

Add acceptance coverage for:

- compiled target discovery from `Intermediates.noindex`
- entry target validation against compiled target set
- compiled-file filtering without `source-root`
- multi-target state persistence based on compiled targets
- fast-path invalidation when a newly compiled target appears

### Build discovery tests

Add focused tests for:

- extracting target names from `*.build` directories
- ignoring irrelevant directories in `Intermediates.noindex`
- deterministic ordering of compiled targets
- resolving a `DerivedData` root from an explicit `--index-store` path
- ignoring stale or unrelated `*.build` directories

### Regression coverage

Keep regression tests for the previously fixed issues:

- multi-target state merge
- placeholder reuse across targets
- fast-path skip only when all requested targets are already known

## Migration Notes

This is a behavior change for users who relied on `--source-root`.

Migration plan:

1. remove `--source-root` from CLI help and parsing
2. update reader / ingest filtering to use compiled files rather than path prefixes
3. update Orchard skill/docs/examples
4. describe the new default as "ingest compiled targets from current Xcode build"

## Recommendation

Adopt this design as the default Xcode ingest model.

It matches real build output, fixes the `zPSApp` class of misses, and removes the most brittle part of the current interface: directory-based source filtering.
