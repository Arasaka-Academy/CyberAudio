import Foundation
import CyberAudioCore

@main enum AssetsCommand {
    static func main() async {
        do {
            _ = try AssetStore.lock()
            let args = Array(CommandLine.arguments.dropFirst())
            guard let command = args.first, ["prepare","download","verify"].contains(command), args.count%2 == 1 else {
                fputs("Usage: CyberAudioAssets prepare|download|verify [--root path] [--port 8765] [--ffmpeg path] [--https-url url] [--proxy http://host:port]\n",stderr); exit(64)
            }
            var options: [String:String] = [:]
            for index in stride(from:1,to:args.count,by:2) {
                guard ["--root","--port","--ffmpeg","--https-url","--proxy"].contains(args[index]),options[args[index]] == nil else { throw ServiceError(400,"invalid_options","命令参数无效。") }
                options[args[index]] = args[index+1]
            }
            let root = options["--root"].map { URL(fileURLWithPath:$0,isDirectory:true) } ?? AssetStore.defaultRoot
            guard let port = Int(options["--port"] ?? "8765"),(1...65535).contains(port) else { throw ServiceError(400,"invalid_port","端口无效。") }
            if command == "prepare" {
                try AssetStore.prepare(root:root,port:port,ffmpeg:options["--ffmpeg"] ?? "/opt/homebrew/bin/ffmpeg",https:options["--https-url"] ?? "")
                print("Configuration prepared. Existing settings and token preserved; credentials are not printed.")
            } else {
                if command == "download" { try await AssetStore.download(root:root,proxy:options["--proxy"]) }
                let config = try AssetStore.configuration(root:root)
                let report = try AssetStore.verify(config)
                let asrTokenizer = try AssetStore.prepareTokenizer(directory:URL(fileURLWithPath:config.asr_dir),asr:true)
                let ttsTokenizer = try AssetStore.prepareTokenizer(directory:URL(fileURLWithPath:config.tts_dir),asr:false)
                let result: [String:Any] = ["status":"verified","models":report,"tokenizers":["asr":asrTokenizer,"tts":ttsTokenizer]]
                let data = try jsonData(result)
                try FileManager.default.createDirectory(at:root.appendingPathComponent("reports"),withIntermediateDirectories:true)
                try data.write(to:root.appendingPathComponent("reports/native-assets-verified.json"),options:.atomic)
                print(String(decoding:data,as:UTF8.self))
            }
        } catch {
            fputs("CyberAudioAssets: \((error as? ServiceError)?.message ?? "无法准备或验证资源，请检查目录权限和模型文件。")\n",stderr); exit(1)
        }
    }
}
