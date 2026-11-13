import Foundation
import Darwin
import CyberAudioCore

@main enum AssetsCommand {
    static func main() async {
        let task = Task { await run() }
        var signals: [DispatchSourceSignal] = []
        for number in [SIGTERM,SIGINT] {
            signal(number,SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal:number,queue:.global())
            source.setEventHandler { task.cancel() }; source.resume(); signals.append(source)
        }
        let args = CommandLine.arguments
        var parentMonitor: DispatchSourceTimer?
        if let index = args.firstIndex(of:"--parent-pid"),index+1 < args.count,let parent = Int32(args[index+1]),parent > 1 {
            let monitor = DispatchSource.makeTimerSource(queue:.global())
            monitor.schedule(deadline:.now(),repeating:.milliseconds(250))
            monitor.setEventHandler { if getppid() != parent { task.cancel() } }
            monitor.resume(); parentMonitor = monitor
        }
        let status = await task.value
        parentMonitor?.cancel()
        for source in signals { source.cancel() }
        exit(status)
    }

    static func run() async -> Int32 {
        let args = Array(CommandLine.arguments.dropFirst())
        var structured = false
        if let index = args.firstIndex(of:"--json"),index+1 < args.count { structured = args[index+1] == "true" }
        do {
            _ = try AssetStore.lock()
            guard let command = args.first,["prepare","download","verify","status","set-ffmpeg","catalog","models","select-model","delete-model","move-models","commit-model-change","rollback-model-change"].contains(command),args.count%2 == 1 else {
                throw ServiceError(400,"invalid_options","用法：CyberAudioAssets catalog|models|prepare|status|download|verify|set-ffmpeg|select-model|delete-model|move-models|commit-model-change|rollback-model-change [--root 路径] [--model 型号] [--json true]。")
            }
            var options: [String:String] = [:]
            for index in stride(from:1,to:args.count,by:2) {
                guard ["--root","--port","--ffmpeg","--https-url","--proxy","--source","--json","--restart","--parent-pid","--model","--destination","--transaction"].contains(args[index]),options[args[index]] == nil else {
                    throw ServiceError(400,"invalid_options","命令参数无效。")
                }
                options[args[index]] = args[index+1]
            }
            guard ["true","false"].contains(options["--json"] ?? "false"),["true","false"].contains(options["--restart"] ?? "false"),
                  ["true","false"].contains(options["--transaction"] ?? "false") else {
                throw ServiceError(400,"invalid_options","布尔参数应为 true 或 false。")
            }
            guard let source = AssetDownloadSource(rawValue:options["--source"] ?? "huggingface") else {
                throw ServiceError(400,"invalid_source","下载源请选择 huggingface 或 modelscope。")
            }
            let root = options["--root"].map { URL(fileURLWithPath:$0,isDirectory:true) } ?? AssetStore.defaultRoot
            if let value = options["--parent-pid"] {
                guard let parent = Int32(value),parent > 1,parent == getppid() else {
                    throw ServiceError(400,"invalid_parent","准备工具的所属应用已退出。")
                }
            }
            guard let port = Int(options["--port"] ?? "8765"),(1...65535).contains(port) else { throw ServiceError(400,"invalid_port","端口无效。") }
            try Task.checkCancellation()
            switch command {
            case "catalog":
                write(try jsonData(["event":"catalog","models":try ModelCatalog.descriptors().map(\.summary)]))
            case "models": write(try jsonData(ModelStore.status(root:root,modelID:options["--model"])))
            case "select-model", "delete-model":
                guard let model = options["--model"] else { throw ServiceError(400,"invalid_model","请选择模型型号。") }
                if command == "select-model" { try ModelStore.select(root:root,modelID:model,transactional:options["--transaction"] == "true") }
                else { try ModelStore.delete(root:root,modelID:model) }
                emit(["event":"managed"])
            case "move-models":
                guard let destination = options["--destination"],destination.hasPrefix("/") else {
                    throw ServiceError(400,"invalid_directory","请选择模型存储目录。")
                }
                let reporter = ProgressOutput(structured:structured)
                try ModelStore.migrate(root:root,destination:URL(fileURLWithPath:destination,isDirectory:true),transactional:options["--transaction"] == "true") { completed,total,path in
                    reporter.report(AssetDownloadProgress(event:"progress",path:path,completed_bytes:completed,total_bytes:total,phase:"moving"))
                }
                emit(["event":"managed"])
            case "commit-model-change", "rollback-model-change":
                try ModelStore.complete(root:root,commit:command == "commit-model-change")
                emit(["event":"managed"])
            case "prepare":
                let ffmpeg = options["--ffmpeg"] ?? ["/opt/homebrew/bin/ffmpeg","/usr/local/bin/ffmpeg","/opt/local/bin/ffmpeg"].first(where:AssetPreparation.isFFmpeg) ?? "/opt/homebrew/bin/ffmpeg"
                try AssetStore.prepare(root:root,port:port,ffmpeg:ffmpeg,https:options["--https-url"] ?? "")
                if structured { emit(["event":"prepared"]) }
                else { print("Configuration prepared. Existing settings and token preserved; credentials are not printed.") }
            case "status":
                if let model = options["--model"] { write(try jsonData(ModelStore.status(root:root,modelID:model))) }
                else { emit(try AssetStore.preparationStatus(root:root)) }
            case "set-ffmpeg":
                guard let path = options["--ffmpeg"] else { throw ServiceError(400,"invalid_options","请选择 FFmpeg 可执行文件。") }
                try AssetStore.setFFmpeg(root:root,path:path)
                emit(try AssetStore.preparationStatus(root:root))
            default:
                if command == "download" {
                    let reporter = ProgressOutput(structured:structured)
                    try await AssetStore.download(root:root,source:source,proxy:options["--proxy"],restart:options["--restart"] == "true",modelID:options["--model"]) { reporter.report($0) }
                }
                if let model = options["--model"] {
                    if structured { emit(["event":"phase","phase":"verifying","model_id":model]) }
                    try ModelStore.verify(root:root,modelID:model)
                    emit(["event":"verified","model_id":model])
                    break
                }
                let config = try AssetStore.configuration(root:root)
                let report = try AssetStore.verify(config) { model in
                    if structured { emit(["event":"phase","phase":"verifying","model_id":model]) }
                }
                let asrTokenizer = try AssetStore.prepareTokenizer(directory:URL(fileURLWithPath:config.asr_dir),asr:true)
                let ttsTokenizer = try AssetStore.prepareTokenizer(directory:URL(fileURLWithPath:config.tts_dir),asr:false)
                var result: [String:Any] = ["status":"verified","models":report,"tokenizers":["asr":asrTokenizer,"tts":ttsTokenizer]]
                let data = try jsonData(result)
                try FileManager.default.createDirectory(at:root.appendingPathComponent("reports"),withIntermediateDirectories:true)
                try data.write(to:root.appendingPathComponent("reports/native-assets-verified.json"),options:.atomic)
                if structured {
                    result["event"] = "verified"
                    write(try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]))
                } else { print(String(decoding:data,as:UTF8.self)) }
            }
            return 0
        } catch {
            let cancelled = error is CancellationError || Task.isCancelled
            let code = cancelled ? "cancelled" : (error as? ServiceError)?.code ?? "setup_failed"
            let message = cancelled ? "已取消，已完成的下载进度会保留。" : (error as? ServiceError)?.message ?? "无法准备或验证资源，请检查目录权限和模型文件。"
            if structured { emit(["event":"error","code":code,"message":message]) }
            else { fputs("CyberAudioAssets: \(message)\n",stderr) }
            return cancelled ? 130 : 1
        }
    }
    static func emit<T: Encodable>(_ value: T) {
        if let data = try? JSONEncoder().encode(value) { write(data) }
    }
    static func write(_ data: Data) {
        try? FileHandle.standardOutput.write(contentsOf:data+Data([10]))
    }
}

private final class ProgressOutput: @unchecked Sendable {
    private let lock = NSLock()
    private let structured: Bool
    private var path = "",lastTime = 0.0
    init(structured: Bool) { self.structured = structured }
    func report(_ value: AssetDownloadProgress) {
        lock.lock(); defer { lock.unlock() }
        let changedFile = path != value.path
        let now = uptime()
        guard changedFile || now-lastTime >= 0.1 || value.completed_bytes == value.total_bytes else { return }
        path = value.path; lastTime = now
        if structured { AssetsCommand.emit(value) }
        else if changedFile { print("Preparing \(value.path); verified files and interrupted downloads are reused.") }
    }
}
