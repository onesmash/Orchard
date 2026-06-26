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

// MARK: - Argument parsing

let args = CommandLine.arguments
var storePath: String?
var libIndexStore: String?
var sourceRoot: String?

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
  if a == "--help" || a == "-h" {
    print("usage: orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>] [--source-root <dir>]")
    exit(0)
  }
  if storePath == nil { storePath = a }
  i += 1
}

guard let storePath, !storePath.isEmpty else {
  FileHandle.standardError.write("error: index store path required\n".data(using: .utf8)!)
  print("usage: orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>]")
  exit(2)
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

// MARK: - Open IndexStoreDB (per-run temp database)

let dbPath = NSTemporaryDirectory() + "orchard-indexstore-db-\(getpid())"
try? FileManager.default.removeItem(atPath: dbPath)
try? FileManager.default.createDirectory(atPath: dbPath, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(atPath: dbPath) }

let library = try IndexStoreLibrary(dylibPath: dylibPath)
let db = try IndexStoreDB(
  storePath: storePath,
  databasePath: dbPath,
  library: library,
  waitUntilDoneInitializing: true,
  listenToUnitEvents: false
)
db.pollForUnitChangesAndWait(isInitialScan: true)

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
  var seen = Set<String>()
  for name in db.allSymbolNames() {
    for occ in db.canonicalOccurrences(ofName: name) {
      let p = occ.location.path
      if !p.isEmpty { seen.insert(p) }
    }
  }
  filePaths = Array(seen)
}

// 2. Emit per file.
var emittedRels = Set<String>()
var emittedSymbols = Set<String>()

for file in filePaths {
  for occ in db.symbolOccurrences(inFilePath: file) {
    let roles = occ.roles
    let path = occ.location.path
    let line = occ.location.line
    let col = occ.location.utf8Column

    // Symbol rows: each unique USR gets one descriptor line.
    let usr = occ.symbol.usr
    if emittedSymbols.insert(usr).inserted {
      let symName = js(occ.symbol.name)
      let symKind = js(String(describing: occ.symbol.kind))
      let langStr = { () -> String in
        switch occ.symbol.language { case .swift: return "swift"; case .objc: return "objc"; case .c: return "c"; case .cxx: return "cxx" }
      }()
      let symLang = js(langStr)
      let symMod  = js(occ.location.moduleName)
      let symLine = "{\"kind\":\"symbol\",\"usr\":\(js(usr)),\"name\":\(symName),\"symbol_kind\":\(symKind),\"language\":\(symLang),\"module\":\(symMod),\"file\":\(js(file))}"
      writeLine(symLine)
    }

    // Keep direct source call-site occurrences so Python can distinguish
    // source-level calls from relation-only call edges later on.
    if roles.contains(.definition) || roles.contains(.declaration) || roles.contains(.call) {
      writeLine(
        "{\"kind\":\"occurrence\",\"usr\":\(js(usr)),"
        + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col),"
        + "\"role\":\(js(occurrenceRoleName(roles)))}"
      )
    }

    for rel in occ.relations {
      for roleName in relationRoleNames(rel.roles) {
        let occRoleName = occurrenceRoleName(roles)
        let key = "\(usr)\u{1}\(rel.symbol.usr)\u{1}\(roleName)\u{1}\(occRoleName)"
        if emittedRels.insert(key).inserted {
          writeLine(
            "{\"kind\":\"relation\",\"from_usr\":\(js(usr)),"
            + "\"to_usr\":\(js(rel.symbol.usr)),\"role\":\(js(roleName)),"
            + "\"occurrence_role\":\(js(occRoleName)),"
            + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col)}"
          )
        }
      }
    }
  }
}
