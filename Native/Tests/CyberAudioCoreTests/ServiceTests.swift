import Foundation
import Darwin
import CyberAudioCore

private let testToken = "isolated-native-test-token-never-a-deployment-secret"

private final class Engine: InferenceEngine {
    let started = DispatchSemaphore(value:0)
    let release = DispatchSemaphore(value:0)
    var block = false, fail = false, lateFail = false
    var cancellation: CancellationToken?
    var chunkCount = 3, chunkBytes = 4800
    private let progressLock = NSLock()
    private var chunks = 0
    var producedChunks: Int { progressLock.lock(); defer { progressLock.unlock() }; return chunks }
    var metrics: [String:Any] { ["device":"test"] }
    func memory() -> [String:Any] { [:] }
    func transcribe(audio: URL,language: String?,cancellation: CancellationToken) throws -> [String:Any] {
        enter(); try cancellation.check()
        if fail { throw NSError(domain:"private text or path must not leak",code:1) }
        return ["text":"打开 CyberAudio。","language":"zh"]
    }
    func enter() { started.signal(); if block { _ = release.wait(timeout:.now()+5) } }
    func synthesize(_ speech: Speech,cancellation: CancellationToken,emit: @escaping (Data) throws -> Void) throws {
        self.cancellation = cancellation
        enter()
        if fail { throw NSError(domain:"private text or path must not leak",code:1) }
        for i in 1...chunkCount {
            try cancellation.check()
            if lateFail && i == 2 { throw ServiceError(422,"generation_limit","shorten text") }
            try emit(Data(repeating:UInt8(i%255),count:chunkBytes))
            progressLock.lock(); chunks += 1; progressLock.unlock()
        }
    }
}

final class ServiceTests: XCTestCase {
    private func setup(timeout: Double = 5,install: Bool = true) throws -> (URL,ServiceCoordinator,Engine) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("CyberAudio-tests-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        let config = ServiceConfiguration(asr_dir:root.path,tts_dir:root.path,ffmpeg:"/opt/homebrew/bin/ffmpeg")
        let state = ServiceCoordinator(settings:try ServiceSettings(root:root,configuration:config,token:testToken,instance:"test",timeout:timeout))
        let engine = Engine(); if install { state.install(engine) }
        addTeardownBlock { try? FileManager.default.removeItem(at:root) }
        return (root,state,engine)
    }
    private func speech(_ fields: [String:Any] = [:]) throws -> Speech {
        try Speech(data:jsonData(["input":"你好。"].merging(fields) { _,new in new }))
    }
    private func assertCode(_ expected: String,_ work: () throws -> Void,file: StaticString = #filePath,line: UInt = #line) {
        XCTAssertThrowsError(try work(),file:file,line:line) { XCTAssertEqual(($0 as? ServiceError)?.code,expected,file:file,line:line) }
    }
    private func request(_ base: URL,_ path: String,body: Data? = nil,type: String = "application/json",auth: Bool = true) async throws -> (Data,HTTPURLResponse) {
        var request = URLRequest(url:base.appendingPathComponent(path))
        request.httpMethod = body == nil ? "GET" : "POST"; request.httpBody = body
        request.setValue(type,forHTTPHeaderField:"content-type")
        if auth { request.setValue("Bearer "+testToken,forHTTPHeaderField:"authorization") }
        let (data,response) = try await URLSession.shared.data(for:request)
        return (data,response as! HTTPURLResponse)
    }
    private func multipart(_ data: Data,fields: String = "") -> Data {
        var body = Data("--test-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n".utf8)
        body.append(data); body.append(Data(("\r\n"+fields+"--test-boundary--\r\n").utf8)); return body
    }
    private func normalizedWAV(_ pcm: Data) -> Data {
        var data = AudioFile.wav(pcm)
        data.replaceSubrange(24..<28,with:[0x80,0x3e,0,0]); data.replaceSubrange(28..<32,with:[0,0x7d,0,0]); return data
    }

