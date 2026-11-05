// Isolated acceptance driver. Compile with swiftc -parse-as-library; no Python needed.
import Foundation
import Darwin
import CryptoKit

private struct Failure: Error { let message: String }
private func require(_ condition: @autoclosure () throws -> Bool,_ message: String) throws {
    if try !condition() { throw Failure(message:message) }
}
private func now() -> Double { Double(DispatchTime.now().uptimeNanoseconds)/1e9 }
private func json(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject:value,options:[.sortedKeys,.prettyPrinted]) }
private func object(_ data: Data) throws -> [String:Any] {
    guard let value = try JSONSerialization.jsonObject(with:data) as? [String:Any] else { throw Failure(message:"Expected JSON object") }; return value
}
private func command(_ path: String,_ arguments: [String]) throws -> String {
    let process = Process(),output = Pipe()
    process.executableURL = URL(fileURLWithPath:path); process.arguments = arguments
    process.standardOutput = output; process.standardError = FileHandle.nullDevice
    try process.run(); let data = output.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
    try require(process.terminationStatus == 0,"Command failed: \(URL(fileURLWithPath:path).lastPathComponent)")
    return String(decoding:data,as:UTF8.self)
}
private final class ResponseBox: @unchecked Sendable {
    var data = Data(),response: HTTPURLResponse?,error: Error?
}
private func http(_ base: URL,_ path: String,token: String? = nil,body: Data? = nil,type: String = "application/json",headers: [String:String] = [:]) throws -> (Data,HTTPURLResponse) {
    var request = URLRequest(url:base.appendingPathComponent(path)); request.httpMethod = body == nil ? "GET" : "POST"; request.httpBody = body
    request.timeoutInterval = 150; request.setValue(type,forHTTPHeaderField:"Content-Type")
    if let token { request.setValue("Bearer "+token,forHTTPHeaderField:"Authorization") }
    for (key,value) in headers { request.setValue(value,forHTTPHeaderField:key) }
    let result = ResponseBox(),done = DispatchSemaphore(value:0)
    let task = URLSession.shared.dataTask(with:request) { data,response,error in
        result.data = data ?? Data(); result.response = response as? HTTPURLResponse; result.error = error; done.signal()
    }
    task.resume()
    if done.wait(timeout:.now()+155) == .timedOut { task.cancel(); throw Failure(message:"HTTP driver timeout") }
    if let error = result.error { throw error }
    guard let response = result.response else { throw Failure(message:"No HTTP response") }; return (result.data,response)
}
private final class Stream: NSObject,URLSessionDataDelegate,@unchecked Sendable {
    let done = DispatchSemaphore(value:0),start = now()
    var response: HTTPURLResponse?,error: Error?,hookError: Error?,firstMS: Double?,data = Data(),packets = 0
    let onFirst: ((HTTPURLResponse,URLSessionDataTask) throws -> Void)?
    init(onFirst: ((HTTPURLResponse,URLSessionDataTask) throws -> Void)?) { self.onFirst = onFirst }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didReceive response: URLResponse,completionHandler: @escaping (URLSession.ResponseDisposition)->Void) {
        self.response = response as? HTTPURLResponse; completionHandler(.allow)
    }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didReceive data: Data) {
        packets += 1; self.data.append(data)
        if firstMS == nil {
            firstMS = (now()-start)*1000
            do { if let response { try onFirst?(response,dataTask) } } catch { self.hookError = error; dataTask.cancel() }
        }
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,didCompleteWithError error: Error?) { self.error = self.error ?? error; done.signal() }
}
private func stream(_ base: URL,token: String,text: String,allowDisconnect: Bool = false,onFirst: ((HTTPURLResponse,URLSessionDataTask) throws -> Void)? = nil) throws -> Stream {
    let value = Stream(onFirst:onFirst),queue = OperationQueue(); queue.maxConcurrentOperationCount = 1
    let session = URLSession(configuration:.ephemeral,delegate:value,delegateQueue:queue)
    defer { session.invalidateAndCancel() }
    var request = URLRequest(url:base.appendingPathComponent("v1/audio/speech")); request.httpMethod = "POST"
    request.setValue("Bearer "+token,forHTTPHeaderField:"Authorization"); request.setValue("application/json",forHTTPHeaderField:"Content-Type")
    request.httpBody = try json(["input":text,"language":"chinese","response_format":"pcm","stream":true]); request.timeoutInterval = 150
    let task = session.dataTask(with:request); task.resume()
    try require(value.done.wait(timeout:.now()+155) == .success,"Stream driver timeout")
    try require(value.response?.statusCode == 200,"Stream HTTP failure")
    if let error = value.hookError { throw error }
    if let error = value.error, !allowDisconnect && (onFirst == nil || (error as NSError).code != NSURLErrorCancelled) { throw error }
    try require(!value.data.isEmpty && value.data.count%2 == 0,"No valid PCM received")
    return value
}

