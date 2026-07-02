// orchard-indexstore-reader
//
// Thin Swift CLI that reads an Apple IndexStore (the `Index.noindex/DataStore`
// produced by Swift/clang during compilation) and emits stable JSONL that the
// Python ingest layer (`orchard.ingest.indexstore`) parses directly.
//
// Usage:
//   orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>]
//                             [--source-root <dir>]
//
// Output (one JSON object per line):
//   {"kind":"occurrence","usr":...,"file":...,"line":N,"column":N,"role":"definition"}
//   {"kind":"relation","from_usr":...,"to_usr":...,"role":"calledBy"}
//
// --source-root limits emission to files under that directory (avoids dumping
// the entire SDK). Omit to emit everything.
//
// Relation direction contract (verified against real Swift index data):
//   A `calledBy` relation row has from_usr = the symbol that is *called*
//   (the occurrence's symbol) and to_usr = the symbol that *calls* it (the
//   relation's symbol). I.e. to_usr calls from_usr. This matches
//   `orchard.normalize.identity.upsert_calls`, which maps such a row to
//   Calls(to_usr -> from_usr) = Calls(caller -> callee).

import IndexStoreDB
import Foundation

// MARK: - Progress logging

let runStart = Date()
let progressOut = FileHandle.standardError
var hasActiveInlineProgress = false

func elapsedSeconds() -> String {
  let dt = Date().timeIntervalSince(runStart)
  return String(format: "%.1fs", dt)
}

func logProgress(_ message: String) {
  if hasActiveInlineProgress {
    progressOut.write("\n".data(using: .utf8)!)
    hasActiveInlineProgress = false
  }
  progressOut.write("[orchard-indexstore-reader +\(elapsedSeconds())] \(message)\n".data(using: .utf8)!)
}

func scanProgressMessage(_ processed: Int, _ total: Int) -> String {
  "scanning files \(processed)/\(total)"
}

func logInlineProgress(_ message: String, finished: Bool = false) {
  let rendered = "\r\u{001B}[2K[orchard-indexstore-reader +\(elapsedSeconds())] \(message)"
  progressOut.write(rendered.data(using: .utf8)!)
  hasActiveInlineProgress = !finished
  if finished {
    progressOut.write("\n".data(using: .utf8)!)
  }
}

func stageSeconds(since start: Date) -> String {
  let dt = Date().timeIntervalSince(start)
  return String(format: "%.3fs", dt)
}

// MARK: - Argument parsing

let args = CommandLine.arguments
var storePath: String?
var libIndexStore: String?
var sourceRoots: [String] = []
var targets: [String] = []
var incrementalSince: Double?       // Unix epoch seconds
var listFilesOnly: Bool = false
var emitOccurrences = false
var dumpUnitOutputPaths = false

var i = 1
while i < args.count {
  let a = args[i]
  if a == "--libindexstore", i + 1 < args.count {
    libIndexStore = args[i + 1]; i += 2; continue
  }
  if a.hasPrefix("--libindexstore=") {
    libIndexStore = String(a.dropFirst("--libindexstore=".count)); i += 1; continue
  }
  if a == "--source-root", i + 1 < args.count {
    sourceRoots.append(args[i + 1]); i += 2; continue
  }
  if a.hasPrefix("--source-root=") {
    sourceRoots.append(String(a.dropFirst("--source-root=".count))); i += 1; continue
  }
  if a == "--target", i + 1 < args.count {
    targets.append(args[i + 1]); i += 2; continue
  }
  if a.hasPrefix("--target=") {
    targets.append(String(a.dropFirst("--target=".count))); i += 1; continue
  }
  if a == "--incremental-since", i + 1 < args.count {
    incrementalSince = Double(args[i + 1]); i += 2; continue
  }
  if a.hasPrefix("--incremental-since=") {
    incrementalSince = Double(a.dropFirst("--incremental-since=".count)); i += 1; continue
  }
  if a == "--list-files" {
    listFilesOnly = true; i += 1; continue
  }
  if a == "--emit-occurrences" {
    emitOccurrences = true; i += 1; continue
  }
  if a == "--dump-unit-output-paths" {
    dumpUnitOutputPaths = true; i += 1; continue
  }
  if a == "--help" || a == "-h" {
    print("usage: orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>] [--source-root <dir>] [--incremental-since <ts>] [--list-files] [--emit-occurrences] [--dump-unit-output-paths]")
    exit(0)
  }
  if storePath == nil { storePath = a }
  i += 1
}

if listFilesOnly && incrementalSince != nil {
  FileHandle.standardError.write("error: --list-files and --incremental-since are mutually exclusive\n".data(using: .utf8)!)
  exit(2)
}

guard let storePath, !storePath.isEmpty else {
  FileHandle.standardError.write("error: index store path required\n".data(using: .utf8)!)
  print("usage: orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>]")
  exit(2)
}

logProgress("starting for storePath=\(storePath)")
if !sourceRoots.isEmpty {
  logProgress("sourceRoots=\(sourceRoots.joined(separator: ","))")
}
if !targets.isEmpty {
  logProgress("targets=\(targets.joined(separator: ","))")
}

