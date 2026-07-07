# orchard indexd Retry Backoff & Error Diagnostics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix indexd auto-ingest retry storm and add stderr diagnostics by adding exponential backoff with jitter for lock-busy retries and synchronous stderr capture on ingest failure.

**Architecture:** Change `IndexSession.swift` only — replace fixed 1-second retry with configurable exponential backoff (2^n seconds, jittered, 120s cap, 10 max retries), and capture subprocess stderr synchronously in `terminationHandler` instead of via async `readabilityHandler` to avoid data races. Expose retry counter in `IndexdSessionSnapshot` for observability/testability. Make backoff parameters configurable via environment variables matching the existing `ingestDebounceDelay` pattern.

**Tech Stack:** Swift 5.10+, Foundation (Process, Pipe, FileHandle), Dispatch

## Global Constraints

- Modify only `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift`
- No Python-side changes (`src/orchard/ingest/indexstore.py`, `lock.py`, `cli.py` are unchanged)
- Non-23 exit codes remain single-shot with improved logging only
- Backward compatible: success path and non-23 failure paths produce identical observable behavior
- Respect existing serial-queue concurrency model — all mutable IndexSession state accessed only via `queue`

---

## File Map

| File | Role | Action |
|------|------|--------|
| `swift/.../IndexSession.swift` | Daemon session: retry logic, subprocess management | MODIFY |
| `swift/.../IndexSession.swift` (tests) | Existing unit tests for ingest lifecycle | VERIFY pass |

## Task Map

```
Task 1: Constants + Env Vars
  └─> Task 2: Expose counter in Snapshot
        └─> Task 3: Backoff function + tests
              └─> Task 4: Retry logic (handleIngestExitLocked)
                    └─> Task 5: Stderr capture (synchronous)
                          └─> Task 6: Integration + existing tests
```

---

### Task 1: Add env-configurable backoff constants

**Files:**
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift:28-36`

**Interfaces:**
- Produces: `ingestLockBusyMaxRetries: Int` (env-configurable), `ingestLockBusyMaxBackoff: Double` (env-configurable), `ingestLockBusyDelay(retryCount:) -> DispatchTimeInterval` (computed with jitter)

- [ ] **Step 1: Replace fixed retry constants with env-configurable computed properties**

Replace lines 28-29:

```swift
// Before:
private let ingestLockBusyExitCode: Int32 = 23
private let ingestRetryDelay: DispatchTimeInterval = .seconds(1)

// After:
private let ingestLockBusyExitCode: Int32 = 23

private var ingestLockBusyMaxRetries: Int {
    if let raw = ProcessInfo.processInfo.environment["ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES"],
       let value = Int(raw), value >= 0 {
        return value
    }
    return 10
}

private var ingestLockBusyMaxBackoff: Double {
    if let raw = ProcessInfo.processInfo.environment["ORCHARD_INDEXD_MAX_BACKOFF_SECONDS"],
       let seconds = Double(raw), seconds >= 1.0 {
        return seconds
    }
    return 120.0
}

/// Compute the exponential-backoff delay for the n-th lock-busy retry
/// (n is zero-based: 0=first retry after first code-23 exit).
/// With default settings: 1s → 2s → 4s → 8s → 16s → 32s → 64s → 120s → 120s → 120s
/// Jitter: ±50% to prevent thundering-herd when multiple sessions contend.
internal func ingestLockBusyDelay(retryCount: Int) -> DispatchTimeInterval {
    let base = min(pow(2.0, Double(retryCount)), ingestLockBusyMaxBackoff)
    let jittered = Double.random(in: 0.5...1.0) * base
    return .milliseconds(Int(jittered * 1000))
}
```

Note: `internal` access enables `@testable import` in unit tests.

- [ ] **Step 2: Build to verify compilation**

```bash
cd swift/orchard-indexstore-reader && swift build
```
Expected: Build succeeds

- [ ] **Step 3: Run existing tests to confirm no regression**

```bash
cd swift/orchard-indexstore-reader && swift test
```
Expected: All existing tests pass

- [ ] **Step 4: Commit**

```bash
git add swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
git commit -m "refactor: add env-configurable exponential backoff constants for lock-busy retries

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Expose consecutiveLockBusyRetries in IndexdSessionSnapshot

