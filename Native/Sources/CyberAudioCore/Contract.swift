import Foundation
import CoreFoundation

public enum ModelCatalog {
    public static let asrID = "qwen3-asr-1.7b"
    public static let asrRepository = "mlx-community/Qwen3-ASR-1.7B-8bit"
    public static let asrRevision = "a8379a2e2f9e313c9292cdf1af4055ab56d50d55"
    public static let ttsID = "qwen3-tts-1.7b"
    public static let ttsRepository = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
    public static let ttsRevision = "41d3337e8b7f2843a75841595fc14e4b9a7a4b96"
    public static let voices = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]
    public static let maxUpload = 20 * 1024 * 1024
    public static let maxTokens = 1200
    public static var models: [String: Any] {
        ["object": "list", "data": [
            (asrID, asrRepository, asrRevision), (ttsID, ttsRepository, ttsRevision)
        ].map { id, repository, revision in
            ["id": id, "object": "model", "owned_by": "local", "repository": repository,
             "revision": revision, "quantization": "8bit"]
        }]
    }
}

public struct ServiceConfiguration: Codable {
    public var asr_dir: String
    public var tts_dir: String
    public var ffmpeg: String
    public var port: Int
    public var https_url: String
    public init(asr_dir: String, tts_dir: String, ffmpeg: String, port: Int = 8765, https_url: String = "") {
        self.asr_dir = asr_dir; self.tts_dir = tts_dir; self.ffmpeg = ffmpeg
        self.port = port; self.https_url = https_url
    }
}

public struct ServiceSettings {
    public let root: URL
    public let configuration: ServiceConfiguration
    public let token: String
    public let instance: String
    public let timeout: Double
    public var temporaryDirectory: URL { root.appendingPathComponent("tmp") }
    public init(root: URL, configuration: ServiceConfiguration, token: String, instance: String, timeout: Double = 120) throws {
        guard token.utf8.count >= 32, !token.contains("\r"), !token.contains("\n"),
              (1...65535).contains(configuration.port), !instance.isEmpty, timeout > 0 else {
            throw ServiceError(500, "invalid_configuration", "服务配置无效。")
        }
        self.root = root; self.configuration = configuration; self.token = token
        self.instance = instance; self.timeout = timeout
    }
}

public struct ServiceError: Error, Equatable {
    public let status: Int
    public let code: String
    public let message: String
    public init(_ status: Int, _ code: String, _ message: String) {
        self.status = status; self.code = code; self.message = message
    }
    public var json: [String: Any] { ["error": ["code": code, "message": message]] }
    public static let cancelled = ServiceError(499, "cancelled", "请求已取消。")
    public static let timeout = ServiceError(504, "inference_timeout", "处理超时，请缩短输入。")
    public static let inference = ServiceError(500, "inference_failed", "推理失败，请重试。")
}

public struct Speech {
    public var input: String
    public var voice: String
    public var language: String
    public var instructions: String
    public var format: String
    public var stream: Bool
    public init(data: Data) throws {
        let invalid = ServiceError(400, "invalid_request", "请输入 1–500 字符文本，指令最多 200 字符；检查字段类型。")
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys).isSubset(of: ["model", "input", "voice", "language", "instructions", "response_format", "stream"]),
              let input = object["input"] as? String, (1...500).contains(input.unicodeScalars.count) else { throw invalid }
        func string(_ name: String, _ fallback: String) throws -> String {
            guard let value = object[name] else { return fallback }
            guard let value = value as? String else { throw invalid }; return value
        }
        let model = try string("model", ModelCatalog.ttsID)
        guard [ModelCatalog.ttsID, ModelCatalog.ttsRepository].contains(model) else {
            throw ServiceError(400, "invalid_model", "仅支持已部署的 TTS 模型。")
        }
        self.input = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !self.input.isEmpty else { throw ServiceError(400, "empty_input", "文本不能为空。") }
        voice = try string("voice", "serena").lowercased()
        let lang = try string("language", "auto")
        language = ["zh": "chinese", "en": "english", "Chinese": "chinese", "English": "english"][lang] ?? lang
        guard ModelCatalog.voices.contains(voice), ["auto", "chinese", "english"].contains(language) else {
            throw ServiceError(400, "invalid_voice_or_language", "音色或语言无效。")
        }
        instructions = try string("instructions", "")
        guard instructions.unicodeScalars.count <= 200 else { throw invalid }
        format = try string("response_format", "wav")
        if let value = object["stream"] {
            guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { throw invalid }
            stream = number.boolValue
        } else { stream = false }
        guard ["wav", "pcm"].contains(format), !stream || format == "pcm" else {
            throw ServiceError(400, "invalid_format", "支持 WAV、PCM；流式输出必须选择 PCM。")
        }
    }
}

public struct Transcription {
    public let audio: Data
    public let language: String?
    public init(parts: [MultipartPart]) throws {
        let files = parts.filter { $0.filename != nil }
        guard files.count == 1, let file = files.first, file.name == "file" else {
            throw ServiceError(400, "missing_audio", "请选择音频文件。")
        }
        guard file.data.count <= ModelCatalog.maxUpload else {
            throw ServiceError(413, "upload_too_large", "上传文件不能超过 20MiB。")
        }
        var fields: [String: String] = [:]
        for part in parts where part.filename == nil {
            guard fields[part.name] == nil, let text = String(data: part.data, encoding: .utf8) else {
                throw ServiceError(400, "invalid_request", "音频上传请求无效。")
            }
            fields[part.name] = text
        }
        guard [ModelCatalog.asrID, ModelCatalog.asrRepository].contains(fields["model"] ?? ModelCatalog.asrID) else {
            throw ServiceError(400, "invalid_model", "仅支持已部署的 ASR 模型。")
        }
        let lang = fields["language"] ?? "auto"
        guard ["auto", "zh", "en", "Chinese", "English", ""].contains(lang) else {
            throw ServiceError(400, "invalid_language", "语言请选择自动、中文或英文。")
        }
        guard fields["response_format", default: "json"] == "json" else {
            throw ServiceError(400, "invalid_format", "STT 仅支持 JSON 输出。")
        }
        language = ["zh": "Chinese", "en": "English"][lang] ?? (["auto", ""].contains(lang) ? nil : lang)
        audio = file.data
    }
}

public func uptime() -> Double { Double(DispatchTime.now().uptimeNanoseconds) / 1e9 }
public func jsonData(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) }
