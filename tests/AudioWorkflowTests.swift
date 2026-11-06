import AppKit
import AVFoundation
import Darwin
import Foundation

/// Exercises the production workspace and native HTTP service with the Swift test engine.
@main
@MainActor
struct AudioWorkflowTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func until(_ condition: () -> Bool, message: String) async throws {
        let deadline = Date().addingTimeInterval(15)
        while !condition() {
            try require(Date() < deadline, message)
            try await Task.sleep(for: .milliseconds(20))
        }
    }
    static func main() async throws {
        let backend = BackendController()
        for name in ["config", "models", "reports"] {
            try FileManager.default.createDirectory(at: backend.root.appendingPathComponent(name), withIntermediateDirectories: true)
        }
        let socketFD = socket(AF_INET, SOCK_STREAM, 0)
        try require(socketFD >= 0, "Cannot allocate a test port")
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(socketFD, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(socketFD, $0, &length) }
        }
        close(socketFD)
        try require(bound == 0 && named == 0, "Cannot select an unused test port")
        let configuration = ServiceConfiguration(asr_dir: backend.root.appendingPathComponent("models").path,
                                                tts_dir: backend.root.appendingPathComponent("models").path,
                                                ffmpeg: ProcessInfo.processInfo.environment["CYBERAUDIO_TEST_FFMPEG"] ?? "/opt/homebrew/bin/ffmpeg",
                                                port: Int(UInt16(bigEndian: address.sin_port)), https_url: "")
        try JSONEncoder().encode(configuration).write(to: backend.root.appendingPathComponent("config/service.json"))
        try (UUID().uuidString + UUID().uuidString).write(to: backend.root.appendingPathComponent("config/api-token"), atomically: true, encoding: .utf8)
        let audio = AudioWorkspace(recordingsDirectory: backend.root.appendingPathComponent("recordings"))
        defer { audio.cleanup(); backend.stopForTermination() }
        backend.start()
        try await until({ backend.ready || backend.status == "failed" }, message: "Native fixture did not become ready")
        try require(backend.ready, "Native fixture startup failed: \(backend.detail)")
        func options(_ fields: [String: Any]) throws {
            try JSONSerialization.data(withJSONObject: fields).write(to: backend.root.appendingPathComponent("config/ui-fixture.json"), options: .atomic)
        }
        try options([:])
        let input = backend.root.appendingPathComponent("import.wav")
        let samples = (0..<24000).map { Int16(3000 * sin(Double($0) * 2 * .pi * 440 / 24000)).littleEndian }
        let pcm = samples.withUnsafeBytes { Data($0) }
        try AudioWorkspace.wav(pcm: pcm).write(to: input)
        let id = UUID()
        let result = await audio.transcribe(url: input, using: backend, sessionID: id)
        if case .success(let returnedID, let value) = result {
            try require(returnedID == id && !value.text.isEmpty && audio.transcript == value.text, "Import must return the correct session and update the preview")
        } else { throw Failure(message: "File transcription did not succeed: \(audio.errorMessage ?? "cancelled")") }
        let preview = audio.transcript
        try options(["hold": true])
        let oldID = UUID()
        let cancelled = Task { await audio.transcribe(url: input, using: backend, sessionID: oldID) }
        try await until({ audio.transcribing }, message: "Transcription did not enter its active state")
        audio.cancelTranscription()
        let cancelledResult = await cancelled.value
        if case .cancelled(let returnedID) = cancelledResult {
            try require(returnedID == oldID && audio.transcript == preview && !audio.transcribing, "Cancellation must retain the previous preview and return its own session")
        } else { throw Failure(message: "Cancelled transcription returned a usable result") }
        try options([:])
        try await Task.sleep(for: .milliseconds(300))

        audio.text = "测试语音播放。"; audio.instructions = "自然。"
        audio.streaming = true
        audio.generate(using: backend)
        try await until({ !audio.generating }, message: "Streaming generation did not finish")
        try require(audio.errorMessage == nil && audio.generated.starts(with: Data("RIFF".utf8)) && audio.generated.count > 44,
                    "Streaming generation must retain a complete WAV: \(audio.errorMessage ?? "no audio")")
        try require(audio.playing, "The stream must still have queued playback for the replay regression")
        try audio.playGenerated()
        try await Task.sleep(for: .milliseconds(100))
        try require(audio.playing, "A stopped stream's completion must not mark the new replay idle")
        var installed = false
        let gate = UpdateGate(audioBusy: { audio.working || audio.playing }, prepare: { _ in true }, resume: { _ in })
        gate.begin { installed = true }
        try await Task.sleep(for: .milliseconds(100))
        try require(gate.waiting && !installed, "Updates must wait for the replacement playback")
        try await until({ installed }, message: "Update did not resume when replacement playback finished")
        gate.reset()

        let stalePlayer = try AVAudioPlayer(data: audio.generated)
        audio.audioPlayerDidFinishPlaying(stalePlayer, successfully: true)
        try audio.playGenerated()
        try await Task.sleep(for: .milliseconds(100))
        try require(audio.playing, "A queued delegate callback from another player must not finish the current replay")
        for _ in 0..<3 {
            audio.stopPlayback()
            try audio.playGenerated()
            try await Task.sleep(for: .milliseconds(20))
            try require(audio.playing, "Rapid stop/replay must retain the current playback state")
        }
        try await until({ !audio.playing }, message: "Queued PCM playback did not finish")
        let saved = backend.root.appendingPathComponent("generated.wav")
        try audio.generated.write(to: saved)
        let savedAudio = try AVAudioFile(forReading: saved)
        try require(savedAudio.processingFormat.sampleRate == 24000 && savedAudio.length == 24000, "Saved WAV must contain the complete 24 kHz mono audio")
        try audio.playGenerated()
        try require(audio.playing, "Replay should start from retained audio")
        audio.stopPlayback()
        try require(!audio.playing, "Stop playback must release the player")

        // Observe the real owned AVFoundation objects without adding production test hooks.
        func resource<T>(_ name: String, as type: T.Type) throws -> T {
            guard let field = Mirror(reflecting: audio).children.first(where: { $0.label == name }),
                  let value = field.value as? T else { throw Failure(message: "Missing audio resource: \(name)") }
            return value
        }
        try options(["chunk_delay": 0.5])
        audio.streaming = true
        audio.generate(using: backend)
        try await until({ audio.playing && audio.generating }, message: "PCM playback did not begin during streaming")
        let interruptedEngine = try resource("engine", as: AVAudioEngine.self)
        var deviceInstalled = false, deviceResumed = false
        let deviceGate = UpdateGate(audioBusy: { audio.working || audio.playing },
                                    prepare: { try await backend.prepareForUpdate(lease: $0) },
                                    resume: { await backend.cancelUpdatePreparation(lease: $0); deviceResumed = true })
        deviceGate.begin { deviceInstalled = true }
        try require(deviceGate.waiting && !deviceInstalled, "An interrupted stream must still own update admission")
        // A route change stops the engine before delivering this documented notification.
        interruptedEngine.stop()
        NotificationCenter.default.post(name: .AVAudioEngineConfigurationChange, object: interruptedEngine)
        try await Task.sleep(for: .milliseconds(100))
        try require(!audio.playing, "A stopped PCM engine must not remain marked as playing after a route change")
        try await until({ !audio.generating && deviceInstalled }, message: "Interrupted generation and HTTP response did not drain for update")
        try require(audio.generated.isEmpty && audio.errorMessage?.contains("输出设备") == true,
                    "Device interruption must cancel partial generation and explain how to retry")
        deviceGate.reset()
        try await until({ deviceResumed }, message: "Device regression update lease did not resume")

        try options([:])
        audio.generate(using: backend)
        try await until({ !audio.generating }, message: "PCM retry after a route change did not finish")
        let retainedWAV = audio.generated
        let replacedEngine = try resource("engine", as: AVAudioEngine.self)
        NotificationCenter.default.post(name: .AVAudioEngineConfigurationChange, object: replacedEngine)
        try audio.playGenerated()
        try await Task.sleep(for: .milliseconds(100))
        try require(audio.playing && audio.errorMessage == nil, "A queued notification from a replaced PCM engine must not cancel replay")
        NotificationCenter.default.post(name: .AVAudioEngineConfigurationChange, object: interruptedEngine)
        let replay = try resource("player", as: AVAudioPlayer.self)
        let delegate: AVAudioPlayerDelegate = audio
        delegate.audioPlayerDecodeErrorDidOccur?(stalePlayer, error: nil)
        try await Task.sleep(for: .milliseconds(50))
        try require(audio.playing && audio.errorMessage == nil, "Old engine and player errors must not alter current playback")
        delegate.audioPlayerDecodeErrorDidOccur?(replay, error: NSError(domain: "test.decode", code: 1))
        try await until({ !audio.playing }, message: "A current player decoding error must release playback ownership")
        try require(audio.generated == retainedWAV && audio.errorMessage != nil,
                    "Player errors must explain the failure and preserve complete generated audio")
        try audio.playGenerated()
        try require(audio.playing && audio.errorMessage == nil, "Successful replay must clear the previous speech error")
        audio.audioPlayerDidFinishPlaying(stalePlayer, successfully: false)
        try await Task.sleep(for: .milliseconds(50))
        try require(audio.playing && audio.errorMessage == nil, "An old unsuccessful completion must not interrupt replay")
        let unsuccessfulPlayer = try resource("player", as: AVAudioPlayer.self)
        audio.audioPlayerDidFinishPlaying(unsuccessfulPlayer, successfully: false)
        try await until({ !audio.playing }, message: "Unsuccessful playback must release ownership")
        try require(audio.generated == retainedWAV && audio.errorMessage != nil,
                    "Unsuccessful completion must preserve audio and report its failure")
        audio.reportError(Failure(message: "transcription failure"), for: .transcription)
        let transcriptionError = audio.errorMessage
        try audio.playGenerated()
        try require(audio.errorMessage == transcriptionError && audio.errorOperation == .transcription,
                    "Replay must not dismiss an unrelated transcription error")
        audio.stopPlayback()

        audio.generate(using: backend)
        try await until({ !audio.generating }, message: "Completed PCM route regression did not finish")
        let completedEngine = try resource("engine", as: AVAudioEngine.self)
        let completedWAV = audio.generated
        let completedNode = try resource("node", as: AVAudioPlayerNode.self)
        try require(!completedWAV.isEmpty && audio.playing, "Route regression requires completed audio with queued playback")
        completedEngine.stop()
        // A device reconfiguration can flush the queued tail before its
        // configuration notification reaches MainActor. Force that ordering.
        completedNode.stop()
        try await until({ !audio.playing }, message: "Flushed playback tail did not release the stopped engine")
        NotificationCenter.default.post(name: .AVAudioEngineConfigurationChange, object: completedEngine)
        try await until({ !audio.playing }, message: "A route change after generation must release queued playback")
        try require(audio.generated == completedWAV && !completedWAV.isEmpty,
                    "An output route change must retain a complete WAV for saving or replay; before \(completedWAV.count), after \(audio.generated.count)")
        try require(audio.errorMessage?.contains("输出设备") == true,
                    "A flushed tail on a stopped engine must explain the device interruption before its late configuration notification")

        try options(["hold": true])
        audio.generate(using: backend)
        try await until({ audio.generating }, message: "Generation did not start")
        audio.cancelGeneration()
        try await until({ !audio.generating }, message: "Cancelled generation did not finish")
        try require(!audio.playing && audio.generated.isEmpty, "Cancellation must not expose incomplete audio")
        try options([:])
        audio.streaming = false
        audio.generate(using: backend)
        try await until({ !audio.generating }, message: "WAV generation did not finish")
        try require(audio.errorMessage == nil && !audio.generated.isEmpty, "The service must accept new generation after cancellation")
        audio.stopPlayback()
        print("AudioWorkflow: native HTTP import, session result, cancellation, streaming PCM, complete WAV, replay ownership, device interruption, stale engine/player events, update wait and recovery passed")
        print("Evidence uses the Swift synthetic fixture; it does not establish microphone, speaker or Qwen accuracy acceptance.")
    }
}
