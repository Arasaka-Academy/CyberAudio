// Test-only helper. Synthetic audio does not establish Qwen model correctness.
import Foundation
import Darwin
import CyberAudioCore

private final class Fixture: InferenceEngine, @unchecked Sendable {
    let root: URL
    let lock = NSLock()
    var metrics: [String:Any] { ["load_ms":1,"warmup_ms":1,"device":"synthetic-fixture"] }
    init(root: URL) { self.root = root }
    func options() -> [String:Any] {
        guard let data = try? Data(contentsOf:root.appendingPathComponent("config/ui-fixture.json")) else { return [:] }
        return (try? JSONSerialization.jsonObject(with:data) as? [String:Any]) ?? [:]
    }
    func record(_ value: [String:Any]) {
        lock.lock(); defer { lock.unlock() }
        let file = root.appendingPathComponent("reports/ui-requests.jsonl")
        try? FileManager.default.createDirectory(at:file.deletingLastPathComponent(),withIntermediateDirectories:true)
        if !FileManager.default.fileExists(atPath:file.path) { FileManager.default.createFile(atPath:file.path,contents:nil) }
        guard let handle = try? FileHandle(forWritingTo:file), let data = try? jsonData(value) else { return }
        defer { try? handle.close() }; _ = try? handle.seekToEnd(); try? handle.write(contentsOf:data+Data([10]))
    }
    func wait(_ seconds: Double,cancellation: CancellationToken) throws {
        let end = uptime()+seconds
        while uptime() < end { try cancellation.check(); Thread.sleep(forTimeInterval:0.01) }
    }
    func begin(_ path: String,cancellation: CancellationToken,fields: [String:Any] = [:]) throws {
        record(["event":"started","path":path].merging(fields) { _,new in new })
        try wait(options()["response_delay"] as? Double ?? 0,cancellation:cancellation)
        while options()["hold"] as? Bool == true { try wait(0.05,cancellation:cancellation) }
        if options()["fail"] as? Bool == true { throw ServiceError(500,"ui_fixture","界面测试：生成失败，请重试。") }
    }
    func memory() -> [String:Any] { [:] }
    func transcribe(audio: URL,language: String?,cancellation: CancellationToken) throws -> [String:Any] {
        try begin("/v1/audio/transcriptions",cancellation:cancellation)
        record(["event":"completed","path":"/v1/audio/transcriptions"])
        return ["text":"这是 CyberAudio 的界面测试结果。","language":"zh"]
    }
    func synthesize(_ speech: Speech,cancellation: CancellationToken,emit: @escaping (Data) throws -> Void) throws {
        let path = "/v1/audio/speech"
        do {
            try begin(path,cancellation:cancellation,fields:["stream":speech.stream,"voice":speech.voice])
            let samples = (0..<24000).map { Int16(3000*sin(Double($0)*2*Double.pi*440/24000)).littleEndian }
            let pcm = samples.withUnsafeBytes { Data($0) }
            for offset in stride(from:0,to:pcm.count,by:4800) {
                if offset >= 9600, let code = options()["late_failure"] as? String {
                    let errors = [
                        "generation_limit": ServiceError(422,"generation_limit","语音未能在长度上限内完整结束，请重试或分段生成。"),
                        "inference_timeout": ServiceError.timeout,
                        "inference_failed": ServiceError.inference
                    ]
                    if let error = errors[code] { throw error }
                }
                try cancellation.check(); try emit(pcm.subdata(in:offset..<min(offset+4800,pcm.count)))
                try wait(options()["chunk_delay"] as? Double ?? 0,cancellation:cancellation)
            }
            record(["event":"completed","path":path])
        } catch {
            record(["event":cancellation.isCancelled ? "disconnected" : "failed","path":path]); throw error
        }
    }
}

@main enum FixtureCommand {
    static func main() throws {
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard arguments.count%2 == 0 else { exit(64) }
        var args: [String:String] = [:]
        for index in stride(from:0,to:arguments.count,by:2) { args[arguments[index]] = arguments[index+1] }
        guard let path = args["--root"],let instance = args["--instance"],let parent = args["--parent-pid"].flatMap(Int32.init),parent == getppid() else { exit(64) }
        let root = URL(fileURLWithPath:path),fixture = Fixture(root:URL(fileURLWithPath:path))
        let token = try String(contentsOf:root.appendingPathComponent("config/api-token"),encoding:.utf8).trimmingCharacters(in:.whitespacesAndNewlines)
        let state = ServiceCoordinator(settings:try ServiceSettings(root:root,configuration:AssetStore.configuration(root:root),token:token,instance:instance),log:{ text in
            if text.hasPrefix("update_control") {
                fixture.record(["event":"update-control","path":"/internal/update/"+(text.contains("action=prepare") ? "prepare" : "cancel"),"busy":text.contains("busy=true")])
            }
        })
        let server = HTTPService(coordinator:state); _ = try server.start()
        DispatchQueue.global().async {
            Thread.sleep(forTimeInterval:fixture.options()["startup_delay"] as? Double ?? 0)
            if fixture.options()["status"] as? String == "failed" { state.initializationFailed() }
            else { state.worker.async { state.install(fixture) } }
        }
        let stop = DispatchSemaphore(value:0)
        signal(SIGTERM,SIG_IGN); signal(SIGINT,SIG_IGN); signal(SIGPIPE,SIG_IGN)
        let term = DispatchSource.makeSignalSource(signal:SIGTERM,queue:.global()); term.setEventHandler { stop.signal() }; term.resume()
        let interrupt = DispatchSource.makeSignalSource(signal:SIGINT,queue:.global()); interrupt.setEventHandler { stop.signal() }; interrupt.resume()
        let timer = DispatchSource.makeTimerSource(queue:.global()); timer.schedule(deadline:.now(),repeating:1)
        timer.setEventHandler { if getppid() != parent { stop.signal() } }; timer.resume()
        DispatchQueue.global().async { var byte: UInt8 = 0; while read(STDIN_FILENO,&byte,1)>0 {}; stop.signal() }
        stop.wait(); try server.stop()
        withExtendedLifetime([term,interrupt,timer]) {}
    }
}