// MARK: - Resolve libIndexStore.dylib

func resolveDylib() -> String? {
  if let l = libIndexStore { return l }
  if let env = ProcessInfo.processInfo.environment["ORCHARD_LIBINDEXSTORE"] {
    return env
  }
  // Resolve via `xcode-select -p` -> <dev>/Toolchains/XcodeDefault.xctoolchain/usr/lib
  let p = Process()
  p.launchPath = "/usr/bin/xcode-select"
  p.arguments = ["-p"]
  let pipe = Pipe()
  p.standardOutput = pipe
  p.standardError = Pipe()
  do { try p.run(); p.waitUntilExit() } catch { return nil }
  let dev = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
    .trimmingCharacters(in: .whitespacesAndNewlines)
    ?? "/Applications/Xcode.app/Contents/Developer"
  let candidate = dev + "/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"
  return FileManager.default.fileExists(atPath: candidate) ? candidate : nil
}

guard let dylibPath = resolveDylib() else {
  FileHandle.standardError.write(
    "error: cannot locate libIndexStore.dylib; pass --libindexstore <path>\n".data(using: .utf8)!
  )
  exit(3)
}

logProgress("resolved libIndexStore at \(dylibPath)")

// MARK: - Open IndexStoreDB (per-run temp database)

let expectedDBPath = persistentDatabasePath(storePath: storePath)
logProgress("opening IndexStoreDB with persistent databasePath=\(expectedDBPath)")
let dbOpenStart = Date()
let openedSession = try openIndexSession(
  storePath: storePath,
  dylibPath: dylibPath,
  waitUntilDoneInitializing: true,
  listenToUnitEvents: false
)
let db = openedSession.db
logProgress("IndexStoreDB opened in \(stageSeconds(since: dbOpenStart)); starting initial scan")
let initialScanStart = Date()
db.pollForUnitChangesAndWait(isInitialScan: true)
logProgress("initial scan completed in \(stageSeconds(since: initialScanStart))")

// MARK: - Role normalization
// SymbolRole.description yields abbreviated pipe-joined strings (e.g.
// "calledBy|contBy"); the Python side matches exact role names, so we test
// each role explicitly and emit canonical names.

func relationRoleNames(_ roles: SymbolRole) -> [String] {
  var out: [String] = []
  if roles.contains(.calledBy) { out.append("calledBy") }
  if roles.contains(.childOf) { out.append("childOf") }
  if roles.contains(.baseOf) { out.append("baseOf") }
  if roles.contains(.overrideOf) { out.append("overrideOf") }
  if roles.contains(.containedBy) { out.append("containedBy") }
  if roles.contains(.extendedBy) { out.append("extendedBy") }
  if roles.contains(.accessorOf) { out.append("accessorOf") }
  if roles.contains(.receivedBy) { out.append("receivedBy") }
  if roles.contains(.ibTypeOf) { out.append("ibTypeOf") }
  if roles.contains(.specializationOf) { out.append("specializationOf") }
  return out
}

func occurrenceRoleName(_ roles: SymbolRole) -> String {
  if roles.contains(.definition) { return "definition" }
  if roles.contains(.declaration) { return "declaration" }
  if roles.contains(.call) { return "call" }
  if roles.contains(.reference) { return "reference" }
  if roles.contains(.read) { return "read" }
  if roles.contains(.write) { return "write" }
  return "reference"
}

// MARK: - JSON string escaping

func js(_ s: String) -> String {
  var out = "\""
  for c in s.unicodeScalars {
    switch c {
    case "\"": out += "\\\""
    case "\\": out += "\\\\"
    case "\n": out += "\\n"
    case "\r": out += "\\r"
    case "\t": out += "\\t"
    default:
      if c.value < 0x20 { out += String(format: "\\u%04x", c.value) }
      else { out += String(c) }
    }
  }
  return out + "\""
}

// MARK: - Emit JSONL
//
// indexstore-db is USR/name-keyed; enumerating every occurrence via per-USR
// queries is O(symbols) and far too slow on real Xcode builds (hundreds of
// thousands of symbols). Instead we fetch all occurrences per FILE via
// symbolOccurrences(inFilePath:), which returns definitions + call sites +
// their relations in one call — O(files), ~seconds for thousands of files.
//
// File discovery:
//   - with --source-root: filesystem-scan that directory for source files
//     (fast, and naturally limits output to the project, not the SDK).
//   - without --source-root: fall back to allSymbolNames + canonical lookups
//     (slow on large stores; emits SDK symbols too).

let out = FileHandle.standardOutput
var lineWriter = BufferedLineWriter { data in
  out.write(data)
}
func writeLine(_ s: String) {
  lineWriter.writeLine(s)
}

func underRoot(_ p: String) -> Bool {
  if !sourceRoots.isEmpty {
    return sourceRoots.contains(where: { prefix in
      p == prefix || p.hasPrefix(prefix + "/")
    })
  }
  return true
}

