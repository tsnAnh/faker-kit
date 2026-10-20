// swift-tools-version: 6.2
import PackageDescription

let strict: [SwiftSetting] = [
    .treatAllWarnings(as: .error),
    .enableUpcomingFeature("ExistentialAny"),
]

let package = Package(
    name: "App",
    targets: [
        .target(name: "Cart", swiftSettings: strict),
        .testTarget(name: "CartTests", dependencies: ["Cart"], swiftSettings: strict),
    ],
    swiftLanguageModes: [.v6]
)
