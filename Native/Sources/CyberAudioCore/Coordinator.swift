import Foundation

public final class CancellationToken: @unchecked Sendable {
    private let lock = NSLock()
    private var reason: ServiceError?
    private let deadline: Double
    public init(timeout: Double) { deadline = uptime() + timeout }
    public func cancel(_ error: ServiceError = .cancelled) {
        lock.lock(); defer { lock.unlock() }
        if reason == nil { reason = error }
    }
    public func check() throws {
        lock.lock(); defer { lock.unlock() }
        if let reason { throw reason }
        if uptime() >= deadline { reason = .timeout; throw ServiceError.timeout }
    }
    public var isCancelled: Bool { do { try check(); return false } catch { return true } }
    public var failure: ServiceError? {
        lock.lock(); defer { lock.unlock() }; return reason
    }
}

public struct InferenceSlot {
    public let id: String
    public let cancellation: CancellationToken
}

public protocol InferenceEngine: AnyObject {
    var metrics: [String: Any] { get }
    func memory() -> [String: Any]
    func transcribe(audio: URL, language: String?, cancellation: CancellationToken) throws -> [String: Any]
    /// Must return only after the producer has stopped, including on cancellation.
    /// emit is synchronous and may block to bound network buffering.
    func synthesize(_ speech: Speech, cancellation: CancellationToken, emit: @escaping (Data) throws -> Void) throws
}

public final class ServiceCoordinator: @unchecked Sendable {
    public let settings: ServiceSettings
    public let worker = DispatchQueue(label: "local.cyberaudio.inference", qos: .userInitiated)
    private let lock = NSLock()
    private var state = "loading"
    private var engine: InferenceEngine?
    private var active: InferenceSlot?
    private var audioResponses: Set<UUID> = []
    private var records: [String: [String: Any]] = [:]
    private var recordOrder: [String] = []
    private var updateDeadline = 0.0
    private var updateLease: String?
    private var cancelledUpdates: [String] = []
    private var closing = false
    private let clock: () -> Double
    private let log: (String) -> Void

