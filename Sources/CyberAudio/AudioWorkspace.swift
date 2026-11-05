import AppKit
import AVFoundation
import Combine
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
    @Published var generated = Data()
    private var recorder: AVAudioRecorder?
    private var recordingURL: URL?
    private var ticker: Task<Void, Never>?
    private var meter: Task<Void, Never>?
    private var transcription: Task<Void, Never>?
    private var generation: Task<Void, Never>?
    private var player: AVAudioPlayer?
    private var engine: AVAudioEngine?
    private var node: AVAudioPlayerNode?
    private let recordings = FileManager.default.temporaryDirectory.appendingPathComponent("CyberAudio-recordings", isDirectory: true)

    override init() {
        super.init()
        try? FileManager.default.createDirectory(at: recordings, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        // Recover only this application's temporary recordings after a previous crash.
        for url in (try? FileManager.default.contentsOfDirectory(at: recordings, includingPropertiesForKeys: nil)) ?? [] {
            if url.lastPathComponent.hasPrefix("recording-"), url.pathExtension == "wav" { try? FileManager.default.removeItem(at: url) }
        }
    }

    var working: Bool { generating || transcribing || recording || requestingMicrophone }

    func beginRecording() async {
        guard !working else { return }
        requestingMicrophone = true
        defer { requestingMicrophone = false }
        errorOperation = .transcription
        errorMessage = nil
        let permitted = await AVCaptureDevice.requestAccess(for: .audio)
        guard permitted else { errorMessage = "麦克风权限未开启，请在系统设置 → 隐私与安全性 → 麦克风中允许 CyberAudio。"; return }
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
            guard recorder.record() else { throw CyberAudioError.message("无法开启麦克风，请检查输入设备。") }
            self.recorder = recorder
            recordingURL = url
            recording = true
            recordingSeconds = 0
            recordingLevels = [Float](repeating: 0, count: 48)
            startMetering()
            ticker = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(1))
                    guard !Task.isCancelled, let self, self.recording else { return }
                    self.recordingSeconds += 1
                    if self.recordingSeconds >= 59 {
                        self.recorder?.pause()
                        self.stopMetering()
                        return
                    }
                }
            }
        } catch {
            if let recordingURL { try? FileManager.default.removeItem(at: recordingURL) }
            recordingURL = nil
            errorMessage = error.localizedDescription
        }
    }

    func stopAndTranscribe(using backend: BackendController) async {
        recorder?.stop()
        recorder = nil
        ticker?.cancel()
        ticker = nil
        stopMetering()
        recording = false
        guard let url = recordingURL else { return }
        recordingURL = nil
        defer { try? FileManager.default.removeItem(at: url) }
        await transcribe(url: url, using: backend)
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
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.audio, .movie]
        panel.allowsMultipleSelection = false
        panel.message = "选择不超过 60 秒、20MiB 的音频"
        present(panel) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { await self?.transcribe(url: url, using: backend) }
        }
    }

    func transcribe(url: URL, using backend: BackendController) async {
        guard !working else { return }
        stopPlayback()
        errorOperation = .transcription
        errorMessage = nil
        transcribing = true
        sttTiming = ""
        defer { transcribing = false; transcription = nil }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performTranscription(url: url, using: backend)
        }
        transcription = task
        await task.value
    }

    private func performTranscription(url: URL, using backend: BackendController) async {
        do {
            try Task.checkCancellation()
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= 20 * 1024 * 1024 else { throw CyberAudioError.message("文件不能超过 20MiB。") }
            let audio = try Data(contentsOf: url)
            let boundary = "CyberAudio-\(UUID().uuidString)"
            var body = Data()
            for (key, value) in [("model", "qwen3-asr-1.7b"), ("language", sttLanguage), ("response_format", "json")] {
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
        } catch {
            if Task.isCancelled { sttTiming = "已取消" }
            else { reportError(error, for: .transcription) }
        }
    }

    func cancelTranscription() { transcription?.cancel() }

    func generate(using backend: BackendController) {
        guard !working else { return }
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
            do {
                let object: [String: Any] = ["model": "qwen3-tts-1.7b", "input": self.text,
                    "voice": self.voice, "language": self.ttsLanguage, "instructions": self.instructions,
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
                    try self.startPCMPlayer()
                    var pcm = Data()
                    var pending = Data()
                    var first: Double?
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        pending.append(byte)
                        if pending.count >= 9600 {
                            if first == nil { first = Date().timeIntervalSince(start) }
                            pcm.append(pending)
                            self.schedule(pending)
                            pending.removeAll(keepingCapacity: true)
                            // Limit audio queued ahead of real time to two seconds.
                            while Double(pcm.count) / 48000 - (Date().timeIntervalSince(start) - (first ?? 0)) > 2 {
                                try await Task.sleep(for: .milliseconds(50))
                            }
                        }
                    }
                    guard pending.count % 2 == 0 else { throw CyberAudioError.message("PCM 数据不完整。") }
                    if !pending.isEmpty { pcm.append(pending); self.schedule(pending) }
                    guard let id = http.value(forHTTPHeaderField: "X-Request-ID") else { throw CyberAudioError.message("响应缺少请求编号。") }
                    let (stateData, stateResponse) = try await backend.session.data(for: backend.request(path: "/v1/requests/\(id)"))
                    try backend.check(stateResponse, data: stateData)
                    let state = try JSONSerialization.jsonObject(with: stateData) as? [String: Any]
                    guard state?["status"] as? String == "completed", !pcm.isEmpty else {
                        throw CyberAudioError.message("语音生成未完整结束，请缩短文本后重试。")
                    }
                    self.generated = Self.wav(pcm: pcm)
                    self.ttsTiming = String(format: "首段 %.2f 秒 · 音频 %.2f 秒 · 总耗时 %.2f 秒", first ?? 0, Double(pcm.count) / 48000, Date().timeIntervalSince(start))
                    // A zero-length completion buffer marks when queued audio finishes.
                    if let format = self.node?.outputFormat(forBus: 0), let tail = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1) {
                        tail.frameLength = 1
                        tail.floatChannelData?[0][0] = 0
                        self.node?.scheduleBuffer(tail, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                            Task { @MainActor in self?.playing = false }
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
                self.ttsTiming = "已取消"
            } catch {
                self.stopPlayback()
                self.generated = Data()
                if Task.isCancelled { self.ttsTiming = "已取消" }
                else { self.reportError(error, for: .speech) }
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
        playing = true
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
        stopPlayback()
        let player = try AVAudioPlayer(data: generated)
        player.delegate = self
        guard player.play() else { throw CyberAudioError.message("无法播放音频，请检查输出设备。") }
        self.player = player
        playing = true
    }

    func stopPlayback() {
        player?.stop(); player = nil
        node?.stop(); node = nil
        engine?.stop(); engine = nil
        playing = false
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
        ticker?.cancel()
        stopMetering()
        recorder?.stop()
        if let recordingURL { try? FileManager.default.removeItem(at: recordingURL) }
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
        Task { @MainActor in self.playing = false }
    }
}
