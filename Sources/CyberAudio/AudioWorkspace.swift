import AppKit
import AVFoundation
import Combine
import Darwin
import UniformTypeIdentifiers

struct Transcription: Decodable {
    let text: String
    let language: String?
    let duration: Double
    let processing_ms: Double
}

enum AudioOperation {
    case transcription, speech
}

enum TranscriptionOutcome {
    case success(UUID, Transcription)
    case cancelled(UUID)
    case failed(UUID, String)
}

@MainActor
final class AudioWorkspace: NSObject, ObservableObject {
    @Published var recording = false
    @Published var recordingSeconds = 0
    @Published private(set) var requestingMicrophone = false
    @Published private(set) var recordingLevel: Float = 0
    @Published private(set) var recordingLevels = [Float](repeating: 0, count: 48)
    @Published var transcribing = false
    @Published var transcript = ""
    @Published var sttTiming = ""
    @Published var errorMessage: String?
    @Published private(set) var errorOperation: AudioOperation = .transcription
    @Published var text = "你好，欢迎使用 CyberAudio。这是运行在 Mac 上的本地语音助手。"
    @Published var instructions = "自然、清晰、亲切地朗读。"
    @Published var voice = "serena"
    @Published var ttsLanguage = "auto"
    @Published var sttLanguage = "auto"
    @Published var streaming = true
    @Published var generating = false
    @Published var playing = false
    @Published var ttsTiming = ""
    @Published var generated = Data() { didSet { refreshSpeechWaveform() } }
    @Published private(set) var speechWaveform = AudioWaveform.empty
    @Published private(set) var playbackSeconds: Double = 0
    @Published var preparingUpdate = false
    @Published var managingModels = false
    private var recorder: AVAudioRecorder?
    private var recordingURL: URL?
    private var ticker: Task<Void, Never>?
    private var meter: Task<Void, Never>?
    private var transcription: Task<TranscriptionOutcome, Never>?
    private var generation: Task<Void, Never>?
    private var waveformAnalysis: Task<Void, Never>?
    private var waveformRevision = UUID()
    private var playbackTicker: Task<Void, Never>?
    private var player: AVAudioPlayer?
    private var engine: AVAudioEngine?
    private var node: AVAudioPlayerNode?
    private var engineConfigurationObserver: NSObjectProtocol?
    private var playbackSession = UUID()
    private let recordings: URL
    private let microphoneAccess: () async -> Bool
    private var recordingAttempt = UUID()

