import Foundation
import CryptoKit
import Darwin
import MLX
import MLXAudioCore
import MLXAudioSTT
import MLXAudioTTS
import MLXRandom

private struct ProbeError: Error, CustomStringConvertible {
    let description: String
    init(_ text: String) { description = text }
}

private struct Options {
    let command: String
    let values: [String: String]
    init(_ args: [String]) throws {
        guard let command = args.first, ["smoke", "asr", "tts"].contains(command), args.count % 2 == 1 else {
            throw ProbeError("Use smoke|asr|tts with --key value options. See Experiments/QwenSwift/README.md")
        }
        let allowed: Set<String> = ["--models", "--lock", "--metallib", "--report", "--corpus", "--audio", "--repeat", "--voice", "--text", "--instructions", "--language", "--output", "--cancel-after-chunks", "--timeout"]
        var values: [String: String] = [:]
        for index in stride(from: 1, to: args.count, by: 2) {
            let key = args[index]
            guard allowed.contains(key), values[key] == nil else { throw ProbeError("Invalid or duplicate option: \(key)") }
            values[key] = args[index + 1]
        }
        self.command = command; self.values = values
    }
    func required(_ key: String) throws -> String {
        guard let value = values[key], !value.isEmpty else { throw ProbeError("Missing \(key)") }
        return value
    }
    func integer(_ key: String, default fallback: Int, range: ClosedRange<Int>) throws -> Int {
        guard let number = Int(values[key] ?? String(fallback)), range.contains(number) else { throw ProbeError("Invalid \(key)") }
        return number
    }
}

private struct Asset: Decodable { let path: String; let size: Int; let sha256: String }
private struct AssetLock: Decodable { let files: [Asset] }
private struct Utterance: Decodable {
    let id: String; let audio: String; let audio_sha256: String; let reference: String; let language: String
}

private func now() -> Double { Double(DispatchTime.now().uptimeNanoseconds) / 1e9 }
private func digest(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hash = SHA256()
    while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty { hash.update(data: data) }
    return hash.finalize().map { String(format: "%02x", $0) }.joined()
}
private func json(_ value: [String: Any], to path: String?) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys, .fragmentsAllowed])
    if let path {
        let url = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    } else { print(String(decoding: data, as: UTF8.self)) }
}
private func memory() -> [String: Any] {
    var usage = rusage(); getrusage(RUSAGE_SELF, &usage)
    return ["peak_rss_bytes": usage.ru_maxrss, "metal_peak_bytes": Memory.peakMemory, "metal_active_bytes": Memory.activeMemory]
}
private func host() -> [String: Any] {
    var length = 0
    sysctlbyname("machdep.cpu.brand_string", nil, &length, nil, 0)
    var value = [CChar](repeating: 0, count: max(length, 1))
    sysctlbyname("machdep.cpu.brand_string", &value, &length, nil, 0)
    return ["cpu": String(cString: value), "os": ProcessInfo.processInfo.operatingSystemVersionString,
            "physical_memory_bytes": ProcessInfo.processInfo.physicalMemory]
}
private func verify(_ root: URL, lock: URL, component: String) throws {
    let assets = try JSONDecoder().decode(AssetLock.self, from: Data(contentsOf: lock))
    let files = assets.files.filter { $0.path.hasPrefix(component + "/") }
    guard !files.isEmpty else { throw ProbeError("No locked assets for \(component)") }
    for asset in files {
        let url = root.appendingPathComponent(asset.path).resolvingSymlinksInPath()
        guard !asset.path.hasPrefix("/"), !asset.path.split(separator: "/").contains(".."),
              url.path.hasPrefix(root.resolvingSymlinksInPath().path + "/"),
              (try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.intValue == asset.size,
              try digest(url) == asset.sha256 else { throw ProbeError("Invalid model asset: \(asset.path)") }
    }
    let config = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent(component + "/config.json"))) as! [String: Any]
    let quantization = config["quantization"] as? [String: Any]
    guard config["model_type"] as? String == "qwen3_\(component)", quantization?["bits"] as? Int == 8 else {
        throw ProbeError("Expected Qwen3 \(component) 8-bit")
    }
    if component == "tts" {
        guard config["tts_model_type"] as? String == "custom_voice", config["tts_model_size"] as? String == "1b7" else {
            throw ProbeError("Expected 1.7B CustomVoice")
        }
    }
}
private func wav(_ pcm: Data) -> Data {
    var result = Data()
    func ascii(_ value: String) { result.append(contentsOf: value.utf8) }
    func u16(_ value: UInt16) { var little = value.littleEndian; withUnsafeBytes(of: &little) { result.append(contentsOf: $0) } }
    func u32(_ value: UInt32) { var little = value.littleEndian; withUnsafeBytes(of: &little) { result.append(contentsOf: $0) } }
    ascii("RIFF"); u32(UInt32(pcm.count + 36)); ascii("WAVEfmt "); u32(16); u16(1); u16(1)
    u32(24000); u32(48000); u16(2); u16(16); ascii("data"); u32(UInt32(pcm.count)); result.append(pcm)
    return result
}