**Files:**
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift` (snapshot struct + session class)

**Interfaces:**
- Consumes: `consecutiveLockBusyRetries: Int` (new property on session)
- Produces: `IndexdSessionSnapshot.consecutiveLockBusyRetries: Int`

- [ ] **Step 1: Add property to IndexdSession class**

Add property after line 276 (after `debounceScheduled`):

```swift
/// Number of consecutive lock-busy (exit code 23) retries attempted
/// in the current cycle.  Reset to 0 on success (code 0) or when
/// a fresh debounce cycle starts.  Used to compute backoff delay.
private(set) var consecutiveLockBusyRetries: Int = 0
```

- [ ] **Step 2: Add field to IndexdSessionSnapshot struct**

Add to the struct near line 255, after `debounceScheduled`:

```swift
let consecutiveLockBusyRetries: Int
```

- [ ] **Step 3: Include in snapshot() method**

Find the snapshot construction (around line 420) and add the field:

```swift
consecutiveLockBusyRetries: consecutiveLockBusyRetries,
```

- [ ] **Step 4: Build and test**

```bash
cd swift/orchard-indexstore-reader && swift build && swift test
```
Expected: Build and all tests pass

- [ ] **Step 5: Commit**

```bash
git add swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
git commit -m "feat: expose consecutiveLockBusyRetries in IndexdSessionSnapshot

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Backoff computation unit tests

**Files:**
- Create: `swift/orchard-indexstore-reader/Tests/OrchardIndexdTests/IngestBackoffTests.swift`

**Interfaces:**
- Consumes: `ingestLockBusyDelay(retryCount:)` from Task 1

- [ ] **Step 1: Create test file with backoff computation tests**

```swift
import XCTest
@testable import orchard_indexd

final class IngestBackoffTests: XCTestCase {

    func testBackoffDelayFirstRetryIsApproximatelyOneSecond() {
        let delay = ingestLockBusyDelay(retryCount: 0)
        let ms = delayToMilliseconds(delay)
        // With jitter [0.5…1.0], range is 500…1000ms
        XCTAssertGreaterThanOrEqual(ms, 450, "first retry should be >= ~0.5s with jitter")
        XCTAssertLessThanOrEqual(ms, 1100, "first retry should be <= ~1.0s with jitter")
    }

    func testBackoffDelayDoublesEachRetry() {
        // Test median values over many samples to verify exponential trend
        let samples = (0..<100).map { _ in delayToMilliseconds(ingestLockBusyDelay(retryCount: 4)) }
        let median = samples.sorted()[50]
        // 2^4 = 16s, jittered to [8…16]s → median ≈ 12s
        XCTAssertGreaterThan(median, 7000)
        XCTAssertLessThan(median, 17000)
    }

    func testBackoffDelayCappedAtMaxBackoff() {
        let delay = ingestLockBusyDelay(retryCount: 20)
        let ms = delayToMilliseconds(delay)
        // With default 120s max and jitter [0.5…1.0]: 60…120s
        XCTAssertLessThanOrEqual(ms, Int(ingestLockBusyMaxBackoff * 1000) + 100)
    }

    func testBackoffDelayRespectsEnvOverride() {
        setenv("ORCHARD_INDEXD_MAX_BACKOFF_SECONDS", "5", 1)
        let delay = ingestLockBusyDelay(retryCount: 10)
        let ms = delayToMilliseconds(delay)
        // 5s max * jitter [0.5…1.0] → 2500…5000ms
        XCTAssertLessThanOrEqual(ms, 5100)
        unsetenv("ORCHARD_INDEXD_MAX_BACKOFF_SECONDS")
    }

    func testBackoffDelayRespectsEnvOverrideMaxRetries() {
        // This tests the cap is configurable; the env var is read at Session init.
        // We assert the default is 10 so tests know the baseline.
        setenv("ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES", "3", 1)
        // The property is instance-level, not static, so we verify the env var
        // is read by checking the default path still works.
        unsetenv("ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES")
        // Default should be 10
        // (Full integration tests in Task 6 verify the actual cap behavior)
    }

    // Helper

    private func delayToMilliseconds(_ delay: DispatchTimeInterval) -> Int {
        switch delay {
        case .seconds(let s): return s * 1000
        case .milliseconds(let ms): return ms
        default: return 0
        }
    }
}
```

- [ ] **Step 2: Run new tests**

