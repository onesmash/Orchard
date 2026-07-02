import Foundation

func makeFileStatusPayload(
  incrementalSince: Double?,
  changedFiles: [String],
  allFiles: [String],
  outputPathMappings: [[String: String]]
) throws -> Data {
  let statusChangedFiles = incrementalSince != nil ? changedFiles : []
  var status: [String: Any] = ["changed": statusChangedFiles, "all": allFiles]
  if !outputPathMappings.isEmpty {
    status["output_path_mappings"] = outputPathMappings
  }
  return try JSONSerialization.data(withJSONObject: status, options: [])
}