var filePaths: [String] = []
let effectiveSourceRoots = resolveEffectiveSourceRoots(
  explicitSourceRoots: sourceRoots,
  targets: targets,
  storePath: storePath
)
if sourceRoots.isEmpty && !effectiveSourceRoots.isEmpty {
  logProgress("derived \(effectiveSourceRoots.count) source roots from targets")
}

if !effectiveSourceRoots.isEmpty {
  logProgress("enumerating source files under source roots")
  let enumerationStart = Date()
  filePaths = enumerateSourceFiles(sourceRoots: effectiveSourceRoots, underRoot: underRoot)
  logProgress("filesystem enumeration completed in \(stageSeconds(since: enumerationStart))")
} else {
  FileHandle.standardError.write(
    "warning: no --source-root given; falling back to slow allSymbolNames discovery\n".data(using: .utf8)!
  )
  logProgress("discovering file paths via allSymbolNames fallback")
  let fallbackDiscoveryStart = Date()
  filePaths = discoverFilePaths(db: db)
  logProgress("allSymbolNames discovery completed in \(stageSeconds(since: fallbackDiscoveryStart))")
}

logProgress("discovered \(filePaths.count) source files to inspect")

// --list-files mode: just output the file paths and exit.
if listFilesOnly {
  let json = try! JSONSerialization.data(withJSONObject: filePaths, options: [])
  FileHandle.standardError.write(json)
  FileHandle.standardError.write("\n".data(using: .utf8)!)
  exit(0)
}

// --incremental-since: filter to only changed files.
var allFiles = filePaths
var changedFiles: [String] = []
if let since = incrementalSince {
  let filtered = filterChangedFiles(db: db, allFiles: filePaths, since: since)
  changedFiles = filtered.changedFiles
  let skipped = filtered.skippedCount
  let sinceDate = Date(timeIntervalSince1970: since)
  logProgress("incremental: \(changedFiles.count) changed, \(skipped) skipped (since \(sinceDate))")
  if changedFiles.isEmpty {
    // No changes — emit empty output but report file list via stderr.
    let json = try! makeFileStatusPayload(
      incrementalSince: incrementalSince,
      changedFiles: [],
      allFiles: allFiles,
      outputPathMappings: []
    )
    FileHandle.standardError.write(json)
    FileHandle.standardError.write("\n".data(using: .utf8)!)
    exit(0)
  }
  filePaths = changedFiles
}

var outputPathMappingsForStatus: [[String: String]] = []
if dumpUnitOutputPaths {
  let dumpStart = Date()
  logProgress("collecting raw unit output-path mappings")
  let mappings = try collectRawUnitOutputPathMappings(
    indexStorePath: storePath,
    dylibPath: dylibPath,
    db: db,
    filePaths: allFiles
  )
  let payload = mappings.map { mapping in
    [
      "unit_name": mapping.unitName,
      "main_file": mapping.mainFile,
      "output_file": mapping.outputFile,
    ]
  }
  let json = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
  FileHandle.standardOutput.write(json)
  FileHandle.standardOutput.write("\n".data(using: .utf8)!)
  logProgress("raw unit output-path mapping completed in \(stageSeconds(since: dumpStart))")
  exit(0)
} else if incrementalSince == nil {
  let mappingStart = Date()
  logProgress("collecting raw unit output-path mappings")
  let mappings = try collectRawUnitOutputPathMappings(
    indexStorePath: storePath,
    dylibPath: dylibPath,
    db: db,
    filePaths: allFiles
  )
  outputPathMappingsForStatus = mappings.map { mapping in
    [
      "unit_name": mapping.unitName,
      "main_file": mapping.mainFile,
      "output_file": mapping.outputFile,
    ]
  }
  logProgress("raw unit output-path mapping completed in \(stageSeconds(since: mappingStart))")
}

logProgress("pass 1: scanning all files for canonical symbols + relations")
let scanSummary = try scanCanonicalSymbolsAndRelations(
  db: db,
  filePaths: filePaths,
  emitOccurrences: emitOccurrences,
  emit: { writeLine($0) },
  progress: { processedFileCount, totalFileCount in
    if processedFileCount == 1 || processedFileCount % 250 == 0 || processedFileCount == totalFileCount {
      logInlineProgress(
        scanProgressMessage(processedFileCount, totalFileCount),
        finished: processedFileCount == totalFileCount
      )
    }
  }
)
logProgress("pass 1 done: canonical symbols=\(scanSummary.symbolCount) (upgraded \(scanSummary.duplicateUpgradeCount) duplicates)")

// ---- File status (stderr, for Python to consume) ----
let statusJSON = try! makeFileStatusPayload(
  incrementalSince: incrementalSince,
  changedFiles: changedFiles,
  allFiles: allFiles,
  outputPathMappings: outputPathMappingsForStatus
)
lineWriter.flush()
FileHandle.standardError.write(statusJSON)
FileHandle.standardError.write("\n".data(using: .utf8)!)

logProgress("completed emit: symbols=\(scanSummary.symbolCount) relations=\(scanSummary.relationCount)")
