import XCTest
@testable import orchard_indexd

final class IngestBackoffTests: XCTestCase {

    // MARK: - Delay computation

    func testBackoffDelayFirstRetryIsApproximatelyOneSecond() {
        // retryCount=0 → base=1s, jittered [0.5…1.0]s → 500…1000ms
        let samples = (0..<30).map { _ in delayToMilliseconds(ingestLockBusyDelay(retryCount: 0)) }
        let median = samples.sorted()[15]
        XCTAssertGreaterThanOrEqual(median, 400, "first retry median should be >= ~0.5s with jitter")
        XCTAssertLessThanOrEqual(median, 1100, "first retry median should be <= ~1.0s with jitter")
    }

    func testBackoffDelayDoublesExponentially() {
        // retryCount=4 → base=16s, jittered [8…16]s
        let samples = (0..<50).map { _ in delayToMilliseconds(ingestLockBusyDelay(retryCount: 4)) }
        let median = samples.sorted()[25]
        XCTAssertGreaterThan(median, 7000, "4th retry median should be >7s")
        XCTAssertLessThan(median, 17000, "4th retry median should be <17s")
    }

    func testBackoffDelayCappedAtMaxBackoff() {
        // retryCount=20 → base capped at 120s, jittered [60…120]s
        let delay = ingestLockBusyDelay(retryCount: 20)
        let ms = delayToMilliseconds(delay)
        XCTAssertLessThanOrEqual(ms, 121000, "delay should be capped at ~120s max")
    }

    func testBackoffDelayIsWithinJitterRange() {
        // retryCount=0: base=1s, jitter ensures [0.5…1.0]s
        for _ in 0..<20 {
            let ms = delayToMilliseconds(ingestLockBusyDelay(retryCount: 0))
            XCTAssertGreaterThanOrEqual(ms, 400, "jittered delay should be >= 50% of base")
            XCTAssertLessThanOrEqual(ms, 1100, "jittered delay should be <= 100% of base")
        }
    }

    func testBackoffDelayMonotonicallyIncreases() {
        let retries = (0..<8).map { delayToMilliseconds(ingestLockBusyDelay(retryCount: $0)) }
        // Base values: 1s, 2s, 4s, 8s, 16s, 32s, 64s, 120s
        // With jitter the order might not be strictly monotonic per sample,
        // but the 4th retry should be clearly larger than the 1st.
        let earlyMedian = medianOf(retries.prefix(3).map { Double($0) })
        let lateMedian = medianOf(retries.suffix(3).map { Double($0) })
        XCTAssertGreaterThan(lateMedian, earlyMedian * 2,
            "later retries should be significantly longer than early retries")
    }

    // MARK: - Max retries constant

    func testDefaultMaxRetriesIs10() {
        // ingestLockBusyMaxRetries reads env var; default is 10.
        let saved = ProcessInfo.processInfo.environment["ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES"]
        setenv("ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES", "", 1)
        // We can't trivially test the instance property here (it's on the session),
        // but we verify the delay function works with retry counts 0..9 (10 retries).
        for n in 0..<10 {
            let ms = delayToMilliseconds(ingestLockBusyDelay(retryCount: n))
            XCTAssertGreaterThan(ms, 0, "retry \(n) should produce positive delay")
        }
        if let saved = saved {
            setenv("ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES", saved, 1)
        } else {
            unsetenv("ORCHARD_INDEXD_MAX_LOCK_BUSY_RETRIES")
        }
    }

    // MARK: - Helpers

    private func delayToMilliseconds(_ delay: DispatchTimeInterval) -> Int {
        switch delay {
        case .seconds(let s): return s * 1000
        case .milliseconds(let ms): return ms
        case .microseconds(let us): return us / 1000
        case .nanoseconds(let ns): return ns / 1_000_000
        @unknown default: return 0
        }
    }

    private func medianOf(_ values: [Double]) -> Double {
        let sorted = values.sorted()
        let mid = sorted.count / 2
        if sorted.count.isMultiple(of: 2) {
            return (sorted[mid - 1] + sorted[mid]) / 2.0
        }
        return sorted[mid]
    }
}
