import Foundation
import IndexStoreDB
import Dispatch
import CryptoKit

private let daemonSourceExtensions: Set<String> = [
  "swift", "m", "mm", "c", "cc", "cpp", "cxx", "c++",
  "h", "hh", "hpp", "hxx",
]

private struct DaemonRelationDedupKey: Hashable {
  let fromUSR: String
  let toUSR: String
  let role: String
  let occurrenceRole: String
}

private struct DaemonCanonicalSlot {
  let usr: String
  let name: String
  let symbolKind: String
  let language: String
  let module: String
  let file: String
  let priority: Int
}

private let ingestLockBusyExitCode: Int32 = 23
private let ingestRetryDelay: DispatchTimeInterval = .seconds(1)
private let ingestDebounceDelay: DispatchTimeInterval = .milliseconds(200)

private final class IndexdSessionDelegate: IndexDelegate {
  weak var session: IndexdSession?

  init(session: IndexdSession?) {
    self.session = session
  }

  func processingAddedPending(_ count: Int) {
    guard count > 0 else {
      return
    }
    session?.handleObservedUnitActivity()
  }

  func processingCompleted(_ count: Int) {}
}

struct IndexdSessionSnapshot {
  let sourceRoots: [String]
  let targets: [String]
  let ingestContext: IngestContext?
  let seenGeneration: UInt64
  let ackedGeneration: UInt64
  let ingestRunning: Bool
  let retryScheduled: Bool
  let retryScheduledForLastExit: Bool
  let debounceScheduled: Bool
  let hasIngestContext: Bool
}

final class IndexdSession {
  let sessionId: String
  let storePath: String
  let dbPath: String

  private(set) var sourceRoots: [String]
  private(set) var targets: [String]
  private(set) var ingestContext: IngestContext?
  private(set) var seenGeneration: UInt64 = 0
  private(set) var ackedGeneration: UInt64 = 0
  private(set) var ingestRunning = false
  private(set) var ingestTargetGeneration: UInt64?
  private(set) var retryScheduled = false
  private(set) var retryScheduledForLastExit = false
  private(set) var debounceScheduled = false
  let library: IndexStoreLibrary
  let db: IndexStoreDB
  let dylibPath: String
  private let queue: DispatchQueue
  private let delegate: IndexdSessionDelegate
  private var hasPolled = false
  private var retryWorkItem: DispatchWorkItem?
  private var debounceWorkItem: DispatchWorkItem?
  private var orchardCLIPath: String?
  private var beginGraphDBIngest: (() -> Bool)?
  private var endGraphDBIngest: (() -> Void)?

  init(
    sessionId: String,
    storePath: String,
    sourceRoots: [String],
    targets: [String],
    dylibPath: String?,
    ingestContext: IngestContext?
  ) throws {
    self.sessionId = sessionId
    self.storePath = storePath
    self.sourceRoots = sourceRoots
    self.targets = targets
    self.ingestContext = ingestContext
    self.queue = DispatchQueue(label: "orchard.indexd.\(sessionId)")
    self.delegate = IndexdSessionDelegate(session: nil)

    let resolvedDylib = dylibPath ?? ProcessInfo.processInfo.environment["ORCHARD_LIBINDEXSTORE"] ?? "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libIndexStore.dylib"
    self.dylibPath = resolvedDylib
    let cacheKey = "v2:\(storePath)"
    let digest = SHA256.hash(data: Data(cacheKey.utf8)).map { String(format: "%02x", $0) }.joined()
    self.dbPath = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
      .appendingPathComponent(".orchard/indexstore-db", isDirectory: true)
      .appendingPathComponent(digest, isDirectory: true)
      .path

    try FileManager.default.createDirectory(atPath: dbPath, withIntermediateDirectories: true)
    self.library = try IndexStoreLibrary(dylibPath: resolvedDylib)
    self.db = try IndexStoreDB(
      storePath: storePath,
      databasePath: dbPath,
      library: library,
      delegate: delegate,
      waitUntilDoneInitializing: false,
      listenToUnitEvents: true
    )
    self.delegate.session = self
  }

