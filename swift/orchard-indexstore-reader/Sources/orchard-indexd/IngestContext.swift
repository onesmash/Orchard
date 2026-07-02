import Foundation

struct IngestContext: Codable, Equatable {
  let projectDir: String
  let indexStorePath: String
  let graphDBPath: String
  let targetArgs: [String]
  let entryTarget: String
  let incremental: Bool
}
