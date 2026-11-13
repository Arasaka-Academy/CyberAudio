import Foundation
import Darwin
import CyberAudioCore
import MLX
import MLXAudioCore
import MLXAudioSTT
import MLXAudioTTS

// Transferred once after load; all subsequent model operations and destruction
// are confined to ServiceCoordinator.worker. metrics is immutable after load.
public final class QwenEngine: InferenceEngine, @unchecked Sendable {
    private let asr: Qwen3ASRModel
    private let tts: Qwen3TTSModel
    private let eos: Int
    public private(set) var metrics: [String:Any] = [:]
    private init(asr: Qwen3ASRModel,tts: Qwen3TTSModel,eos: Int) { self.asr = asr; self.tts = tts; self.eos = eos }
    public static func load(settings: ServiceSettings, metallib: URL) async throws -> QwenEngine {
        guard try AssetStore.digest(metallib) == "c6d7a3facb83568f6e4cb2cf4a8c736ee29302571a48343cb9a3a4d1cd3e3893" else {
            throw ServiceError(500,"invalid_metal_resource","Metal 资源校验失败，请重新安装 App。")
        }
        GPU.metallib = metallib
        Memory.cacheLimit = 512*1024*1024
        let verified = uptime()
        _ = try AssetStore.verify(settings.configuration)
        // The SDK enumerates model directories and rejects symlink URLs on
        // macOS even though individual files passed our asset verification.
        let asrRoot = URL(fileURLWithPath:settings.configuration.asr_dir,isDirectory:true).resolvingSymlinksInPath()
        let ttsRoot = URL(fileURLWithPath:settings.configuration.tts_dir,isDirectory:true).resolvingSymlinksInPath()
        _ = try AssetStore.prepareTokenizer(directory:asrRoot,asr:true)
        _ = try AssetStore.prepareTokenizer(directory:ttsRoot,asr:false)
        let verification = (uptime()-verified)*1000
        return try await Device.withDefaultDevice(.gpu) {
            let begin = uptime()
            let asr = try await Qwen3ASRModel.fromModelDirectory(asrRoot)
            let tts = try await Qwen3TTSModel.fromModelDirectory(ttsRoot)
            let config = try JSONSerialization.jsonObject(with:Data(contentsOf:ttsRoot.appendingPathComponent("config.json"))) as! [String:Any]
            let selected = try ModelCatalog.descriptor(settings.configuration.ttsModelID)
            guard let talker = config["talker_config"] as? [String:Any], let eos = talker["codec_eos_token_id"] as? Int,
                  config["tts_model_type"] as? String == "custom_voice",
                  config["tts_model_size"] as? String == (selected.parameters == "0.6B" ? "0b6" : "1b7"),tts.sampleRate == 24000 else { throw ServiceError.inference }
            let engine = QwenEngine(asr:asr,tts:tts,eos:eos)
            let loaded = uptime()
            _ = asr.generate(audio:MLXArray.zeros([16000]),maxTokens:16,temperature:0,language:"Chinese")
            try tts.generateManaged(text:"你好。",voice:"serena",language:"chinese",maxTokens:48,
                checkCancellation:{},onToken:{ _ in },onAudioChunk:{ _ in })
            Memory.clearCache()
            engine.metrics = ["device":"metal","backend":"mlx-swift","load_ms":(loaded-begin)*1000,
                "warmup_ms":(uptime()-loaded)*1000,"verification_ms":verification].merging(engine.memory()) { _,new in new }
            return engine
        }
    }
    public func memory() -> [String:Any] {
        var usage = rusage(); getrusage(RUSAGE_SELF,&usage)
        return ["metal_peak_bytes":Memory.peakMemory,"metal_active_bytes":Memory.activeMemory,"process_peak_rss_bytes":usage.ru_maxrss]
    }
    public func transcribe(audio: URL,language: String?,cancellation: CancellationToken) throws -> [String:Any] {
        try cancellation.check()
        return try Device.withDefaultDevice(.gpu) {
            defer { Memory.clearCache() }
            let (_,samples) = try loadAudioArray(from:audio,sampleRate:16000)
            let result = try asr.generateManaged(audio:samples,maxTokens:1024,temperature:0,language:language,
                                                checkCancellation:{ try cancellation.check() })
            try cancellation.check()
            guard result.generationTokens < 1024 else { throw ServiceError(422,"generation_limit","识别达到长度上限，请缩短音频。") }
            let detected = result.language.map { ["Chinese":"zh","English":"en"][$0] ?? $0 }
            return ["text":result.text.trimmingCharacters(in:.whitespacesAndNewlines),"language":detected as Any? ?? NSNull()]
                .merging(memory()) { _,new in new }
        }
    }
    public func synthesize(_ speech: Speech,cancellation: CancellationToken,emit: @escaping (Data) throws -> Void) throws {
        try Device.withDefaultDevice(.gpu) {
            defer { Memory.clearCache() }
            var eosSeen = false, failure: Error?
            let prompt = speech.instructions.isEmpty ? speech.voice : speech.voice+", "+speech.instructions
            // The synchronous patched SDK method owns the decoder reset defer.
            // No detached producer can outlive this call or release the service slot early.
            try tts.generateManaged(text:speech.input,voice:prompt,language:speech.language,maxTokens:ModelCatalog.maxTokens,
                checkCancellation:{
                    if let failure { throw failure }
                    try cancellation.check()
                },onToken:{ token in eosSeen = eosSeen || token == self.eos },onAudioChunk:{ array in
                    do {
                        try cancellation.check()
                        let samples = array.asArray(Float.self)
                        guard samples.allSatisfy(\.isFinite) else { throw ServiceError.inference }
                        let pcm = samples.map { Int16(max(-1,min(1,$0))*32767).littleEndian }
                        try emit(pcm.withUnsafeBytes { Data($0) })
                    } catch { failure = error }
                })
            if let failure { throw failure }
            try cancellation.check()
            guard eosSeen else { throw ServiceError(422,"generation_limit","生成达到长度上限，请缩短文本。") }
        }
    }
}
