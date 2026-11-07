import Foundation

/// Transport selection never changes the files or digests in Models.lock.json.
public enum AssetDownloadSource: String, Sendable {
    case huggingFace = "huggingface"
    case modelScope = "modelscope"

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
