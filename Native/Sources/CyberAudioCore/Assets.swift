import Foundation
import CryptoKit
import Security
import CoreFoundation
import Darwin

public struct LockedAsset: Codable,Sendable {
    public let path: String
    public let size: Int
    public let sha256: String
}
public struct ModelLock: Decodable { public let files: [LockedAsset] }

public enum AssetStore {
    public static var defaultRoot: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/CyberAudio") }
    public static func digest(_ file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom:file); defer { try? handle.close() }
        var hash = SHA256()
        while let data = try handle.read(upToCount:1024*1024), !data.isEmpty {
            try Task.checkCancellation()
            hash.update(data:data)
        }
        return hash.finalize().map { String(format:"%02x",$0) }.joined()
    }
    public static func lock() throws -> ModelLock {
        try JSONDecoder().decode(ModelLock.self,from:Data(contentsOf:Bundle.module.url(forResource:"Models.lock",withExtension:"json")!))
    }
    public static func configuration(root: URL) throws -> ServiceConfiguration {
        try JSONDecoder().decode(ServiceConfiguration.self,from:Data(contentsOf:root.appendingPathComponent("config/service.json")))
    }
    public static func verify(_ config: ServiceConfiguration, progress: (String) -> Void = { _ in }) throws -> [[String:Any]] {
        var result: [[String:Any]] = []
        for (component,path,id) in [("asr",config.asr_dir,config.asrModelID),("tts",config.tts_dir,config.ttsModelID)] {
            let model = try ModelCatalog.descriptor(id)
            progress(id)
            guard model.component == component else { throw ServiceError(400,"invalid_model","模型用途与配置不匹配。") }
            let root = URL(fileURLWithPath:path).resolvingSymlinksInPath()
            let files = model.files
            for file in files {
                let relative = String(file.path.dropFirst(component.count+1))
                let target = root.appendingPathComponent(relative).resolvingSymlinksInPath()
                guard target.path.hasPrefix(root.path+"/"),
                      (try? target.resourceValues(forKeys:[.isRegularFileKey]).isRegularFile) == true,
                      (try FileManager.default.attributesOfItem(atPath:target.path)[.size] as? NSNumber)?.intValue == file.size,
                      try digest(target) == file.sha256 else {
                    throw ServiceError(500,"invalid_assets","模型文件校验失败，请运行 CyberAudioAssets verify。")
                }
            }
            result.append(["component":component,"model":id,"files_verified":files.count,"revision":model.revision])
        }
        return result
    }
    /// Deterministic native conversion of the pinned SDK's Qwen tokenizer schema.
    /// See ThirdParty/MLX-Audio-Swift-LICENSE for the upstream MIT notice.
    public static func prepareTokenizer(directory: URL, asr: Bool) throws -> String {
        let vocabulary = try JSONSerialization.jsonObject(with:Data(contentsOf:directory.appendingPathComponent("vocab.json")))
        let merges = try String(contentsOf:directory.appendingPathComponent("merges.txt"),encoding:.utf8)
            .components(separatedBy:"\n").filter { !$0.isEmpty && !$0.hasPrefix("#") }
        let config = try JSONSerialization.jsonObject(with:Data(contentsOf:directory.appendingPathComponent("tokenizer_config.json"))) as! [String:Any]
        let decoder = config["added_tokens_decoder"] as? [String:[String:Any]] ?? [:]
        let tokens: [[String:Any]] = decoder.keys.compactMap(Int.init).sorted().map { id in
            let token = decoder[String(id)]!
            return ["id":id,"content":token["content"] as? String ?? "", "single_word":token["single_word"] as? Bool ?? false,
                "lstrip":token["lstrip"] as? Bool ?? false,"rstrip":token["rstrip"] as? Bool ?? false,
                "normalized":token["normalized"] as? Bool ?? false,"special":token["special"] as? Bool ?? !asr]
        }
        var model: [String:Any] = ["type":"BPE","dropout":NSNull(),"unk_token":NSNull(),"continuing_subword_prefix":"",
            "end_of_word_suffix":"","fuse_unk":false,"byte_fallback":false,"vocab":vocabulary,"merges":merges]
        if !asr { model["ignore_merges"] = false }
        let tokenizer: [String:Any] = ["version":"1.0","truncation":NSNull(),"padding":NSNull(),"added_tokens":tokens,
            "normalizer":asr ? ["type":"NFC"] : NSNull(),
            "pre_tokenizer":["type":"Sequence","pretokenizers":[
                ["type":"Split","pattern":["Regex":"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\\r\\n\\p{L}\\p{N}]?\\p{L}+|\\p{N}{1,3}| ?[^\\s\\p{L}\\p{N}]+[\\r\\n]*|\\s*[\\r\\n]+|\\s+(?!\\S)|\\s+"],"behavior":"Isolated","invert":false] as [String:Any],
                ["type":"ByteLevel","add_prefix_space":false,"trim_offsets":true,"use_regex":false] as [String:Any]
            ]] as [String:Any],"post_processor":NSNull(),
            "decoder":["type":"ByteLevel","add_prefix_space":true,"trim_offsets":true,"use_regex":true],"model":model]
        let file = directory.appendingPathComponent("tokenizer.json")
        if FileManager.default.fileExists(atPath:file.path) {
            let existing = try JSONSerialization.jsonObject(with:Data(contentsOf:file))
            guard equalJSON(existing,tokenizer) else { throw ServiceError(500,"invalid_tokenizer","Tokenizer 与固定模型不一致，请检查模型目录。") }
        } else { try jsonData(tokenizer).write(to:file,options:.atomic) }
        return try digest(file)
    }
    // Foundation's sortedKeys is locale/collation dependent for long Qwen
    // punctuation tokens. Compare values without changing existing file bytes.
    // Preserve literal UTF-8 strings and distinguish booleans from numeric IDs.
    private static func equalJSON(_ left: Any,_ right: Any) -> Bool {
        if let left = left as? NSDictionary {
            guard let right = right as? NSDictionary,left.count == right.count else { return false }
            return left.allKeys.allSatisfy { key in
                guard let a = left.object(forKey:key),let b = right.object(forKey:key) else { return false }
                return equalJSON(a,b)
            }
        }
        if let left = left as? [Any] {
            guard let right = right as? [Any],left.count == right.count else { return false }
            return zip(left,right).allSatisfy { equalJSON($0.0,$0.1) }
        }
        if let left = left as? NSNumber {
            guard let right = right as? NSNumber,CFGetTypeID(left) == CFGetTypeID(right) else { return false }
            return left == right
        }
        if let left = left as? String,let right = right as? String { return left.utf8.elementsEqual(right.utf8) }
        return left is NSNull && right is NSNull
    }
    public static func prepare(root: URL, port: Int = 8765, ffmpeg: String = "/opt/homebrew/bin/ffmpeg", https: String = "") throws {
        try ModelStore.recover(root:root)
        for name in ["", "config", "models", "reports", "logs", "tmp"] {
            let directory = name.isEmpty ? root : root.appendingPathComponent(name)
            try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
            try FileManager.default.setAttributes([.posixPermissions:0o700],ofItemAtPath:directory.path)
        }
        let file = root.appendingPathComponent("config/service.json")
        if !FileManager.default.fileExists(atPath:file.path) {
            let configuration = ServiceConfiguration(asr_dir:root.appendingPathComponent("models/Qwen3-ASR-1.7B-8bit").path,
                tts_dir:root.appendingPathComponent("models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit").path,ffmpeg:ffmpeg,port:port,https_url:https)
            try JSONEncoder().encode(configuration).write(to:file,options:.atomic)
        }
        let token = root.appendingPathComponent("config/api-token")
        if !FileManager.default.fileExists(atPath:token.path) {
            var bytes = [UInt8](repeating:0,count:48)
            guard SecRandomCopyBytes(kSecRandomDefault,bytes.count,&bytes) == errSecSuccess else { throw ServiceError.inference }
            try Data((Data(bytes).base64EncodedString()+"\n").utf8).write(to:token,options:.atomic)
        }
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:token.path)
        // The app also carries this policy and does not trust an older allow-all file.
        let sandbox = root.appendingPathComponent("config/offline.sb")
        if !FileManager.default.fileExists(atPath:sandbox.path) {
            try Data("(version 1)\n(allow default)\n(deny network-outbound)\n".utf8).write(to:sandbox,options:.atomic)
        }
    }
    public static func download(root: URL,source: AssetDownloadSource = .huggingFace,proxy: String? = nil,restart: Bool = false,modelID: String? = nil,
                                progress: @escaping @Sendable (AssetDownloadProgress)->Void = { _ in }) async throws {
        let original = try configuration(root:root)
        let models = try (modelID.map { [$0] } ?? [original.asrModelID,original.ttsModelID]).map { try ModelCatalog.descriptor($0) }
        guard models.allSatisfy({ $0.sources[source.rawValue] != nil }) else {
            throw ServiceError(400,"source_unavailable","该型号尚未核验所选下载源，请选择其他可用下载源。")
        }
        let config = modelID == nil ? original : original.validating(models[0],root:root)
        for model in models { try ModelStore.requireStorage(at:original.directory(for:model,root:root)) }
        try ModelStore.requireStorage(at:config.stagingDirectory(root:root))
        let network = URLSessionConfiguration.ephemeral
        network.timeoutIntervalForRequest = 60
        network.timeoutIntervalForResource = 1800
        if let proxy {
            guard let url = URLComponents(string:proxy),["http","socks5"].contains(url.scheme ?? ""),
                  let host = url.url?.host,!host.isEmpty,let port = url.port,(1...65535).contains(port),
                  url.user == nil,url.password == nil,url.query == nil,url.fragment == nil,
                  url.path.isEmpty || url.path == "/" else {
                throw ServiceError(400,"invalid_proxy","代理格式为 http://主机:端口 或 socks5://主机:端口，不接受凭据。")
            }
            // URL.host removes IPv6 URI brackets; CFNetwork expects the address itself.
            if url.scheme == "socks5" { network.connectionProxyDictionary = ["SOCKSEnable":1,"SOCKSProxy":host,"SOCKSPort":port] }
            else { network.connectionProxyDictionary = ["HTTPEnable":1,"HTTPProxy":host,"HTTPPort":port,"HTTPSEnable":1,"HTTPSProxy":host,"HTTPSPort":port] }
        }
        let session = URLSession(configuration:network)
        defer { session.invalidateAndCancel() }
        let temporaryRoot = root.appendingPathComponent("tmp")
        try FileManager.default.createDirectory(at:temporaryRoot,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        let descriptor = open(temporaryRoot.appendingPathComponent("asset-download.lock").path,O_CREAT|O_RDWR|O_NOFOLLOW,0o600)
        guard descriptor >= 0 else { throw AssetDownloader.unsafePath }
        defer { close(descriptor) }
        guard flock(descriptor,LOCK_EX|LOCK_NB) == 0 else {
            throw ServiceError(409,"setup_busy","已有模型准备任务正在进行，请等待它结束。")
        }
        guard !FileManager.default.fileExists(atPath:root.appendingPathComponent("config/model-change.json").path) else {
            throw ServiceError(409,"model_change_pending","模型配置正在切换，请完成或恢复操作后再下载。")
        }
        let files = models.flatMap(\.files)
        var reusable: [String:[URL]] = [:]
        for known in try ModelCatalog.descriptors() {
            let local = original.validating(known,root:root)
            for asset in known.files {
                let path = try AssetPreparation.destination(asset,configuration:local)
                reusable[asset.sha256,default:[]].append(path)
            }
        }
        let status = try AssetPreparation.inspect(configuration:config,assets:files,staging:config.stagingDirectory(root:root),
            availableBytes:ModelStore.availableBytes(configuration:config,root:root),checkFFmpeg:false)
        guard status.invalid_files.isEmpty else {
            throw ServiceError(500,"invalid_assets","已有模型文件摘要不匹配；保留文件，请检查后重试。")
        }
        guard status.can_download else {
            throw ServiceError(507,"insufficient_storage","剩余磁盘空间不足，请释放空间后继续下载。")
        }
        let total = files.reduce(Int64(0)) { $0+Int64($1.size) }
        var completed: Int64 = 0
        for file in files {
            try Task.checkCancellation()
            let target = try AssetPreparation.destination(file,configuration:config)
            let model = models.first { file.path.hasPrefix($0.component+"/") }!
            let base = completed
            // Qwen variants share large codec assets. Reuse verified local
            // bytes without moving or modifying the other installed model.
            if try AssetDownloader.regularSize(target) == nil {
                for candidate in reusable[file.sha256] ?? [] where candidate != target {
                    try Task.checkCancellation()
                    guard (try? AssetDownloader.regularSize(candidate)) == Int64(file.size),
                          (try? digest(candidate)) == file.sha256 else { continue }
                    let staging = config.stagingDirectory(root:root)
                    try AssetDownloader.directory(staging)
                    let copy = staging.appendingPathComponent(".reuse-"+UUID().uuidString)
                    defer { try? FileManager.default.removeItem(at:copy) }
                    try FileManager.default.copyItem(at:candidate,to:copy)
                    try Task.checkCancellation()
                    guard try digest(copy) == file.sha256 else { throw ServiceError(500,"invalid_assets","本地复用文件校验失败，已有模型已保留。") }
                    try FileManager.default.createDirectory(at:target.deletingLastPathComponent(),withIntermediateDirectories:true)
                    try AssetDownloader.publish(copy,to:target)
                    break
                }
            }
            let url = try source.url(for:file,model:model)
            try await AssetDownloader.download(file,from:url,to:target,staging:config.stagingDirectory(root:root),
                session:session,restart:restart) { bytes in
                progress(AssetDownloadProgress(event:"progress",path:file.path,completed_bytes:base+bytes,total_bytes:total,phase:"downloading",model_id:model.id))
            }
            completed += Int64(file.size)
        }
    }
}
