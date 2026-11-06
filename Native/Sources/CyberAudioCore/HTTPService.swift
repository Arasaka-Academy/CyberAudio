import Foundation
import NIOCore
import NIOPosix
import NIOHTTP1

public final class HTTPService {
    public let coordinator: ServiceCoordinator
    private let group = MultiThreadedEventLoopGroup(numberOfThreads: 2)
    private var listener: Channel?
    private let connections = ConnectionRegistry()
    public init(coordinator: ServiceCoordinator) { self.coordinator = coordinator }
    public func start(port: Int? = nil) throws -> Int {
        let channel = try ServerBootstrap(group: group)
            .serverChannelOption(ChannelOptions.backlog, value: 64)
            .serverChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
            .childChannelOption(ChannelOptions.maxMessagesPerRead, value: 8)
            .childChannelOption(ChannelOptions.writeBufferWaterMark, value: ChannelOptions.Types.WriteBufferWaterMark(low: 32*1024, high: 64*1024))
            .childChannelInitializer { channel in
                guard self.connections.insert(channel) else { return channel.close() }
                channel.closeFuture.whenComplete { _ in self.connections.remove(channel) }
                return channel.pipeline.configureHTTPServerPipeline(withPipeliningAssistance: false, withErrorHandling: true)
                    .flatMap { channel.pipeline.addHandler(HTTPHandler(coordinator: self.coordinator)) }
            }.bind(host: "127.0.0.1", port: port ?? coordinator.settings.configuration.port).wait()
        listener = channel
        return channel.localAddress!.port!
    }
    public func stop() throws {
        coordinator.shutdown()
        try listener?.close().wait()
        connections.closeAll()
        try group.syncShutdownGracefully()
    }
}

private final class ConnectionRegistry: @unchecked Sendable {
    private let lock = NSLock()
    private var channels: [ObjectIdentifier: Channel] = [:]
    func insert(_ channel: Channel) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard channels.count < 64 else { return false }
        channels[ObjectIdentifier(channel)] = channel; return true
    }
    func remove(_ channel: Channel) { lock.lock(); channels.removeValue(forKey: ObjectIdentifier(channel)); lock.unlock() }
    func closeAll() {
        lock.lock(); let current = Array(channels.values); lock.unlock()
        for channel in current { _ = channel.close() }
    }
}

/// NIO handles HTTP framing, duplicate content-length, transfer-encoding and chunked decoding.
/// One request per connection avoids pipelined bodies accumulating during inference.
public final class HTTPHandler: ChannelInboundHandler {
    public typealias InboundIn = HTTPServerRequestPart
    public typealias OutboundOut = HTTPServerResponsePart
    private let coordinator: ServiceCoordinator
    private var head: HTTPRequestHead?
    private var body = Data()
    private var handling = false
    private var sentHeaders = false
    private var finished = false
    private var slot: InferenceSlot?
    private var audioResponse: UUID?
    private var timeout: Scheduled<Void>?
    private var bodyLimit = 16*1024
    public init(coordinator: ServiceCoordinator) { self.coordinator = coordinator }

