import Foundation
import Darwin

/// Local asset operations share the downloader's lock. Configuration changes
/// remain reversible until the App confirms that the candidate engine is ready.
public enum ModelStore {
    private struct Transfer: Codable {
        let source: String
        let target: String
        let sha256: String
        let size: Int64
        let created: Bool
    }
    private struct Change: Codable {
        let original: Data
        let candidate: ServiceConfiguration
        var transfers: [Transfer]
        let staging: String?
        var committed = false
    }
    private static func journal(_ root: URL) -> URL { root.appendingPathComponent("config/model-change.json") }
    private static func configFile(_ root: URL) -> URL { root.appendingPathComponent("config/service.json") }
    private static let conflict = ServiceError(409,"model_conflict","目标存在不同的文件，已保留原文件。请检查模型目录。")

    public static func storageAvailable(at directory: URL) -> Bool {
        let parts = directory.standardizedFileURL.pathComponents
        guard parts.count >= 3,parts[1] == "Volumes" else { return true }
        let volume = URL(fileURLWithPath:"/Volumes/"+parts[2],isDirectory:true)
        guard FileManager.default.fileExists(atPath:volume.path),
              let info = try? FileManager.default.attributesOfFileSystem(forPath:volume.path),
              let parent = try? FileManager.default.attributesOfFileSystem(forPath:"/Volumes"),
              let device = info[.systemNumber] as? NSNumber,let parentDevice = parent[.systemNumber] as? NSNumber else { return false }
        return device != parentDevice
    }
    public static func requireStorage(at directory: URL) throws {
        guard storageAvailable(at:directory) else {
            throw ServiceError(503,"model_storage_unavailable","模型磁盘未连接，请重新连接磁盘后重试；原配置已保留。")
        }
    }

    public static func availableBytes(configuration: ServiceConfiguration, root: URL) -> Int64? {
        let locations = [URL(fileURLWithPath:configuration.asr_dir), URL(fileURLWithPath:configuration.tts_dir),
                         configuration.stagingDirectory(root:root)]
        guard locations.allSatisfy({ storageAvailable(at:$0) }) else { return nil }
        return [URL(fileURLWithPath:configuration.asr_dir), URL(fileURLWithPath:configuration.tts_dir),
         configuration.stagingDirectory(root:root)].compactMap { location in
            var existing = location
            while !FileManager.default.fileExists(atPath:existing.path),existing.path != "/" { existing.deleteLastPathComponent() }
            return AssetPreparation.availableBytes(at:existing)
        }.min()
    }