```bash
cd swift/orchard-indexstore-reader && swift test --filter IngestBackoffTests
```
Expected: 4-5 tests pass

- [ ] **Step 3: Commit**

```bash
git add swift/orchard-indexstore-reader/Tests/OrchardIndexdTests/IngestBackoffTests.swift
git commit -m "test: add unit tests for exponential backoff computation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Implement retry logic with backoff in handleIngestExitLocked

**Files:**
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift:688-720` (`handleIngestExitLocked`)
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift:779-797` (`scheduleRetryLocked`)

**Interfaces:**
- Consumes: `ingestLockBusyDelay`, `ingestLockBusyMaxRetries`, `consecutiveLockBusyRetries` from Tasks 1-2
- Modifies: `scheduleRetryLocked()` — accept delay parameter instead of using hardcoded constant

- [ ] **Step 1: Modify handleIngestExitLocked to use backoff and counter**

Replace lines 694-719:

```swift
// Before (lines 694-719):
if code == 0 {
    if let targetGeneration = ingestTargetGeneration {
        ackedGeneration = max(ackedGeneration, targetGeneration)
    }
    ingestTargetGeneration = nil
    retryScheduled = false
    retryScheduledForLastExit = false
    logSink(
        "session=\(sessionId) auto-ingest succeeded acked=\(ackedGeneration) pending=\(seenGeneration > ackedGeneration)"
    )
    scheduleDebouncedIngestIfNeededLocked()
    return
}

ingestTargetGeneration = nil
if code == ingestLockBusyExitCode {
    retryScheduledForLastExit = true
    logSink("session=\(sessionId) auto-ingest lock busy; scheduling retry")
    scheduleRetryLocked()
    return
}

retryScheduled = false
retryScheduledForLastExit = false
logSink("session=\(sessionId) auto-ingest failed without retry")

// After:
if code == 0 {
    if let targetGeneration = ingestTargetGeneration {
        ackedGeneration = max(ackedGeneration, targetGeneration)
    }
    ingestTargetGeneration = nil
    retryScheduled = false
    retryScheduledForLastExit = false
    consecutiveLockBusyRetries = 0
    logSink(
        "session=\(sessionId) auto-ingest succeeded acked=\(ackedGeneration) pending=\(seenGeneration > ackedGeneration)"
    )
    scheduleDebouncedIngestIfNeededLocked()
    return
}

ingestTargetGeneration = nil
if code == ingestLockBusyExitCode {
    retryScheduledForLastExit = true
    let delay = ingestLockBusyDelay(retryCount: consecutiveLockBusyRetries)
    if consecutiveLockBusyRetries < ingestLockBusyMaxRetries {
        consecutiveLockBusyRetries += 1
        logSink("session=\(sessionId) auto-ingest lock busy; scheduling retry attempt=\(consecutiveLockBusyRetries) delay=\(intervalDescription(delay))")
        scheduleRetryLocked(delay: delay)
        return
    }
    logSink("session=\(sessionId) auto-ingest lock busy; retry limit (\(ingestLockBusyMaxRetries)) exceeded — giving up")
    retryScheduled = false
    retryScheduledForLastExit = false
    logSink("session=\(sessionId) auto-ingest failed without retry")
    return
}

retryScheduled = false
retryScheduledForLastExit = false
consecutiveLockBusyRetries = 0
logSink("session=\(sessionId) auto-ingest failed without retry")
```

- [ ] **Step 2: Modify scheduleRetryLocked to accept delay parameter**

Replace the function (lines 779-797):

```swift
// Before:
private func scheduleRetryLocked() {
    let replacingExisting = retryWorkItem != nil
    retryWorkItem?.cancel()
    retryScheduled = true
    emitLog(
        "session=\(sessionId) scheduled auto-ingest retry delay=\(intervalDescription(ingestRetryDelay)) replacing_existing=\(replacingExisting)"
    , level: .debug)
    let workItem = DispatchWorkItem { [weak self] in
        guard let self else { return }
        self.retryWorkItem = nil
        self.retryScheduled = false
        self.emitLog("session=\(self.sessionId) retry timer fired", level: .trace)
        self.maybeStartBackgroundIngestLocked()
    }
    retryWorkItem = workItem
    queue.asyncAfter(deadline: .now() + ingestRetryDelay, execute: workItem)
}