    public func handlerAdded(context: ChannelHandlerContext) {
        timeout = context.eventLoop.scheduleTask(in: .seconds(30)) { [weak self] in
            self?.expire(ServiceError(408, "upload_timeout", "上传超时。"), context: context)
        }
    }
    public func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        guard !finished else { return }
        do {
            switch unwrapInboundIn(data) {
            case .head(let request):
                guard head == nil, !handling else { throw ServiceError(400, "invalid_request", "不支持流水线请求。") }
                head = request
                let path = request.uri.split(separator: "?", maxSplits: 1).first.map(String.init) ?? request.uri
                if path.hasPrefix("/v1/") || path.hasPrefix("/internal/") {
                    let values = request.headers["authorization"]
                    guard values.count == 1, Self.constantTimeEqual(values[0], "Bearer " + coordinator.settings.token) else {
                        throw ServiceError(401, "unauthorized", "令牌无效或缺失。")
                    }
                }
                if request.method == .POST, ["/v1/audio/speech","/v1/audio/transcriptions"].contains(path) {
                    audioResponse = try coordinator.admitAudioRequest()
                }
                bodyLimit = path == "/v1/audio/transcriptions" ? ModelCatalog.maxUpload+1024*1024 : 16*1024
                if let length = request.headers.first(name: "content-length") {
                    guard let number = Int(length), number >= 0 else { throw ServiceError(400, "invalid_request", "请求长度无效。") }
                    guard number <= bodyLimit else { throw ServiceError(413, "upload_too_large", "请求超过大小限制。") }
                }
                if request.headers.first(name: "expect")?.lowercased() == "100-continue" {
                    context.writeAndFlush(wrapOutboundOut(.head(HTTPResponseHead(version: .http1_1, status: .continue))), promise: nil)
                }
            case .body(var bytes):
                guard head != nil, !handling else { throw ServiceError(400, "invalid_request", "请求无效。") }
                guard bytes.readableBytes <= bodyLimit-body.count else { throw ServiceError(413, "upload_too_large", "请求超过大小限制。") }
                if let value = bytes.readBytes(length: bytes.readableBytes) { body.append(contentsOf: value) }
            case .end:
                guard let head, !handling else { throw ServiceError(400, "invalid_request", "请求无效。") }
                handling = true; timeout?.cancel(); timeout = nil
                try route(head, context: context)
                body.removeAll(keepingCapacity: false)
            }
        } catch { fail(error as? ServiceError ?? ServiceError(400,"invalid_request","请求无效。"), context: context) }
    }
    public func channelInactive(context: ChannelHandlerContext) {
        finished = true
        timeout?.cancel(); slot?.cancellation.cancel(); completeAudioResponse(); context.fireChannelInactive()
    }
    public func errorCaught(context: ChannelHandlerContext, error: Error) {
        slot?.cancellation.cancel()
        fail(ServiceError(400,"invalid_request","请求无效。"), context: context)
    }
    private static func constantTimeEqual(_ left: String, _ right: String) -> Bool {
        let a = Array(left.utf8), b = Array(right.utf8)
        guard a.count == b.count else { return false }
        return zip(a,b).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) } == 0
    }
    private func route(_ head: HTTPRequestHead, context: ChannelHandlerContext) throws {
        let path = String(head.uri.split(separator: "?", maxSplits: 1).first ?? "")
        if head.method == .GET {
            switch path {
            case "/health": return respond(["status":"ok","application":"CyberAudio","instance":coordinator.settings.instance], context: context)
            case "/ready":
                let ready = coordinator.ready
                return respond(ready, status: ready["status"] as? String == "ready" ? 200 : 503, context: context)
            case "/v1/models": return respond(ModelCatalog.models, context: context)
            case "/v1/audio/voices": return respond(["model":ModelCatalog.ttsID,"voices":ModelCatalog.voices,"default":"serena","sample_rate":24000], context: context)
            case "/": return send(Data("<!doctype html><meta charset='utf-8'><title>CyberAudio</title><h1>CyberAudio</h1><p>请在 Mac 上打开 CyberAudio App。统一 API 已提供 STT 与 TTS。</p><p>就绪状态：/ready。API：/v1/audio/transcriptions、/v1/audio/speech。</p>".utf8), type:"text/html; charset=utf-8", context:context)
            default:
                if path.hasPrefix("/v1/requests/") { return respond(try coordinator.record(String(path.dropFirst("/v1/requests/".count))), context: context) }
            }
        }
        if head.method == .POST {
            if ["/internal/update/prepare","/internal/update/cancel"].contains(path) {
                let value = try coordinator.update(action: path.hasSuffix("prepare") ? "prepare" : "cancel",
                    instance: head.headers.first(name: "x-cyberaudio-instance"), lease: head.headers.first(name: "x-cyberaudio-update"))
                return respond(value, context: context)
            }
            if path == "/v1/audio/transcriptions" {
                let input = try Transcription(parts: Multipart.parse(body, contentType: head.headers.first(name: "content-type") ?? ""))
                let slot = try begin(kind: "stt", context: context)
                coordinator.transcribe(input, slot: slot) { result in
                    context.eventLoop.execute {
                        switch result {
                        case .success(let value): self.respond(value, context: context)
                        case .failure(let error): self.fail(error, context: context)
                        }
                    }
                }
                return
            }
            if path == "/v1/audio/speech" {
                let speech = try Speech(data: body)
                let slot = try begin(kind: "tts", context: context)
                let headers = ["x-request-id":slot.id,"x-audio-sample-rate":"24000","x-audio-channels":"1","x-audio-format":"pcm_s16le","x-model":ModelCatalog.ttsID]
                // The worker waits for each write promise. At most one chunk crosses the
                // event loop boundary; disconnect and deadline interrupt blocked writers.
                coordinator.synthesize(speech, slot: slot, emit: { chunk in
                    let semaphore = DispatchSemaphore(value: 0)
                    let result = WriteResult()
                    context.eventLoop.execute {
                        guard context.channel.isActive, !self.finished else { result.error = .cancelled; semaphore.signal(); return }
                        if !self.sentHeaders { self.start(status:200, type:"application/octet-stream", headers:headers, length:nil, context:context) }
                        var buffer = context.channel.allocator.buffer(capacity: chunk.count); buffer.writeBytes(chunk)
                        context.writeAndFlush(self.wrapOutboundOut(.body(.byteBuffer(buffer)))).whenComplete { outcome in
                            if case .failure = outcome { result.error = .cancelled }
                            semaphore.signal()
                        }
                    }
                    while semaphore.wait(timeout: .now()+0.1) == .timedOut { try slot.cancellation.check() }
                    if let error = result.error { throw error }
                    try slot.cancellation.check()
                }, completion: { result in
                    context.eventLoop.execute {
                        switch result {
                        case .failure(let error): self.fail(error, context: context)
                        case .success(let (audio, metrics)):
                            if speech.stream { self.end(context:context) }
                            else {
                                var headers = headers
                                headers["x-processing-ms"] = String(describing:metrics["processing_ms"]!)
                                headers["x-first-audio-ms"] = String(describing:metrics["first_audio_ms"]!)
                                headers["x-audio-duration"] = String(describing:metrics["duration"]!)
                                self.send(audio,type:speech.format == "wav" ? "audio/wav" : "application/octet-stream",headers:headers,context:context)
                            }
                        }
                    }
                })
                return
            }
        }
        throw ServiceError(404,"not_found","接口不存在。")
    }
    private func begin(kind: String, context: ChannelHandlerContext) throws -> InferenceSlot {
        let slot = try coordinator.acquire(kind: kind); self.slot = slot
        timeout = context.eventLoop.scheduleTask(in: .milliseconds(Int64(coordinator.settings.timeout*1000))) { [weak self] in
            self?.expire(.timeout,context:context)
        }
        return slot
    }
    private func respond(_ object: [String:Any], status: Int = 200, context: ChannelHandlerContext) {
        send((try? jsonData(object)) ?? Data(), status:status,type:"application/json",context:context)
    }
    private func expire(_ error: ServiceError, context: ChannelHandlerContext) {
        slot?.cancellation.cancel(error)
        // A queued .end is not a completed send. Close even when finished was
        // set by end(), so an unread response cannot retain update drain forever.
        if sentHeaders {
            finished = true
            context.close(promise:nil)
        } else {
            fail(error,context:context)
        }
    }
    private func fail(_ error: ServiceError, context: ChannelHandlerContext) {
        guard !finished else { return }
        if sentHeaders { finished = true; timeout?.cancel(); context.close(promise:nil); return }
        var headers: [String:String] = [:]
        if error.status == 401 { headers["www-authenticate"] = "Bearer" }
        if error.status == 429 || error.code == "updating" { headers["retry-after"] = "2" }
        send((try? jsonData(error.json)) ?? Data(),status:error.status,type:"application/json",headers:headers,context:context)
    }
    private func start(status: Int,type: String,headers: [String:String],length: Int?,context: ChannelHandlerContext) {
        guard !finished, !sentHeaders else { return }; sentHeaders = true
        var fields = HTTPHeaders([("content-type",type),("cache-control","no-store"),("x-content-type-options","nosniff"),
            ("referrer-policy","no-referrer"),("content-security-policy","default-src 'none'; frame-ancestors 'none'"),("connection","close")])
        for (name,value) in headers { fields.add(name:name,value:value) }
        if let length { fields.add(name:"content-length",value:String(length)) }
        else { fields.add(name:"transfer-encoding",value:"chunked") }
        context.write(wrapOutboundOut(.head(HTTPResponseHead(version:.http1_1,status:HTTPResponseStatus(statusCode:status),headers:fields))),promise:nil)
    }
    private func send(_ data: Data,status: Int = 200,type: String,headers: [String:String] = [:],context: ChannelHandlerContext) {
        guard !finished else { return }
        start(status:status,type:type,headers:headers,length:data.count,context:context)
        if !data.isEmpty {
            var buffer = context.channel.allocator.buffer(capacity:data.count); buffer.writeBytes(data)
            context.write(wrapOutboundOut(.body(.byteBuffer(buffer))),promise:nil)
        }
        end(context:context)
    }
    private func end(context: ChannelHandlerContext) {
        guard !finished else { return }; finished = true
        context.writeAndFlush(wrapOutboundOut(.end(nil))).whenComplete { _ in
            self.timeout?.cancel(); self.timeout = nil
            self.completeAudioResponse(); context.close(promise:nil)
        }
    }
    private func completeAudioResponse() {
        if let id = audioResponse { audioResponse = nil; coordinator.completeAudioResponse(id) }
    }
}

private final class WriteResult: @unchecked Sendable {
    // Access is synchronized by the semaphore used by the worker and event loop.
    var error: ServiceError?
}