@main
private struct Probe {
    static func main() async {
        var options: Options?
        do {
            let args = try Options(Array(CommandLine.arguments.dropFirst())); options = args
            if let path = args.values["--metallib"] { GPU.metallib = URL(fileURLWithPath: path) }
            try await Device.withDefaultDevice(.gpu) { try await run(args) }
        } catch {
            try? json(["status": "failed", "error": String(describing: error)], to: options?.values["--report"])
            fputs("Qwen Swift probe: \(error)\n", stderr)
            exit(1)
        }
    }

    static func run(_ args: Options) async throws {
        Memory.cacheLimit = 512 * 1024 * 1024
        if args.command == "smoke" {
            let result = (MLXArray([Float(1), 2, 3]) + 1).sum().item(Float.self)
            guard result == 9 else { throw ProbeError("GPU smoke mismatch") }
            try json(["status": "completed", "gpu_result": result, "host": host()], to: args.values["--report"])
            return
        }
        let root = URL(fileURLWithPath: try args.required("--models"), isDirectory: true)
        let verificationStart = now()
        try verify(root, lock: URL(fileURLWithPath: args.required("--lock")), component: args.command)
        let verificationMS = (now() - verificationStart) * 1000
        let repetitions = try args.integer("--repeat", default: 1, range: 1...10)
        var report: [String: Any] = ["status": "running", "backend": "mlx-swift", "configured_device": "gpu",
            "host": host(), "verification_ms": verificationMS,
            "sdk_revision": "927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe", "mlx_swift_version": "0.33.0"]
        let loadStart = now()
        if args.command == "asr" {
            let model = try await Qwen3ASRModel.fromModelDirectory(root.appendingPathComponent("asr"))
            report["load_ms"] = (now() - loadStart) * 1000
            let warmupStart = now()
            _ = model.generate(audio: MLXArray.zeros([16000]), maxTokens: 16, temperature: 0, language: "Chinese")
            report["warmup_ms"] = (now() - warmupStart) * 1000
            report["model"] = "qwen3-asr-1.7b"
            report["repository"] = "mlx-community/Qwen3-ASR-1.7B-8bit"
            report["revision"] = "a8379a2e2f9e313c9292cdf1af4055ab56d50d55"
            report["decoding"] = ["language": "auto", "max_tokens": 1024, "temperature": 0, "repetition_penalty": 1.0]
            report["tokenizer_sha256"] = try digest(root.appendingPathComponent("asr/tokenizer.json"))
            let items: [Utterance]; let audioRoot: URL
            if let path = args.values["--corpus"] {
                let url = URL(fileURLWithPath: path)
                items = try JSONDecoder().decode([Utterance].self, from: Data(contentsOf: url)); audioRoot = url.deletingLastPathComponent()
            } else {
                let audio = URL(fileURLWithPath: try args.required("--audio"))
                items = [Utterance(id: audio.lastPathComponent, audio: audio.lastPathComponent, audio_sha256: try digest(audio), reference: "", language: "auto")]
                audioRoot = audio.deletingLastPathComponent()
            }
            guard !items.isEmpty, Set(items.map(\.id)).count == items.count else { throw ProbeError("Empty corpus or duplicate IDs") }
            var results: [[String: Any]] = []
            for item in items { for repetition in 0..<repetitions {
                let audio = audioRoot.appendingPathComponent(item.audio).resolvingSymlinksInPath()
                guard audio.path.hasPrefix(audioRoot.resolvingSymlinksInPath().path + "/"), try digest(audio) == item.audio_sha256 else { throw ProbeError("Corpus hash/path mismatch") }
                let start = now()
                let (sampleRate, samples) = try loadAudioArray(from: audio, sampleRate: 16000)
                guard sampleRate == 16000 else { throw ProbeError("Unexpected ASR sample rate") }
                let output = model.generate(audio: samples, maxTokens: 1024, temperature: 0, language: nil)
                let elapsed = (now() - start) * 1000
                let language = ["Chinese": "zh", "English": "en"][output.language ?? ""] ?? output.language ?? "unknown"
                results.append(["id": item.id, "repetition": repetition, "audio_sha256": item.audio_sha256, "reference": item.reference,
                    "language": item.language, "text": output.text, "detected_language": language,
                    "milliseconds": elapsed, "generation_tokens": output.generationTokens, "prompt_tokens": output.promptTokens,
                    "sdk_seconds": output.totalTime])
                report["results"] = results; report.merge(memory()) { _, new in new }
                try json(report, to: args.values["--report"])
                fputs("ASR qwen-swift: \(results.count)/\(items.count * repetitions) \(item.id) \(String(format: "%.1f", elapsed))ms\n", stderr)
            } }
        } else {
            let modelRoot = root.appendingPathComponent("tts")
            let config = try JSONSerialization.jsonObject(with: Data(contentsOf: modelRoot.appendingPathComponent("config.json"))) as! [String: Any]
            let talker = config["talker_config"] as! [String: Any]
            let speakers = talker["spk_id"] as! [String: Int]
            let eos = talker["codec_eos_token_id"] as! Int
            let allVoices = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]
            let selected = args.values["--voice"] ?? "serena"
            let voices = selected == "all" ? allVoices : [selected]
            guard voices.allSatisfy({ allVoices.contains($0) && speakers[$0] != nil }) else { throw ProbeError("Invalid speaker") }
            let model = try await Qwen3TTSModel.fromModelDirectory(modelRoot)
            guard model.sampleRate == 24000 else { throw ProbeError("Expected 24kHz TTS") }
            report["load_ms"] = (now() - loadStart) * 1000
            report["model"] = "qwen3-tts-1.7b"; report["repository"] = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
            report["revision"] = "41d3337e8b7f2843a75841595fc14e4b9a7a4b96"
            report["tokenizer_sha256"] = try digest(modelRoot.appendingPathComponent("tokenizer.json"))
            report["streaming_interval_seconds"] = 0.32
            let text = args.values["--text"] ?? "今天下午三点十五分，请提醒我开会。"
            let instruction = args.values["--instructions"] ?? "请用自然、轻快的语气说话。"
            let language = args.values["--language"] ?? "chinese"
            let cancelAfter = try args.integer("--cancel-after-chunks", default: 0, range: 0...100)
            let timeout = try args.integer("--timeout", default: 120, range: 1...300)
            let outputRoot = URL(fileURLWithPath: try args.required("--output"), isDirectory: true)
            try FileManager.default.createDirectory(at: outputRoot, withIntermediateDirectories: true)
            var results: [[String: Any]] = []
            for voice in voices { for repetition in 0..<repetitions {
                let cancelThisRequest = cancelAfter > 0 && results.isEmpty
                let prompt = instruction.isEmpty ? voice : voice + ", " + instruction
                MLXRandom.seed(42)
                let start = now()
                let task = Task { () throws -> [String: Any] in
                    let stream = model.generateStream(text: text, voice: prompt, refAudio: nil, refText: nil, language: language,
                        generationParameters: GenerateParameters(maxTokens: 1200, temperature: 0.7, topP: 1, topK: 50, repetitionPenalty: 1.05), streamingInterval: 0.32)
                    var pcm = Data(); var chunks: [[String: Any]] = []; var tokens: [Int] = []
                    var eosVerified = false; var cancelled = false; var timedOut = false; var info: [String: Any] = [:]
                    do {
                        for try await event in stream {
                            if now() - start > Double(timeout) {
                                timedOut = true; withUnsafeCurrentTask { $0?.cancel() }; break
                            }
                            switch event {
                            case .token(let token): tokens.append(token); if token == eos { eosVerified = true }
                            case .info(let value): info = ["generation_tokens": value.generationTokenCount, "generate_seconds": value.generateTime, "tokens_per_second": value.tokensPerSecond]
                            case .audio(let audio):
                                let samples = audio.asArray(Float.self)
                                guard samples.allSatisfy(\.isFinite) else { throw ProbeError("Non-finite TTS output") }
                                var values = samples.map { Int16(max(-1, min(1, $0)) * 32767).littleEndian }
                                let bytes = values.withUnsafeMutableBytes { Data($0) }; pcm.append(bytes)
                                chunks.append(["bytes": bytes.count, "milliseconds": (now() - start) * 1000])
                                if cancelThisRequest && chunks.count >= cancelAfter {
                                    cancelled = true; withUnsafeCurrentTask { $0?.cancel() }; break
                                }
                            case .progress: break
                            }
                            if cancelled || timedOut { break }
                        }
                    } catch is CancellationError { cancelled = !timedOut }
                    let elapsed = now() - start; let duration = Double(pcm.count) / 48000
                    let file = outputRoot.appendingPathComponent("\(voice)-\(repetition)")
                    try pcm.write(to: file.appendingPathExtension("pcm"))
                    try wav(pcm).write(to: file.appendingPathExtension("wav"))
                    var result: [String: Any] = ["voice": voice, "speaker_token": speakers[voice]!, "repetition": repetition,
                        "text": text, "instructions": instruction, "language": language, "voice_prompt": prompt,
                        "status": timedOut ? "timeout" : cancelled ? "cancelled" : eosVerified ? "completed" : "generation_limit",
                        "eos_verified": eosVerified, "eos_token": eos, "tokens": tokens, "chunks": chunks, "pcm_bytes": pcm.count,
                        "sample_rate": 24000, "channels": 1, "format": "pcm_s16le", "milliseconds": elapsed * 1000,
                        "audio_seconds": duration, "real_time_factor": duration > 0 ? elapsed / duration : NSNull(),
                        "pcm_sha256": try digest(file.appendingPathExtension("pcm")), "audio_file": file.appendingPathExtension("wav").path]
                    if let first = chunks.first { result["first_chunk_ms"] = first["milliseconds"] }
                    result["sdk_info"] = info
                    return result
                }
                let result = try await task.value
                results.append(result); report["results"] = results; report.merge(memory()) { _, new in new }
                try json(report, to: args.values["--report"])
                fputs("TTS qwen-swift: \(voice) \(repetition) \(result["status"]!)\n", stderr)
                if result["status"] as? String == "cancelled" || result["status"] as? String == "timeout" {
                    // The SDK cancels its producer on stream termination but
                    // exposes no producer join. Record recovery after settling;
                    // this does not prove immediate cancellation acknowledgement.
                    try await Task.sleep(for: .seconds(2))
                    report["cancellation_settle_ms"] = 2000
                    report["producer_cancellation_join_verified"] = false
                }
                Memory.clearCache()
            } }
        }
        report["status"] = "completed"; report.merge(memory()) { _, new in new }
        try json(report, to: args.values["--report"])
    }
}