    init(recordingsDirectory: URL? = nil, microphoneAccess: @escaping () async -> Bool = { await AVCaptureDevice.requestAccess(for: .audio) }) {
        recordings = recordingsDirectory ?? FileManager.default.temporaryDirectory.appendingPathComponent("CyberAudio-recordings", isDirectory: true)
        self.microphoneAccess = microphoneAccess
        super.init()
        try? FileManager.default.createDirectory(at: recordings, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        // Recover only this application's temporary recordings after a previous crash.
        for url in (try? FileManager.default.contentsOfDirectory(at: recordings, includingPropertiesForKeys: nil)) ?? [] {
            if url.lastPathComponent.hasPrefix("recording-"), url.pathExtension == "wav" { try? FileManager.default.removeItem(at: url) }
        }
    }

    deinit {
        if let engineConfigurationObserver { NotificationCenter.default.removeObserver(engineConfigurationObserver) }
    }

    var working: Bool { generating || transcribing || recording || requestingMicrophone }
    var cannotStart: Bool { working || preparingUpdate || managingModels }

    @discardableResult
    func beginRecording(requestPermission: Bool = true) async -> Bool {
        guard !cannotStart, !Task.isCancelled else { return false }
        let attempt = UUID()
        recordingAttempt = attempt
        requestingMicrophone = true
        defer { if recordingAttempt == attempt { requestingMicrophone = false } }
        errorOperation = .transcription
        errorMessage = nil
        let permitted = requestPermission ? await microphoneAccess() : AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        guard recordingAttempt == attempt, !Task.isCancelled else { return false }
        guard permitted else { errorMessage = "麦克风权限未开启，请在系统设置 → 隐私与安全性 → 麦克风中允许 CyberAudio。"; return false }
        do {
            stopPlayback()
            let url = recordings.appendingPathComponent("recording-\(UUID().uuidString).wav")
            recordingURL = url
            let recorder = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000.0,
                AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false
            ])
            recorder.isMeteringEnabled = true
            guard recorder.record(forDuration: 59) else { throw CyberAudioError.message("无法开启麦克风，请检查输入设备。") }
            self.recorder = recorder
            recordingURL = url
            recording = true
            recordingSeconds = 0
            recordingLevels = [Float](repeating: 0, count: 48)
            startMetering()
            let startedAt = ProcessInfo.processInfo.systemUptime
            ticker = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(200))
                    guard !Task.isCancelled, let self, self.recording else { return }
                    self.recordingSeconds = min(59, Int(ProcessInfo.processInfo.systemUptime - startedAt))
                    if self.recordingSeconds >= 59 {
                        self.stopMetering()
                        return
                    }
                }
            }
            return true
        } catch {
            if let recordingURL { try? FileManager.default.removeItem(at: recordingURL) }
            recordingURL = nil
            errorMessage = error.localizedDescription
            return false
        }
    }

    func cancelRecording() {
        recordingAttempt = UUID()
        requestingMicrophone = false
        recorder?.stop(); recorder = nil
        ticker?.cancel(); ticker = nil
        stopMetering()
        recording = false
        if let recordingURL { try? FileManager.default.removeItem(at: recordingURL) }
        recordingURL = nil
    }

    @discardableResult
    func stopAndTranscribe(using backend: BackendController, sessionID: UUID = UUID()) async -> TranscriptionOutcome {
        recordingAttempt = UUID()
        recorder?.stop()
        recorder = nil
        ticker?.cancel()
        ticker = nil
        stopMetering()
        recording = false
        guard let url = recordingURL else { return .cancelled(sessionID) }
        recordingURL = nil
        defer { try? FileManager.default.removeItem(at: url) }
        return await transcribe(url: url, using: backend, completingRecording: true, sessionID: sessionID)
    }

    private func startMetering() {
        meter?.cancel()
        meter = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(50))
                guard !Task.isCancelled, let self, self.recording,
                      let recorder = self.recorder, recorder.isRecording else { return }
                recorder.updateMeters()
                let level = max(0, min(1, (recorder.averagePower(forChannel: 0) + 50) / 50))
                self.recordingLevel = level
                self.recordingLevels.removeFirst()
                self.recordingLevels.append(level)
            }
        }
    }

    private func stopMetering() {
        meter?.cancel()
        meter = nil
        recordingLevel = 0
    }

    func reportError(_ error: Error, for operation: AudioOperation) {
        errorOperation = operation
        errorMessage = error.localizedDescription
    }

    func importAudio(using backend: BackendController) {
        guard !cannotStart else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.audio, .movie]
        panel.allowsMultipleSelection = false
        panel.message = "选择不超过 60 秒、20MiB 的音频"
        present(panel) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { await self?.transcribe(url: url, using: backend) }
        }
    }

    @discardableResult
    func transcribe(url: URL, using backend: BackendController, completingRecording: Bool = false, sessionID: UUID = UUID()) async -> TranscriptionOutcome {
        guard !working, !(preparingUpdate || managingModels) || completingRecording, !Task.isCancelled else { return .cancelled(sessionID) }
        stopPlayback()
        errorOperation = .transcription
        errorMessage = nil
        transcribing = true
        sttTiming = ""
        defer { transcribing = false; transcription = nil }
        let task = Task { [weak self] in
            guard let self else { return TranscriptionOutcome.cancelled(sessionID) }
            return await self.performTranscription(url: url, using: backend, sessionID: sessionID)
        }
        transcription = task
        return await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
    }

    private func performTranscription(url: URL, using backend: BackendController, sessionID: UUID) async -> TranscriptionOutcome {
        do {
            try Task.checkCancellation()
            let read = Task.detached(priority: .userInitiated) { try Self.readAudio(at: url) }
            let audio = try await withTaskCancellationHandler { try await read.value } onCancel: { read.cancel() }
            try Task.checkCancellation()
            let boundary = "CyberAudio-\(UUID().uuidString)"
            var body = Data()
            for (key, value) in [("model", backend.configuration?.asrModelID ?? "qwen3-asr-1.7b"), ("language", sttLanguage), ("response_format", "json")] {
                body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(key)\"\r\n\r\n\(value)\r\n".utf8))
            }
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.input\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8))
            body.append(audio)
            body.append(Data("\r\n--\(boundary)--\r\n".utf8))
            let request = try backend.request(path: "/v1/audio/transcriptions", method: "POST", body: body, contentType: "multipart/form-data; boundary=\(boundary)")
            let (data, response) = try await backend.session.data(for: request)
            try Task.checkCancellation()
            try backend.check(response, data: data)
            let result = try JSONDecoder().decode(Transcription.self, from: data)
            transcript = result.text
            sttTiming = String(format: "音频 %.1f 秒 · 处理 %.2f 秒 · %@", result.duration, result.processing_ms / 1000, result.language ?? "自动")
            return .success(sessionID, result)
        } catch {
            if Task.isCancelled { sttTiming = "已取消"; return .cancelled(sessionID) }
            reportError(error, for: .transcription)
            return .failed(sessionID, error.localizedDescription)
        }
    }

    nonisolated private static func readAudio(at url: URL) throws -> Data {
        try Task.checkCancellation()
        // Follow valid links, then validate and read the same opened file. A
        // nonblocking open lets us reject a FIFO before waiting for its writer.
        let descriptor = url.withUnsafeFileSystemRepresentation { path in
            path.map { Darwin.open($0, O_RDONLY | O_NONBLOCK | O_CLOEXEC) } ?? -1
        }
        guard descriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? file.close() }
        var info = stat()
        guard fstat(descriptor, &info) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        guard info.st_mode & S_IFMT == S_IFREG else { throw CyberAudioError.message("请选择普通音频文件。") }
        let limit = 20 * 1024 * 1024
        guard info.st_size <= limit else { throw CyberAudioError.message("文件不能超过 20MiB。") }
        var audio = Data()
        while true {
            try Task.checkCancellation()
            // Also bound the actual bytes if the file grows after fstat.
            let chunk = try file.read(upToCount: min(64 * 1024, limit + 1 - audio.count)) ?? Data()
            if chunk.isEmpty { return audio }
            audio.append(chunk)
            guard audio.count <= limit else { throw CyberAudioError.message("文件不能超过 20MiB。") }
        }
    }

    func cancelTranscription() { transcription?.cancel() }

    private func refreshSpeechWaveform() {
        waveformAnalysis?.cancel()
        let revision = UUID()
        waveformRevision = revision
        guard !generated.isEmpty else { speechWaveform = .empty; waveformAnalysis = nil; return }
        let data = generated
        waveformAnalysis = Task { [weak self] in
            let worker = Task.detached(priority: .utility) { try AudioWaveform.decodeWAV(data) }
            let waveform = await withTaskCancellationHandler { try? await worker.value } onCancel: { worker.cancel() }
            guard !Task.isCancelled, let self, self.waveformRevision == revision else { return }
            self.speechWaveform = waveform ?? .empty
            self.waveformAnalysis = nil
        }
    }

    func generate(using backend: BackendController) {
        guard !cannotStart else { return }
        stopPlayback()
        errorOperation = .speech
        errorMessage = nil
        generated = Data()
        ttsTiming = ""
        generating = true
        generation = Task { [weak self] in
            guard let self else { return }
            defer { self.generating = false; self.generation = nil }
            let start = Date()
            var streamingRequestID: String?
            var inspectStreamingFailure = false
            do {
                let object: [String: Any] = ["model": backend.configuration?.ttsModelID ?? "qwen3-tts-1.7b", "input": self.text,
                    "voice": self.voice, "language": self.ttsLanguage, "instructions": backend.supportsInstructions ? self.instructions : "",
                    "response_format": self.streaming ? "pcm" : "wav", "stream": self.streaming]
                let request = try backend.request(path: "/v1/audio/speech", method: "POST", body: JSONSerialization.data(withJSONObject: object), contentType: "application/json")
                if self.streaming {
                    let (bytes, response) = try await backend.session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                        var data = Data()
                        for try await byte in bytes { data.append(byte); if data.count > 16384 { break } }
                        try backend.check(response, data: data)
                        return
                    }
                    guard let id = http.value(forHTTPHeaderField: "X-Request-ID") else { throw CyberAudioError.message("响应缺少请求编号。") }
                    streamingRequestID = id
                    try self.startPCMPlayer()
                    inspectStreamingFailure = true
                    var pcm = Data()
                    var pending = Data()
                    var waveform = WaveformAccumulator()
                    var first: Double?
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        pending.append(byte)
                        if pending.count >= 9600 {
                            if first == nil { first = Date().timeIntervalSince(start) }
                            pcm.append(pending)
                            waveform.appendPCM16(pending)
                            self.speechWaveform = waveform.waveform
                            self.schedule(pending)
                            pending.removeAll(keepingCapacity: true)
                            // Limit audio queued ahead of real time to two seconds.
                            while Double(pcm.count) / 48000 - (Date().timeIntervalSince(start) - (first ?? 0)) > 2 {
                                try await Task.sleep(for: .milliseconds(50))
                            }
                        }
                    }
                    try Task.checkCancellation()
                    guard pending.count % 2 == 0 else { throw CyberAudioError.message("PCM 数据不完整。") }
                    if !pending.isEmpty {
                        pcm.append(pending)
                        waveform.appendPCM16(pending)
                        self.speechWaveform = waveform.waveform
                        self.schedule(pending)
                    }
                    inspectStreamingFailure = false
                    let state = try await backend.speechRequestStatus(id: id)
                    try Task.checkCancellation()
                    if let failure = state.failure { throw failure }
                    guard state.status == .completed, !pcm.isEmpty else {
                        throw CyberAudioError.message("语音生成未完整结束，请缩短文本后重试。")
                    }
                    self.generated = Self.wav(pcm: pcm)
                    self.ttsTiming = String(format: "首段 %.2f 秒 · 音频 %.2f 秒 · 总耗时 %.2f 秒", first ?? 0, Double(pcm.count) / 48000, Date().timeIntervalSince(start))
                    // A zero-length completion buffer marks when queued audio finishes.
                    if let format = self.node?.outputFormat(forBus: 0), let tail = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1) {
                        let session = self.playbackSession
                        tail.frameLength = 1
                        tail.floatChannelData?[0][0] = 0
                        self.node?.scheduleBuffer(tail, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                            Task { @MainActor in
                                guard let self, self.playbackSession == session else { return }
                                if self.engine?.isRunning == false { self.outputDeviceInterrupted() }
                                else { self.stopPlayback() }
                            }
                        }
                    }
                } else {
                    let (data, response) = try await backend.session.data(for: request)
                    try Task.checkCancellation()
                    try backend.check(response, data: data)
                    self.generated = data
                    self.ttsTiming = String(format: "生成耗时 %.2f 秒", Date().timeIntervalSince(start))
                    try self.playGenerated()
                }
            } catch is CancellationError {
                self.stopPlayback()
                self.generated = Data()
                self.ttsTiming = "已取消"
            } catch {
                self.stopPlayback()
                self.generated = Data()
                if Task.isCancelled { self.ttsTiming = "已取消" }
                else {
                    var failure = error
                    // A late engine failure can close chunked HTTP before its final
                    // chunk. Recover only an authoritative failure for this request;
                    // a completed/running/unavailable record cannot validate partial PCM.
                    if inspectStreamingFailure, let id = streamingRequestID,
                       let state = try? await backend.speechRequestStatus(id: id),
                       let terminalFailure = state.failure {
                        failure = terminalFailure
                    }
                    if Task.isCancelled { self.ttsTiming = "已取消" }
                    else { self.reportError(failure, for: .speech) }
                }
            }
        }
    }

    private func startPCMPlayer() throws {
        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        let format = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        try engine.start()
        node.play()
        self.engine = engine
        self.node = node
        let engineID = ObjectIdentifier(engine)
        let session = playbackSession
        engineConfigurationObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil
        ) { [weak self] _ in
            // The audio thread must not wait for MainActor while it stops the engine.
            // Carry values across threads; access the current engine only on MainActor.
            Task { @MainActor in
                guard let self, self.playbackSession == session,
                      let currentEngine = self.engine, ObjectIdentifier(currentEngine) == engineID else { return }
                self.outputDeviceInterrupted()
            }
        }
        playing = true
        startPlaybackTicker()
    }

    private func outputDeviceInterrupted() {
        cancelGeneration()
        reportError(CyberAudioError.message("音频输出设备发生变化，播放已停止。请检查设备后重试。"), for: .speech)
    }

    private func schedule(_ pcm: Data) {
        guard let node, let format = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(pcm.count / 2)) else { return }
        buffer.frameLength = buffer.frameCapacity
        pcm.withUnsafeBytes { raw in
            for index in 0..<Int(buffer.frameLength) {
                let value = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))
                buffer.floatChannelData![0][index] = Float(value) / 32768
            }
        }
        node.scheduleBuffer(buffer)
    }

    func playGenerated() throws {
        guard !preparingUpdate, !managingModels else { return }
        stopPlayback()
        let player = try AVAudioPlayer(data: generated)
        player.delegate = self
        guard player.play() else { throw CyberAudioError.message("无法播放音频，请检查输出设备。") }
        self.player = player
        playing = true
        startPlaybackTicker()
        if errorOperation == .speech { errorMessage = nil }
    }

    func stopPlayback() {
        // Stopping a node invokes its queued completions. Invalidate them first.
        playbackSession = UUID()
        playbackTicker?.cancel(); playbackTicker = nil
        playbackSeconds = 0
        if let engineConfigurationObserver { NotificationCenter.default.removeObserver(engineConfigurationObserver) }
        engineConfigurationObserver = nil
        player?.stop(); player = nil
        node?.stop(); node = nil
        engine?.stop(); engine = nil
        playing = false
    }

    private func startPlaybackTicker() {
        playbackTicker?.cancel()
        let session = playbackSession
        playbackTicker = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.playbackSession == session, self.playing else { return }
                if let player = self.player { self.playbackSeconds = player.currentTime }
                else if let node = self.node, let time = node.lastRenderTime,
                        let position = node.playerTime(forNodeTime: time), position.sampleRate > 0 {
                    self.playbackSeconds = max(0, Double(position.sampleTime) / position.sampleRate)
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }

    func cancelGeneration() { generation?.cancel(); stopPlayback() }

    func save() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.wav]
        panel.nameFieldStringValue = "CyberAudio.wav"
        let data = generated
        present(panel) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            do { try data.write(to: url, options: .atomic) }
            catch { self?.reportError(error, for: .speech) }
        }
    }

    private func present(_ panel: NSSavePanel, completion: @escaping (NSApplication.ModalResponse) -> Void) {
        if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "main" }) ?? NSApp.mainWindow {
            panel.beginSheetModal(for: window, completionHandler: completion)
        } else {
            panel.begin(completionHandler: completion)
        }
    }

    func cleanup() {
        transcription?.cancel()
        generation?.cancel()
        waveformAnalysis?.cancel(); waveformAnalysis = nil
        waveformRevision = UUID()
        speechWaveform = .empty
        cancelRecording()
        stopPlayback()
    }

    static func wav(pcm: Data) -> Data {
        var output = Data("RIFF".utf8)
        func append<T: FixedWidthInteger>(_ value: T) {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { output.append(contentsOf: $0) }
        }
        append(UInt32(pcm.count + 36)); output.append(Data("WAVEfmt ".utf8)); append(UInt32(16))
        append(UInt16(1)); append(UInt16(1)); append(UInt32(24000)); append(UInt32(48000))
        append(UInt16(2)); append(UInt16(16)); output.append(Data("data".utf8)); append(UInt32(pcm.count)); output.append(pcm)
        return output
    }
}

extension AudioWorkspace: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            guard let self, self.player === player else { return }
            self.stopPlayback()
            if !flag { self.reportError(CyberAudioError.message("音频播放未完成，请检查输出设备后重试。"), for: .speech) }
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor [weak self] in
            guard let self, self.player === player else { return }
            self.stopPlayback()
            self.reportError(CyberAudioError.message("音频解码失败，播放已停止。请重新生成语音后重试。"), for: .speech)
        }
    }
}
