# Orchard Indexd Watch-Driven Ingest Design

Date: 2026-07-02
Status: Draft approved in chat
Scope: Background IndexStore watch, daemon-triggered ingest, and graph update serialization

## Summary

This design adds a watch-driven update loop on top of the existing `orchard-indexd` daemon so Orchard can keep both `IndexStoreDB` and the graph database warm without forcing every update to pay full cold-start cost.

The design keeps responsibilities intentionally narrow:

1. `orchard-indexd` keeps `IndexStoreDB` open, watches for compilation-unit changes, and decides when a graph refresh should be attempted.
2. `orchard ingest` remains the only writer for ingest state and graph database updates.
3. Locking remains entirely inside the CLI ingest path, so manual CLI runs and daemon-triggered CLI runs share one serialization mechanism.

The primary goal is eventual graph freshness with low background complexity: the daemon reacts quickly to changes, but graph writes are still performed by the existing CLI pipeline.

## Goals

- Keep `IndexStoreDB` hot in a long-lived daemon instead of reopening it for every ingest run.
- Detect Xcode / IndexStore changes in the background and trigger graph refresh automatically.
- Preserve one graph-write path by reusing `orchard ingest` rather than teaching the daemon to mutate graph state directly.
- Ensure eventual graph freshness even when a background update collides with a user-started ingest.
- Keep locking semantics shared across all ingest entry points.

## Non-Goals

- Do not make the daemon write graph database rows directly in this iteration.
- Do not move graph-update business logic out of `orchard ingest`.
- Do not add multi-writer graph concurrency.
- Do not auto-retry every ingest failure; only lock contention gets automatic retry treatment.
- Do not require the daemon to understand graph lock ownership or graph transaction semantics.

## Problem

Today Orchard already benefits from a persistent `orchard-indexd`, but graph updates still require an explicit CLI-driven ingest pass.

This leaves two gaps:

- `IndexStoreDB` can stay warm while the graph becomes stale.
- Background-triggered updates can race with manual CLI runs unless both paths share the same lock semantics.

Naively teaching the daemon to maintain its own lock would create split-brain behavior:

- daemon-triggered updates would obey one lock
- manual `orchard ingest` would obey another

That would make correctness depend on which entry point happened to start first.

## Design Principles

### One writer path

There must be exactly one code path that updates graph state:

- `orchard ingest`

The daemon may decide *when* to attempt an update, but it must not become a second graph writer.

### One lock authority

There must be exactly one lock authority:

- the CLI ingest process that is attempting to write graph state

The daemon does not hold or interpret graph locks. It only launches the same CLI the user can launch manually.

### Eventual consistency over immediate perfection

The system should prefer a simple, reliable "eventually catches up" model over a fragile attempt at perfect immediate synchronization.

That means:

- use watch events for responsiveness
- use debounce to collapse event bursts
- use targeted retry only for lock contention

## Proposed Architecture

### `orchard-indexd`

The daemon gains watch-driven scheduling responsibility:

- keep `IndexStoreDB` open with unit-event listening enabled
- observe that new IndexStore activity happened
- coalesce bursts of change with debounce
- launch `orchard ingest` asynchronously when work is pending and no ingest is already running
- retry later only when the launched ingest reports lock contention

The daemon does **not**:

- acquire graph locks
- update graph rows
- persist ingest state on behalf of the CLI

### `orchard ingest`

The CLI remains responsible for:

- acquiring the graph/update lock
- computing incremental changed files and deletion cleanup
- reading hot data from `orchard-indexd` / `IndexStoreDB`
- updating graph database and ingest state
- returning structured process outcomes to its caller

### Ingest launch context

When the daemon launches `orchard ingest`, it must do so with a concrete remembered ingest context rather than trying to rediscover intent from scratch on every event.

That remembered context should be the same logical scope a user previously established through an explicit ingest or warm-up request, for example:

- `project-dir`
- resolved `index-store`
- entry `target`
- any other ingest-scope arguments that materially affect graph contents

This keeps background updates scoped to a known project/session rather than letting the daemon guess which workspace or target should be refreshed.

In v1, the daemon should only schedule background graph refresh for sessions that already have such a remembered ingest context.

### Locking model

All graph-mutating ingest runs, regardless of how they were started, must acquire the same cross-process lock.

Examples:

- user runs `orchard ingest`
- daemon launches `orchard ingest`
- future automation launches `orchard ingest`

All of them contend on the same lock. The daemon itself is lock-agnostic.

### CLI file lock design

The lock should be owned by the CLI ingest process and keyed by the graph database path that the process is about to update.

Recommended design:

- lock identity is derived from the absolute graph database path
- lock file lives under `~/.orchard/locks/`
- file name uses a stable hash of the graph database path
- suggested pattern: `orchard-ingest-<hash>.lock`

This makes the protected resource explicit:

- one graph database path
- one lock namespace

It also guarantees that daemon-triggered and user-triggered ingests naturally contend on the same lock as long as they target the same graph database.

### Lock acquisition semantics

The CLI should attempt to acquire the lock immediately at startup using a non-blocking OS-level file lock.

Preferred behavior:

- open or create the lock file
- acquire a non-blocking advisory file lock
- if lock acquisition fails, exit with the dedicated `LOCK_BUSY` outcome

The daemon should not wait inside the child process. Retry scheduling remains a daemon concern after the CLI reports `LOCK_BUSY`.

### Lock scope

The lock should cover the entire ingest lifecycle, not only the final graph write phase.

That means the protected region includes:

- incremental boundary calculation
- changed-file and deletion cleanup planning
- reader / daemon interaction needed for that ingest run
- graph updates
- ingest-state persistence

This avoids two concurrent CLI processes independently computing incompatible incremental deltas against the same graph database and ingest-state files.

### Lock implementation notes

The design prefers a real OS-managed file lock over a "lock file exists" convention.

Reasons:

- stale lock files after crashes do not automatically imply stale OS locks
- OS lock release semantics are tied to process lifetime
- implementation is simpler and more trustworthy under abnormal exits

`fcntl`-style locking is the preferred first implementation direction. Exact API choice remains an implementation detail as long as the CLI presents the same observable behavior.

## Change Detection Model

### Why watch is useful

`IndexStoreDB` watch support is useful for two reasons:

- it keeps the backing database current in the background
- it gives the daemon a signal that "new unit activity happened"

This lets Orchard avoid cold reopen plus full initial poll for every update.

### Why watch is not enough on its own

Watch notifications do not directly replace Orchard's incremental graph logic.

Orchard still needs its own changed-file / cleanup calculation because graph updates require:

- deleting stale symbol rows for modified files
- handling deleted files
- constraining downstream graph work to the right file set

So watch provides a freshness trigger, not the final graph delta itself.

## Scheduling Model

### Normal path

1. unit activity is observed
2. daemon marks work as pending
3. daemon starts or resets a debounce timer
4. when debounce fires, daemon launches one asynchronous `orchard ingest`

### Lock-contention path

If the launched CLI cannot acquire the ingest lock, it returns a dedicated `LOCK_BUSY` outcome.

In that case:

- pending work remains pending
- the daemon schedules a retry timer
- when the retry timer fires, the daemon attempts another `orchard ingest`

### Non-lock failure path

If ingest fails for any other reason, the daemon does not loop indefinitely.

Instead it:

- keeps pending state unchanged
- logs the failure
- waits for a new watch event or operator intervention

This prevents repeated retries for real failures such as DB corruption, bad configuration, or unexpected reader errors.

The daemon should still keep the session marked as logically behind, so a later watch event or explicit CLI run can bring the graph current after the underlying problem is fixed.

## Process Model

`orchard ingest` should be launched asynchronously from the daemon.

The daemon must keep running its watch and timer loop while an ingest is in flight, but it must enforce single-flight semantics:

- only one ingest subprocess may run at a time

While an ingest is running:

- new watch events are still accepted
- they only advance pending state
- they do not start another ingest immediately

This preserves responsiveness without allowing overlapping graph writes.

## State Model

The daemon should use generation-based pending tracking rather than a single boolean.

Recommended state:

```swift
struct DaemonState {
    var seenGeneration: UInt64 = 0
    var ackedGeneration: UInt64 = 0

    var ingestRunning: Bool = false
    var ingestTargetGeneration: UInt64? = nil

    var debounceTask: Task<Void, Never>? = nil
    var retryTask: Task<Void, Never>? = nil
}
```

### Semantics

- `seenGeneration`
  - newest observed batch of IndexStore activity
- `ackedGeneration`
  - newest generation that the graph is known to have ingested successfully