    public init(settings: ServiceSettings, clock: @escaping () -> Double = uptime, log: @escaping (String) -> Void = { _ in }) {
        self.settings = settings; self.clock = clock; self.log = log
    }
    public func install(_ engine: InferenceEngine) {
        lock.lock(); defer { lock.unlock() }
        guard !closing else { return }
        self.engine = engine; state = "ready"
    }
    public func initializationFailed(_ error: Error? = nil) {
        lock.lock(); state = "failed"; lock.unlock()
        log("initialization_failed code=\((error as? ServiceError)?.code ?? "model_load_failed")")
    }
    public func shutdown() {
        lock.lock(); closing = true; state = "stopping"; active?.cancellation.cancel(); lock.unlock()
        // Destruction is ordered after every queued model operation.
        worker.async { self.lock.lock(); self.engine = nil; self.lock.unlock() }
    }
    public var ready: [String: Any] {
        lock.lock(); defer { lock.unlock() }
        return ["status": state, "busy": active != nil, "updating": updating, "instance": settings.instance,
                "application": "CyberAudio", "model": settings.configuration.asrModelID,
                "models": ["stt": state, "tts": state], "startup": engine?.metrics ?? [:],
                "supports_instructions": (try? ModelCatalog.descriptor(settings.configuration.ttsModelID).supports_instructions) ?? false]
    }
    private var updating: Bool { clock() < updateDeadline }
    /// Admit before reading an audio body; keep update drain busy through HTTP flush.
    public func admitAudioRequest() throws -> UUID {
        lock.lock(); defer { lock.unlock() }
        guard !updating else { throw ServiceError(503,"updating","正在准备更新，请稍后重试。") }
        let id = UUID(); audioResponses.insert(id); return id
    }
    public func completeAudioResponse(_ id: UUID) {
        lock.lock(); audioResponses.remove(id); lock.unlock()
    }
    public func update(action: String, instance: String?, lease: String?) throws -> [String: Any] {
        guard instance == settings.instance else { throw ServiceError(409, "wrong_instance", "更新请求不属于当前引擎。") }
        guard let lease, !lease.isEmpty, lease.utf8.count <= 64 else { throw ServiceError(400, "invalid_update", "更新标识无效。") }
        lock.lock(); defer { lock.unlock() }
        if action == "prepare" {
            guard !cancelledUpdates.contains(lease) else { throw ServiceError(409, "update_cancelled", "这次更新等待已取消。") }
            guard !updating || updateLease == lease else { throw ServiceError(409, "update_in_progress", "已有更新正在准备。") }
            updateLease = lease; updateDeadline = clock() + 30
        } else {
            cancelledUpdates.removeAll { $0 == lease }; cancelledUpdates.append(lease)
            if cancelledUpdates.count > 64 { cancelledUpdates.removeFirst() }
            if updateLease == lease { updateDeadline = 0 }
        }
        let busy = active != nil || !audioResponses.isEmpty
        log("update_control action=\(action) busy=\(busy)")
        return ["instance": settings.instance, "busy": busy, "updating": updating]
    }
    public func acquire(kind: String) throws -> InferenceSlot {
        lock.lock(); defer { lock.unlock() }
        if updating { throw ServiceError(503, "updating", "正在准备更新，请稍后重试。") }
        guard !closing, state == "ready" else { throw ServiceError(503, "not_ready", "模型尚未就绪。") }
        guard active == nil else { throw ServiceError(429, "busy", "STT／TTS 正在处理，请稍后重试。") }
        let slot = InferenceSlot(id: String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(16)).lowercased(),
                                 cancellation: CancellationToken(timeout: settings.timeout))
        active = slot
        records[slot.id] = ["request_id": slot.id, "kind": kind, "status": "running"]
        recordOrder.append(slot.id)
        if recordOrder.count > 64 { records.removeValue(forKey: recordOrder.removeFirst()) }
        return slot
    }
    public func record(_ id: String) throws -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        guard let record = records[id] else { throw ServiceError(404, "unknown_request", "请求记录不存在或已过期。") }
        return record
    }
    /// Called only by the worker, after decode/generation and all synchronous callbacks return.
    private func finish(_ slot: InferenceSlot, result: [String: Any] = [:], error: Error? = nil) {
        lock.lock(); defer { lock.unlock() }
        var values = result
        if let error {
            let error = error as? ServiceError ?? .inference
            values.merge(["status": error.code == "cancelled" ? "cancelled" : "failed",
                          "code": error.code, "message": error.message]) { _, new in new }
        } else { values["status"] = "completed" }
        records[slot.id]?.merge(values) { _, new in new }
        if active?.id == slot.id { active = nil }
        log("request_done id=\(slot.id) status=\(values["status"]!)")
    }
    public func transcribe(_ value: Transcription, slot: InferenceSlot, completion: @escaping (Result<[String: Any], ServiceError>) -> Void) {
        worker.async {
            let began = uptime()
            do {
                try slot.cancellation.check()
                let directory = self.settings.temporaryDirectory.appendingPathComponent("request-" + slot.id)
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                defer { try? FileManager.default.removeItem(at: directory) }
                let source = directory.appendingPathComponent("upload.input"), normalized = directory.appendingPathComponent("audio.wav")
                try value.audio.write(to: source)
                let decoded = try AudioFile.decode(ffmpeg: self.settings.configuration.ffmpeg, source: source, destination: normalized, cancellation: slot.cancellation)
                let start = uptime()
                guard let engine = self.engine else { throw ServiceError.inference }
                var result: [String: Any] = decoded.silent ? ["text": "", "language": value.language as Any? ?? NSNull()] :
                    try engine.transcribe(audio: normalized, language: value.language, cancellation: slot.cancellation)
                try slot.cancellation.check()
                let model = try ModelCatalog.descriptor(self.settings.configuration.asrModelID)
                result.merge(["model": model.id, "repository": model.repository, "revision": model.revision,
                              "duration": decoded.duration, "inference_ms": (uptime()-start)*1000, "processing_ms": (uptime()-began)*1000,
                              "request_id": slot.id, "no_speech": decoded.silent]) { _, new in new }
                self.finish(slot, result: ["processing_ms": result["processing_ms"]!]); completion(.success(result))
            } catch {
                let failure = error as? ServiceError ?? .inference
                self.finish(slot, error: failure); completion(.failure(failure))
            }
        }
    }
    public func synthesize(_ value: Speech, slot: InferenceSlot, emit: @escaping (Data) throws -> Void,
                           completion: @escaping (Result<(Data, [String: Any]), ServiceError>) -> Void) {
        worker.async {
            let start = uptime()
            var pcm = Data(), total = 0, first: Double?
            do {
                guard let engine = self.engine else { throw ServiceError.inference }
                try slot.cancellation.check()
                try engine.synthesize(value, cancellation: slot.cancellation) { chunk in
                    try slot.cancellation.check()
                    guard !chunk.isEmpty, chunk.count % 2 == 0 else { throw ServiceError.inference }
                    if first == nil { first = (uptime()-start)*1000 }
                    total += chunk.count
                    // 1200 codec frames at 12.5Hz plus decoder margin; also bounds nonstreaming output.
                    guard total <= 100 * 48000 else { throw ServiceError(422, "generation_limit", "生成达到长度上限，请缩短文本。") }
                    if value.stream { try emit(chunk) } else { pcm.append(chunk) }
                }
                try slot.cancellation.check()
                guard total > 0, let first else { throw ServiceError(500, "empty_output", "模型未生成音频。") }
                let metrics = engine.memory().merging(["duration": Double(total)/48000, "first_audio_ms": first,
                    "processing_ms": (uptime()-start)*1000]) { _, new in new }
                self.finish(slot, result: metrics)
                completion(.success((value.format == "wav" ? AudioFile.wav(pcm) : pcm, metrics)))
            } catch {
                let failure = error as? ServiceError ?? .inference
                self.finish(slot, error: failure); completion(.failure(failure))
            }
        }
    }
}