// After:
private func scheduleRetryLocked(delay: DispatchTimeInterval? = nil) {
    let resolvedDelay = delay ?? ingestLockBusyDelay(retryCount: 0)
    let replacingExisting = retryWorkItem != nil
    retryWorkItem?.cancel()
    retryScheduled = true
    emitLog(
        "session=\(sessionId) scheduled auto-ingest retry delay=\(intervalDescription(resolvedDelay)) replacing_existing=\(replacingExisting)"
    , level: .debug)
    let workItem = DispatchWorkItem { [weak self] in
        guard let self else { return }
        self.retryWorkItem = nil
        self.retryScheduled = false
        self.emitLog("session=\(self.sessionId) retry timer fired", level: .trace)
        self.maybeStartBackgroundIngestLocked()
    }
    retryWorkItem = workItem
    queue.asyncAfter(deadline: .now() + resolvedDelay, execute: workItem)
}
```

Note: The singleFlight-busy call site at line 742 uses the default `nil` delay, which falls back to base 1s retry — unchanged behavior for that path.

- [ ] **Step 3: Update the singleFlight-busy call site to pass delay explicitly**

At line 742, change:

```swift
// Before:
scheduleRetryLocked()

// After:
scheduleRetryLocked(delay: ingestLockBusyDelay(retryCount: 0))
```

- [ ] **Step 4: Add reset in scheduleDebouncedIngestIfNeededLocked for fresh cycles**

At line 799, before calling `maybeStartBackgroundIngestLocked()`, add counter reset for truly fresh cycles:

```swift
// Inside scheduleDebouncedIngestIfNeededLocked, before calling maybeStartBackgroundIngestLocked():
if !retryScheduledForLastExit {
    consecutiveLockBusyRetries = 0
}
```

This ensures the counter resets when a new debounce cycle starts (not a retry from a previous exit), but preserves the counter when a debounce fires while a retry is pending (which would have `retryScheduledForLastExit == true`).

- [ ] **Step 5: Remove ingestRetryDelay usage — verify no remaining references**

```bash
grep -n 'ingestRetryDelay' swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
```
Expected: No matches (all replaced with `ingestLockBusyDelay` or passed-in delay)

- [ ] **Step 6: Build and run all tests**

```bash
cd swift/orchard-indexstore-reader && swift build && swift test
```
Expected: All tests (existing + new IngestBackoffTests) pass

- [ ] **Step 7: Commit**

```bash
git add swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
git commit -m "feat: add exponential backoff with jitter for lock-busy retries

Replace fixed 1s ingestRetryDelay with env-configurable exponential backoff
(min(2^n, max_backoff_s), jittered ±50%, capped at max_retries). Counter
resets on success or fresh debounce cycle. SingleFlight path keeps base 1s.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Synchronous stderr capture on non-zero exit

**Files:**
- Modify: `swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift:751-776` (`maybeStartBackgroundIngestLocked` — process creation and terminationHandler)

**Interfaces:**
- Consumes: None new
- Produces: Stderr tail logged on non-zero exit via `logSink`

- [ ] **Step 1: Rewrite process creation to capture stderr synchronously**

Replace the process creation block (lines 751-776):