  func update(sourceRoots: [String], targets: [String]) {
    self.sourceRoots = sourceRoots
    self.targets = targets
  }

  func refresh(sourceRoots: [String], targets: [String], ingestContext: IngestContext) {
    self.sourceRoots = sourceRoots
    self.targets = targets
    self.ingestContext = ingestContext
  }

  func recordWatchActivity() {
    queue.sync {
      seenGeneration &+= 1
    }
  }

  func handleObservedUnitActivity() {
    queue.async {
      self.seenGeneration &+= 1
      if self.orchardCLIPath != nil, self.beginGraphDBIngest != nil, self.endGraphDBIngest != nil {
        self.scheduleDebouncedIngestIfNeededLocked()
      }
    }
  }

  @discardableResult
  func beginIngest(targetGeneration: UInt64) -> Bool {
    queue.sync {
      beginIngestLocked(targetGeneration: targetGeneration)
    }
  }

  func handleIngestExit(code: Int32) {
    queue.sync {
      handleIngestExitLocked(code: code)
    }
  }

  func maybeScheduleBackgroundIngest(
    orchardCLIPath: String,
    beginInFlight: @escaping () -> Bool,
    endInFlight: @escaping () -> Void
  ) {
    queue.async {
      self.orchardCLIPath = orchardCLIPath
      self.beginGraphDBIngest = beginInFlight
      self.endGraphDBIngest = endInFlight
      self.scheduleDebouncedIngestIfNeededLocked()
    }
  }

  func snapshot() -> IndexdSessionSnapshot {
    queue.sync {
      IndexdSessionSnapshot(
        sourceRoots: sourceRoots,
        targets: targets,
        ingestContext: ingestContext,
        seenGeneration: seenGeneration,
        ackedGeneration: ackedGeneration,
        ingestRunning: ingestRunning,
        retryScheduled: retryScheduled,
        retryScheduledForLastExit: retryScheduledForLastExit,
        debounceScheduled: debounceScheduled,
        hasIngestContext: ingestContext != nil
      )
    }
  }

  func poll() {
    queue.sync {
      db.pollForUnitChangesAndWait(isInitialScan: !hasPolled)
      hasPolled = true
    }
  }

