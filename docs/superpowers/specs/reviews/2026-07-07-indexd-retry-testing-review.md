---
perspective: testing
---

**日期**: 2026-07-07
**审查者**: subagent (testing perspective)
**设计文档**: 2026-07-07-fix-indexd-retry-backoff-and-error-classification-design.md

# 测试视角审查

**Overall Assessment: APPROVE WITH CHANGES**

## Critical Gaps

### 1. consecutiveLockBusyRetries not observable (HIGH)
Counter is proposed as private state. Not exposed in IndexdSessionSnapshot. Without snapshot exposure, no test can observe reset logic or cap behavior.

**Fix**: Add to IndexdSessionSnapshot, matching pattern of `retryScheduled`, `retryScheduledForLastExit`.

### 2. Retry cap behavior untested (HIGH)
"10th retry → failed without retry" is a critical behavioral change. No test verifies this.

### 3. Stderr capture untestable without Process factory (HIGH)
Current tests point orchardCLIPath at `/definitely/missing/orchard` to trigger launch error, bypassing terminationHandler entirely. Stderr pipe code path never exercised.

**Fix**: Make Process creation injectable, or use `/bin/sh -c` as test binary.

### 4. Env var configuration for testability (MEDIUM)
120s cap makes fast tests impossible. Follow existing pattern for ingestDebounceDelay (env var override).

**Fix**: Wire `ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES` and `ORCHARD_INDEXD_MAX_BACKOFF_SECONDS`.

## Recommended Test Cases (14 total)

**Group A: Backoff delay computation**
- `testBackoffDelayProducesExpectedValues` — verify 1s/2s/4s/8s/16s/32s/64s/120s/120s/120s

**Group B: Retry counter lifecycle**
- `testLockBusyRetriesStopAfterCap` — 11 × code 23, verify 11th is "failed without retry"
- `testLockBusyRetriesResetOnSuccess` — 3 × code 23 → code 0 → code 23, verify counter reset
- `testLockBusyRetriesNotResetByOtherFailure` — code 23 × 2 → code 1, verify counter not reset

**Group C: Path interaction**
- `testSingleFlightAndLockBusyUseIndependentCounters`
- `testSeenGenerationAdvancingDoesNotResetRetryCounter`

**Group D: Stderr capture**
- `testStderrTailLoggedOnNonZeroExit`
- `testStderrRingBufferTruncatesBeyond100Lines`
- `testStderrBufferNotLoggedOnZeroExit`
- `testStderrBufferHandlesPartialUTF8`
- `testStderrPipeDoesNotLeakFileHandle`

**Group E: Regression guardrails**
- Update existing `testLockBusySchedulesRetryButOtherFailuresDoNot`
- Update existing `testGraphDBSingleFlightConflictPreservesPendingWorkAndRetries`

## Edge Cases

- Daemon killed during retry backoff: counter resets on restart (safe, document as intentional)
- seenGeneration changes during retry: retry picks up latest generation (code handles this)
- Lock holder crashes during backoff: backoff delays recovery unnecessarily (document trade-off)
- Two daemon instances retrying same lock: independent counters, no coordination (acceptable)
- Thread safety: `readabilityHandler` runs on Foundation I/O queue, needs lock or serial queue
