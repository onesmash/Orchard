---
perspective: design
---

**日期**: 2026-07-07
**审查者**: subagent (design perspective)
**设计文档**: 2026-07-07-fix-indexd-retry-backoff-and-error-classification-design.md

# 设计视角审查

**Overall Assessment: APPROVE WITH CHANGES**

## Finding 1 -- scheduleRetryLocked has two call sites, singleFlight path not addressed (MEDIUM)

`scheduleRetryLocked()` is called from: (a) `handleIngestExitLocked` when code==23 (lock busy), and (b) `maybeStartBackgroundIngestLocked` when `beginGraphDBIngest()` returns false (singleFlight busy). The design only addresses path (a). Path (b) will keep the old 1-second fixed delay.

**Recommendation**: Either apply same backoff unconditionally, or document that singleFlight path intentionally stays at 1s.

## Finding 2 -- Missing jitter in exponential backoff (MEDIUM)

Deterministic `min(2^n, 120)` creates thundering-herd: two indexd sessions targeting the same graph.db will synchronize their retry attempts and repeatedly collide.

**Fix**: `delay = Double.random(in: 0.5...1.0) * min(pow(2.0, retryCount-1), 120)`

## Finding 3 -- Exit code 120 is hypothetical (LOW)

Code 120 is never emitted by orchard source. The only exit codes in cmd_ingest are 0, 2, and 23. Clarify in spec.

## Finding 4 -- Python stderr pipe buffering (LOW)

Python uses block buffering (8KB) when stderr is a pipe (vs. line buffering for TTY). Data arrives in chunks at process exit, not line-by-line during execution. This actually works in favor of the ring buffer approach.

## Backward Compatibility

Clean — no breaking changes. Success path identical. Non-23 failure paths identical. Lock-busy path changes from fixed-1s to exponential backoff (intended improvement).
