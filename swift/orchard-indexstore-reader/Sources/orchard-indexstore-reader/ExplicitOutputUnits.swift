import Foundation

private typealias OutputFileMap = [String: [String: String]]

private func buildDatabasePathForIndexNoIndex(indexNoIndexURL: URL) -> String {
  indexNoIndexURL
    .appendingPathComponent("Build", isDirectory: true)
    .appendingPathComponent("Intermediates.noindex", isDirectory: true)
    .appendingPathComponent("XCBuildData", isDirectory: true)
    .appendingPathComponent("build.db")
    .path
}

private func canonicalizeOutputPath(_ path: String, intermediatesURL: URL) -> String {
  let standardizedIntermediates = intermediatesURL.standardizedFileURL.path
  let standardizedPath = URL(fileURLWithPath: path).standardizedFileURL.path
  let absolutePrefix = standardizedIntermediates + "/"
  if standardizedPath == standardizedIntermediates {
    return "/"
  }
  if standardizedPath.hasPrefix("/") && !standardizedPath.hasPrefix(absolutePrefix) {
    return path
  }
  if standardizedPath.hasPrefix(absolutePrefix) {
    return "/" + standardizedPath.dropFirst(absolutePrefix.count)
  }
  if standardizedPath.hasPrefix(standardizedIntermediates) {
    return "/" + standardizedPath.dropFirst(standardizedIntermediates.count)
  }
  return path.hasPrefix("/") ? path : "/" + path
}

private func sqliteRows(databasePath: String, sql: String) -> [String] {
  let process = Process()
  process.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
  process.arguments = [databasePath, sql]

  let output = Pipe()
  process.standardOutput = output
  process.standardError = Pipe()

  do {
    try process.run()
  } catch {
    return []
  }

  let data = output.fileHandleForReading.readDataToEndOfFile()
  process.waitUntilExit()

  guard process.terminationStatus == 0 else { return [] }

  let text = String(data: data, encoding: .utf8) ?? ""
  return text.split(separator: "\n").map(String.init)
}

private func compileCOutputUnitPaths(buildDatabasePath: String, intermediatesURL: URL) -> [String] {
  let rows = sqliteRows(
    databasePath: buildDatabasePath,
    sql: "select key from key_names where key GLOB 'CP1:*:CompileC *';"
  )

  var paths = Set<String>()
  for line in rows {
    guard let range = line.range(of: "CompileC ") else { continue }
    let payload = line[range.upperBound...]
    guard let outputPath = payload.split(separator: " ").first else { continue }
    paths.insert(canonicalizeOutputPath(String(outputPath), intermediatesURL: intermediatesURL))
  }
  return paths.sorted()
}

private func compileCSourceRoots(buildDatabasePath: String, targets: [String]) -> [String] {
  let rows = sqliteRows(
    databasePath: buildDatabasePath,
    sql: "select key from key_names where key GLOB 'CP1:*:CompileC *';"
  )

  let targetMarkers = targets.map { "/\($0).build/" }
  var roots = Set<String>()
  for line in rows {
    guard let range = line.range(of: "CompileC ") else { continue }
    let payload = line[range.upperBound...]
    let parts = payload.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: true)
    guard parts.count >= 2 else { continue }
    let outputPath = String(parts[0])
    guard targetMarkers.contains(where: outputPath.contains) else { continue }
    let sourcePath = String(parts[1])
    guard sourcePath.hasPrefix("/") else { continue }
    roots.insert(URL(fileURLWithPath: sourcePath).deletingLastPathComponent().path)
  }
  return roots.sorted()
}

private func swiftOutputFileMapPaths(buildDatabasePath: String) -> [String] {
  let rows = sqliteRows(
    databasePath: buildDatabasePath,
    sql: "select key from key_names where key GLOB 'CP2:*OutputFileMap.json';"
  )

  var paths = Set<String>()
  for line in rows {
    guard let jsonRange = line.range(of: ".json") else { continue }
    let candidate = String(line[..<jsonRange.upperBound])
    guard let slashRange = candidate.range(of: "/") else { continue }
    paths.insert(String(candidate[slashRange.lowerBound...]))
  }
  return paths.sorted()
}

private func outputPathsFromSwiftOutputFileMaps(
  buildDatabasePath: String,
  intermediatesURL: URL
) throws -> [String] {
  var paths = Set<String>()
  for fileMapPath in swiftOutputFileMapPaths(buildDatabasePath: buildDatabasePath) {
    let data = try Data(contentsOf: URL(fileURLWithPath: fileMapPath))
    let outputFileMap = try JSONDecoder().decode(OutputFileMap.self, from: data)
    for outputs in outputFileMap.values {
      if let path = outputs["index-unit-output-path"], !path.isEmpty {
        paths.insert(canonicalizeOutputPath(path, intermediatesURL: intermediatesURL))
      }
    }
  }
  return paths.sorted()
}

