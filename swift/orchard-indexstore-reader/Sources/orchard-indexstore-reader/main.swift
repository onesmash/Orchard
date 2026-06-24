// orchard-indexstore-reader
//
// Thin Swift CLI that reads an Apple IndexStore (the `Index.noindex/DataStore`
// produced by Swift/clang during compilation) and emits stable JSONL that the
// Python ingest layer (`orchard.ingest.indexstore`) parses directly.
//
// Usage:
//   orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>]
//
// Output (one JSON object per line):
//   {"kind":"occurrence","usr":...,"file":...,"line":N,"column":N,"role":"definition"}
//   {"kind":"relation","from_usr":...,"to_usr":...,"role":"calledBy"}
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

var i = 1
while i < args.count {
  let a = args[i]
  if a == "--libindexstore", i + 1 < args.count {
    libIndexStore = args[i + 1]; i += 2; continue
  }
  if a.hasPrefix("--libindexstore=") {
    libIndexStore = String(a.dropFirst("--libindexstore=".count)); i += 1; continue
  }
  if a == "--help" || a == "-h" {
    print("usage: orchard-indexstore-reader <index-store-path> [--libindexstore <dylib>]")
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

let out = FileHandle.standardOutput
func writeLine(_ s: String) {
  out.write((s + "\n").data(using: .utf8)!)
}

var seenUsr = Set<String>()
var emittedRels = Set<String>()

for name in db.allSymbolNames() {
  for canon in db.canonicalOccurrences(ofName: name) {
    let usr = canon.symbol.usr
    if !seenUsr.insert(usr).inserted { continue }

    // Enumerate every occurrence of this symbol (definitions, references,
    // and call sites) so we capture `calledBy` relations on call sites.
    for occ in db.occurrences(ofUSR: usr, roles: .all) {
      let roles = occ.roles
      let path = occ.location.path
      let line = occ.location.line
      let col = occ.location.utf8Column

      // Occurrence rows are symbol-definition records (what the Python side
      // stores as OccurrenceRecord). Emit only definitions/declarations to
      // keep the stream lean; relations carry the rest.
      if roles.contains(.definition) || roles.contains(.declaration) {
        writeLine(
          "{\"kind\":\"occurrence\",\"usr\":\(js(occ.symbol.usr)),"
          + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col),"
          + "\"role\":\(js(occurrenceRoleName(roles)))}"
        )
      }

      for rel in occ.relations {
        for roleName in relationRoleNames(rel.roles) {
          let key = "\(usr)\u{1}\(rel.symbol.usr)\u{1}\(roleName)"
          if emittedRels.insert(key).inserted {
            writeLine(
              "{\"kind\":\"relation\",\"from_usr\":\(js(occ.symbol.usr)),"
              + "\"to_usr\":\(js(rel.symbol.usr)),\"role\":\(js(roleName))}"
            )
          }
        }
      }
    }
  }
}
