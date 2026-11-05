// swift-tools-version: 6.2
import PackageDescription
import Foundation

// A local, commit-verified source checkout can avoid refetching SDK history.
// Release/reproducible builds use the pinned remote dependencies below.
let sourceRoot = ProcessInfo.processInfo.environment["CYBERAUDIO_COREML_SOURCES"]
let dependencies: [Package.Dependency] = sourceRoot.map { root in [
    .package(path: root + "/argmax-oss-swift"),
    .package(path: root + "/FluidAudio", traits: [])
] } ?? [
    .package(url: "https://github.com/argmaxinc/argmax-oss-swift.git", revision: "9f99b5cb6a907730543823ad271b82532782a899"),
    .package(url: "https://github.com/FluidInference/FluidAudio.git", revision: "ee78635d23d52379117554ba72358b65a0612447", traits: [])
]

let package = Package(
    name: "CyberAudioCoreMLValidation",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "CyberAudioCoreMLProbe", targets: ["CoreMLProbe"])],
    dependencies: dependencies + [
        .package(url: "https://github.com/apple/swift-argument-parser.git", exact: "1.7.0")
    ],
    targets: [
        .target(name: "CoreMLEvidence"),
        .executableTarget(name: "CoreMLProbe", dependencies: [
            "CoreMLEvidence", .product(name: "TTSKit", package: "argmax-oss-swift"),
            .product(name: "WhisperKit", package: "argmax-oss-swift"),
            .product(name: "FluidAudio", package: "FluidAudio")
        ])
    ],
    swiftLanguageModes: [.v5]
)
