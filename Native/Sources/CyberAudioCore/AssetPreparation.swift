import Foundation
import Darwin

public struct AssetPreparationStatus: Codable,Sendable {
    public let event: String
    public let total_bytes: Int64
    public let verified_bytes: Int64
    public let resumable_bytes: Int64
    public let missing_files: Int
    public let invalid_files: [String]
    public let invalid_partials: [String]
    public let models_ready: Bool
    public let ffmpeg_ready: Bool
    public let ffmpeg_path: String
    public let available_bytes: Int64?
    public let required_bytes: Int64
    public let can_download: Bool
}

public struct AssetDownloadProgress: Codable,Sendable {
    public let event: String
    public let path: String
    public let completed_bytes: Int64
    public let total_bytes: Int64
    public let phase: String
}

public enum AssetPreparation {
    public static func inspect(configuration: ServiceConfiguration,assets: [LockedAsset],staging: URL,
                               availableBytes: Int64?) throws -> AssetPreparationStatus {
        var total: Int64 = 0,verified: Int64 = 0,resumable: Int64 = 0,largest: Int64 = 0
        var missing = 0,invalid: [String] = [],invalidPartials: [String] = []
        for asset in assets {
            try Task.checkCancellation()
            try AssetDownloader.validate(asset)
            let size = Int64(asset.size)
            guard size > 0 else { throw ServiceError.inference }
            total += size
            let target = try destination(asset,configuration:configuration)
            if let existing = try AssetDownloader.regularSize(target) {
                if existing == size,try AssetStore.digest(target) == asset.sha256 { verified += size; continue }
                invalid.append(asset.path)
            } else { missing += 1 }
            largest = max(largest,size)
            if let partial = try AssetDownloader.regularSize(staging.appendingPathComponent(asset.sha256+".partial")) {
                if partial == size,try AssetStore.digest(staging.appendingPathComponent(asset.sha256+".partial")) != asset.sha256 { invalidPartials.append(asset.path) }
                else if partial <= size { resumable += partial }
                else { invalidPartials.append(asset.path) }
            }
        }
        let modelsReady = total == verified && invalid.isEmpty
        let required = modelsReady ? 0 : max(0,total-verified-resumable)+largest+256*1024*1024
        return AssetPreparationStatus(event:"status",total_bytes:total,verified_bytes:verified,resumable_bytes:resumable,
            missing_files:missing,invalid_files:invalid,invalid_partials:invalidPartials,models_ready:modelsReady,
            ffmpeg_ready:isFFmpeg(configuration.ffmpeg),ffmpeg_path:configuration.ffmpeg,available_bytes:availableBytes,
            required_bytes:required,can_download:invalid.isEmpty && (availableBytes.map { $0 >= required } ?? true))
    }
    static func destination(_ asset: LockedAsset,configuration: ServiceConfiguration) throws -> URL {
        let parts = asset.path.split(separator:"/",maxSplits:1)
        guard parts.count == 2,["asr","tts"].contains(String(parts[0])),
              !parts[1].split(separator:"/",omittingEmptySubsequences:false).contains(where:{ $0 == "." || $0 == ".." || $0.isEmpty }) else { throw AssetDownloader.unsafePath }
        let root = URL(fileURLWithPath:parts[0] == "asr" ? configuration.asr_dir : configuration.tts_dir).resolvingSymlinksInPath()
        let target = root.appendingPathComponent(String(parts[1]))
        guard target.resolvingSymlinksInPath().path.hasPrefix(root.path+"/") else { throw AssetDownloader.unsafePath }
        return target
    }
    public static func availableBytes(at root: URL) -> Int64? {
        (try? FileManager.default.attributesOfFileSystem(forPath:root.path)[.systemFreeSize] as? NSNumber)?.int64Value
    }
    public static func isFFmpeg(_ path: String) -> Bool {
        guard FileManager.default.isExecutableFile(atPath:path) else { return false }
        let process = Process(),pipe = Pipe(),output = VersionOutput()
        defer { try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close() }
        process.executableURL = URL(fileURLWithPath:path); process.arguments = ["-version"]
        process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return false }
        try? pipe.fileHandleForWriting.close()
        DispatchQueue.global().async {
            output.data = (try? pipe.fileHandleForReading.read(upToCount:256)) ?? Data()
            output.done.signal()
        }
        let deadline = uptime()+2
        while process.isRunning && uptime() < deadline { Thread.sleep(forTimeInterval:0.01) }
        if process.isRunning { kill(process.processIdentifier,SIGKILL); process.waitUntilExit(); return false }
        guard output.done.wait(timeout:.now()+1) == .success,process.terminationStatus == 0 else { return false }
        return String(decoding:output.data,as:UTF8.self).hasPrefix("ffmpeg version ")
    }
}

private final class VersionOutput: @unchecked Sendable {
    let done = DispatchSemaphore(value:0)
    // The reader publishes data before signalling done.
    var data = Data()
}

extension AssetStore {
    public static func preparationStatus(root: URL) throws -> AssetPreparationStatus {
        try AssetPreparation.inspect(configuration:configuration(root:root),assets:lock().files,
            staging:root.appendingPathComponent("tmp/downloads"),availableBytes:AssetPreparation.availableBytes(at:root))
    }
    public static func setFFmpeg(root: URL,path: String) throws {
        guard AssetPreparation.isFFmpeg(path) else {
            throw ServiceError(400,"invalid_ffmpeg","所选程序不是可用的 FFmpeg，请选择已安装的 ffmpeg 可执行文件。")
        }
        var config = try configuration(root:root)
        config.ffmpeg = path
        try JSONEncoder().encode(config).write(to:root.appendingPathComponent("config/service.json"),options:.atomic)
    }
}
