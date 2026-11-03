// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CyberAudio",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "CyberAudio", targets: ["CyberAudio"])],
    targets: [.executableTarget(name: "CyberAudio", path: "Sources/CyberAudio")],
    swiftLanguageModes: [.v5]
)