    func testStrictSpeechOptionsAndAliases() throws {
        for fields: [String:Any] in [["input":""],["input":" "],["input":42],["input":String(repeating:"字",count:501)],
            ["instructions":String(repeating:"a",count:201)],["voice":"missing"],["language":"fr"],["stream":"true"],["stream":1],
            ["response_format":"mp3"],["stream":true],["speed":2],["model":"other"]] {
            XCTAssertThrowsError(try speech(fields))
        }
        let value = try speech(["voice":"RYAN","language":"en","stream":true,"response_format":"pcm"])
        XCTAssertEqual(value.voice,"ryan"); XCTAssertEqual(value.language,"english"); XCTAssertTrue(value.stream)
    }
    func testMultipartBinaryBoundaryAndLimits() throws {
        let payload = Data([0,255,13,10])+Data("--test-boundary-not-a-delimiter".utf8)+Data([0,254])
        let part = try Multipart.parse(multipart(payload),contentType:"multipart/form-data; boundary=\"test-boundary\"")
        XCTAssertEqual(try Transcription(parts:part).audio,payload)
        XCTAssertThrowsError(try Multipart.parse(multipart(payload).dropLast(4),contentType:"multipart/form-data; boundary=test-boundary"))
        let duplicate = "--test-boundary\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nzh\r\n"
        XCTAssertThrowsError(try Transcription(parts:Multipart.parse(multipart(payload,fields:duplicate+duplicate),contentType:"multipart/form-data; boundary=test-boundary")))
        XCTAssertThrowsError(try Multipart.parse(multipart(payload),contentType:"application/json"))
    }
    func testUpdateLeasesAndAdmissionAreAtomic() throws {
        let (_,state,_) = try setup()
        let slot = try state.acquire(kind:"tts")
        XCTAssertEqual(try state.update(action:"prepare",instance:"test",lease:"one")["busy"] as? Bool,true)
        assertCode("updating") { _ = try state.acquire(kind:"stt") }
        assertCode("update_in_progress") { _ = try state.update(action:"prepare",instance:"test",lease:"two") }
        _ = try state.update(action:"cancel",instance:"test",lease:"one")
        assertCode("update_cancelled") { _ = try state.update(action:"prepare",instance:"test",lease:"one") }
        _ = try state.update(action:"prepare",instance:"test",lease:"two")
        _ = try state.update(action:"cancel",instance:"test",lease:"one")
        XCTAssertEqual(state.ready["updating"] as? Bool,true)
        slot.cancellation.cancel()
        assertCode("wrong_instance") { _ = try state.update(action:"cancel",instance:"other",lease:"two") }
    }
    func testAbandonedLeaseExpiresAndCancelledLeaseCannotRenew() throws {
        let (root,original,_) = try setup(); var time = 10.0
        let state = ServiceCoordinator(settings:original.settings,clock:{ time }); state.install(Engine())
        _ = try state.update(action:"prepare",instance:"test",lease:"old")
        time = 41
        XCTAssertEqual(state.ready["updating"] as? Bool,false)
        _ = try state.update(action:"cancel",instance:"test",lease:"old")
        assertCode("update_cancelled") { _ = try state.update(action:"prepare",instance:"test",lease:"old") }
        XCTAssertNotNil(root); _ = try state.acquire(kind:"stt")
    }
    func testCancelledWorkerKeepsSlotUntilProducerReturns() throws {
        let (_,state,engine) = try setup(); engine.block = true
        let slot = try state.acquire(kind:"tts"), done = expectation(description:"worker returned")
        state.synthesize(try speech(),slot:slot,emit:{ _ in }) { result in
            if case .failure(let error) = result { XCTAssertEqual(error.code,"cancelled") } else { XCTFail("Cancellation became a success") }
            done.fulfill()
        }
        XCTAssertEqual(engine.started.wait(timeout:.now()+2),.success)
        slot.cancellation.cancel()
        assertCode("busy") { _ = try state.acquire(kind:"stt") }
        XCTAssertEqual(try state.record(slot.id)["status"] as? String,"running")
        engine.release.signal(); wait(for:[done],timeout:3)
        XCTAssertEqual(try state.record(slot.id)["status"] as? String,"cancelled")
        _ = try state.acquire(kind:"stt")
    }
    func testTimeoutKeepsSlotAndRecordsFailure() throws {
        let (_,state,engine) = try setup(timeout:0.02); engine.block = true
        let slot = try state.acquire(kind:"tts"), done = expectation(description:"timed out worker")
        state.synthesize(try speech(),slot:slot,emit:{ _ in }) { _ in done.fulfill() }
        XCTAssertEqual(engine.started.wait(timeout:.now()+2),.success)
        Thread.sleep(forTimeInterval:0.04)
        assertCode("inference_timeout") { try slot.cancellation.check() }
        assertCode("busy") { _ = try state.acquire(kind:"stt") }
        engine.release.signal(); wait(for:[done],timeout:3)
        XCTAssertEqual(try state.record(slot.id)["code"] as? String,"inference_timeout")
    }
    func testActualHTTPAuthenticationWAVStreamingAndStatuses() async throws {
        let (_,state,_) = try setup(); let server = HTTPService(coordinator:state)
        let port = try server.start(port:0); defer { try? server.stop() }
        let base = URL(string:"http://127.0.0.1:\(port)")!
        let (unauthorized,response) = try await request(base,"v1/models",auth:false)
        XCTAssertEqual(response.statusCode,401); XCTAssertFalse(unauthorized.isEmpty)
        let (models,_) = try await request(base,"v1/models")
        XCTAssertTrue(String(decoding:models,as:UTF8.self).contains(ModelCatalog.asrRevision))
        let (wav,wavResponse) = try await request(base,"v1/audio/speech",body:jsonData(["input":"test"]))
        XCTAssertEqual(wavResponse.statusCode,200); XCTAssertEqual(String(decoding:wav.prefix(4),as:UTF8.self),"RIFF")
        let (pcm,streamResponse) = try await request(base,"v1/audio/speech",body:jsonData(["input":"test","stream":true,"response_format":"pcm"]))
        XCTAssertEqual(streamResponse.statusCode,200); XCTAssertEqual(pcm,Data(wav.dropFirst(44)))
        XCTAssertEqual(streamResponse.value(forHTTPHeaderField:"x-audio-format"),"pcm_s16le")
        let id = try XCTUnwrap(streamResponse.value(forHTTPHeaderField:"x-request-id"))
        let (record,_) = try await request(base,"v1/requests/"+id)
        XCTAssertEqual((try JSONSerialization.jsonObject(with:record) as? [String:Any])?["status"] as? String,"completed")
        let (invalid,invalidResponse) = try await request(base,"v1/audio/speech",body:jsonData(["input":true]))
        XCTAssertEqual(invalidResponse.statusCode,400); XCTAssertFalse(invalid.isEmpty)
    }
    func testActualFFmpegDecodeSilenceLongAudioAndCleanup() async throws {
        guard FileManager.default.isExecutableFile(atPath:"/opt/homebrew/bin/ffmpeg") else { throw XCTSkip("FFmpeg unavailable") }
        let (root,state,_) = try setup(); let server = HTTPService(coordinator:state)
        let port = try server.start(port:0); defer { try? server.stop() }
        let base = URL(string:"http://127.0.0.1:\(port)")!
        for (payload,status) in [(Data("invalid audio".utf8),400),(normalizedWAV(Data(repeating:0,count:3200)),200),
            (normalizedWAV(Data(repeating:1,count:3200)),200),(normalizedWAV(Data(repeating:0,count:60*32000+4000)),413)] {
            let (data,response) = try await request(base,"v1/audio/transcriptions",body:multipart(payload),type:"multipart/form-data; boundary=test-boundary")
            XCTAssertEqual(response.statusCode,status,String(decoding:data,as:UTF8.self))
            if status == 200 {
                let json = try JSONSerialization.jsonObject(with:data) as! [String:Any]
                XCTAssertEqual(json["revision"] as? String,ModelCatalog.asrRevision)
            }
        }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath:root.appendingPathComponent("tmp").path),[])
    }
    func testRawHTTPChunkedLimitAndDuplicateAuthorization() throws {
        let (_,state,_) = try setup(); let server = HTTPService(coordinator:state)
        let port = try server.start(port:0); defer { try? server.stop() }
        let oversized = String(repeating:"a",count:17000)
        let raw = "POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nTransfer-Encoding: chunked\r\n\r\n\(String(oversized.utf8.count,radix:16))\r\n\(oversized)\r\n0\r\n\r\n"
        XCTAssertTrue(try rawRequest(port:port,request:raw).hasPrefix("HTTP/1.1 413"))
        XCTAssertTrue(try rawRequest(port:port,request:"GET /v1/models HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nAuthorization: Bearer \(testToken)\r\n\r\n").hasPrefix("HTTP/1.1 401"))
    }
    func testPrivateFailureAndLateStreamingFailure() async throws {
        let (_,state,engine) = try setup(); engine.fail = true
        let server = HTTPService(coordinator:state); let port = try server.start(port:0); defer { try? server.stop() }
        let base = URL(string:"http://127.0.0.1:\(port)")!
        let (data,response) = try await request(base,"v1/audio/speech",body:jsonData(["input":"test"]))
        XCTAssertEqual(response.statusCode,500); XCTAssertFalse(String(decoding:data,as:UTF8.self).contains("private"))
        engine.fail = false; engine.lateFail = true
        let (failure,late) = try await request(base,"v1/audio/speech",body:jsonData(["input":"test"]))
        XCTAssertEqual(late.statusCode,422); XCTAssertTrue(String(decoding:failure,as:UTF8.self).contains("generation_limit"))
        let payload = "{\"input\":\"test\",\"stream\":true,\"response_format\":\"pcm\"}"
        let raw = try rawRequest(port:port,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)")
        XCTAssertTrue(raw.hasPrefix("HTTP/1.1 200")); XCTAssertFalse(raw.hasSuffix("0\r\n\r\n"))
        let idLine = try XCTUnwrap(raw.components(separatedBy:"\r\n").first { $0.lowercased().hasPrefix("x-request-id: ") })
        let id = String(idLine.dropFirst("x-request-id: ".count))
        let (recordData,recordResponse) = try await request(base,"v1/requests/"+id)
        XCTAssertEqual(recordResponse.statusCode,200)
        let record = try JSONSerialization.jsonObject(with:recordData) as! [String:Any]
        XCTAssertEqual(record["status"] as? String,"failed")
        XCTAssertEqual(record["code"] as? String,"generation_limit")
        XCTAssertEqual(record["message"] as? String,"shorten text", "Late stream failure must preserve the same safe message as the WAV response")
        engine.lateFail = false; engine.fail = true
        let slot = try state.acquire(kind:"tts"), done = expectation(description:"private failure recorded")
        state.synthesize(try speech(),slot:slot,emit:{ _ in }) { _ in done.fulfill() }
        wait(for:[done],timeout:2)
        let privateRecord = try state.record(slot.id)
        XCTAssertEqual(privateRecord["message"] as? String,ServiceError.inference.message)
        XCTAssertFalse(String(decoding:try jsonData(privateRecord),as:UTF8.self).contains("private text"))
    }
    func testLoadingFailureHealthAndScopedCleanup() async throws {
        let (root,state,_) = try setup(install:false); state.initializationFailed()
        let temp = root.appendingPathComponent("tmp"); try FileManager.default.createDirectory(at:temp,withIntermediateDirectories:true)
        for name in ["request-stale","keep"] { try FileManager.default.createDirectory(at:temp.appendingPathComponent(name),withIntermediateDirectories:true) }
        try FileManager.default.createSymbolicLink(at:temp.appendingPathComponent("request-symlink"),withDestinationURL:root)
        try AudioFile.cleanup(temp)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath:temp.path).sorted(),["keep","request-symlink"])
        let server = HTTPService(coordinator:state); let port = try server.start(port:0); defer { try? server.stop() }
        let base = URL(string:"http://127.0.0.1:\(port)")!
        let (_,health) = try await request(base,"health"); XCTAssertEqual(health.statusCode,200)
        let (_,ready) = try await request(base,"ready"); XCTAssertEqual(ready.statusCode,503)
    }
    func testHTTPDisconnectJoinsProducerAndUpdateCanPauseAdmission() async throws {
        let (_,state,engine) = try setup(); engine.block = true
        let server = HTTPService(coordinator:state),payload = "{\"input\":\"test\"}"
        let port = try server.start(port:0); defer { try? server.stop() }
        let base = URL(string:"http://127.0.0.1:\(port)")!
        var fd = try connectedSocket(port:port); defer { if fd >= 0 { close(fd) } }
        try sendAll(fd,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)")
        XCTAssertEqual(engine.started.wait(timeout:.now()+2),.success)
        close(fd); fd = -1
        let deadline = uptime()+2
        while engine.cancellation?.isCancelled != true && uptime() < deadline { try await Task.sleep(nanoseconds:10_000_000) }
        XCTAssertEqual(engine.cancellation?.isCancelled,true)
        assertCode("busy") { _ = try state.acquire(kind:"stt") }
        var update = URLRequest(url:base.appendingPathComponent("internal/update/prepare")); update.httpMethod = "POST"
        update.setValue("Bearer "+testToken,forHTTPHeaderField:"authorization")
        update.setValue("test",forHTTPHeaderField:"x-cyberaudio-instance"); update.setValue("drain",forHTTPHeaderField:"x-cyberaudio-update")
        let (data,response) = try await URLSession.shared.data(for:update)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode,200)
        XCTAssertEqual((try JSONSerialization.jsonObject(with:data) as? [String:Any])?["busy"] as? Bool,true)
        let (_,paused) = try await request(base,"v1/audio/speech",body:jsonData(["input":"test"]))
        XCTAssertEqual(paused.statusCode,503)
        engine.release.signal()
        let drained = uptime()+2
        while state.ready["busy"] as? Bool == true && uptime() < drained { try await Task.sleep(nanoseconds:10_000_000) }
        XCTAssertEqual(state.ready["busy"] as? Bool,false)
        _ = try state.update(action:"cancel",instance:"test",lease:"drain")
        engine.block = false
        let (_,recovered) = try await request(base,"v1/audio/speech",body:jsonData(["input":"test"]))
        XCTAssertEqual(recovered.statusCode,200)
    }
    func testHTTPTimeoutRespondsBeforeProducerReturns() throws {
        let (_,state,engine) = try setup(timeout:0.1); engine.block = true
        let server = HTTPService(coordinator:state),payload = "{\"input\":\"test\"}"
        let port = try server.start(port:0); defer { try? server.stop() }
        let response = try rawRequest(port:port,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)")
        XCTAssertTrue(response.hasPrefix("HTTP/1.1 504"))
        assertCode("busy") { _ = try state.acquire(kind:"stt") }
        engine.release.signal()
        let deadline = uptime()+2
        while state.ready["busy"] as? Bool == true && uptime() < deadline { Thread.sleep(forTimeInterval:0.01) }
        XCTAssertEqual(state.ready["busy"] as? Bool,false)
    }
    func testSlowHTTPReaderBoundsProducerAndDisconnectRecovers() throws {
        let (_,state,engine) = try setup(); engine.chunkCount = 96; engine.chunkBytes = 48000
        let server = HTTPService(coordinator:state)
        let port = try server.start(port:0); defer { try? server.stop() }
        var fd = try connectedSocket(port:port); defer { if fd >= 0 { close(fd) } }
        var smallBuffer: Int32 = 1024
        setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&smallBuffer,socklen_t(MemoryLayout<Int32>.size))
        let body = "{\"input\":\"test\",\"stream\":true,\"response_format\":\"pcm\"}"
        try sendAll(fd,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(body.utf8.count)\r\n\r\n\(body)")
        XCTAssertEqual(engine.started.wait(timeout:.now()+2),.success)
        Thread.sleep(forTimeInterval:0.2)
        let first = engine.producedChunks
        Thread.sleep(forTimeInterval:0.15)
        XCTAssertEqual(engine.producedChunks,first,"Producer must stall while the client does not consume")
        XCTAssertTrue(first < 96,"Slow socket buffered all audio")
        assertCode("busy") { _ = try state.acquire(kind:"stt") }
        close(fd); fd = -1
        let deadline = uptime()+2
        while state.ready["busy"] as? Bool == true && uptime() < deadline { Thread.sleep(forTimeInterval:0.01) }
        XCTAssertEqual(state.ready["busy"] as? Bool,false)
        XCTAssertEqual(engine.cancellation?.isCancelled,true)
        _ = try state.acquire(kind:"tts")
    }
    func testNativeAssetSetupPreservesExistingTokenAndConfiguration() throws {
        let (root,_,_) = try setup()
        try AssetStore.prepare(root:root,port:9876)
        let config = try Data(contentsOf:root.appendingPathComponent("config/service.json")),token = try Data(contentsOf:root.appendingPathComponent("config/api-token"))
        try AssetStore.prepare(root:root,port:1111)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("config/service.json")),config)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("config/api-token")),token)
        XCTAssertThrowsError(try AssetStore.verify(AssetStore.configuration(root:root)))
    }
    func testUpdateRejectsAudioAtHeadersAfterAuthentication() throws {
        let (_,state,_) = try setup()
        let server = HTTPService(coordinator:state),port = try server.start(port:0)
        defer { try? server.stop() }
        _ = try state.update(action:"prepare",instance:"test",lease:"headers")
        for path in ["/v1/audio/speech","/v1/audio/transcriptions"] {
            for authenticated in [false,true] {
                let auth = authenticated ? "Authorization: Bearer \(testToken)\r\n" : ""
                // No body is sent: rejection must arrive before parsing or upload timeout.
                let response = try rawRequest(port:port,request:"POST \(path) HTTP/1.1\r\nHost: localhost\r\n\(auth)Content-Length: 1000\r\n\r\n")
                XCTAssertTrue(response.hasPrefix(authenticated ? "HTTP/1.1 503" : "HTTP/1.1 401"))
                if authenticated { XCTAssertTrue(response.contains("updating")); XCTAssertTrue(response.lowercased().contains("retry-after: 2")) }
            }
        }
    }
    func testUpdateWaitsForCompletedModelResponseToFlush() throws {
        let (_,state,engine) = try setup(); engine.chunkCount = 1; engine.chunkBytes = 4_000_000
        let server = HTTPService(coordinator:state),port = try server.start(port:0)
        defer { try? server.stop() }
        var fd = try connectedSocket(port:port); defer { if fd >= 0 { close(fd) } }
        var smallBuffer: Int32 = 1024
        setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&smallBuffer,socklen_t(MemoryLayout<Int32>.size))
        let payload = "{\"input\":\"test\"}"
        try sendAll(fd,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)")
        XCTAssertEqual(engine.started.wait(timeout:.now()+2),.success)
        let finished = uptime()+2
        while state.ready["busy"] as? Bool == true && uptime() < finished { Thread.sleep(forTimeInterval:0.01) }
        XCTAssertEqual(state.ready["busy"] as? Bool,false,"Model has returned, but the client is not reading its WAV")
        XCTAssertEqual(try state.update(action:"prepare",instance:"test",lease:"flush")["busy"] as? Bool,true)
        close(fd); fd = -1
        let drained = uptime()+2
        while try state.update(action:"prepare",instance:"test",lease:"flush")["busy"] as? Bool == true,uptime() < drained { Thread.sleep(forTimeInterval:0.01) }
        XCTAssertEqual(try state.update(action:"prepare",instance:"test",lease:"flush")["busy"] as? Bool,false)
    }
    func testUnreadWAVExpiresWithoutClientDisconnect() throws {
        let (_,state,engine) = try setup(timeout:0.3)
        engine.chunkCount = 1; engine.chunkBytes = 4_000_000
        let server = HTTPService(coordinator:state),port = try server.start(port:0)
        defer { try? server.stop() }
        let fd = try connectedSocket(port:port); defer { close(fd) }
        var smallBuffer: Int32 = 1024
        setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&smallBuffer,socklen_t(MemoryLayout<Int32>.size))
        let payload = "{\"input\":\"test\"}"
        try sendAll(fd,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)")
        XCTAssertEqual(engine.started.wait(timeout:.now()+2),.success)
        let generated = uptime()+0.2
        while state.ready["busy"] as? Bool == true && uptime() < generated { Thread.sleep(forTimeInterval:0.005) }
        XCTAssertEqual(state.ready["busy"] as? Bool,false)
        XCTAssertEqual(try state.update(action:"prepare",instance:"test",lease:"send-timeout")["busy"] as? Bool,true,
                       "The response must block updates while still within its deadline")
        let expired = uptime()+2
        while try state.update(action:"prepare",instance:"test",lease:"send-timeout")["busy"] as? Bool == true,uptime() < expired {
            Thread.sleep(forTimeInterval:0.01)
        }
        XCTAssertEqual(try state.update(action:"prepare",instance:"test",lease:"send-timeout")["busy"] as? Bool,false,
                       "An unread WAV must release update drain at its deadline without client cleanup")
        _ = try state.update(action:"cancel",instance:"test",lease:"send-timeout")
        engine.chunkBytes = 4800
        XCTAssertTrue(try rawRequest(port:port,request:"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n").hasPrefix("HTTP/1.1 200"))
        let resumed = try rawRequest(port:port,request:"POST /v1/audio/speech HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer \(testToken)\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)")
        XCTAssertTrue(resumed.hasPrefix("HTTP/1.1 200"),"A timed-out writer must not prevent subsequent inference")
    }
    func testTokenizerComparisonAcrossProcessesAndLocales() throws {
        let (root,_,_) = try setup()
        var vocabulary: [String:Int] = [:]
        for number in 0..<400 {
            for prefix in ["A","a","Á","á","İ","i","Z","z","000","00"] {
                vocabulary[prefix+String(number)] = vocabulary.count
            }
        }
        // Qwen contains long punctuation tokens. Foundation's sorted-key
        // collation can order these differently between processes/locales.
        for number in 1...300 {
            for suffix in ["","Ċ"] {
                vocabulary["//"+String(repeating:"-",count:number)+suffix] = vocabulary.count
            }
        }
        try jsonData(vocabulary).write(to:root.appendingPathComponent("vocab.json"))
        try Data("#version: 0.2\na b\n".utf8).write(to:root.appendingPathComponent("merges.txt"))
        try jsonData(["added_tokens_decoder":[:]]).write(to:root.appendingPathComponent("tokenizer_config.json"))
        _ = try AssetStore.prepareTokenizer(directory:root,asr:false)
        for locale in ["en_US","zh_CN","tr_TR","ja_JP","en_US","zh_CN"] {
            let process = Process()
            process.executableURL = URL(fileURLWithPath:CommandLine.arguments[0])
            process.arguments = ["--check-tokenizer",root.path,"-AppleLocale",locale]
            process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            try process.run(); process.waitUntilExit()
            XCTAssertEqual(process.terminationStatus,0,"Same tokenizer rejected by locale \(locale)")
        }
        let file = root.appendingPathComponent("tokenizer.json")
        let original = try Data(contentsOf:file)
        var value = try JSONSerialization.jsonObject(with:original) as! [String:Any]
        var model = value["model"] as! [String:Any],vocab = model["vocab"] as! [String:Any]
        vocab["A0"] = false; model["vocab"] = vocab; value["model"] = model
        try jsonData(value).write(to:file)
        XCTAssertThrowsError(try AssetStore.prepareTokenizer(directory:root,asr:false))
        value = try JSONSerialization.jsonObject(with:original) as! [String:Any]
        value["normalizer"] = ["type":"Lowercase"]
        try jsonData(value).write(to:file)
        XCTAssertThrowsError(try AssetStore.prepareTokenizer(directory:root,asr:false))
    }
    func testIPv6DownloadProxyRoutingAndPreservation() async throws {
        let (root,_,_) = try setup(); try AssetStore.prepare(root:root)
        let configuration = root.appendingPathComponent("config/service.json"),token = root.appendingPathComponent("config/api-token")
        let originalConfiguration = try Data(contentsOf:configuration),originalToken = try Data(contentsOf:token)
        let server = socket(AF_INET6,SOCK_STREAM,0)
        guard server >= 0 else { throw ServiceError.inference }; defer { close(server) }
        var address = sockaddr_in6(); address.sin6_len = UInt8(MemoryLayout<sockaddr_in6>.size); address.sin6_family = sa_family_t(AF_INET6)
        guard inet_pton(AF_INET6,"::1",&address.sin6_addr) == 1 else { throw ServiceError.inference }
        let bound = withUnsafePointer(to:&address) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { bind(server,$0,socklen_t(MemoryLayout<sockaddr_in6>.size)) } }
        guard bound == 0,listen(server,4) == 0 else { throw ServiceError.inference }
        var length = socklen_t(MemoryLayout<sockaddr_in6>.size)
        let named = withUnsafeMutablePointer(to:&address) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { getsockname(server,$0,&length) } }
        guard named == 0 else { throw ServiceError.inference }
        let port = Int(UInt16(bigEndian:address.sin6_port))
        for (scheme,source,host) in [("http",AssetDownloadSource.huggingFace,"huggingface.co"),("socks5",.modelScope,"modelscope.cn")] {
            let seen = ProxyCapture()
            DispatchQueue.global().async {
                defer { seen.done.signal() }
                var descriptor = pollfd(fd:server,events:Int16(POLLIN),revents:0)
                guard poll(&descriptor,1,5000)>0 else { return }
                let peer = accept(server,nil,nil); guard peer >= 0 else { return }; defer { close(peer) }
                var timeout = timeval(tv_sec:2,tv_usec:0); setsockopt(peer,SOL_SOCKET,SO_RCVTIMEO,&timeout,socklen_t(MemoryLayout<timeval>.size))
                do {
                    if scheme == "http" {
                        var request = Data()
                        while !request.suffix(4).elementsEqual([13,10,13,10]),request.count < 8192 {
                            request.append(try receiveExactly(peer,count:1))
                        }
                        seen.request = String(decoding:request,as:UTF8.self)
                        try sendAll(peer,request:"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    } else {
                        let greeting = try receiveExactly(peer,count:2)
                        guard greeting[0] == 5 else { throw ServiceError.inference }
                        let methods = try receiveExactly(peer,count:Int(greeting[1]))
                        guard methods.contains(0) else { throw ServiceError.inference }
                        try sendAll(peer,request:"\u{5}\u{0}")
                        guard try receiveExactly(peer,count:4) == Data([5,1,0,3]) else { throw ServiceError.inference }
                        let hostLength = try receiveExactly(peer,count:1)[0]
                        let name = try receiveExactly(peer,count:Int(hostLength)),port = try receiveExactly(peer,count:2)
                        seen.request = String(decoding:name,as:UTF8.self)+":"+String(Int(port[0])*256+Int(port[1]))
                        // Reject locally after observing the destination; never forward upstream.
                        try sendAll(peer,request:String(decoding:[5,2,0,1,0,0,0,0,0,0],as:UTF8.self))
                    }
                } catch { seen.request = "Invalid proxy handshake" }
            }
            do { try await AssetStore.download(root:root,source:source,proxy:"\(scheme)://[::1]:\(port)"); XCTFail("Proxy rejection was accepted") }
            catch { XCTAssertEqual((error as? ServiceError)?.code,"download_unavailable") }
            XCTAssertEqual(seen.done.wait(timeout:.now()+6),.success)
            if scheme == "http" { XCTAssertTrue(seen.request.hasPrefix("CONNECT \(host):443 HTTP/1.1"),"IPv6 HTTP proxy did not receive CONNECT: "+seen.request) }
            else { XCTAssertEqual(seen.request,host+":443","IPv6 SOCKS5 proxy did not receive the requested destination") }
        }
        XCTAssertEqual(try Data(contentsOf:configuration),originalConfiguration)
        XCTAssertEqual(try Data(contentsOf:token),originalToken)
        XCTAssertFalse(FileManager.default.fileExists(atPath:root.appendingPathComponent("models/Qwen3-ASR-1.7B-8bit/README.md").path))
    }

    func testNativeDownloadUsesProxyAndPreservesAssetsOnFailure() async throws {
        let (root,_,_) = try setup(); try AssetStore.prepare(root:root)
        let server = socket(AF_INET,SOCK_STREAM,0); guard server >= 0 else { throw ServiceError.inference }; defer { close(server) }
        var address = sockaddr_in(); address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size); address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to:&address) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { bind(server,$0,socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        guard bound == 0,listen(server,4) == 0 else { throw ServiceError.inference }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to:&address) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { getsockname(server,$0,&length) } }
        let port = Int(UInt16(bigEndian:address.sin_port))
        for source in [AssetDownloadSource.huggingFace,.modelScope] {
            let seen = ProxyCapture()
            DispatchQueue.global().async {
                var descriptor = pollfd(fd:server,events:Int16(POLLIN),revents:0)
                guard poll(&descriptor,1,5000)>0 else { seen.done.signal(); return }
                let peer = accept(server,nil,nil); guard peer >= 0 else { seen.done.signal(); return }; defer { close(peer); seen.done.signal() }
                var timeout = timeval(tv_sec:2,tv_usec:0); setsockopt(peer,SOL_SOCKET,SO_RCVTIMEO,&timeout,socklen_t(MemoryLayout<timeval>.size))
                var bytes = [UInt8](repeating:0,count:8192); let count = recv(peer,&bytes,bytes.count,0)
                if count > 0 { seen.request = String(decoding:bytes.prefix(count),as:UTF8.self) }
                try? sendAll(peer,request:"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            }
            do { try await AssetStore.download(root:root,source:source,proxy:"http://127.0.0.1:\(port)"); XCTFail("Proxy failure was accepted") }
            catch { XCTAssertEqual((error as? ServiceError)?.code,"download_unavailable") }
            XCTAssertEqual(seen.done.wait(timeout:.now()+6),.success)
            let host = source == .huggingFace ? "huggingface.co" : "modelscope.cn"
            XCTAssertTrue(seen.request.hasPrefix("CONNECT \(host):443 HTTP/1.1"),seen.request)
        }
        let directory = root.appendingPathComponent("models/Qwen3-ASR-1.7B-8bit")
        XCTAssertFalse(FileManager.default.fileExists(atPath:directory.appendingPathComponent("README.md").path))
        let original = Data("preserve mismatched existing asset".utf8),file = directory.appendingPathComponent("README.md")
        try original.write(to:file)
        do { try await AssetStore.download(root:root); XCTFail("Mismatched asset was overwritten") }
        catch { XCTAssertEqual((error as? ServiceError)?.code,"invalid_assets") }
        XCTAssertEqual(try Data(contentsOf:file),original)
        for proxy in ["http://name:password@localhost:7890","https://localhost:7890","http://localhost","http://localhost:7890/path"] {
            do { try await AssetStore.download(root:root,proxy:proxy); XCTFail("Invalid proxy accepted") }
            catch { XCTAssertEqual((error as? ServiceError)?.code,"invalid_proxy") }
        }
    }
}