    public static func status(root: URL, modelID: String? = nil) throws -> [String:Any] {
        if let modelID { _ = try ModelCatalog.descriptor(modelID) }
        let config = try AssetStore.configuration(root:root)
        let available = availableBytes(configuration:config,root:root)
        var occupied: Int64 = 0
        let models = try ModelCatalog.descriptors().map { model -> [String:Any] in
            if !storageAvailable(at:config.directory(for:model,root:root)) {
                var item = model.summary
                item["directory"] = config.directory(for:model,root:root).path
                item["installed_bytes"] = Int64(0); item["verified_bytes"] = Int64(0); item["resumable_bytes"] = Int64(0)
                item["state"] = "unavailable"; item["invalid_partials"] = [String]()
                item["active"] = model.id == config.asrModelID || model.id == config.ttsModelID
                return item
            }
            let local = config.validating(model,root:root)
            var result: AssetPreparationStatus?
            var problem: String?
            do {
                result = try AssetPreparation.inspect(configuration:local,assets:model.files,
                    staging:config.stagingDirectory(root:root),availableBytes:available,checkFFmpeg:false)
            } catch is CancellationError { throw CancellationError() }
            catch { problem = (error as? ServiceError)?.message ?? "无法读取此模型，请检查文件与目录权限。" }
            var item = model.summary
            let installed = model.files.reduce(Int64(0)) { total,asset in
                total + ((try? AssetDownloader.regularSize(try AssetPreparation.destination(asset,configuration:local))) ?? 0)
            }
            occupied += installed
            if !model.files.contains(where:{ $0.path == model.component+"/tokenizer.json" }) {
                occupied += (try? AssetDownloader.regularSize(config.directory(for:model,root:root).appendingPathComponent("tokenizer.json"))) ?? 0
            }
            item["directory"] = config.directory(for:model,root:root).path
            item["installed_bytes"] = installed; item["verified_bytes"] = result?.verified_bytes ?? 0
            item["resumable_bytes"] = result?.resumable_bytes ?? 0
            item["state"] = result.map { $0.models_ready ? "ready" : (!$0.invalid_files.isEmpty || !$0.invalid_partials.isEmpty ? "invalid" :
                (installed > 0 || $0.resumable_bytes > 0 ? "partial" : "missing")) } ?? "invalid"
            item["invalid_partials"] = result?.invalid_partials ?? []
            if let problem { item["problem"] = problem }
            item["active"] = model.id == config.asrModelID || model.id == config.ttsModelID
            return item
        }
        let staging = config.stagingDirectory(root:root)
        if storageAvailable(at:staging) {
            for digest in Set(try ModelCatalog.descriptors().flatMap(\.files).map(\.sha256)) {
                occupied += (try? AssetDownloader.regularSize(staging.appendingPathComponent(digest+".partial"))) ?? 0
            }
        }
        return ["event":"models","models":models.filter { modelID == nil || $0["id"] as? String == modelID },"asr_model_id":config.asrModelID,"tts_model_id":config.ttsModelID,
                "models_directory":config.modelsDirectory(root:root).path,"occupied_bytes":occupied,
                "available_bytes":available as Any? ?? NSNull(),"pending_change":FileManager.default.fileExists(atPath:journal(root).path)]
    }

    public static func verify(root: URL, modelID: String) throws {
        let model = try ModelCatalog.descriptor(modelID),config = try AssetStore.configuration(root:root)
        try requireStorage(at:config.directory(for:model,root:root))
        let result = try AssetPreparation.inspect(configuration:config.validating(model,root:root),assets:model.files,
            staging:config.stagingDirectory(root:root),availableBytes:nil,checkFFmpeg:false)
        guard result.models_ready else { throw ServiceError(500,"invalid_assets","该模型尚未下载完成或校验失败，请完成下载后再启用。") }
        _ = try AssetStore.prepareTokenizer(directory:config.directory(for:model,root:root),asr:model.component == "asr")
    }

    private static func withLock<T>(root: URL, _ operation: () throws -> T) throws -> T {
        let temporary = root.appendingPathComponent("tmp",isDirectory:true)
        try FileManager.default.createDirectory(at:temporary,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        let fd = open(temporary.appendingPathComponent("asset-download.lock").path,O_CREAT|O_RDWR|O_NOFOLLOW,0o600)
        guard fd >= 0 else { throw AssetDownloader.unsafePath }
        defer { close(fd) }
        guard flock(fd,LOCK_EX|LOCK_NB) == 0 else { throw ServiceError(409,"setup_busy","已有模型管理任务，请等待任务结束。") }
        return try operation()
    }
    private static func save(_ change: Change, root: URL) throws {
        try JSONEncoder().encode(change).write(to:journal(root),options:.atomic)
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:journal(root).path)
    }
    private static func ensureNoChange(_ root: URL) throws {
        guard !FileManager.default.fileExists(atPath:journal(root).path) else {
            throw ServiceError(409,"model_change_pending","已有模型配置等待确认，请先完成或恢复该操作。")
        }
    }
    private static func writeConfig(_ config: ServiceConfiguration, root: URL) throws {
        try JSONEncoder().encode(config).write(to:configFile(root),options:.atomic)
    }

