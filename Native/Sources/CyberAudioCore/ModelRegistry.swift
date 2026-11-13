import Foundation

public struct ModelSource: Codable, Sendable {
    public let revision: String
}

public struct ModelDescriptor: Codable, Sendable {
    public let id: String
    public let component: String
    public let name: String
    public let parameters: String
    public let quantization: String
    public let repository: String
    public let revision: String
    public let supports_instructions: Bool
    public let sources: [String: ModelSource]
    public let files: [LockedAsset]
    public var totalBytes: Int64 { files.reduce(0) { $0 + Int64($1.size) } }
    public var directoryName: String { String(repository.split(separator: "/").last!) }
    public var summary: [String: Any] {
        ["id": id, "component": component, "name": name, "parameters": parameters,
         "quantization": quantization, "repository": repository, "revision": revision,
         "supports_instructions": supports_instructions, "total_bytes": totalBytes,
         "sources": sources.keys.sorted()]
    }
    public var api: [String: Any] {
        ["id": id, "object": "model", "owned_by": "local", "repository": repository,
         "revision": revision, "quantization": quantization, "supports_instructions": supports_instructions]
    }
}

extension ModelCatalog {
    private struct Registry: Decodable { let models: [ModelDescriptor] }
    private static let registry: Result<[ModelDescriptor], Error> = Result {
        guard let url = Bundle.module.url(forResource: "ModelCatalog.lock", withExtension: "json") else {
            throw ServiceError(500, "invalid_catalog", "App 缺少模型目录，请重新安装完整 App。")
        }
        let models = try JSONDecoder().decode(Registry.self, from: Data(contentsOf: url)).models
        guard models.count == 8, Set(models.map(\.id)).count == models.count,
              models.allSatisfy({ !$0.files.isEmpty && ["asr", "tts"].contains($0.component) && $0.revision.count == 40 }) else {
            throw ServiceError(500, "invalid_catalog", "模型目录无效，请重新安装完整 App。")
        }
        return models
    }
    public static func descriptors() throws -> [ModelDescriptor] { try registry.get() }
    public static func descriptor(_ id: String) throws -> ModelDescriptor {
        guard let model = try descriptors().first(where: { $0.id == id }) else {
            throw ServiceError(400, "invalid_model", "请选择模型目录中支持的型号。")
        }
        return model
    }
    public static func models(configuration: ServiceConfiguration) throws -> [String: Any] {
        ["object": "list", "data": try [configuration.asrModelID, configuration.ttsModelID].map { try descriptor($0).api }]
    }
}

extension ServiceConfiguration {
    public var asrModelID: String { asr_model_id ?? ModelCatalog.asrID }
    public var ttsModelID: String { tts_model_id ?? ModelCatalog.ttsID }
    public func modelsDirectory(root: URL) -> URL {
        models_dir.map { URL(fileURLWithPath: $0, isDirectory: true) } ?? root.appendingPathComponent("models", isDirectory: true)
    }
    public func directory(for model: ModelDescriptor, root: URL) -> URL {
        if model.id == asrModelID { return URL(fileURLWithPath: asr_dir, isDirectory: true) }
        if model.id == ttsModelID { return URL(fileURLWithPath: tts_dir, isDirectory: true) }
        if let path = model_directories?[model.id] { return URL(fileURLWithPath: path, isDirectory: true) }
        return modelsDirectory(root: root).appendingPathComponent(model.directoryName, isDirectory: true)
    }
    public func stagingDirectory(root: URL) -> URL {
        models_dir == nil ? root.appendingPathComponent("tmp/downloads", isDirectory: true)
            : modelsDirectory(root: root).appendingPathComponent(".downloads", isDirectory: true)
    }
    public func selecting(_ model: ModelDescriptor, root: URL) -> ServiceConfiguration {
        var next = self
        var paths = model_directories ?? [:]
        paths[asrModelID] = asr_dir; paths[ttsModelID] = tts_dir
        let path = directory(for: model, root: root).path
        paths[model.id] = path; next.model_directories = paths
        if model.component == "asr" { next.asr_model_id = model.id; next.asr_dir = path }
        else { next.tts_model_id = model.id; next.tts_dir = path }
        return next
    }
    public func validating(_ model: ModelDescriptor, root: URL) -> ServiceConfiguration {
        var config = self
        if model.component == "asr" { config.asr_dir = directory(for: model, root: root).path }
        else { config.tts_dir = directory(for: model, root: root).path }
        return config
    }
}
