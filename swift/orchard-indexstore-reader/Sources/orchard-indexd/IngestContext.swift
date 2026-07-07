import Foundation

struct IngestContext: Codable, Equatable {
  let projectDir: String
  let indexStorePath: String
  let graphDBPath: String
  let targetArgs: [String]
  let entryTarget: String
  let incremental: Bool
  /// When false, the daemon skips scheduling a background auto-ingest after
  /// register_session.  Manual `orchard ingest` runs set this to false because
  /// they already perform a full scan+upsert in-process; the follow-up
  /// auto-ingest would only hit the fast path redundantly.
  let triggerAutoIngest: Bool

  init(
    projectDir: String,
    indexStorePath: String,
    graphDBPath: String,
    targetArgs: [String],
    entryTarget: String,
    incremental: Bool,
    triggerAutoIngest: Bool = true
  ) {
    self.projectDir = projectDir
    self.indexStorePath = indexStorePath
    self.graphDBPath = graphDBPath
    self.targetArgs = targetArgs
    self.entryTarget = entryTarget
    self.incremental = incremental
    self.triggerAutoIngest = triggerAutoIngest
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    projectDir = try container.decode(String.self, forKey: .projectDir)
    indexStorePath = try container.decode(String.self, forKey: .indexStorePath)
    graphDBPath = try container.decode(String.self, forKey: .graphDBPath)
    targetArgs = try container.decode([String].self, forKey: .targetArgs)
    entryTarget = try container.decode(String.self, forKey: .entryTarget)
    incremental = try container.decode(Bool.self, forKey: .incremental)
    // Default to true for backward compatibility: if the field is absent
    // (e.g. older orchard CLI or warm RPC), auto-ingest stays enabled.
    triggerAutoIngest = try container.decodeIfPresent(Bool.self, forKey: .triggerAutoIngest) ?? true
  }
}