    public static func select(root: URL, modelID: String, transactional: Bool = false) throws {
        try withLock(root:root) {
            try ensureNoChange(root)
            try verify(root:root,modelID:modelID)
            let model = try ModelCatalog.descriptor(modelID),config = try AssetStore.configuration(root:root)
            let change = Change(original:try Data(contentsOf:configFile(root)),candidate:config.selecting(model,root:root),transfers:[],staging:nil)
            try save(change,root:root)
            try writeConfig(change.candidate,root:root)
            if !transactional { try finish(root:root,commit:true) }
        }
    }

    public static func delete(root: URL, modelID: String) throws {
        try withLock(root:root) {
            try ensureNoChange(root)
            let config = try AssetStore.configuration(root:root),model = try ModelCatalog.descriptor(modelID)
            try requireStorage(at:config.directory(for:model,root:root))
            guard ![config.asrModelID,config.ttsModelID].contains(modelID) else {
                throw ServiceError(409,"model_in_use","当前使用的模型不能删除，请先切换到其他型号。")
            }
            let local = config.validating(model,root:root)
            // Confirm every final file before removing anything in a potentially user-owned directory.
            var files: [URL] = []
            for asset in model.files {
                let target = try AssetPreparation.destination(asset,configuration:local)
                if let size = try AssetDownloader.regularSize(target) {
                    guard size == Int64(asset.size),try AssetStore.digest(target) == asset.sha256 else { throw conflict }
                    files.append(target)
                }
            }
            let tokenizer = config.directory(for:model,root:root).appendingPathComponent("tokenizer.json")
            if !model.files.contains(where: { $0.path == model.component+"/tokenizer.json" }),
               (try? AssetDownloader.regularSize(tokenizer)) != nil {
                _ = try AssetStore.prepareTokenizer(directory:config.directory(for:model,root:root),asr:model.component == "asr")
                files.append(tokenizer)
            }
            let shared = Set(try ModelCatalog.descriptors().filter { $0.id != modelID }.flatMap(\.files).map(\.sha256))
            let staging = config.stagingDirectory(root:root)
            try requireStorage(at:staging)
            for asset in model.files where !shared.contains(asset.sha256) {
                let partial = staging.appendingPathComponent(asset.sha256+".partial")
                if try AssetDownloader.regularSize(partial) != nil { files.append(partial) }
            }
            for file in files { try FileManager.default.removeItem(at:file) }
            // Retain content-addressed partials shared with other catalog variants.
            let directories = Set(files.map { $0.deletingLastPathComponent().path }).sorted { $0.count > $1.count }
            for directory in directories { _ = rmdir(directory) }
        }
    }

