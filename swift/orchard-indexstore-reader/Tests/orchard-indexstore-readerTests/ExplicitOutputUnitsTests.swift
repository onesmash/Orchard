import IndexStoreDB
import XCTest
@testable import orchard_indexstore_reader
@testable import orchard_indexd

final class ExplicitOutputUnitsTests: XCTestCase {
  private func openIndexStoreDB(storePath: String) throws -> IndexStoreDB {
    let dylibPath = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"
    let library = try IndexStoreLibrary(dylibPath: dylibPath)
    let databasePath = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
      .path
    try FileManager.default.createDirectory(atPath: databasePath, withIntermediateDirectories: true)
    let db = try IndexStoreDB(
      storePath: storePath,
      databasePath: databasePath,
      library: library,
      waitUntilDoneInitializing: true,
      listenToUnitEvents: false
    )
    db.pollForUnitChangesAndWait(isInitialScan: true)
    return db
  }

  @discardableResult
  private func buildMinimalSwiftIndex(
    tmp: URL,
    sourceText: String = """
    public func callee() -> Int { return 1 }
    public func caller() -> Int { return callee() }
    """
  ) throws -> (storePath: URL, sourceFile: URL, unitOutputPath: URL) {
    let storePath = tmp.appendingPathComponent("idx", isDirectory: true)
    let srcDir = tmp.appendingPathComponent("src", isDirectory: true)
    let sourceFile = srcDir.appendingPathComponent("Lib.swift")
    let unitOutputPath = srcDir.appendingPathComponent("Lib.o")
    let dylibPath = srcDir.appendingPathComponent("libtest.dylib")

    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: srcDir, withIntermediateDirectories: true)
    try sourceText.write(to: sourceFile, atomically: true, encoding: .utf8)

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

