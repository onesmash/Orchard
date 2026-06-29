import XCTest
@testable import orchard_indexstore_reader

final class ExplicitOutputUnitsTests: XCTestCase {
  func testScanProgressMessageHidesFilePaths() {
    XCTAssertEqual(scanProgressMessage(250, 7228), "scanning files 250/7228")
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
}
