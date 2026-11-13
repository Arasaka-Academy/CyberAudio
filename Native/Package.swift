// swift-tools-version: 6.3
import PackageDescription

let package = Package(
    name: "CyberAudioNative",
    platforms: [.macOS("26.0")],
    products: [
        .executable(name: "CyberAudioService", targets: ["CyberAudioService"]),
        .executable(name: "CyberAudioAssets", targets: ["CyberAudioAssets"])
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-nio.git", exact: "2.90.1"),
        .package(url: "https://github.com/Neywa-Labs/mlx-audio-swift.git", revision: "927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe"),
        .package(url: "https://github.com/ml-explore/mlx-swift.git", exact: "0.33.0")
    ],
    targets: [
        .target(name: "CyberAudioCore", dependencies: [
            .product(name: "NIOCore", package: "swift-nio"),
            .product(name: "NIOPosix", package: "swift-nio"),
            .product(name: "NIOHTTP1", package: "swift-nio")
        ], resources: [.copy("Models.lock.json"), .copy("ModelCatalog.lock.json")]),
        .target(name: "CyberAudioInference", dependencies: ["CyberAudioCore",
            .product(name: "MLXAudioSTT", package: "mlx-audio-swift"),
            .product(name: "MLXAudioTTS", package: "mlx-audio-swift"),
            .product(name: "MLXAudioCore", package: "mlx-audio-swift"),
            .product(name: "MLX", package: "mlx-swift")
        ]),
        .executableTarget(name: "CyberAudioService", dependencies: ["CyberAudioCore", "CyberAudioInference"]),
        .executableTarget(name: "CyberAudioAssets", dependencies: ["CyberAudioCore"]),
        // Synthetic audio for UI/update automation; never copied into production Apps.
        .executableTarget(name: "CyberAudioFixture", dependencies: ["CyberAudioCore"]),
        // An executable test harness also runs with Command Line Tools alone.
        // XCTest is distributed only with full Xcode on macOS.
        .executableTarget(name: "CyberAudioCoreTests", dependencies: ["CyberAudioCore"], path: "Tests/CyberAudioCoreTests"),
        .executableTarget(name: "CyberAudioInferenceTests", dependencies: ["CyberAudioCore", "CyberAudioInference",
            .product(name: "MLXAudioSTT", package: "mlx-audio-swift"),
            .product(name: "MLXAudioCore", package: "mlx-audio-swift"),
            .product(name: "MLX", package: "mlx-swift")
        ], path: "Tests/CyberAudioInferenceTests")
    ],
    swiftLanguageModes: [.v5]
)
