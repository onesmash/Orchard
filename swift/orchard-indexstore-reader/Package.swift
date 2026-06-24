// swift-tools-version:5.9
import PackageDescription

let package = Package(
  name: "orchard-indexstore-reader",
  platforms: [.macOS(.v14)],
  dependencies: [
    // IndexStoreDB Swift bindings over the toolchain's libIndexStore.
    .package(url: "https://github.com/apple/indexstore-db", branch: "main"),
  ],
  targets: [
    .executableTarget(
      name: "orchard-indexstore-reader",
      dependencies: [
        .product(name: "IndexStoreDB", package: "indexstore-db"),
      ]
    ),
  ]
)