- `ingestTargetGeneration`
  - the generation the current in-flight ingest is trying to catch up to

Derived predicate:

```text
hasPendingWork = ackedGeneration < seenGeneration
```

This model correctly handles the case where new IndexStore changes arrive while an ingest is already running.

## State Transitions

### Watch event

```text
seenGeneration += 1
scheduleDebounce()
```

### Debounce fires

```text
if !hasPendingWork:
  return

if ingestRunning:
  return

spawnIngest(targetGeneration = seenGeneration)
```

### Spawn ingest

```text
ingestRunning = true
ingestTargetGeneration = seenGeneration
async spawn "orchard ingest"
```

### Ingest exits successfully

```text
ingestRunning = false
ackedGeneration = max(ackedGeneration, ingestTargetGeneration)
ingestTargetGeneration = nil
cancel retry timer

if ackedGeneration < seenGeneration:
  schedule short debounce
```

The short debounce handles tail-end event bursts without immediately launching back-to-back ingest runs.

### Ingest exits with lock contention

```text
ingestRunning = false
ingestTargetGeneration = nil
arm retry timer
```

`ackedGeneration` does not advance.

### Ingest exits with any other error

```text
ingestRunning = false
ingestTargetGeneration = nil
log error
```

No automatic retry is scheduled for this class of failure.

## CLI Exit Contract

The daemon needs a stable machine-readable way to distinguish lock contention from other failures.

Recommended contract:

- exit `0`: ingest succeeded
- dedicated non-zero exit code: ingest could not proceed because the graph/update lock is already held
- all other non-zero exit codes: regular failure

The dedicated lock outcome should also print a short stable stderr marker for human-readable logs, but exit code is the primary contract.

Recommended preference:

- use both a dedicated exit code and a short error marker

This gives:

- robust machine interpretation for the daemon
- useful logs for developers

## Timers

Suggested first-pass defaults:

- normal debounce: `5s` to `10s`
- post-success tail debounce: `1s` to `2s`
- lock-busy retry: `10s` to `15s`

These values are intentionally conservative and can be tuned after observing real Xcode build behavior.

## Persistence

The generation counters are runtime scheduling state for the daemon and do not need to become the authoritative ingest-state file in the first version.

Authoritative persistent ingest state remains owned by `orchard ingest`.

If the daemon restarts:

- it may lose in-memory generation counters
- the next watch activity or explicit ingest will repopulate freshness state

This is acceptable for v1 because missed background scheduling after a daemon restart is recoverable through the next build event or manual ingest.

## Incremental Graph Update Interaction

This design does not remove the need for Orchard's existing changed-file logic.

That logic still determines:

- which previously ingested file-backed symbols need cleanup
- which files should be reprocessed
- what downstream graph work can stay incremental

The daemon-triggered path should therefore call the same ingest logic used by the manual CLI, not a separate shortcut path.

## Error Handling

### Lock contention

Treat as expected and recoverable:

- do not surface as a daemon-level failure
- schedule retry

### Reader / DB / graph failures

Treat as real failures:

- emit logs with exit status and brief context
- do not enter infinite retry
- rely on next watch event or explicit user action

### Daemon shutdown during in-flight ingest

The daemon should not kill a user-visible ingest that it already launched unless shutdown semantics explicitly require that behavior.

Preferred behavior:

- stop scheduling new work
- allow the in-flight child process to finish

## Testing

Testing should cover:

- watch event bursts collapsing into one scheduled ingest
- lock-busy result causing retry scheduling
- non-lock failure not causing retry loops
- generation accounting when new changes arrive during an in-flight ingest
- single-flight enforcement preventing overlapping ingest subprocesses
- successful ingest clearing pending work only up to the target generation

## Rollout Notes

The safest rollout is incremental:

1. daemon watch updates `IndexStoreDB`
2. daemon schedules CLI ingest from watch events
3. CLI returns explicit lock-busy outcome
4. daemon adds lock-busy retry timer

This keeps each step observable and limits the size of any regression surface.

## Open Implementation Decisions

The following decisions are intentionally left for implementation planning rather than design:

- exact lock-file mechanism and path
- exact dedicated exit code value for `LOCK_BUSY`
- exact process-launch API in Python / Swift glue
- whether retry/debounce scheduling lives in Swift daemon code only or partially in Python wrapper code

These are local implementation choices that do not change the architecture above.
