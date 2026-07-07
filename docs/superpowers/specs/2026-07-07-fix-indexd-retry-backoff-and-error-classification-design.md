# Design: orchard indexd Auto-Ingest Retry Backoff & Error Diagnostics

**Date**: 2026-07-07  
**Status**: approved  
**Scope**: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift` only

## Problem Summary

Analysis of `orchard-indexd.log` (2026-07-07, zach.chen's machine) revealed three issues:

| # | Severity | Symptom | Root Cause |
|---|----------|---------|------------|
| 1 | HIGH | ~60 retries/sec when graph.db lock is busy | `ingestRetryDelay = .seconds(1)` with no backoff and no upper limit (`IndexSession.swift:29`) |
| 2 | MEDIUM | Exit code 120 failures have zero diagnostic info | indexd `Process` does not capture stderr from the `orchard ingest` subprocess |
| 3 | MEDIUM | Non-23 exit codes never retry | `handleIngestExitLocked()` only retries on `ingestLockBusyExitCode` (23); all other non-zero codes immediately `failed without retry` |

Scope decision: problems 1 and 2 are addressed in this design. Problem 3 (retry for non-23 codes) is deferred — the immediate friction is the retry storm and zero visibility into failures.

## Design

### Change 1: Exponential Backoff for Lock-Busy Retries

**Current state** (`IndexSession.swift:29`, `scheduleRetryLocked()`):

```swift
private let ingestRetryDelay: DispatchTimeInterval = .seconds(1)
// scheduleRetryLocked always uses ingestRetryDelay, no backoff, no ceiling
```

**Target state**:

- Track `consecutiveLockBusyRetries: Int` on the session (starts at 0, resets when any auto-ingest exits with code 0)
- Compute delay per retry attempt: `min(2^retryCount, 120)` seconds
  - Attempt 1: 1s → 2: 2s → 3: 4s → 4: 8s → 5: 16s → 6: 32s → 7: 64s → 8–10: 120s
- Cap at 10 retries; on the 11th lock-busy exit, fall through to `failed without retry`
- `consecutiveLockBusyRetries` resets to 0 when:
  - Any auto-ingest exits with code 0 (success)
  - `scheduleDebouncedIngestIfNeededLocked()` fires and starts a fresh ingest cycle

**Rationale for the parameters**:

- `2^n` provides rapid initial retries (first 5 attempts cover ~30s) then slows down
- 120s cap prevents excessive waits while still being reasonable
- 10 retries at `2^n` with 120s cap covers ~10 minutes total — more than enough for a typical ingest (30-90s) to complete and release the lock
- If the lock is still busy after 10 retries, the holder is likely a zombie process; continued retrying is harmful

### Change 2: Stderr Capture from Ingest Subprocess

**Current state** (`maybeStartBackgroundIngestLocked()`):

```swift
let process = Process()
process.executableURL = URL(fileURLWithPath: orchardCLIPath)
process.arguments = makeIngestArguments(context: context)
process.terminationHandler = { [weak self] proc in
    self?.queue.async {
        endGraphDBIngest()
        self?.handleIngestExitLocked(code: proc.terminationStatus)
    }
}
try process.run()
```

**Target state**:

- Attach a `Pipe` to `process.standardError`
- Accumulate stderr lines in a ring buffer (max 100 lines per run) during execution
- On non-zero exit: log the last 5 stderr lines as `auto-ingest stderr tail:` entries
- On zero exit: discard the buffer (success path doesn't need diagnostics)
- The buffer is not persisted across ingest runs — each `Process` launch gets a fresh buffer

**Why ring buffer instead of full capture**:

- A large IndexStore scan can produce many stderr lines (progress, warnings)
- We only need the tail — the final error messages are what matter for diagnosis
- 100-line buffer is generous enough to capture multi-line error cascades
- Zero memory concern: 100 × ~200 bytes ≈ 20KB per ingest run

## File Changes (1 file)

### `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift`

1. **Replace** `ingestRetryDelay` constant with a computed delay function:

```swift
// Before:
private let ingestRetryDelay: DispatchTimeInterval = .seconds(1)

// After:
private let ingestLockBusyMaxRetries = 10
private let ingestLockBusyMaxBackoff: Double = 120.0  // seconds

private func ingestLockBusyDelay(retryCount: Int) -> DispatchTimeInterval {
    let seconds = min(pow(2.0, Double(retryCount)), ingestLockBusyMaxBackoff)
    return .milliseconds(Int(seconds * 1000))
}
```

2. **Add** `consecutiveLockBusyRetries` property to the session class (initialized to 0).

3. **Modify** `handleIngestExitLocked(code:)`:

```swift
// On success (code == 0): reset consecutiveLockBusyRetries = 0
// On lock busy (code == 23):
//   - if consecutiveLockBusyRetries < ingestLockBusyMaxRetries → schedule retry with backoff
//   - else → fall through to failed without retry, log warning
// On other non-zero: unchanged (failed without retry)
```

4. **Modify** `scheduleRetryLocked()` to accept and use the computed delay.

5. **Modify** `maybeStartBackgroundIngestLocked()` to wire up stderr pipe:

```swift
let stderrPipe = Pipe()
process.standardError = stderrPipe
var stderrRing: [String] = []
stderrRing.reserveCapacity(100)
stderrPipe.fileHandleForReading.readabilityHandler = { handle in
    let data = handle.availableData
    guard !data.isEmpty, let str = String(data: data, encoding: .utf8) else { return }
    let lines = str.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    for line in lines {
        if stderrRing.count >= 100 { stderrRing.removeFirst() }
        stderrRing.append(line)
    }
}
```

In `terminationHandler`, if `proc.terminationStatus != 0`, log the stderr tail before calling `handleIngestExitLocked`.

6. **Add** `beginIngestLocked()` to reset `consecutiveLockBusyRetries` when a non-lock-busy exit triggers a fresh ingest start (the debounce path naturally creates a fresh cycle).

## Non-Goals (Explicitly Deferred)

- **Retrying non-23 exit codes**: Only lock-busy errors get retry. Exit code 120 and other failures are still one-shot with improved logging.
- **Python-side changes**: `src/orchard/ingest/indexstore.py`, `lock.py`, `cli.py` are not modified.
- **Structured error classification**: The stderr capture is human-readable log improvement, not a parseable protocol. A future iteration could add `ORCHARD_ERROR:type=<code>` markers if needed.

## Testing

- **Unit test (Swift)**: Verify backoff delay computation produces expected values for retry counts 1–11.
- **Manual test (simulated)**: Run two concurrent `orchard ingest` against the same graph.db; observe the second process's lock-busy retries follow exponential backoff in the indexd log.
- **Manual test (error visibility)**: Force an ingest failure (e.g., point at a non-existent IndexStore) and verify stderr tail appears in the indexd log.

## Rollback

The changes are additive to retry behavior and logging. Rolling back means reverting to the 1-second fixed retry and no stderr capture. The graph.db data and ingest logic are unaffected.