    public static func migrate(root: URL, destination: URL, transactional: Bool = false,
                               progress: (Int64,Int64,String) -> Void = { _,_,_ in }) throws {
        try withLock(root:root) {
            try ensureNoChange(root)
            let original = try AssetStore.configuration(root:root)
            try requireStorage(at:destination)
            let targetRoot = destination.standardizedFileURL.resolvingSymlinksInPath()
            let currentRoot = original.modelsDirectory(root:root).standardizedFileURL.resolvingSymlinksInPath()
            guard targetRoot != currentRoot else { return }
            guard targetRoot.path != "/", !targetRoot.path.hasPrefix(currentRoot.path+"/"),
                  !currentRoot.path.hasPrefix(targetRoot.path+"/") else { throw AssetDownloader.unsafePath }
            var candidate = original
            candidate.models_dir = targetRoot.path; candidate.model_directories = [:]
            let models = try ModelCatalog.descriptors()
            var transfers: [Transfer] = []
            var seen: Set<String> = []
            for model in models {
                try requireStorage(at:original.directory(for:model,root:root))
                let sourceRoot = original.directory(for:model,root:root).resolvingSymlinksInPath()
                guard !targetRoot.path.hasPrefix(sourceRoot.path+"/"),!sourceRoot.path.hasPrefix(targetRoot.path+"/") else { throw AssetDownloader.unsafePath }
                let modelTarget = targetRoot.appendingPathComponent(model.directoryName,isDirectory:true)
                candidate.model_directories?[model.id] = modelTarget.path
                if model.id == original.asrModelID { candidate.asr_dir = modelTarget.path }
                if model.id == original.ttsModelID { candidate.tts_dir = modelTarget.path }
                var entries = model.files.map { (String($0.path.dropFirst(model.component.count+1)),Optional($0)) }
                if !entries.contains(where: { $0.0 == "tokenizer.json" }) { entries.append(("tokenizer.json",nil)) }
                for (relative,asset) in entries {
                    let source = sourceRoot.appendingPathComponent(relative)
                    guard let size = try AssetDownloader.regularSize(source) else { continue }
                    guard source.resolvingSymlinksInPath().path.hasPrefix(sourceRoot.path+"/") else { throw AssetDownloader.unsafePath }
                    let digest = try AssetStore.digest(source)
                    if let asset { guard size == Int64(asset.size),digest == asset.sha256 else { throw conflict } }
                    if relative == "tokenizer.json",asset == nil {
                        _ = try AssetStore.prepareTokenizer(directory:sourceRoot,asr:model.component == "asr")
                    }
                    let target = modelTarget.appendingPathComponent(relative)
                    guard target.resolvingSymlinksInPath().path.hasPrefix(targetRoot.path+"/") else { throw AssetDownloader.unsafePath }
                    guard seen.insert(target.path).inserted else { continue }
                    let existing = try AssetDownloader.regularSize(target)
                    if let existing { guard existing == size,try AssetStore.digest(target) == digest else { throw conflict } }
                    transfers.append(Transfer(source:source.path,target:target.path,sha256:digest,size:size,created:existing == nil))
                }
            }
            // Preserve only recognized content-addressed partials, including legacy staging.
            let oldStage = original.stagingDirectory(root:root)
            if FileManager.default.fileExists(atPath:oldStage.path) { try AssetDownloader.directory(oldStage) }
            let assets = Dictionary(models.flatMap(\.files).map { ($0.sha256,$0.size) },uniquingKeysWith:max)
            for (digest,size) in assets {
                let source = oldStage.appendingPathComponent(digest+".partial")
                guard let length = try AssetDownloader.regularSize(source) else { continue }
                guard length <= Int64(size) else { throw conflict }
                let hash = try AssetStore.digest(source),target = targetRoot.appendingPathComponent(".downloads/"+digest+".partial")
                guard target.resolvingSymlinksInPath().path.hasPrefix(targetRoot.path+"/") else { throw AssetDownloader.unsafePath }
                let existing = try AssetDownloader.regularSize(target)
                if let existing { guard existing == length,try AssetStore.digest(target) == hash else { throw conflict } }
                transfers.append(Transfer(source:source.path,target:target.path,sha256:hash,size:length,created:existing == nil))
            }
            var parent = targetRoot
            while !FileManager.default.fileExists(atPath:parent.path),parent.path != "/" { parent.deleteLastPathComponent() }
            let required = transfers.filter(\.created).reduce(Int64(256*1024*1024)) { $0+$1.size }
            if let space = AssetPreparation.availableBytes(at:parent),space < required {
                throw ServiceError(507,"insufficient_storage","目标磁盘空间不足，原模型和配置已保留。")
            }
            try FileManager.default.createDirectory(at:targetRoot,withIntermediateDirectories:true)
            try AssetDownloader.directory(targetRoot)
            let staging = targetRoot.appendingPathComponent(".cyberaudio-migration-"+UUID().uuidString,isDirectory:true)
            let change = Change(original:try Data(contentsOf:configFile(root)),candidate:candidate,transfers:transfers,staging:staging.path)
            try save(change,root:root)
            do {
                try FileManager.default.createDirectory(at:staging,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
                let total = transfers.reduce(Int64(0)) { $0+$1.size }; var completed: Int64 = 0
                for (index,transfer) in transfers.enumerated() {
                    try Task.checkCancellation()
                    if transfer.created {
                        let temporary = staging.appendingPathComponent(String(index))
                        let input = try FileHandle(forReadingFrom:URL(fileURLWithPath:transfer.source))
                        defer { try? input.close() }
                        FileManager.default.createFile(atPath:temporary.path,contents:nil,attributes:[.posixPermissions:0o600])
                        let output = try FileHandle(forWritingTo:temporary)
                        defer { try? output.close() }
                        var copied: Int64 = 0
                        while let data = try input.read(upToCount:1024*1024),!data.isEmpty {
                            try Task.checkCancellation(); try output.write(contentsOf:data); copied += Int64(data.count)
                            progress(completed+copied,total,URL(fileURLWithPath:transfer.target).lastPathComponent)
                        }
                        try output.synchronize()
                        guard copied == transfer.size,try AssetStore.digest(temporary) == transfer.sha256 else { throw conflict }
                        let target = URL(fileURLWithPath:transfer.target)
                        try FileManager.default.createDirectory(at:target.deletingLastPathComponent(),withIntermediateDirectories:true)
                        guard target.resolvingSymlinksInPath().path.hasPrefix(targetRoot.path+"/") else { throw AssetDownloader.unsafePath }
                        try AssetDownloader.publish(temporary,to:target)
                    }
                    completed += transfer.size; progress(completed,total,URL(fileURLWithPath:transfer.target).lastPathComponent)
                }
                try Task.checkCancellation()
                try writeConfig(candidate,root:root)
                if !transactional { try finish(root:root,commit:true) }
            } catch {
                try? finish(root:root,commit:false)
                throw error
            }
        }
    }

    public static func complete(root: URL, commit: Bool) throws {
        try withLock(root:root) { try finish(root:root,commit:commit) }
    }
    public static func recover(root: URL) throws {
        guard FileManager.default.fileExists(atPath:journal(root).path) else { return }
        let change = try JSONDecoder().decode(Change.self,from:Data(contentsOf:journal(root)))
        try complete(root:root,commit:change.committed)
    }
    private static func finish(root: URL, commit: Bool) throws {
        guard FileManager.default.fileExists(atPath:journal(root).path) else { return }
        var change = try JSONDecoder().decode(Change.self,from:Data(contentsOf:journal(root)))
        let commit = commit || change.committed
        if commit {
            for entry in change.transfers {
                try requireStorage(at:URL(fileURLWithPath:entry.target))
                guard try AssetDownloader.regularSize(URL(fileURLWithPath:entry.target)) == entry.size,
                      try AssetStore.digest(URL(fileURLWithPath:entry.target)) == entry.sha256 else { throw conflict }
            }
            change.committed = true
            try save(change,root:root)
        } else { try change.original.write(to:configFile(root),options:.atomic) }
        for entry in change.transfers {
            if commit { try Task.checkCancellation() }
            let path = commit ? entry.source : entry.target
            guard entry.source != entry.target,commit || entry.created else { continue }
            let file = URL(fileURLWithPath:path)
            if (try? AssetDownloader.regularSize(file)) == entry.size,(try? AssetStore.digest(file)) == entry.sha256 {
                try? FileManager.default.removeItem(at:file)
                _ = rmdir(file.deletingLastPathComponent().path)
            }
        }
        if let staging = change.staging {
            let directory = URL(fileURLWithPath:staging)
            if directory.deletingLastPathComponent().path == change.candidate.models_dir,
               directory.lastPathComponent.hasPrefix(".cyberaudio-migration-") { try? FileManager.default.removeItem(at:directory) }
        }
        if commit { try Task.checkCancellation() }
        try FileManager.default.removeItem(at:journal(root))
    }
}