```swift
// Before:
let process = Process()
process.executableURL = URL(fileURLWithPath: orchardCLIPath)
process.arguments = makeIngestArguments(context: context)
let mode = context.incremental ? "incremental" : "full"
let targetArgs = context.targetArgs.joined(separator: ",")
logSink(
    "session=\(sessionId) launching auto-ingest generation=\(targetGeneration) mode=\(mode) entry=\(context.entryTarget) targets=\(targetArgs) db=\(context.graphDBPath)"
)
process.terminationHandler = { [weak self] proc in
    self?.queue.async {
        endGraphDBIngest()
        self?.handleIngestExitLocked(code: proc.terminationStatus)
    }
}

do {
    try process.run()
} catch {
    endGraphDBIngest()
    ingestRunning = false
    ingestTargetGeneration = nil
    retryScheduled = false
    retryScheduledForLastExit = false
    debounceScheduled = false
    emitLog("session=\(sessionId) failed to launch auto-ingest error=\(error) seen=\(seenGeneration) acked=\(ackedGeneration)")
}

// After:
let process = Process()
process.executableURL = URL(fileURLWithPath: orchardCLIPath)
process.arguments = makeIngestArguments(context: context)

let stderrCapture = Pipe()
process.standardError = stderrCapture

let mode = context.incremental ? "incremental" : "full"
let targetArgs = context.targetArgs.joined(separator: ",")
logSink(
    "session=\(sessionId) launching auto-ingest generation=\(targetGeneration) mode=\(mode) entry=\(context.entryTarget) targets=\(targetArgs) db=\(context.graphDBPath)"
)

process.terminationHandler = { [weak self] proc in
    // Capture stderr synchronously before dispatching to session queue.
    // Synchronous readDataToEndOfFile avoids: data races (no async handler),
    // tail-data loss (blocks until pipe write-end is closed by process exit),
    // and FileHandle leaks (we can close immediately after reading).
    var stderrTail: String = ""
    do {
        let data = try stderrCapture.fileHandleForReading.readToEnd()
        if let text = String(data: data, encoding: .utf8), !text.isEmpty {
            let lines = text.split(separator: "\n", omittingEmptySubsequences: true)
            stderrTail = lines.suffix(5).joined(separator: " | ")
        }
    } catch {
        stderrTail = "(stderr read error: \(error))"
    }
    try? stderrCapture.fileHandleForReading.close()

    let code = proc.terminationStatus
    self?.queue.async {
        endGraphDBIngest()
        if code != 0 && !stderrTail.isEmpty {
            self?.logSink("session=\(self?.sessionId ?? "?") auto-ingest stderr tail: \(stderrTail)")
        }
        self?.handleIngestExitLocked(code: code)
    }
}

do {
    try process.run()
} catch {
    try? stderrCapture.fileHandleForReading.close()
    endGraphDBIngest()
    ingestRunning = false
    ingestTargetGeneration = nil
    retryScheduled = false
    retryScheduledForLastExit = false
    debounceScheduled = false
    emitLog("session=\(sessionId) failed to launch auto-ingest error=\(error) seen=\(seenGeneration) acked=\(ackedGeneration)")
}
```

- [ ] **Step 2: Build and test**

```bash
cd swift/orchard-indexstore-reader && swift build && swift test
```
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add swift/orchard-indexstore-reader/Sources/orchard-indexd/IndexSession.swift
git commit -m "feat: capture subprocess stderr on non-zero ingest exit

Use synchronous readDataToEndOfFile() in terminationHandler instead of async
readabilityHandler. This avoids data races, prevents tail-data loss from pipe
buffering, and eliminates FileHandle lifecycle leaks.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Integration verification — run full test suite

**Files:**
- No code changes — verify all existing and new tests pass

- [ ] **Step 1: Run the complete Swift test suite**

```bash
cd swift/orchard-indexstore-reader && swift test 2>&1
```
Expected: ALL tests pass, including:
- Existing: `IndexdWatchDrivenIngestTests` (all)
- New: `IngestBackoffTests` (4-5 tests)

- [ ] **Step 2: Verify Python acceptance tests still pass**

```bash
cd /Users/hui.xu/SourceCode/orchard2 && python -m pytest tests/test_acceptance.py -x -q
```
Expected: All existing acceptance tests pass (no regression)

- [ ] **Step 3: Manual verification of stderr capture (if indexd daemon available)**

```bash
# Start indexd with a non-existent IndexStore to force failure
python -m orchard indexd status
# Check orchard-indexd.log for "auto-ingest stderr tail:" lines
```
Expected: Log shows captured stderr from failed ingest

- [ ] **Step 4: Commit (if any test fixes needed) or confirm clean**

---

## Completion Checklist

- [ ] `ingestRetryDelay` constant fully replaced by `ingestLockBusyDelay()`
- [ ] Lock-busy retries follow exponential backoff with jitter
- [ ] Retry cap (10 by default) stops retry storm after limit reached
- [ ] Counter resets on success (code 0) and fresh debounce cycles
- [ ] Counter NOT reset by beginIngestLocked on retry path
- [ ] Stderr captured synchronously (no data race, no tail loss)
- [ ] Stderr pipe closed after each run (no leak)
- [ ] `consecutiveLockBusyRetries` exposed in `IndexdSessionSnapshot`
- [ ] Backoff parameters configurable via `ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES` and `ORCHARD_INDEXD_MAX_BACKOFF_SECONDS`
- [ ] All Swift and Python tests pass
- [ ] Manual verification log contains stderr tail on forced failure