  func scan(
    incrementalSince: Double?,
    emitOccurrences: Bool
  ) -> (records: [String], summary: ScanSummary, fileStatus: [String: Any]) {
    queue.sync {
      let allFiles = listFiles()
      var filePaths = allFiles
      var changedFiles: [String] = []

      if let since = incrementalSince {
        let sinceDate = Date(timeIntervalSince1970: since)
        changedFiles = allFiles.filter { filePath in
          guard let unitDate = db.dateOfLatestUnitFor(filePath: filePath) else {
            return true
          }
          return unitDate > sinceDate
        }
        filePaths = changedFiles
      }

      let outputPathMappings = incrementalSince == nil
        ? collectOutputPathMappings(filePaths: allFiles)
        : []

      var records: [String] = []
      var bestSlot: [String: DaemonCanonicalSlot] = [:]
      var emittedRels = Set<DaemonRelationDedupKey>()

      for file in filePaths {
        for occ in db.symbolOccurrences(inFilePath: file) {
          let usr = occ.symbol.usr
          let roles = occ.roles
          let occurrenceRole = occurrenceRoleName(roles)
          let path = occ.location.path
          let line = occ.location.line
          let col = occ.location.utf8Column
          let priority = canonicalPriority(roles)

          if let current = bestSlot[usr] {
            if priority > current.priority {
              bestSlot[usr] = DaemonCanonicalSlot(
                usr: usr,
                name: occ.symbol.name,
                symbolKind: String(describing: occ.symbol.kind),
                language: langString(occ.symbol.language),
                module: occ.location.moduleName,
                file: file,
                priority: priority
              )
            }
          } else {
            bestSlot[usr] = DaemonCanonicalSlot(
              usr: usr,
              name: occ.symbol.name,
              symbolKind: String(describing: occ.symbol.kind),
              language: langString(occ.symbol.language),
              module: occ.location.moduleName,
              file: file,
              priority: priority
            )
          }

          if emitOccurrences && (roles.contains(.definition) || roles.contains(.declaration) || roles.contains(.call)) {
            records.append(
              "{\"kind\":\"occurrence\",\"usr\":\(js(usr)),"
              + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col),"
              + "\"role\":\(js(occurrenceRole))}"
            )
          }

          for rel in occ.relations {
            for roleName in relationRoleNames(rel.roles) {
              let key = DaemonRelationDedupKey(
                fromUSR: usr,
                toUSR: rel.symbol.usr,
                role: roleName,
                occurrenceRole: occurrenceRole
              )
              if emittedRels.insert(key).inserted {
                records.append(
                  "{\"kind\":\"relation\",\"from_usr\":\(js(usr)),"
                  + "\"from_usr_name\":\(js(occ.symbol.name)),"
                  + "\"to_usr\":\(js(rel.symbol.usr)),"
                  + "\"to_usr_name\":\(js(rel.symbol.name)),"
                  + "\"role\":\(js(roleName)),"
                  + "\"occurrence_role\":\(js(occurrenceRole)),"
                  + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col)}"
                )
              }
            }
          }
        }
      }

      for (_, slot) in bestSlot {
        records.append(
          "{\"kind\":\"symbol\",\"usr\":\(js(slot.usr)),"
          + "\"name\":\(js(slot.name)),\"symbol_kind\":\(js(slot.symbolKind)),"
          + "\"language\":\(js(slot.language)),\"module\":\(js(slot.module)),"
          + "\"file\":\(js(slot.file))}"
        )
      }

      let summary = ScanSummary(
        symbols: bestSlot.count,
        relations: emittedRels.count,
        changedFiles: changedFiles.count,
        allFiles: allFiles.count
      )
      let fileStatus: [String: Any] = [
        "changed": incrementalSince != nil ? changedFiles : [],
        "all": allFiles,
        "output_path_mappings": outputPathMappings,
      ]
      return (records, summary, fileStatus)
    }
  }

  func listFilesResponse() -> [String] {
    queue.sync {
      listFiles()
    }
  }

  func dumpUnitOutputPathsResponse() -> [[String: String]] {
    queue.sync {
      db.pollForUnitChangesAndWait(isInitialScan: !hasPolled)
      hasPolled = true
      return collectOutputPathMappings(filePaths: listFiles())
    }
  }

  private func listFiles() -> [String] {
    if !sourceRoots.isEmpty {
      var filePaths: [String] = []
      var seen = Set<String>()
      for root in sourceRoots {
        let baseURL = URL(fileURLWithPath: root)
        if let enumerator = FileManager.default.enumerator(at: baseURL, includingPropertiesForKeys: nil) {
          while let url = enumerator.nextObject() as? URL {
            if daemonSourceExtensions.contains(url.pathExtension) {
              let path = url.path
              if seen.insert(path).inserted {
                filePaths.append(path)
              }
            }
          }
        }
      }
      return filePaths
    }

    var seen = Set<String>()
    for name in db.allSymbolNames() {
      for occ in db.canonicalOccurrences(ofName: name) {
        let path = occ.location.path
        if !path.isEmpty {
          seen.insert(path)
        }
      }
    }
    return Array(seen)
  }

  private func collectOutputPathMappings(filePaths: [String]) -> [[String: String]] {
    guard !filePaths.isEmpty else {
      return []
    }
    do {
      return try collectRawUnitOutputPathMappings(
        indexStorePath: storePath,
        dylibPath: dylibPath,
        db: db,
        filePaths: filePaths
      ).map { mapping in
        [
          "unit_name": mapping.unitName,
          "main_file": mapping.mainFile,
          "output_file": mapping.outputFile,
        ]
      }
    } catch {
      return []
    }
  }

  private func beginIngestLocked(targetGeneration: UInt64) -> Bool {
    guard !ingestRunning else {
      return false
    }
    ingestRunning = true
    ingestTargetGeneration = targetGeneration
    retryScheduled = false
    retryScheduledForLastExit = false
    retryWorkItem?.cancel()
    retryWorkItem = nil
    debounceWorkItem?.cancel()
    debounceWorkItem = nil
    debounceScheduled = false
    return true
  }

  private func handleIngestExitLocked(code: Int32) {
    ingestRunning = false

    if code == 0 {
      if let targetGeneration = ingestTargetGeneration {
        ackedGeneration = max(ackedGeneration, targetGeneration)
      }
      ingestTargetGeneration = nil
      retryScheduled = false
      retryScheduledForLastExit = false
      scheduleDebouncedIngestIfNeededLocked()
      return
    }

    ingestTargetGeneration = nil
    if code == ingestLockBusyExitCode {
      retryScheduledForLastExit = true
      scheduleRetryLocked()
      return
    }

    retryScheduled = false
    retryScheduledForLastExit = false
  }

  private func maybeStartBackgroundIngestLocked() {
    guard let context = ingestContext,
          let orchardCLIPath,
          let beginGraphDBIngest,
          let endGraphDBIngest else {
      return
    }
    guard ackedGeneration < seenGeneration else {
      return
    }
    guard !ingestRunning else {
      return
    }

    let targetGeneration = seenGeneration
    guard beginGraphDBIngest() else {
      scheduleRetryLocked()
      return
    }
    guard beginIngestLocked(targetGeneration: targetGeneration) else {
      endGraphDBIngest()
      return
    }

    let process = Process()
    process.executableURL = URL(fileURLWithPath: orchardCLIPath)
    process.arguments = makeIngestArguments(context: context)
    process.terminationHandler = { [weak self] proc in
      self?.queue.async {
        endGraphDBIngest()
        self?.handleIngestExitLocked(code: proc.terminationStatus)
      }
    }

    do {
      try process.run()
    } catch {
      endGraphDBIngest()
      ingestRunning = false
      ingestTargetGeneration = nil
      retryScheduled = false
      retryScheduledForLastExit = false
      debounceScheduled = false
    }
  }

  private func scheduleRetryLocked() {
    retryWorkItem?.cancel()
    retryScheduled = true
    let workItem = DispatchWorkItem { [weak self] in
      guard let self else {
        return
      }
      self.retryWorkItem = nil
      self.retryScheduled = false
      self.maybeStartBackgroundIngestLocked()
    }
    retryWorkItem = workItem
    queue.asyncAfter(deadline: .now() + ingestRetryDelay, execute: workItem)
  }

  private func scheduleDebouncedIngestIfNeededLocked() {
    guard ackedGeneration < seenGeneration else {
      debounceScheduled = false
      return
    }
    debounceWorkItem?.cancel()
    debounceScheduled = true
    let workItem = DispatchWorkItem { [weak self] in
      guard let self else {
        return
      }
      self.debounceWorkItem = nil
      self.debounceScheduled = false
      self.maybeStartBackgroundIngestLocked()
    }
    debounceWorkItem = workItem
    queue.asyncAfter(deadline: .now() + ingestDebounceDelay, execute: workItem)
  }

  private func makeIngestArguments(context: IngestContext) -> [String] {
    var arguments = [
      "ingest",
      "--index-store", context.indexStorePath,
      "--project-dir", context.projectDir,
      "--target", context.targetArgs.joined(separator: ","),
      "--db", context.graphDBPath,
    ]
    arguments.append(context.incremental ? "--incremental" : "--full")
    return arguments
  }
}

private func relationRoleNames(_ roles: SymbolRole) -> [String] {
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

private func occurrenceRoleName(_ roles: SymbolRole) -> String {
  if roles.contains(.definition) { return "definition" }
  if roles.contains(.declaration) { return "declaration" }
  if roles.contains(.call) { return "call" }
  if roles.contains(.reference) { return "reference" }
  if roles.contains(.read) { return "read" }
  if roles.contains(.write) { return "write" }
  return "reference"
}

private func canonicalPriority(_ roles: SymbolRole) -> Int {
  if roles.contains(.definition) { return 3 }
  if roles.contains(.declaration) { return 2 }
  return 1
}

private func langString(_ lang: Language) -> String {
  switch lang {
  case .swift: return "swift"
  case .objc: return "objc"
  case .c: return "c"
  case .cxx: return "cxx"
  }
}

private func js(_ s: String) -> String {
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
