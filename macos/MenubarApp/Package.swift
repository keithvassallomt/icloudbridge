// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "iCloudBridgeMenubar",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "iCloudBridgeMenubar", targets: ["iCloudBridgeMenubar"]),
        .executable(name: "iCloudBridgeLoginHelper", targets: ["iCloudBridgeLoginHelper"]),
    ],
    targets: [
        .executableTarget(
            name: "iCloudBridgeMenubar",
            path: "Sources",
            exclude: ["LoginItemHelper"],
            resources: [
                .process("Resources")
            ]
        ),
        .executableTarget(
            name: "iCloudBridgeLoginHelper",
            path: "Sources/LoginItemHelper",
            exclude: ["Info.plist"],
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/LoginItemHelper/Info.plist",
                ])
            ]
        ),
    ]
)
