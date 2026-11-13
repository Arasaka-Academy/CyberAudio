import Foundation

/// Transport selection never changes the files or digests in Models.lock.json.
public enum AssetDownloadSource: String, Sendable {
    case huggingFace = "huggingface"
    case modelScope = "modelscope"

    public func url(for asset: LockedAsset, model: ModelDescriptor) throws -> URL {
        try AssetDownloader.validate(asset)
        guard asset.path.hasPrefix(model.component+"/"), let pinned = model.sources[rawValue] else {
            throw ServiceError(400,"source_unavailable","该型号尚未核验所选下载源，请选择其他可用下载源。")
        }
        let relative = String(asset.path.dropFirst(model.component.count+1))
        switch self {
        case .huggingFace:
            return URL(string:"https://huggingface.co/\(model.repository)/resolve/\(pinned.revision)/")!
                .appendingPathComponent(relative).appending(queryItems:[URLQueryItem(name:"download",value:"true")])
        case .modelScope:
            var url = URLComponents(string:"https://modelscope.cn/api/v1/models/\(model.repository)/repo")!
            url.queryItems = [URLQueryItem(name:"Revision",value:pinned.revision),URLQueryItem(name:"FilePath",value:relative)]
            return url.url!
        }
    }

    public func url(for asset: LockedAsset) throws -> URL {
        try AssetDownloader.validate(asset)
        let parts = asset.path.split(separator: "/", maxSplits: 1)
        guard parts.count == 2, ["asr", "tts"].contains(String(parts[0])),
              !parts[1].split(separator: "/", omittingEmptySubsequences: false)
                .contains(where: { $0.isEmpty || $0 == "." || $0 == ".." }) else {
            throw AssetDownloader.unsafePath
        }
        let asr = parts[0] == "asr", relative = String(parts[1])
        let repository = asr ? ModelCatalog.asrRepository : ModelCatalog.ttsRepository
        switch self {
        case .huggingFace:
            let revision = asr ? ModelCatalog.asrRevision : ModelCatalog.ttsRevision
            return URL(string: "https://huggingface.co/\(repository)/resolve/\(revision)/")!
                .appendingPathComponent(relative).appending(queryItems: [URLQueryItem(name: "download", value: "true")])
        case .modelScope:
            // Both immutable snapshots contain all 23 original locked hashes.
            let revision = asr ? "579e237ce6ec925252973afe835d2f98a138602f" : "08b854525817066e04ef6b6430a3faa0eebd24d2"
            var source = URLComponents(string: "https://modelscope.cn/api/v1/models/\(repository)/repo")!
            source.queryItems = [URLQueryItem(name: "Revision", value: revision), URLQueryItem(name: "FilePath", value: relative)]
            return source.url!
        }
    }
}
