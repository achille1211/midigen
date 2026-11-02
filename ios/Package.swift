// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MidigenCore",
    platforms: [.macOS(.v13), .iOS(.v17)],
    products: [.library(name: "MidigenCore", targets: ["MidigenCore"])],
    targets: [
        .target(name: "MidigenCore", path: "Midigen/Core"),
        .testTarget(name: "MidigenCoreTests", dependencies: ["MidigenCore"], path: "CoreTests")
    ]
)
