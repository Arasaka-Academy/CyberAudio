// swift-tools-version: 6.3
import PackageDescription

let package = Package(
    name: "CyberAudioQwenSwiftValidation",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "CyberAudioQwenSwiftProbe", targets: ["QwenSwiftProbe"])],
    dependencies: [
        .package(url: "https://github.com/Neywa-Labs/mlx-audio-swift.git",
                 revision: "927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe"),
        .package(url: "https://github.com/ml-explore/mlx-swift.git", exact: "0.33.0")
    ],
    targets: [
        .executableTarget(name: "QwenSwiftProbe", dependencies: [
            .product(name: "MLXAudioSTT", package: "mlx-audio-swift"),
            .product(name: "MLXAudioTTS", package: "mlx-audio-swift"),
            .product(name: "MLXAudioCore", package: "mlx-audio-swift"),
            .product(name: "MLX", package: "mlx-swift"),
            .product(name: "MLXRandom", package: "mlx-swift")
        ])
    ],
    swiftLanguageModes: [.v5]
)
