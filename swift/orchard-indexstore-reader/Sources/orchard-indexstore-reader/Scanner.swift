import Foundation
import IndexStoreDB

struct ScanSummary {
  let symbolCount: Int
  let relationCount: Int
  let duplicateUpgradeCount: Int
}

struct CanonicalSlot {
  let usr: String
  let name: String
  let symbolKind: String
  let language: String
  let module: String
  let file: String
  let priority: Int
}

func canonicalPriority(_ roles: SymbolRole) -> Int {
  if roles.contains(.definition) { return 3 }
  if roles.contains(.declaration) { return 2 }
  return 1
}

func langString(_ lang: Language) -> String {
  switch lang {
  case .swift: return "swift"
  case .objc: return "objc"
  case .c: return "c"
  case .cxx: return "cxx"
  }
}

func scanCanonicalSymbolsAndRelations(
  db: IndexStoreDB,
  filePaths: [String],
  emitOccurrences: Bool,
  emit: (String) -> Void,
  progress: ((Int, Int) -> Void)? = nil
) throws -> ScanSummary {
  var bestSlot: [String: CanonicalSlot] = [:]
  var dupCount = 0
  bestSlot.reserveCapacity(filePaths.count * 8)
  var emittedRels = Set<RelationDedupKey>()
  emittedRels.reserveCapacity(filePaths.count * 16)
  var processedFileCount = 0

  for file in filePaths {
    processedFileCount += 1
    progress?(processedFileCount, filePaths.count)

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
          dupCount += 1
          bestSlot[usr] = CanonicalSlot(
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
        bestSlot[usr] = CanonicalSlot(
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
        emit(
          "{\"kind\":\"occurrence\",\"usr\":\(js(usr)),"
          + "\"file\":\(js(path)),\"line\":\(line),\"column\":\(col),"
          + "\"role\":\(js(occurrenceRole))}"
        )
      }

      for rel in occ.relations {
        for roleName in relationRoleNames(rel.roles) {
          let key = RelationDedupKey(
            fromUSR: usr,
            toUSR: rel.symbol.usr,
            role: roleName,
            occurrenceRole: occurrenceRole
          )
          if emittedRels.insert(key).inserted {
            emit(
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
    emit(
      "{\"kind\":\"symbol\",\"usr\":\(js(slot.usr)),"
      + "\"name\":\(js(slot.name)),\"symbol_kind\":\(js(slot.symbolKind)),"
      + "\"language\":\(js(slot.language)),\"module\":\(js(slot.module)),"
      + "\"file\":\(js(slot.file))}"
    )
  }

  return ScanSummary(
    symbolCount: bestSlot.count,
    relationCount: emittedRels.count,
    duplicateUpgradeCount: dupCount
  )
}