@main enum NativeValidation {
    static func main() {
        if CommandLine.arguments.contains("--network-probe") {
            let fd = socket(AF_INET,SOCK_STREAM,0); defer { close(fd) }
            var address = sockaddr_in(); address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size); address.sin_family = sa_family_t(AF_INET)
            address.sin_port = UInt16(443).bigEndian; address.sin_addr.s_addr = inet_addr("1.1.1.1")
            let result = withUnsafePointer(to:&address) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { connect(fd,$0,socklen_t(MemoryLayout<sockaddr_in>.size)) } }
            if result == -1 && errno == EPERM { print("outbound_denied"); exit(0) }; exit(1)
        }
        do { try validate() }
        catch { fputs("Native validation failed: \(error)\n",stderr); exit(1) }
    }
    private static func validate() throws {
        signal(SIGPIPE,SIG_IGN); umask(0o077)
        let arguments = Array(CommandLine.arguments.dropFirst()); var options: [String:String] = [:]
        try require(arguments.count%2 == 0,"Use --app path --root NEW_TEST_ROOT --models path --corpus path --port 18765")
        for index in stride(from:0,to:arguments.count,by:2) { options[arguments[index]] = arguments[index+1] }
        guard let appPath = options["--app"],let rootPath = options["--root"],let modelsPath = options["--models"],let corpusPath = options["--corpus"],
              let port = options["--port"].flatMap(Int.init),(1024...65535).contains(port),port != 8765 else { throw Failure(message:"Explicit isolated paths and non-production port are required") }
        let app = URL(fileURLWithPath:appPath),root = URL(fileURLWithPath:rootPath),models = URL(fileURLWithPath:modelsPath),corpus = URL(fileURLWithPath:corpusPath)
        try require(root.resolvingSymlinksInPath().path != FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/CyberAudio").path,"Production root is forbidden")
        let marker = root.appendingPathComponent(".native-validation")
        if FileManager.default.fileExists(atPath:root.path) { try require(FileManager.default.fileExists(atPath:marker.path),"Existing root lacks validation marker") }
        else { try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true); try Data().write(to:marker) }
        let helper = app.appendingPathComponent("Contents/Helpers/CyberAudioService"),assets = app.appendingPathComponent("Contents/Helpers/CyberAudioAssets")
        let policy = app.appendingPathComponent("Contents/Resources/offline.sb"),metal = app.appendingPathComponent("Contents/Resources/mlx.metallib")
        let base = URL(string:"http://127.0.0.1:\(port)")!
        let production = URL(string:"http://127.0.0.1:8765")!
        let beforeProduction = (try? http(production,"health")).flatMap { try? object($0.0)["instance"] as? String }
        try require((try? http(base,"health")) == nil,"Validation port is occupied")
        _ = try command(assets.path,["prepare","--root",root.path,"--port",String(port)])
        let config: [String:Any] = ["asr_dir":models.appendingPathComponent("asr").path,"tts_dir":models.appendingPathComponent("tts").path,
            "ffmpeg":"/opt/homebrew/bin/ffmpeg","port":port,"https_url":""]
        try json(config).write(to:root.appendingPathComponent("config/service.json"),options:.atomic)
        let token = try String(contentsOf:root.appendingPathComponent("config/api-token"),encoding:.utf8).trimmingCharacters(in:.whitespacesAndNewlines)
        var report: [String:Any] = ["started":ISO8601DateFormatter().string(from:Date()),"port":port,"phase":options["--phase"] ?? "all","production_root_modified":false,
            "production_instance_checked":beforeProduction != nil,"os":ProcessInfo.processInfo.operatingSystemVersionString,
            "physical_memory_bytes":ProcessInfo.processInfo.physicalMemory,
            "helper_sha256":SHA256.hash(data:try Data(contentsOf:helper)).map { String(format:"%02x",$0) }.joined()]
        let reportFile = root.appendingPathComponent("reports/native-service.json")
        func save(_ name: String,_ value: Any) throws {
            report[name] = value; try json(report).write(to:reportFile,options:.atomic)
            print("PASS \(name)"); fflush(stdout)
        }
        _ = try command(assets.path,["verify","--root",root.path]); try save("assets",object(Data(contentsOf:root.appendingPathComponent("reports/native-assets-verified.json"))))
        let probe = try command("/usr/bin/sandbox-exec",["-f",policy.path,CommandLine.arguments[0],"--network-probe"])
        try require(probe.contains("outbound_denied"),"Outbound sandbox probe failed"); try save("outbound_denied",true)
        func ready() -> [String:Any]? { (try? http(base,"ready")).flatMap { try? object($0.0) } }
        func waitReady(_ process: Process) throws -> [String:Any] {
            let end = now()+180
            while now() < end && process.isRunning {
                if let value = ready() {
                    if value["status"] as? String == "ready" { return value }
                    try require(value["status"] as? String != "failed","Model initialization failed; inspect isolated service.log")
                }
                Thread.sleep(forTimeInterval:0.2)
            }
            throw Failure(message:"Owned process did not become ready; inspect isolated service.log")
        }
        func drain() throws {
            let end = now()+15
            while now() < end { if ready()?["busy"] as? Bool == false { return }; Thread.sleep(forTimeInterval:0.03) }
            throw Failure(message:"Inference producer did not drain")
        }
        func waitStopped(_ process: Process) throws -> Double {
            let start = now()
            while process.isRunning && now()-start < 12 { Thread.sleep(forTimeInterval:0.03) }
            if process.isRunning { kill(process.processIdentifier,SIGKILL); throw Failure(message:"Owned process exceeded exit deadline") }
            process.waitUntilExit(); try require((try? http(base,"health")) == nil,"Listener survived owned process")
            return (now()-start)*1000
        }
        func launchHelper() throws -> (Process,Pipe) {
            let process = Process(),input = Pipe()
            process.executableURL = URL(fileURLWithPath:"/usr/bin/sandbox-exec")
            process.arguments = ["-f",policy.path,helper.path,"--root",root.path,"--parent-pid",String(getpid()),"--instance",UUID().uuidString,"--metallib",metal.path]
            process.standardInput = input; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            try process.run(); try input.fileHandleForReading.close(); return (process,input)
        }
        if options["--phase"] != "lifecycle" {
        let (service,input) = try launchHelper()
        defer { try? input.fileHandleForWriting.close(); if service.isRunning { service.terminate(); _ = try? waitStopped(service) } }
        let startup = try waitReady(service); try save("startup",startup)
        let (duplicate,duplicateInput) = try launchHelper()
        try duplicateInput.fileHandleForWriting.close(); _ = try waitStoppedDuplicate(duplicate)
        try require(ready()?["instance"] as? String == startup["instance"] as? String,"Duplicate helper displaced owner"); try save("single_owner_lock",true)
        let (_,unauthorized) = try http(base,"v1/models"); try require(unauthorized.statusCode == 401,"Missing authentication accepted")
        let (identity,_) = try http(base,"v1/models",token:token); try save("models",object(identity))
        let corpusRows = try JSONSerialization.jsonObject(with:Data(contentsOf:corpus.appendingPathComponent("corpus.json"))) as! [[String:Any]]
        var stt: [[String:Any]] = []
        for language in ["zh","en"] {
            let row = corpusRows.first { $0["language"] as? String == language }!
            let original = corpus.appendingPathComponent(row["audio"] as! String)
            for format in ["wav","webm","mp3","m4a","flac","ogg","aiff","aac"] {
                let audio: URL
                if format == "wav" { audio = original }
                else {
                    audio = root.appendingPathComponent("tmp/acceptance-\(language).\(format)")
                    _ = try command("/opt/homebrew/bin/ffmpeg",["-nostdin","-hide_banner","-loglevel","error","-i",original.path,"-y",audio.path])
                }
                let boundary = "native-validation-"+UUID().uuidString
                var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.\(format)\"\r\n\r\n".utf8)
                body.append(try Data(contentsOf:audio)); body.append(Data("\r\n--\(boundary)--\r\n".utf8))
                let (data,response) = try http(base,"v1/audio/transcriptions",token:token,body:body,type:"multipart/form-data; boundary=\(boundary)")
                try require(response.statusCode == 200,"STT \(language)/\(format) failed")
                let result = try object(data); try require(!(result["text"] as? String ?? "").isEmpty,"STT returned empty text")
                stt.append(["language":language,"format":format,"result":result])
            }
        }
        try save("stt_formats",stt)
        if let count = options["--asr-count"].flatMap(Int.init),count > 0 {
            try require(corpusRows.count >= count,"Requested corpus count exceeds available recordings")
            var rows: [[String:Any]] = []
            for row in corpusRows.prefix(count) {
                let boundary = "native-corpus-"+UUID().uuidString
                var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n\r\n".utf8)
                let audio = try Data(contentsOf:corpus.appendingPathComponent(row["audio"] as! String))
                let digest = SHA256.hash(data:audio).map { String(format:"%02x",$0) }.joined()
                try require(digest == row["audio_sha256"] as? String,"Corpus audio hash differs from its manifest")
                body.append(audio)
                body.append(Data("\r\n--\(boundary)--\r\n".utf8))
                let began = now(),(data,response) = try http(base,"v1/audio/transcriptions",token:token,body:body,type:"multipart/form-data; boundary=\(boundary)")
                try require(response.statusCode == 200,"Corpus ASR failed for \(row["id"]!)")
                rows.append(["id":row["id"]!,"reference":row["reference"]!,"language":row["language"]!,"audio_sha256":digest,"result":try object(data),"http_ms":(now()-began)*1000])
                if rows.count%20 == 0 { print("ASR corpus \(rows.count)/\(count)"); fflush(stdout) }
            }
            try save("asr_corpus",rows)
        }
        let voices = ["serena","vivian","uncle_fu","ryan","aiden","ono_anna","sohee","eric","dylan"]
        var tts: [[String:Any]] = []
        for voice in voices {
            let text = ["ryan","aiden"].contains(voice) ? "CyberAudio is running entirely in Swift." : "这是原生语音服务测试。"
            let (data,response) = try http(base,"v1/audio/speech",token:token,body:json(["input":text,"voice":voice,"instructions":"语气温和自然。","response_format":"wav"]))
            try require(response.statusCode == 200 && data.prefix(4) == Data("RIFF".utf8) && data.count > 44,"TTS failed for \(voice)")
            try require(Array(data[24..<28]) == [0xc0,0x5d,0,0] && (data.count-44)%2 == 0,"TTS WAV is not 24kHz PCM16")
            try data.write(to:root.appendingPathComponent("reports/\(voice).wav"))
            let duration = Double(data.count-44)/48000,ms = Double(response.value(forHTTPHeaderField:"x-processing-ms") ?? "") ?? -1
            tts.append(["voice":voice,"duration":duration,"processing_ms":ms,"rtf":ms/1000/duration,
                "first_audio_ms":Double(response.value(forHTTPHeaderField:"x-first-audio-ms") ?? "") ?? -1])
        }
        try save("tts_voices",tts)
        let complete = try stream(base,token:token,text:"这是连续播放和完整输出的验证。")
        let completedID = complete.response!.value(forHTTPHeaderField:"x-request-id")!
        let (completed,_) = try http(base,"v1/requests/"+completedID,token:token)
        try require(try object(completed)["status"] as? String == "completed","PCM stream did not complete")
        try save("stream_complete",["packets":complete.packets,"bytes":complete.data.count,"first_audio_ms":complete.firstMS!])
        var cancellations: [[String:Any]] = []
        for index in 0..<3 {
            let lease = UUID().uuidString,activeText = String(repeating:"我们正在检查取消生成后，后续请求能否继续完成。",count:5)
            let cancelled = try stream(base,token:token,text:activeText) { response,task in
                try require(ready()?["busy"] as? Bool == true,"Producer completed before cancellation test")
                if index == 0 {
                    let (_,prepared) = try http(base,"internal/update/prepare",token:token,body:Data(),headers:["x-cyberaudio-instance":startup["instance"] as! String,"x-cyberaudio-update":lease])
                    try require(prepared.statusCode == 200,"Update prepare failed")
                    let (_,paused) = try http(base,"v1/audio/speech",token:token,body:json(["input":"新请求"]))
                    try require(paused.statusCode == 503,"Update did not pause admission")
                }
                task.cancel()
            }
            let began = now(); try drain(); let drainedMS = (now()-began)*1000
            let id = cancelled.response!.value(forHTTPHeaderField:"x-request-id")!,record = try object(http(base,"v1/requests/"+id,token:token).0)
            try require(record["status"] as? String == "cancelled","Disconnect did not produce a cancelled record")
            if index == 0 {
                try require(ready()?["updating"] as? Bool == true,"Update admission reopened before lease cancellation")
                _ = try http(base,"internal/update/cancel",token:token,body:Data(),headers:["x-cyberaudio-instance":startup["instance"] as! String,"x-cyberaudio-update":lease])
            }
            let (_,recovered) = try http(base,"v1/audio/speech",token:token,body:json(["input":"取消后恢复正常。","voice":"serena"]))
            try require(recovered.statusCode == 200,"Decoder did not recover after cancellation")
            cancellations.append(["round":index+1,"drain_ms":drainedMS,"status":record["status"]!,"recovery_http_status":recovered.statusCode])
        }
        try save("stream_disconnect_recovery",cancellations)
        try input.fileHandleForWriting.close(); try save("stdin_eof_stop_ms",waitStopped(service))
        }
        var appExits: [[String:Any]] = []
        for sig in [SIGTERM,SIGKILL] {
            let process = Process(); process.executableURL = app.appendingPathComponent("Contents/MacOS/CyberAudio")
            process.arguments = ["--data-dir",root.path]; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            try process.run()
            defer { if process.isRunning { process.terminate(); _ = try? waitStopped(process) } }
            let started = try waitReady(process)
            let children = try command("/usr/bin/pgrep",["-P",String(process.processIdentifier)]).split(whereSeparator:{$0.isWhitespace}).compactMap { Int32($0) }
            try require(children.count == 1,"App must directly own one native helper")
            let childCommand = try command("/bin/ps",["-p",String(children[0]),"-o","comm="]).trimmingCharacters(in:.whitespacesAndNewlines)
            try require(childCommand.hasSuffix("CyberAudioService"),"App child is not the native helper")
            var exitStarted = now()
            _ = try stream(base,token:token,text:String(repeating:"退出应用时应一起停止原生推理服务。",count:6),allowDisconnect:true) { _,_ in
                try require(ready()?["busy"] as? Bool == true,"App exit did not overlap inference")
                exitStarted = now(); kill(process.processIdentifier,sig)
            }
            _ = try waitStopped(process)
            let elapsed = (now()-exitStarted)*1000,deadline = now()+10
            while children.contains(where:{ kill($0,0) == 0 }) && now() < deadline { Thread.sleep(forTimeInterval:0.05) }
            try require(children.allSatisfy { kill($0,0) != 0 },"Model helper survived App exit")
            Thread.sleep(forTimeInterval:1); try require(ready() == nil,"Helper resurrected after App exit")
            appExits.append(["signal":sig,"exit_ms":elapsed,"instance":started["instance"]!,"active_inference":true,"helper_gone":true,"child_executable":"CyberAudioService"])
        }
        try save("app_active_exit_and_restart",appExits)
        if let beforeProduction {
            let after = try object(http(production,"health").0)["instance"] as? String
            try require(after == beforeProduction,"Production service identity changed during validation")
        }
        try save("production_service_preserved",true)
        report["status"] = "passed"; try save("finished",ISO8601DateFormatter().string(from:Date()))
    }
    private static func waitStoppedDuplicate(_ process: Process) throws -> Bool {
        let end = now()+5
        while process.isRunning && now() < end { Thread.sleep(forTimeInterval:0.02) }
        if process.isRunning { process.terminate(); throw Failure(message:"Duplicate helper did not reject ownership") }
        process.waitUntilExit(); try require(process.terminationStatus != 0,"Duplicate helper unexpectedly succeeded"); return true
    }
}