private func outputFileMapSourceRoots(buildDatabasePath: String, targets: [String]) throws -> [String] {
  let targetMarkers = targets.map { "/\($0).build/" }
  var roots = Set<String>()
  for fileMapPath in swiftOutputFileMapPaths(buildDatabasePath: buildDatabasePath) {
    guard targetMarkers.contains(where: fileMapPath.contains) else { continue }
    let data = try Data(contentsOf: URL(fileURLWithPath: fileMapPath))
    let outputFileMap = try JSONDecoder().decode(OutputFileMap.self, from: data)
    for sourcePath in outputFileMap.keys where sourcePath.hasPrefix("/") {
      roots.insert(URL(fileURLWithPath: sourcePath).deletingLastPathComponent().path)
    }
  }
  return roots.sorted()
}

private func precompiledModuleOutputUnitPaths(
  intermediatesURL: URL,
  fileManager: FileManager
) -> [String] {
  let candidateDirectories = [
    intermediatesURL.appendingPathComponent("SwiftExplicitPrecompiledModules", isDirectory: true),
    intermediatesURL.appendingPathComponent("ExplicitPrecompiledModules", isDirectory: true),
  ]
  var paths = Set<String>()
  for directoryURL in candidateDirectories {
    guard let urls = try? fileManager.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: nil) else {
      continue
    }
    for url in urls where url.pathExtension == "pcm" {
      paths.insert(canonicalizeOutputPath(url.path, intermediatesURL: intermediatesURL))
    }
  }
  return paths.sorted()
}

private func precompiledHeaderOutputUnitPaths(
  intermediatesURL: URL,
  fileManager: FileManager
) -> [String] {
  let precompiledHeadersURL = intermediatesURL.appendingPathComponent("PrecompiledHeaders", isDirectory: true)
  guard let enumerator = fileManager.enumerator(at: precompiledHeadersURL, includingPropertiesForKeys: nil) else {
    return []
  }

  var paths = Set<String>()
  for case let url as URL in enumerator {
    guard ["pch", "gch"].contains(url.pathExtension) else { continue }
    paths.insert(canonicalizeOutputPath(url.path, intermediatesURL: intermediatesURL))
  }
  return paths.sorted()
}

func explicitOutputUnitPaths(
  indexStorePath: String,
  fileManager: FileManager = .default
) throws -> [String] {
  let dataStoreURL = URL(fileURLWithPath: indexStorePath, isDirectory: true)
  let indexNoIndexURL = dataStoreURL.deletingLastPathComponent()
  let derivedDataURL = indexNoIndexURL.deletingLastPathComponent()
  let intermediatesURL = derivedDataURL
    .appendingPathComponent("Build", isDirectory: true)
    .appendingPathComponent("Intermediates.noindex", isDirectory: true)
  let buildDatabasePath = intermediatesURL
    .appendingPathComponent("XCBuildData", isDirectory: true)
    .appendingPathComponent("build.db")
    .path

  var paths = Set<String>()
  for path in try outputPathsFromSwiftOutputFileMaps(
    buildDatabasePath: buildDatabasePath,
    intermediatesURL: intermediatesURL
  ) {
    paths.insert(path)
  }
  for path in compileCOutputUnitPaths(buildDatabasePath: buildDatabasePath, intermediatesURL: intermediatesURL) {
    paths.insert(path)
  }
  for path in precompiledModuleOutputUnitPaths(intermediatesURL: intermediatesURL, fileManager: fileManager) {
    paths.insert(path)
  }
  for path in precompiledHeaderOutputUnitPaths(intermediatesURL: intermediatesURL, fileManager: fileManager) {
    paths.insert(path)
  }

  return paths.sorted()
}

func sourceRootsForTargets(indexStorePath: String, targets: [String]) throws -> [String] {
  let dataStoreURL = URL(fileURLWithPath: indexStorePath, isDirectory: true)
  let indexNoIndexURL = dataStoreURL.deletingLastPathComponent()
  let buildDatabasePath = buildDatabasePathForIndexNoIndex(indexNoIndexURL: indexNoIndexURL)
  var roots = Set(compileCSourceRoots(buildDatabasePath: buildDatabasePath, targets: targets))
  for path in try outputFileMapSourceRoots(buildDatabasePath: buildDatabasePath, targets: targets) {
    roots.insert(path)
  }
  return roots.sorted()
}
