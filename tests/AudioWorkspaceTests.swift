import AppKit
import Darwin
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
        try await oversizedImport(in: directory, audio: audio)
        try await boundedImports(in: directory, audio: audio)
        print("AudioWorkspace: pending permission, stale reply, cancellation and cleanup checks passed")
    }

    static func oversizedImport(in directory: URL, audio: AudioWorkspace) async throws {
        // Keep the backend stopped: file validation must precede service admission.
        let backend = BackendController()
        let oversized = directory.appendingPathComponent("oversized.wav")
        try Data([0]).write(to: oversized)
        let file = try FileHandle(forWritingTo: oversized)
        try file.truncate(atOffset: 20 * 1024 * 1024 + 1)
        try file.close()
        let directID = UUID()
        guard case .failed(let returnedID, let sizeError) = await audio.transcribe(url: oversized, using: backend, sessionID: directID) else {
            throw Failure(message: "Oversized audio must fail before service admission")
        }
        try require(returnedID == directID && sizeError.contains("20MiB"), "Direct oversized import must report its size limit")

        let link = directory.appendingPathComponent("linked-oversized.wav")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: oversized)
        audio.transcript = "previous successful transcription"
        let linkedID = UUID()
        guard case .failed(let returnedLinkedID, let linkedError) = await audio.transcribe(url: link, using: backend, sessionID: linkedID) else {
            throw Failure(message: "A symbolic link cannot bypass the audio import limit")
        }
        try require(returnedLinkedID == linkedID && linkedError == sizeError,
                    "Linked oversized audio must fail the same local size validation as its target; received \(linkedError)")
        try require(!audio.working && audio.transcript == "previous successful transcription",
                    "Rejected imports must release their session and preserve the previous transcript")
        print("AudioWorkspace: direct and linked oversized imports rejected before service admission")
    }

    static func boundedImports(in directory: URL, audio: AudioWorkspace) async throws {
        let backend = BackendController()
        for size in [0, 20 * 1024 * 1024] {
            let regular = directory.appendingPathComponent("allowed-\(size).wav")
            try Data().write(to: regular)
            let file = try FileHandle(forWritingTo: regular)
            try file.truncate(atOffset: UInt64(size))
            try file.close()
            let link = directory.appendingPathComponent("linked-allowed-\(size).wav")
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: regular)
            for url in [regular, link] {
                let id = UUID()
                guard case .failed(let returnedID, let message) = await audio.transcribe(url: url, using: backend, sessionID: id) else {
                    throw Failure(message: "A bounded import must reach the stopped backend")
                }
                try require(returnedID == id && message == "模型尚未就绪。",
                            "Files at or below 20MiB, including links, must pass local size validation")
            }
        }

        let pipe = directory.appendingPathComponent("pipe.wav")
        try require(mkfifo(pipe.path, 0o600) == 0, "Create an isolated FIFO fixture")
        let link = directory.appendingPathComponent("linked-pipe.wav")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: pipe)
        for url in [directory, pipe, link] {
            let id = UUID()
            guard case .failed(let returnedID, let message) = await audio.transcribe(url: url, using: backend, sessionID: id) else {
                throw Failure(message: "Nonregular audio imports must fail locally")
            }
            try require(returnedID == id && message == "请选择普通音频文件。",
                        "Directories and FIFO targets must be rejected without waiting for input")
        }
        try require(!audio.working && audio.transcript == "previous successful transcription",
                    "File validation failures must release their session and preserve prior text")
        print("AudioWorkspace: inclusive size boundary, valid links and nonregular imports checked")
    }
}
