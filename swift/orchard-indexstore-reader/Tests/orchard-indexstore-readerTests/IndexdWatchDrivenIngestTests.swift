import IndexStoreDB
import XCTest
@testable import orchard_indexd

final class IndexdWatchDrivenIngestTests: XCTestCase {
  private final class SynchronizedCounter {
    private let lock = NSLock()
    private var storage = 0

    @discardableResult
    func increment() -> Int {
      lock.lock()
      defer { lock.unlock() }
      storage += 1
      return storage
    }

    var value: Int {
      lock.lock()
      defer { lock.unlock() }
      return storage
    }
  }

  private final class SynchronizedLogBuffer {
    private let lock = NSLock()
    private var storage: [String] = []

    func append(_ line: String) {
      lock.lock()
      defer { lock.unlock() }
      storage.append(line)
    }

    var lines: [String] {
      lock.lock()
      defer { lock.unlock() }
      return storage
    }
  }

  private func waitUntil(
    timeout: TimeInterval = 1.0,
    pollInterval: TimeInterval = 0.01,
    _ condition: @escaping () -> Bool
  ) {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
      if condition() {
        return
      }
      RunLoop.current.run(until: Date().addingTimeInterval(pollInterval))
    }
  }

  private func registerPayload(
    storePath: String = "/tmp/store",
    graphDBPath: String = "/tmp/graph.db",
    contextOverrides: [String: Any] = [:]
  ) -> [String: Any] {
    var context: [String: Any] = [
      "projectDir": "/tmp/project",
      "indexStorePath": storePath,
      "graphDBPath": graphDBPath,
      "targetArgs": ["Zoom"],
      "entryTarget": "Zoom",
      "incremental": true,
    ]
    for (key, value) in contextOverrides {
      context[key] = value
    }
    return [
      "storePath": storePath,
      "graphDBPath": graphDBPath,
      "context": context,
    ]
  }

  private func buildMinimalSwiftIndex(tmp: URL) throws -> URL {
    let storePath = tmp.appendingPathComponent("idx", isDirectory: true)
    let srcDir = tmp.appendingPathComponent("src", isDirectory: true)
    let sourceFile = srcDir.appendingPathComponent("Lib.swift")
    let unitOutputPath = srcDir.appendingPathComponent("Lib.o")
    let dylibPath = srcDir.appendingPathComponent("libtest.dylib")

    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: srcDir, withIntermediateDirectories: true)
    try """
    public func callee() -> Int { return 1 }
    public func caller() -> Int { return callee() }
    """.write(to: sourceFile, atomically: true, encoding: .utf8)

    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/swiftc")
    process.arguments = [
      "-index-store-path", storePath.path,
      "-index-unit-output-path", unitOutputPath.path,
      sourceFile.path,
      "-emit-library",
      "-o", dylibPath.path,
    ]
    try process.run()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationStatus, 0)

    return storePath
  }

  private func makeTestSession() throws -> IndexdSession {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    addTeardownBlock {
      try? FileManager.default.removeItem(at: tmp)
    }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let context = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )

    return try IndexdSession(
      sessionId: UUID().uuidString,
      storePath: storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil,
      ingestContext: context
    )
  }

  func testRegisterOrRefreshSessionReusesSameSessionForSameStoreAndGraphPair() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let firstContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )
    let secondContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "Zoom",
      incremental: true
    )

    let first = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: firstContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let second = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: secondContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    XCTAssertEqual(first.session.sessionId, second.session.sessionId)
    XCTAssertFalse(first.reused)
    XCTAssertTrue(second.reused)
  }

  func testRegisterOrRefreshSessionReplacesRememberedContextOnReuse() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let firstContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )
    let secondContext = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "zPSApp",
      incremental: false
    )

    _ = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: firstContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let refreshed = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: secondContext,
      sourceRoots: [tmp.path],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    let snapshot = refreshed.session.snapshot()
    XCTAssertEqual(snapshot.ingestContext, secondContext)
    XCTAssertEqual(snapshot.sourceRoots, [tmp.path])
    XCTAssertEqual(snapshot.targets, ["Zoom", "zPSApp"])
    XCTAssertEqual(snapshot.seenGeneration, 0)
    XCTAssertEqual(snapshot.ackedGeneration, 0)
    XCTAssertFalse(snapshot.ingestRunning)
  }

  func testLatestRegistrationWinsForRememberedContextOnReusedSession() throws {
    let manager = SessionManager()
    let firstContext = IngestContext(
      projectDir: "/tmp/project",
      indexStorePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom"],
      entryTarget: "Zoom",
      incremental: true
    )
    let secondContext = IngestContext(
      projectDir: "/tmp/project",
      indexStorePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "zPSApp",
      incremental: false
    )

    let first = try manager.registerOrRefreshSession(
      storePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      ingestContext: firstContext,
      sourceRoots: ["/tmp/project"],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let refreshed = try manager.registerOrRefreshSession(
      storePath: "/tmp/store",
      graphDBPath: "/tmp/graph.db",
      ingestContext: secondContext,
      sourceRoots: ["/tmp/project/next"],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    XCTAssertEqual(first.session.sessionId, refreshed.session.sessionId)
    let snapshot = refreshed.session.snapshot()
    XCTAssertEqual(snapshot.ingestContext, secondContext)
    XCTAssertEqual(snapshot.ingestContext?.targetArgs, ["Zoom", "zPSApp"])
    XCTAssertEqual(snapshot.sourceRoots, ["/tmp/project/next"])
    XCTAssertEqual(snapshot.targets, ["Zoom", "zPSApp"])
  }

  func testWarmAndRegisterSessionReuseSameSessionForSameStorePath() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let context = IngestContext(
      projectDir: tmp.path,
      indexStorePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      targetArgs: ["Zoom", "zPSApp"],
      entryTarget: "Zoom",
      incremental: true
    )

    let warmed = try manager.getOrCreateSession(
      storePath: storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )
    let registered = try manager.registerOrRefreshSession(
      storePath: storePath.path,
      graphDBPath: "/tmp/graph.db",
      ingestContext: context,
      sourceRoots: [tmp.path],
      targets: ["Zoom", "zPSApp"],
      dylibPath: nil
    )

    XCTAssertEqual(warmed.session.sessionId, registered.session.sessionId)
    XCTAssertFalse(warmed.reused)
    XCTAssertTrue(registered.reused)
    XCTAssertEqual(registered.session.snapshot().ingestContext, context)
    XCTAssertEqual(registered.session.snapshot().targets, ["Zoom", "zPSApp"])
  }

  func testSingleFlightIsScopedPerGraphDBPath() {
    let manager = SessionManager()

    XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
    XCTAssertFalse(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
    XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/b.db"))

    manager.endGraphDBIngest(graphDBPath: "/tmp/a.db")

    XCTAssertTrue(manager.beginGraphDBIngest(graphDBPath: "/tmp/a.db"))
  }

  func testRegisterSessionBackfillsPendingWorkAfterBackgroundIngestConfiguration() throws {
    let session = try makeTestSession()
    let beginInFlightCalls = SynchronizedCounter()

    waitUntil {
      !session.snapshot().debounceScheduled
    }
    let baselineGeneration = session.snapshot().seenGeneration
    session.recordWatchActivity()
    var snapshot = session.snapshot()
    XCTAssertEqual(snapshot.seenGeneration, baselineGeneration + 1)
    XCTAssertFalse(snapshot.debounceScheduled)

    session.maybeScheduleBackgroundIngest(
      orchardCLIPath: "/definitely/missing/orchard",
      beginInFlight: {
        _ = beginInFlightCalls.increment()
        return false
      },
      endInFlight: {}
    )

    waitUntil {
      session.snapshot().hasIngestContext
    }

    snapshot = session.snapshot()
    XCTAssertGreaterThanOrEqual(snapshot.seenGeneration, baselineGeneration + 1)
    waitUntil {
      beginInFlightCalls.value > 0
    }

    snapshot = session.snapshot()
    XCTAssertGreaterThan(snapshot.seenGeneration, baselineGeneration)
    XCTAssertGreaterThan(beginInFlightCalls.value, 0)
    XCTAssertFalse(snapshot.ingestRunning)
  }

  func testRegisterSessionPrimesUnitEventMonitoringWhenBackgroundIngestIsConfigured() throws {
    let session = try makeTestSession()
    let logs = SynchronizedLogBuffer()
    session.logSink = { logs.append($0) }

    XCTAssertFalse(session.snapshot().hasPolled)

    session.maybeScheduleBackgroundIngest(
      orchardCLIPath: "/definitely/missing/orchard",
      beginInFlight: { false },
      endInFlight: {}
    )

    waitUntil {
      session.snapshot().hasPolled
    }

    XCTAssertTrue(session.snapshot().hasPolled)
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("background warm ready") }))
  }

  func testUnitEventBatchOnlyAdvancesGenerationAfterProcessingCompletes() throws {
    let session = try makeTestSession()
    let baselineGeneration = session.snapshot().seenGeneration

    session.simulateUnitEventBatchForTesting(added: 2, completed: 0)
    XCTAssertEqual(session.snapshot().seenGeneration, baselineGeneration)

    session.simulateUnitEventBatchForTesting(added: 0, completed: 1)
    XCTAssertEqual(session.snapshot().seenGeneration, baselineGeneration)

    session.simulateUnitEventBatchForTesting(added: 0, completed: 1)
    waitUntil {
      session.snapshot().seenGeneration == baselineGeneration + 1
    }

    XCTAssertEqual(session.snapshot().seenGeneration, baselineGeneration + 1)
  }

  func testUnitEventBatchDoesNotEmitFineGrainedLogsAtDefaultLevel() throws {
    unsetenv("ORCHARD_LOG_LEVEL")
    let session = try makeTestSession()
    let logs = SynchronizedLogBuffer()
    session.logSink = { logs.append($0) }

    session.simulateUnitEventBatchForTesting(added: 2, completed: 2)
    waitUntil {
      session.snapshot().seenGeneration > 0
    }

    XCTAssertFalse(logs.lines.contains(where: { $0.contains("unit-event added") }))
    XCTAssertFalse(logs.lines.contains(where: { $0.contains("unit-event completed") }))
    XCTAssertFalse(logs.lines.contains(where: { $0.contains("observed unit activity") }))
  }

  func testGraphDBSingleFlightConflictPreservesPendingWorkAndRetries() throws {
    setenv("ORCHARD_LOG_LEVEL", "trace", 1)
    defer { unsetenv("ORCHARD_LOG_LEVEL") }
    let session = try makeTestSession()
    let beginInFlightCalls = SynchronizedCounter()
    let logs = SynchronizedLogBuffer()
    session.logSink = { logs.append($0) }

    session.recordWatchActivity()
    session.maybeScheduleBackgroundIngest(
      orchardCLIPath: "/definitely/missing/orchard",
      beginInFlight: {
        beginInFlightCalls.increment() > 1
      },
      endInFlight: {}
    )

    waitUntil(timeout: 2.5) {
      beginInFlightCalls.value >= 2 && !session.snapshot().ingestRunning
    }

    let snapshot = session.snapshot()
    XCTAssertGreaterThanOrEqual(beginInFlightCalls.value, 2)
    XCTAssertGreaterThan(snapshot.seenGeneration, snapshot.ackedGeneration)
    XCTAssertFalse(snapshot.ingestRunning)
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("scheduled auto-ingest debounce") }))
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("auto-ingest deferred; reason=graph_db_single_flight_busy") }))
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("scheduled auto-ingest retry") }))
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("retry timer fired") }))
  }

  func testGraphDBSingleFlightConflictHidesSchedulingNoiseAtDefaultLevel() throws {
    unsetenv("ORCHARD_LOG_LEVEL")
    defer { unsetenv("ORCHARD_LOG_LEVEL") }
    let session = try makeTestSession()
    let beginInFlightCalls = SynchronizedCounter()
    let logs = SynchronizedLogBuffer()
    session.logSink = { logs.append($0) }

    session.recordWatchActivity()
    session.maybeScheduleBackgroundIngest(
      orchardCLIPath: "/definitely/missing/orchard",
      beginInFlight: {
        beginInFlightCalls.increment() > 1
      },
      endInFlight: {}
    )

    waitUntil(timeout: 2.5) {
      beginInFlightCalls.value >= 2 && !session.snapshot().ingestRunning
    }

    XCTAssertTrue(logs.lines.contains(where: { $0.contains("auto-ingest deferred; reason=graph_db_single_flight_busy") }))
    XCTAssertFalse(logs.lines.contains(where: { $0.contains("scheduled auto-ingest debounce") }))
    XCTAssertFalse(logs.lines.contains(where: { $0.contains("scheduled auto-ingest retry") }))
    XCTAssertFalse(logs.lines.contains(where: { $0.contains("retry timer fired") }))
    XCTAssertFalse(logs.lines.contains(where: { $0.contains("debounce timer fired") }))
  }

  func testLockBusySchedulesRetryButOtherFailuresDoNot() throws {
    let session = try makeTestSession()
    let logs = SynchronizedLogBuffer()
    session.logSink = { logs.append($0) }

    XCTAssertTrue(session.beginIngest(targetGeneration: 3))
    session.handleIngestExit(code: 23)

    var snapshot = session.snapshot()
    XCTAssertTrue(snapshot.retryScheduled)
    XCTAssertTrue(snapshot.retryScheduledForLastExit)
    XCTAssertFalse(snapshot.ingestRunning)

    XCTAssertTrue(session.beginIngest(targetGeneration: 4))
    session.handleIngestExit(code: 1)

    snapshot = session.snapshot()
    XCTAssertFalse(snapshot.retryScheduled)
    XCTAssertFalse(snapshot.retryScheduledForLastExit)
    XCTAssertFalse(snapshot.ingestRunning)
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("auto-ingest lock busy; scheduling retry") }))
    XCTAssertTrue(logs.lines.contains(where: { $0.contains("auto-ingest failed without retry") }))
  }

  func testDefaultIndexdLogSinkWritesToConfiguredLogFile() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    addTeardownBlock {
      unsetenv("ORCHARD_INDEXD_LOG_PATH")
      try? FileManager.default.removeItem(at: tmp)
    }

    let logPath = tmp.appendingPathComponent("orchard-indexd.log").path
    setenv("ORCHARD_INDEXD_LOG_PATH", logPath, 1)

    defaultIndexdLogSink("hello from test")

    let contents = try String(contentsOfFile: logPath, encoding: .utf8)
    XCTAssertTrue(contents.contains("hello from test"))
  }

  func testDefaultIndexdLogSinkRollsConfiguredLogFileWhenDayChanges() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    addTeardownBlock {
      unsetenv("ORCHARD_INDEXD_LOG_PATH")
      unsetenv("ORCHARD_INDEXD_LOG_MAX_FILES")
      try? FileManager.default.removeItem(at: tmp)
    }

    let logPath = tmp.appendingPathComponent("orchard-indexd.log").path
    setenv("ORCHARD_INDEXD_LOG_PATH", logPath, 1)
    setenv("ORCHARD_INDEXD_LOG_MAX_FILES", "2", 1)

    defaultIndexdLogSink("yesterday message")
    let yesterday = Date().addingTimeInterval(-25 * 60 * 60)
    try FileManager.default.setAttributes(
      [.modificationDate: yesterday],
      ofItemAtPath: logPath
    )

    defaultIndexdLogSink("today message")

    let currentContents = try String(contentsOfFile: logPath, encoding: .utf8)
    let rolledContents = try String(contentsOfFile: "\(logPath).1", encoding: .utf8)

    XCTAssertTrue(currentContents.contains("today message"))
    XCTAssertTrue(rolledContents.contains("yesterday message"))
  }

  func testDecodeRegisterSessionParamsRejectsMismatchedPaths() throws {
    let payload = registerPayload(
      contextOverrides: [
        "indexStorePath": "/tmp/other-store",
        "graphDBPath": "/tmp/other-graph.db",
      ]
    )

    do {
      _ = try decodeRegisterSessionParams(from: payload)
      XCTFail("expected mismatched paths to be rejected")
    } catch let error as RegisterSessionDecodeError {
      XCTAssertEqual(error.code, "mismatched_store_path")
    }
  }

  func testDecodeRegisterSessionParamsRejectsMalformedContext() throws {
    let payload: [String: Any] = [
      "storePath": "/tmp/store",
      "graphDBPath": "/tmp/graph.db",
      "context": [
        "projectDir": "/tmp/project",
        "indexStorePath": "/tmp/store",
        "targetArgs": ["Zoom"],
        "entryTarget": "Zoom",
        "incremental": true,
      ],
    ]

    do {
      _ = try decodeRegisterSessionParams(from: payload)
      XCTFail("expected malformed context to be rejected")
    } catch let error as RegisterSessionDecodeError {
      XCTAssertEqual(error.code, "invalid_context")
    }
  }
}
