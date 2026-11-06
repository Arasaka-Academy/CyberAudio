import AppKit
import Foundation

@MainActor
private final class PendingMicrophone {
    var requests: [CheckedContinuation<Bool, Never>] = []
    func access() async -> Bool { await withCheckedContinuation { requests.append($0) } }
}

@main
@MainActor
struct AudioWorkspaceTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func until(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(3)
        while !condition() {
            try require(Date() < deadline, "Timed out waiting for microphone request")
            await Task.yield()
        }
    }
    static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("CyberAudio-microphone-test-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let permission = PendingMicrophone()
        let audio = AudioWorkspace(recordingsDirectory: directory, microphoneAccess: { await permission.access() })
        let first = Task { await audio.beginRecording() }
        try await until { permission.requests.count == 1 }
        try require(audio.requestingMicrophone && audio.cannotStart, "A pending permission request reserves the recording slot")
        audio.cancelRecording()
        permission.requests[0].resume(returning: true)
        let firstStarted = await first.value
        try require(!firstStarted && !audio.recording && !audio.requestingMicrophone, "Late authorization after release cannot open a microphone")

        let cancelled = Task { await audio.beginRecording() }
        try await until { permission.requests.count == 2 }
        cancelled.cancel()
        permission.requests[1].resume(returning: true)
        let cancelledStarted = await cancelled.value
        try require(!cancelledStarted && !audio.recording && !audio.requestingMicrophone, "Task cancellation during authorization prevents recording")

        let older = Task { await audio.beginRecording() }
        try await until { permission.requests.count == 3 }
        audio.cancelRecording()
        let newer = Task { await audio.beginRecording() }
        try await until { permission.requests.count == 4 }
        permission.requests[2].resume(returning: false)
        _ = await older.value
        try require(audio.requestingMicrophone && audio.errorMessage == nil, "An older permission reply cannot clear or fail a newer attempt")
        audio.cleanup()
        permission.requests[3].resume(returning: true)
        let newerStarted = await newer.value
        try require(!newerStarted && !audio.working, "Exit cleanup invalidates pending microphone authorization")
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
        try require(files.isEmpty, "Cancelled authorization leaves no temporary recordings")
        print("AudioWorkspace: pending permission, stale reply, cancellation and cleanup checks passed")
    }
}
