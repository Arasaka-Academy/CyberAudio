// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CyberAudio",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "CyberAudio", targets: ["CyberAudio"])],
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1")],
    targets: [
        .executableTarget(name: "CyberAudio", dependencies: [.product(name: "Sparkle", package: "Sparkle")],
                          path: "Sources/CyberAudio",
                          linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])])
    ],
    swiftLanguageModes: [.v5]
)
