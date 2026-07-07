---
perspective: development
---

**日期**: 2026-07-07
**审查者**: subagent (development perspective)
**设计文档**: 2026-07-07-fix-indexd-retry-backoff-and-error-classification-design.md

# 开发视角审查

**Overall Assessment: APPROVE WITH CHANGES**

## Finding 1 -- Data race on stderrRing array (CRITICAL)

`readabilityHandler` executes on Foundation's internal I/O queue. `terminationHandler` dispatch-targets to `self.queue`. There is zero synchronization between these two queues. Swift `Array` is not thread-safe for concurrent mutation and read. This is a textbook data race.

**Fix**: Protect `stderrRing` with `NSLock` or `os_unfair_lock`.

## Finding 2 -- Race between readabilityHandler and terminationHandler causes tail data loss (HIGH)

When the subprocess exits, `terminationHandler` fires but the pipe's kernel buffer may still hold undelivered data. Foundation does not guarantee all `readabilityHandler` invocations complete before `terminationHandler` fires.

**Fix**: In `terminationHandler`, call `readDataToEndOfFile()` on the pipe's read handle to synchronously drain remaining buffered data before extracting the tail.

## Finding 3 -- Off-by-one in exponential backoff computation (MEDIUM)

`pow(2.0, Double(retryCount))` with `retryCount=1` produces 2s, not 1s as the design table promises.

**Fix**: Use `pow(2.0, Double(retryCount - 1))` for 1-based counter.

## Finding 4 -- beginIngestLocked() reset breaks exponential backoff on retry path (MEDIUM)

`beginIngestLocked()` is called from both the retry path and debounce path. If retry path resets the counter, every retry goes 0→1→back to beginning. Backoff is defeated.

**Fix**: Reset only on success exit (code 0) and fresh debounce starts (not retry-triggered).

## Finding 5 -- Missing pipe/FileHandle cleanup (MEDIUM)

No `readabilityHandler = nil` or `FileHandle.close()` after process exit.

**Fix**: Set `readabilityHandler = nil` and `try? close()` in terminationHandler.

## Finding 6 -- String boundary issue with availableData (LOW)

`FileHandle.availableData` may split multi-byte UTF-8 sequences. `String(data:encoding:.utf8)` returns nil, silently discarding bytes.

**Fix**: Accept as acceptable for diagnostic use (99.9% OK).

## Finding 7 -- omittingEmptySubsequences: false without justification (LOW)

Produces empty tail lines, wasting a slot in the 5-line output.

**Fix**: Use `omittingEmptySubsequences: true` (default).