private final class ProxyCapture: @unchecked Sendable {
    let done = DispatchSemaphore(value:0)
    // Written by the socket thread and read only after done is signalled.
    var request = ""
}

private func rawRequest(port: Int,request: String) throws -> String {
    let fd = try connectedSocket(port:port); defer { close(fd) }
    try sendAll(fd,request:request)
    var response = Data(),buffer = [UInt8](repeating:0,count:8192)
    while true { let count = recv(fd,&buffer,buffer.count,0); if count <= 0 { break }; response.append(contentsOf:buffer.prefix(count)) }
    return String(decoding:response,as:UTF8.self)
}
private func connectedSocket(port: Int) throws -> Int32 {
    let fd = socket(AF_INET,SOCK_STREAM,0); guard fd >= 0 else { throw ServiceError.inference }
    var timeout = timeval(tv_sec:5,tv_usec:0); setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,socklen_t(MemoryLayout<timeval>.size))
    var address = sockaddr_in(); address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size); address.sin_family = sa_family_t(AF_INET)
    address.sin_port = UInt16(port).bigEndian; address.sin_addr.s_addr = inet_addr("127.0.0.1")
    let status = withUnsafePointer(to:&address) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { connect(fd,$0,socklen_t(MemoryLayout<sockaddr_in>.size)) } }
    guard status == 0 else { close(fd); throw ServiceError.inference }
    return fd
}
private func sendAll(_ fd: Int32,request: String) throws {
    let bytes = Array(request.utf8); var written = 0
    while written < bytes.count {
        let count = bytes.withUnsafeBytes { send(fd,$0.baseAddress!.advanced(by:written),bytes.count-written,0) }
        guard count > 0 else { throw ServiceError.inference }; written += count
    }
}
private func receiveExactly(_ fd: Int32,count: Int) throws -> Data {
    var result = Data(),buffer = [UInt8](repeating:0,count:max(1,count))
    while result.count < count {
        let received = recv(fd,&buffer,count-result.count,0)
        guard received > 0 else { throw ServiceError.inference }
        result.append(contentsOf:buffer.prefix(received))
    }
    return result
}