    return (storePath, sourceFile, unitOutputPath)
  }

  func testScanProgressMessageHidesFilePaths() {
    XCTAssertEqual(scanProgressMessage(250, 7228), "scanning files 250/7228")
  }

  func testReaderScannerHelpersProduceExpectedFixtureRelations() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let db = try openIndexStoreDB(storePath: fixture.storePath.path)
    var lines: [String] = []

    let summary = try scanCanonicalSymbolsAndRelations(
      db: db,
      filePaths: [fixture.sourceFile.path],
      emitOccurrences: false,
      emit: { lines.append($0) }
    )

    XCTAssertEqual(summary.symbolCount, 3)
    XCTAssertEqual(summary.relationCount, 4)
    XCTAssertTrue(lines.contains { $0.contains("\"kind\":\"symbol\"") })
    XCTAssertTrue(lines.contains { $0.contains("\"kind\":\"relation\"") })
  }

  func testIndexdSessionManagerReusesSessionForSameStorePath() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let dylibPath = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"

    let first = try manager.getOrCreateSession(
      storePath: fixture.storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: dylibPath
    )
    let second = try manager.getOrCreateSession(
      storePath: fixture.storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: dylibPath
    )

    XCTAssertEqual(first.session.sessionId, second.session.sessionId)
    XCTAssertFalse(first.reused)
    XCTAssertTrue(second.reused)
  }

  func testIndexdSessionManagerEvictsIdleSession() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let created = try manager.getOrCreateSession(
      storePath: fixture.storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )

    XCTAssertNotNil(manager.session(id: created.session.sessionId))

    let evicted = manager.evictIdleSessions(idleForAtLeast: 0)

    XCTAssertEqual(evicted, 1)
    XCTAssertNil(manager.session(id: created.session.sessionId))
  }

  func testIndexdSessionManagerDoesNotEvictSessionWithPendingWork() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let created = try manager.getOrCreateSession(
      storePath: fixture.storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )

    created.session.recordWatchActivity()

    let evicted = manager.evictIdleSessions(idleForAtLeast: 0)

    XCTAssertEqual(evicted, 0)
    XCTAssertNotNil(manager.session(id: created.session.sessionId))
  }

  func testIndexdSessionManagerRecreatesSessionAfterEviction() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let manager = SessionManager()
    let first = try manager.getOrCreateSession(
      storePath: fixture.storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )

    XCTAssertEqual(manager.evictIdleSessions(idleForAtLeast: 0), 1)

    let second = try manager.getOrCreateSession(
      storePath: fixture.storePath.path,
      sourceRoots: [tmp.path],
      targets: ["Zoom"],
      dylibPath: nil
    )

    XCTAssertFalse(second.reused)
    XCTAssertTrue(first.session !== second.session)
  }

  func testSourceRootsForTargetsGroupsFilesAcrossMultipleRoots() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = tmp
      .appendingPathComponent("Index.noindex", isDirectory: true)
      .appendingPathComponent("DataStore", isDirectory: true)
    let intermediates = tmp
      .appendingPathComponent("Index.noindex", isDirectory: true)
      .appendingPathComponent("Build", isDirectory: true)
      .appendingPathComponent("Intermediates.noindex", isDirectory: true)
    let buildDB = intermediates
      .appendingPathComponent("XCBuildData", isDirectory: true)
      .appendingPathComponent("build.db")
    let outputA = intermediates
      .appendingPathComponent("Zoom.build", isDirectory: true)
      .appendingPathComponent("Debug-iphonesimulator", isDirectory: true)
      .appendingPathComponent("iZipow.build", isDirectory: true)
      .appendingPathComponent("Objects-normal", isDirectory: true)
      .appendingPathComponent("arm64", isDirectory: true)
      .appendingPathComponent("Alpha.o")
    let outputB = intermediates
      .appendingPathComponent("Zoom.build", isDirectory: true)
      .appendingPathComponent("Debug-iphonesimulator", isDirectory: true)
      .appendingPathComponent("iZipow.build", isDirectory: true)
      .appendingPathComponent("Objects-normal", isDirectory: true)
      .appendingPathComponent("arm64", isDirectory: true)
      .appendingPathComponent("Beta.o")

    try FileManager.default.createDirectory(at: buildDB.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)

    let sourceA = "/repo/ios-client/Zoom/Classes/Foo/Alpha.mm"
    let sourceB = "/repo/client-app-common/Shared/Beta.cpp"
    let sql = """
    CREATE TABLE key_names (id INTEGER PRIMARY KEY, key STRING UNIQUE);
    INSERT INTO key_names (id, key) VALUES
      (1, 'CP1:target-iZipow-123-iphonesimulator-iphonesimulator:Debug:CompileC \(outputA.path) \(sourceA) normal arm64 objective-c++ com.apple.compilers.llvm.clang.1_0.compiler'),
      (2, 'CP1:target-iZipow-123-iphonesimulator-iphonesimulator:Debug:CompileC \(outputB.path) \(sourceB) normal arm64 c++ com.apple.compilers.llvm.clang.1_0.compiler');
    """
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
    process.arguments = [buildDB.path, sql]
    try process.run()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationStatus, 0)

    let roots = try sourceRootsForTargets(indexStorePath: storePath.path, targets: ["Zoom"])

    XCTAssertEqual(
      Set(roots),
      Set([
        "/repo/client-app-common/Shared",
        "/repo/ios-client/Zoom/Classes/Foo",
      ])
    )
  }

  func testSourceRootsForTargetsAlsoReadsSwiftOutputFileMaps() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = tmp
      .appendingPathComponent("Index.noindex", isDirectory: true)
      .appendingPathComponent("DataStore", isDirectory: true)
    let intermediates = tmp
      .appendingPathComponent("Index.noindex", isDirectory: true)
      .appendingPathComponent("Build", isDirectory: true)
      .appendingPathComponent("Intermediates.noindex", isDirectory: true)
    let buildDB = intermediates
      .appendingPathComponent("XCBuildData", isDirectory: true)
      .appendingPathComponent("build.db")
    let outputFileMap = intermediates
      .appendingPathComponent("Zoom.build", isDirectory: true)
      .appendingPathComponent("Debug-iphonesimulator", isDirectory: true)
      .appendingPathComponent("iZipow.build", isDirectory: true)
      .appendingPathComponent("Objects-normal", isDirectory: true)
      .appendingPathComponent("arm64", isDirectory: true)
      .appendingPathComponent("iZipow-OutputFileMap.json")

    try FileManager.default.createDirectory(at: outputFileMap.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: buildDB.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)
    try """
    {
      "/repo/ios-client/Zoom/Classes/App/Foo.swift": {
        "index-unit-output-path": "/Zoom.build/Debug-iphonesimulator/iZipow.build/Objects-normal/arm64/Foo.o"
      },
      "/repo/client-app-common/Shared/Bar.swift": {
        "index-unit-output-path": "/Zoom.build/Debug-iphonesimulator/iZipow.build/Objects-normal/arm64/Bar.o"
      }
    }
    """.write(to: outputFileMap, atomically: true, encoding: .utf8)

    let sql = """
    CREATE TABLE key_names (id INTEGER PRIMARY KEY, key STRING UNIQUE);
    INSERT INTO key_names (id, key) VALUES (
      1,
      'CP2:target-iZipow-123-iphonesimulator-iphonesimulator:Debug:WriteAuxiliaryFile \(outputFileMap.path)'
    );
    """
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
    process.arguments = [buildDB.path, sql]
    try process.run()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationStatus, 0)

    let roots = try sourceRootsForTargets(indexStorePath: storePath.path, targets: ["Zoom"])

    XCTAssertEqual(
      Set(roots),
      Set([
        "/repo/client-app-common/Shared",
        "/repo/ios-client/Zoom/Classes/App",
      ])
    )
  }

  func testExplicitOutputUnitPathsReadsSwiftOutputFileMaps() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = tmp
      .appendingPathComponent("Index.noindex", isDirectory: true)
      .appendingPathComponent("DataStore", isDirectory: true)
    let intermediates = tmp
      .appendingPathComponent("Build", isDirectory: true)
      .appendingPathComponent("Intermediates.noindex", isDirectory: true)
    let outputFileMap = intermediates
      .appendingPathComponent("Zoom.build", isDirectory: true)
      .appendingPathComponent("Debug-iphonesimulator", isDirectory: true)
      .appendingPathComponent("iZipow.build", isDirectory: true)
      .appendingPathComponent("Objects-normal", isDirectory: true)
      .appendingPathComponent("arm64", isDirectory: true)
      .appendingPathComponent("iZipow-OutputFileMap.json")
    let buildDB = intermediates
      .appendingPathComponent("XCBuildData", isDirectory: true)
      .appendingPathComponent("build.db")

    try FileManager.default.createDirectory(at: outputFileMap.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: buildDB.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)
    try """
    {
      "/tmp/Foo.swift": {
        "index-unit-output-path": "/Zoom.build/Debug-iphonesimulator/iZipow.build/Objects-normal/arm64/Foo.o"
      },
      "/tmp/Bar.swift": {
        "index-unit-output-path": "/Zoom.build/Debug-iphonesimulator/iZipow.build/Objects-normal/arm64/Bar.o"
      }
    }
    """.write(to: outputFileMap, atomically: true, encoding: .utf8)
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
    process.arguments = [
      buildDB.path,
      "CREATE TABLE key_names (id INTEGER PRIMARY KEY, key STRING UNIQUE); INSERT INTO key_names (id, key) VALUES (1, 'CP2:target-iZipow-123-:Debug:WriteAuxiliaryFile \(outputFileMap.path)');",
    ]
    try process.run()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationStatus, 0)

    let paths = try explicitOutputUnitPaths(indexStorePath: storePath.path)

    XCTAssertEqual(
      paths,
      [
        "/Zoom.build/Debug-iphonesimulator/iZipow.build/Objects-normal/arm64/Bar.o",
        "/Zoom.build/Debug-iphonesimulator/iZipow.build/Objects-normal/arm64/Foo.o",
      ]
    )
  }

  func testExplicitOutputUnitPathsAlsoReadsCompileCAndPCMOutputs() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let storePath = tmp
      .appendingPathComponent("Index.noindex", isDirectory: true)
      .appendingPathComponent("DataStore", isDirectory: true)
    let intermediates = tmp
      .appendingPathComponent("Build", isDirectory: true)
      .appendingPathComponent("Intermediates.noindex", isDirectory: true)
    let buildDB = intermediates
      .appendingPathComponent("XCBuildData", isDirectory: true)
      .appendingPathComponent("build.db")
    let pcmPath = intermediates
      .appendingPathComponent("SwiftExplicitPrecompiledModules", isDirectory: true)
      .appendingPathComponent("Foo-ABC123.pcm")
    let objcOutput = intermediates
      .appendingPathComponent("Zoom.build", isDirectory: true)
      .appendingPathComponent("Debug-iphonesimulator", isDirectory: true)
      .appendingPathComponent("Ext.build", isDirectory: true)
      .appendingPathComponent("Objects-normal", isDirectory: true)
      .appendingPathComponent("arm64", isDirectory: true)
      .appendingPathComponent("Thing.o")

    try FileManager.default.createDirectory(at: buildDB.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: pcmPath.deletingLastPathComponent(), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: storePath, withIntermediateDirectories: true)
    FileManager.default.createFile(atPath: pcmPath.path, contents: Data())

    let sql = """
    CREATE TABLE key_names (id INTEGER PRIMARY KEY, key STRING UNIQUE);
    INSERT INTO key_names (id, key) VALUES (
      1,
      'CP1:target-Ext-123-:Debug:CompileC \(objcOutput.path) /tmp/Thing.m normal arm64 objective-c com.apple.compilers.llvm.clang.1_0.compiler'
    );
    """
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
    process.arguments = [buildDB.path, sql]
    try process.run()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationStatus, 0)

    let paths = try explicitOutputUnitPaths(indexStorePath: storePath.path)

    XCTAssertEqual(
      Set(paths),
      Set([
        "/SwiftExplicitPrecompiledModules/Foo-ABC123.pcm",
        "/Zoom.build/Debug-iphonesimulator/Ext.build/Objects-normal/arm64/Thing.o",
      ])
    )
  }

  func testUnitNamesContainingFileDoesNotExposeExplicitOutputPathIdentifiers() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let db = try openIndexStoreDB(storePath: fixture.storePath.path)
    let unitNames = db.unitNamesContainingFile(path: fixture.sourceFile.path)

    XCTAssertFalse(unitNames.isEmpty, "unitNames should not be empty")
    XCTAssertFalse(unitNames.contains(fixture.unitOutputPath.path), "unitNames=\(unitNames)")
    XCTAssertNil(db.dateOfUnitFor(outputPath: unitNames[0]), "unitNames=\(unitNames)")
    XCTAssertNil(db.dateOfUnitFor(outputPath: fixture.unitOutputPath.path))
  }

  func testCollectRawUnitOutputPathMappingsReadsExplicitOutputFile() throws {
    let tmp = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: tmp) }

    let fixture = try buildMinimalSwiftIndex(tmp: tmp)
    let db = try openIndexStoreDB(storePath: fixture.storePath.path)
    let dylibPath = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"

    let mappings = try orchard_indexstore_reader.collectRawUnitOutputPathMappings(
      indexStorePath: fixture.storePath.path,
      dylibPath: dylibPath,
      db: db,
      filePaths: [fixture.sourceFile.path]
    )

    XCTAssertEqual(mappings.count, 1)
    XCTAssertEqual(mappings[0].mainFile, fixture.sourceFile.path)
    XCTAssertFalse(mappings[0].unitName.isEmpty)
    XCTAssertFalse(mappings[0].outputFile.isEmpty)
    XCTAssertNotEqual(mappings[0].outputFile, fixture.unitOutputPath.path)
    XCTAssertTrue(mappings[0].outputFile.hasSuffix("Lib-1.o"))
  }
}
