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

func elapsedSeconds() -> String {
  let dt = Date().timeIntervalSince(runStart)
  return String(format: "%.1fs", dt)
}

func logProgress(_ message: String) {
  progressOut.write("[orchard-indexstore-reader +\(elapsedSeconds())] \(message)\n".data(using: .utf8)!)
}

// MARK: - Argument parsing

let args = CommandLine.arguments
var storePath: String?
var libIndexStore: String?
var sourceRoot: String?
var incrementalSince: Double?       // Unix epoch seconds
var listFilesOnly: Bool = false

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
    sourceRoot = args[i + 1]; i += 2; continue
  }
  if a.hasPrefix("--source-root=") {
    sourceRoot = String(a.dropFirst("--source-root=".count)); i += 1; continue
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
  if a == "--help" || a == "-h" {
    print("usage: orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>] [--source-root <dir>] [--incremental-since <ts>] [--list-files]")
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
if let sourceRoot {
  logProgress("sourceRoot=\(sourceRoot)")
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

let dbPath = NSTemporaryDirectory() + "orchard-indexstore-db-\(getpid())"
try? FileManager.default.removeItem(atPath: dbPath)
try? FileManager.default.createDirectory(atPath: dbPath, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(atPath: dbPath) }

logProgress("opening IndexStoreDB with temp databasePath=\(dbPath)")
let library = try IndexStoreLibrary(dylibPath: dylibPath)
let db = try IndexStoreDB(
  storePath: storePath,
  databasePath: dbPath,
  library: library,
  waitUntilDoneInitializing: true,
  listenToUnitEvents: false
)
logProgress("IndexStoreDB opened; starting initial scan")
db.pollForUnitChangesAndWait(isInitialScan: true)
logProgress("initial scan completed")

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
func writeLine(_ s: String) {
  out.write((s + "\n").data(using: .utf8)!)
}

func underRoot(_ p: String) -> Bool {
  if let prefix = sourceRoot {
    return p == prefix || p.hasPrefix(prefix + "/")
  }
  return true
}

// Note: URL.pathExtension returns the extension WITHOUT a leading dot.
let sourceExtensions: Set<String> = [
  "swift", "m", "mm", "c", "cc", "cpp", "cxx", "c++",
  "h", "hh", "hpp", "hxx",
]

var filePaths: [String] = []

if let root = sourceRoot {
  let fm = FileManager.default
  let baseURL = URL(fileURLWithPath: root)
  logProgress("enumerating source files under sourceRoot")
  if let enumerator = fm.enumerator(at: baseURL, includingPropertiesForKeys: nil) {
    while let url = enumerator.nextObject() as? URL {
      if sourceExtensions.contains(url.pathExtension) {
        let p = url.path
        if underRoot(p) { filePaths.append(p) }
      }
    }
  }
} else {
  FileHandle.standardError.write(
    "warning: no --source-root given; falling back to slow allSymbolNames discovery\n".data(using: .utf8)!
  )
  logProgress("discovering file paths via allSymbolNames fallback")
  var seen = Set<String>()
  for name in db.allSymbolNames() {
    for occ in db.canonicalOccurrences(ofName: name) {
      let p = occ.location.path
      if !p.isEmpty { seen.insert(p) }
    }
  }
  filePaths = Array(seen)
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
  let sinceDate = Date(timeIntervalSince1970: since)
  var skipped = 0
  changedFiles = filePaths.filter { fp in
    guard let unitDate = db.dateOfLatestUnitFor(filePath: fp) else {
      // No unit → new file, treat as changed.
      return true
    }
    return unitDate > sinceDate
  }
  skipped = filePaths.count - changedFiles.count
  logProgress("incremental: \(changedFiles.count) changed, \(skipped) skipped (since \(sinceDate))")
  if changedFiles.isEmpty {
    // No changes — emit empty output but report file list via stderr.
    let status: [String: Any] = ["changed": [], "all": allFiles]
    let json = try! JSONSerialization.data(withJSONObject: status, options: [])
    FileHandle.standardError.write(json)
    FileHandle.standardError.write("\n".data(using: .utf8)!)
    exit(0)
  }
  filePaths = changedFiles
}

// 2. First pass: collect best (canonical) occurrence per USR.
//    SourceKit-LSP uses `forEachCanonicalSymbolOccurrence` which picks ONE
//    canonical provider per USR across all TUs.  We replicate that by
//    preferring definition / declaration (the "authoritative" occurrence)
//    over call / reference sites.  Priority breaks ties deterministically.

struct CanonicalSlot {
  let usr: String
  let name: String
  let symbolKind: String
  let language: String
  let module: String
  let file: String
  let priority: Int
}

/// Higher = more authoritative (definition > declaration > other).
func _canonicalPriority(_ roles: SymbolRole) -> Int {
  if roles.contains(.definition)   { return 3 }
  if roles.contains(.declaration)  { return 2 }
  return 1
}

func _langString(_ lang: Language) -> String {
  switch lang { case .swift: return "swift"; case .objc: return "objc"; case .c: return "c"; case .cxx: return "cxx" }
}

var bestSlot: [String: CanonicalSlot] = [:]   // usr → canonical descriptor
var dupCount = 0
var emittedRels = Set<String>()
var processedFileCount = 0

logProgress("pass 1: scanning all files for canonical symbols + relations")

for file in filePaths {
  processedFileCount += 1
  if processedFileCount == 1 || processedFileCount % 250 == 0 || processedFileCount == filePaths.count {
    logProgress("scanning file \(processedFileCount)/\(filePaths.count): \(file)")
  }
  for occ in db.symbolOccurrences(inFilePath: file) {
    let usr = occ.symbol.usr
    let roles = occ.roles
    let path = occ.location.path
    let line = occ.location.line
    let col = occ.location.utf8Column

    // ---- Symbol descriptor: prefer canonical (highest-priority) occurrence ----
    let priority = _canonicalPriority(roles)
    if let cur = bestSlot[usr] {
      if priority > cur.priority {
        dupCount += 1
        bestSlot[usr] = CanonicalSlot(
          usr: usr, name: occ.symbol.name,
          symbolKind: String(describing: occ.symbol.kind),
          language: _langString(occ.symbol.language),
          module: occ.location.moduleName, file: file, priority: priority)
      }
    } else {
      bestSlot[usr] = CanonicalSlot(
        usr: usr, name: occ.symbol.name,
        symbolKind: String(describing: occ.symbol.kind),
        language: _langString(occ.symbol.language),
        module: occ.location.moduleName, file: file, priority: priority)
    }

    // ---- Occurrences ----
    if roles.contains(.definition) || roles.contains(.declaration) || roles.contains(.call) {
      writeLine(
        "{\"kind\":\"occurrence\",\"usr\":\(js(usr)),"
        + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col),"
        + "\"role\":\(js(occurrenceRoleName(roles)))}"
      )
    }

    // ---- Relations ----
    for rel in occ.relations {
      for roleName in relationRoleNames(rel.roles) {
        let occRoleName = occurrenceRoleName(roles)
        let key = "\(usr)\u{1}\(rel.symbol.usr)\u{1}\(roleName)\u{1}\(occRoleName)"
        if emittedRels.insert(key).inserted {
          writeLine(
            "{\"kind\":\"relation\",\"from_usr\":\(js(usr)),"
            + "\"from_usr_name\":\(js(occ.symbol.name)),"
            + "\"to_usr\":\(js(rel.symbol.usr)),"
            + "\"to_usr_name\":\(js(rel.symbol.name)),"
            + "\"role\":\(js(roleName)),"
            + "\"occurrence_role\":\(js(occRoleName)),"
            + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col)}"
          )
        }
      }
    }
  }
}

// ---- Emit symbol descriptors from canonical slots ----
logProgress("pass 1 done: canonical symbols=\(bestSlot.count) (upgraded \(dupCount) duplicates)")

for (_, slot) in bestSlot {
  writeLine(
    "{\"kind\":\"symbol\",\"usr\":\(js(slot.usr)),"
    + "\"name\":\(js(slot.name)),\"symbol_kind\":\(js(slot.symbolKind)),"
    + "\"language\":\(js(slot.language)),\"module\":\(js(slot.module)),"
    + "\"file\":\(js(slot.file))}"
  )
}

// ---- File status (stderr, for Python to consume) ----
if incrementalSince != nil {
  let status: [String: Any] = ["changed": changedFiles, "all": allFiles]
  let json = try! JSONSerialization.data(withJSONObject: status, options: [])
  FileHandle.standardError.write(json)
  FileHandle.standardError.write("\n".data(using: .utf8)!)
}

logProgress("completed emit: symbols=\(bestSlot.count) relations=\(emittedRels.count)")
