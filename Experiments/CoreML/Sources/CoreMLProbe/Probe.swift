import Foundation
import CoreML
import CryptoKit
import FluidAudio
import TTSKit
import WhisperKit
import CoreMLEvidence
import Darwin

struct ProbeError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

struct Options {
    let command: String
    var values: [String: String] = [:]
    init(_ args: [String]) throws {
        guard let first = args.first else { throw ProbeError("Commands: verify, asr, tts, compare, gate. See Experiments/CoreML/README.md.") }
        command = first
        var i = 1
        while i < args.count {
            guard args[i].hasPrefix("--"), i + 1 < args.count, !args[i + 1].hasPrefix("--"), values[args[i]] == nil else {
                throw ProbeError("Expected unique --option value pairs")
            }
            values[args[i]] = args[i + 1]
            i += 2
        }
        let common: Set<String> = ["--models", "--lock", "--report"]
        let allowed: Set<String>
        switch command {
        case "asr": allowed = common.union(["--repeat", "--audio", "--corpus", "--reference", "--language", "--text-norm", "--engine"])
        case "tts": allowed = common.union(["--repeat", "--text", "--voice", "--language", "--instructions", "--output", "--timeout", "--cancel-after-chunks", "--decoder-mode", "--multi-code-mode"])
        case "gate": allowed = ["--evidence", "--report"]
        case "compare": allowed = ["--baseline", "--candidate", "--report"]
        case "verify": allowed = common
        default: throw ProbeError("Unknown command")
        }
        guard Set(values.keys).isSubset(of: allowed) else { throw ProbeError("Unknown option") }
    }
    func required(_ key: String) throws -> String {
        guard let value = values[key], !value.isEmpty else { throw ProbeError("Missing " + key) }
        return value
    }
    func count() throws -> Int {
        guard let count = Int(values["--repeat"] ?? "1"), (1...100).contains(count) else { throw ProbeError("--repeat must be 1...100") }
        return count
    }
}

struct AssetLock: Decodable { let files: [Asset] }
struct Asset: Decodable {
    let path: String
    let size: Int
    let sha256: String?
    let git_blob_sha1: String?
}

struct Utterance: Decodable {
    let id: String
    let audio: String
    let reference: String
    let language: String
}

func verify(_ root: URL, lock: URL, component: String) throws {
    let manifest = try JSONDecoder().decode(AssetLock.self, from: Data(contentsOf: lock))
    guard !manifest.files.isEmpty else { throw ProbeError("Empty asset lock") }
    let prefix = root.resolvingSymlinksInPath().path + "/"
    for asset in manifest.files {
        if component == "asr" && !asset.path.hasPrefix("sensevoice/") { continue }
        if component == "tts" && asset.path.hasPrefix("sensevoice/") { continue }
        let file = root.appendingPathComponent(asset.path).resolvingSymlinksInPath()
        guard file.path.hasPrefix(prefix), !asset.path.split(separator: "/").contains(".."),
              let size = try FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int,
              size == asset.size else { throw ProbeError("Missing or invalid asset: " + asset.path) }
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var sha = SHA256()
        var git = Insecure.SHA1()
        git.update(data: Data("blob \(size)\0".utf8))
        while let block = try handle.read(upToCount: 1024 * 1024), !block.isEmpty {
            if asset.sha256 != nil { sha.update(data: block) } else { git.update(data: block) }
        }
        let digest = asset.sha256 != nil ? sha.finalize().map { String(format: "%02x", $0) }.joined() : git.finalize().map { String(format: "%02x", $0) }.joined()
        guard digest == (asset.sha256 ?? asset.git_blob_sha1) else { throw ProbeError("Checksum mismatch: " + asset.path) }
    }
}

func monotonicNow() -> Double { ProcessInfo.processInfo.systemUptime }
func memory() -> Int64 { var usage = rusage(); getrusage(RUSAGE_SELF, &usage); return Int64(usage.ru_maxrss) }
func host() -> [String: Any] {
    var size = 0
    sysctlbyname("machdep.cpu.brand_string", nil, &size, nil, 0)
    var cpu = [CChar](repeating: 0, count: max(size, 1))
    sysctlbyname("machdep.cpu.brand_string", &cpu, &size, nil, 0)
    return ["cpu": String(cString: cpu), "os": ProcessInfo.processInfo.operatingSystemVersionString,
            "physical_memory_bytes": ProcessInfo.processInfo.physicalMemory]
}
func json(_ value: [String: Any], to path: String? = nil) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted])
    if let path {
        let url = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    }
    print(String(decoding: data, as: UTF8.self))
}

func pcm(_ samples: [Float]) throws -> Data {
    guard samples.allSatisfy(\.isFinite) else { throw ProbeError("Non-finite generated audio") }
    var data = Data(capacity: samples.count * 2)
    for sample in samples {
        var value = Int16(max(-1, min(1, sample)) * 32767).littleEndian
        withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
    }
    return data
}

func wav(_ pcm: Data) -> Data {
    var data = Data()
    func string(_ s: String) { data.append(contentsOf: s.utf8) }
    func u32(_ n: UInt32) { var v = n.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
    func u16(_ n: UInt16) { var v = n.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
    string("RIFF"); u32(UInt32(pcm.count) + 36); string("WAVEfmt "); u32(16)
    u16(1); u16(1); u32(24000); u32(48000); u16(2); u16(16); string("data"); u32(UInt32(pcm.count)); data.append(pcm)
    return data
}

/// A bounded, synchronous callback records each real audio frame. No player or
/// whole-utterance buffering is used to infer time to first audio.
final class ChunkRecorder: @unchecked Sendable {
    let start = monotonicNow()
    let timeout: Double
    let cancelAfter: Int
    private let lock = NSLock()
    private(set) var first: Double?
    private(set) var times: [Double] = []
    private(set) var byteCounts: [Int] = []
    private(set) var audio = Data()
    private(set) var stopped = false
    private(set) var stopReason: String?
    private(set) var sdkStopReason: String?
    private(set) var invalid = false
    init(timeout: Double, cancelAfter: Int) { self.timeout = timeout; self.cancelAfter = cancelAfter }
    func receiveSDKLog(_ message: String) {
        guard message.hasPrefix("Loop stopped: ") else { return }
        lock.lock(); defer { lock.unlock() }
        sdkStopReason = String(message.dropFirst("Loop stopped: ".count))
    }
    func receive(_ progress: SpeechProgress) -> Bool? {
        lock.lock(); defer { lock.unlock() }
        let elapsed = monotonicNow() - start
        if !progress.audio.isEmpty {
            if first == nil { first = elapsed }
            times.append(elapsed)
            do {
                let block = try pcm(progress.audio)
                audio.append(block); byteCounts.append(block.count)
            } catch { invalid = true }
        }
        stopReason = invalid ? "invalid_audio" : elapsed > timeout ? "timeout" :
            (cancelAfter > 0 && times.count >= cancelAfter) ? "cancelled" : nil
        stopped = stopReason != nil
        return !stopped
    }
}

@main
struct Probe {
    static func main() async {
        var options: Options?
        do {
            let args = try Options(Array(CommandLine.arguments.dropFirst())); options = args
            if args.command == "gate" {
                let evidence = try JSONDecoder().decode(AcceptanceEvidence.self, from: Data(contentsOf: URL(fileURLWithPath: args.required("--evidence"))))
                try json(["accepted": evidence.accepted, "missing": evidence.missing], to: args.values["--report"])
                if !evidence.accepted { exit(2) }
                return
            }
            if args.command == "compare" {
                func report(_ key: String) throws -> ASRReport {
                    let report = try JSONDecoder().decode(ASRReport.self, from: Data(contentsOf: URL(fileURLWithPath: args.required(key))))
                    guard report.status == "completed" else { throw ProbeError("Cannot compare an incomplete ASR report") }
                    return report
                }
                let baseline = try report("--baseline"), candidate = try report("--candidate")
                let comparison = try ASRComparison(baseline: baseline.results, candidate: candidate.results)
                var output = try JSONSerialization.jsonObject(with: JSONEncoder().encode(comparison)) as! [String: Any]
                output["baseline_model"] = baseline.model; output["candidate_model"] = candidate.model
                try json(output, to: args.values["--report"])
                return
            }
            guard ["verify", "asr", "tts"].contains(args.command) else { throw ProbeError("Unknown command") }
            let engine = args.values["--engine"] ?? "sensevoice"
            guard ["sensevoice", "whisper-turbo"].contains(engine) else { throw ProbeError("Invalid ASR engine") }
            let root = URL(fileURLWithPath: try args.required("--models"), isDirectory: true)
            let verifyStart = monotonicNow()
            try verify(root, lock: URL(fileURLWithPath: args.required("--lock")), component: engine == "whisper-turbo" ? "all" : args.command)
            let verificationMilliseconds = (monotonicNow() - verifyStart) * 1000
            if args.command == "verify" { try json(["verified": true], to: args.values["--report"]); return }
            let count = try args.count()
            let loadStart = monotonicNow()
            if args.command == "asr" {
                let transcribe: (URL) async throws -> (text: String, metadata: [String: Any])
                let model: String
                let textNorm: String
                if engine == "whisper-turbo" {
                    guard args.values["--text-norm"] == nil else { throw ProbeError("--text-norm is SenseVoice-specific") }
                    // Exact local ordinary Turbo variant, including its local
                    // tokenizer. The benchmark also runs in a network-denied
                    // sandbox so an SDK tokenizer fallback cannot fetch assets.
                    let manager = try await WhisperKit(WhisperKitConfig(
                        modelFolder: root.appendingPathComponent("whisper").path,
                        tokenizerFolder: root.appendingPathComponent("whisper-tokenizer"),
                        computeOptions: ModelComputeOptions(melCompute: .cpuOnly,
                            audioEncoderCompute: .cpuAndNeuralEngine, textDecoderCompute: .cpuAndNeuralEngine),
                        verbose: false, logLevel: .none, prewarm: true, load: true, download: false))
                    let decoding = DecodingOptions(task: .transcribe, language: nil,
                        temperature: 0, temperatureFallbackCount: 5, detectLanguage: true,
                        skipSpecialTokens: true, withoutTimestamps: true, wordTimestamps: false,
                        concurrentWorkerCount: 1)
                    transcribe = { audio in
                        let parts = try await manager.transcribe(audioPath: audio.path, decodeOptions: decoding)
                        return (parts.map(\.text).joined(separator: " "), [
                            "detected_languages": parts.map(\.language),
                            "decoding_fallbacks": parts.reduce(0) { $0 + $1.timings.totalDecodingFallbacks },
                            "encoding_ms": parts.reduce(0) { $0 + $1.timings.encoding } * 1000,
                            "decoding_ms": parts.reduce(0) { $0 + $1.timings.decodingLoop } * 1000])
                    }
                    model = "whisper-large-v3-turbo"
                    textNorm = "model-output"
                } else {
                    // Explicit local load: no ModelHub download/recovery or .all fallback.
                    let models = try SenseVoiceModels.load(from: root.appendingPathComponent("sensevoice"), precision: .fp16)
                    textNorm = args.values["--text-norm"] ?? "with-itn"
                    guard ["with-itn", "without-itn"].contains(textNorm) else { throw ProbeError("Invalid text normalization mode") }
                    // SenseVoice's embedding indices: withitn=14, woitn=15.
                    let manager = SenseVoiceManager(models: models, textNorm: textNorm == "with-itn" ? 14 : 15)
                    transcribe = { audio in (try await manager.transcribe(audioURL: audio), [:]) }
                    model = "sensevoice-small"
                }
                let loaded = monotonicNow() - loadStart
                let items: [Utterance]
                let audioRoot: URL
                if let corpus = args.values["--corpus"] {
                    let url = URL(fileURLWithPath: corpus)
                    items = try JSONDecoder().decode([Utterance].self, from: Data(contentsOf: url))
                    audioRoot = url.deletingLastPathComponent()
                    guard !items.isEmpty, Set(items.map(\.id)).count == items.count else { throw ProbeError("Empty corpus or duplicate utterance IDs") }
                } else {
                    let url = URL(fileURLWithPath: try args.required("--audio"))
                    items = [Utterance(id: url.lastPathComponent, audio: url.lastPathComponent,
                        reference: args.values["--reference"] ?? "", language: args.values["--language"] ?? "auto")]
                    audioRoot = url.deletingLastPathComponent()
                }
                var results: [[String: Any]] = []
                for item in items { for repetition in 0..<count {
                    let audio = audioRoot.appendingPathComponent(item.audio)
                    let hash = SHA256.hash(data: try Data(contentsOf: audio)).map { String(format: "%02x", $0) }.joined()
                    let start = monotonicNow()
                    let transcription = try await transcribe(audio)
                    let text = transcription.text
                    var result: [String: Any] = ["id": item.id, "repetition": repetition, "audio_sha256": hash,
                        "text": text, "reference": item.reference, "language": item.language, "milliseconds": (monotonicNow() - start) * 1000]
                    result.merge(transcription.metadata) { _, new in new }
                    if !item.reference.isEmpty {
                        result["cer"] = TextScore.characterErrorRate(reference: item.reference, hypothesis: text)
                        result["wer"] = TextScore.wordErrorRate(reference: item.reference, hypothesis: text)
                    }
                    results.append(result)
                    fputs("ASR \(engine): \(results.count)/\(items.count * count) \(item.id)\n", stderr)
                } }
                var output: [String: Any] = ["status": "completed", "model": model, "configured_compute_units": "cpuAndNeuralEngine", "ane_trace_verified": false,
                          "host": host(), "verification_ms": verificationMilliseconds, "text_norm": textNorm,
                          "load_ms": loaded * 1000, "peak_rss_bytes": memory(), "results": results]
                if engine == "whisper-turbo" {
                    output["variant"] = "openai_whisper-large-v3-v20240930_turbo"
                    output["decoding"] = ["task": "transcribe", "language": "auto", "temperature": 0,
                        "temperature_fallback_count": 5, "without_timestamps": true, "workers": 1]
                    output["mel_compute_units"] = "cpuOnly"
                    output["prewarm"] = true
                }
                try json(output, to: args.values["--report"])
            } else {
                guard let decoderMode = Qwen3SpeechDecoderMode.allCases.first(where: {
                    $0.functionName == (args.values["--decoder-mode"] ?? "latency")
                }), let multiCodeMode = Qwen3MultiCodeDecoderMode(rawValue: args.values["--multi-code-mode"] ?? "stepped") else {
                    throw ProbeError("Invalid decoder mode")
                }
                let config = TTSKitConfig(model: .qwen3TTS_1_7b, modelFolder: root.appendingPathComponent("ttskit"),
                    tokenizerFolder: root.appendingPathComponent("tokenizer"),
                    speechDecoderMode: decoderMode, multiCodeDecoderMode: multiCodeMode,
                    computeOptions: ComputeOptions(embedderComputeUnits: .cpuOnly, codeDecoderComputeUnits: .cpuAndNeuralEngine,
                        multiCodeDecoderComputeUnits: .cpuAndNeuralEngine, speechDecoderComputeUnits: .cpuAndNeuralEngine),
                    verbose: false, download: false, prewarm: true, load: true, seed: 42)
                let tts = try await TTSKit(config)
                let loaded = monotonicNow() - loadStart
                var parameters = GenerationOptions()
                parameters.concurrentWorkerCount = 1
                parameters.chunkingStrategy = TextChunkingStrategy.none
                parameters.maxNewTokens = 1200
                parameters.instruction = args.values["--instructions"]
                let text = try args.required("--text")
                let voice = args.values["--voice"] ?? "serena"
                let allVoices = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]
                guard voice == "all" || allVoices.contains(voice) else { throw ProbeError("Unknown voice") }
                let voices = voice == "all" ? allVoices : [voice]
                let output = URL(fileURLWithPath: try args.required("--output"), isDirectory: true)
                try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
                guard let timeout = Double(args.values["--timeout"] ?? "120"), timeout.isFinite,
                      timeout > 0 && timeout <= 600 else { throw ProbeError("Invalid timeout") }
                guard let cancel = Int(args.values["--cancel-after-chunks"] ?? "0"), (0...1200).contains(cancel) else { throw ProbeError("Invalid cancellation limit") }
                var results: [[String: Any]] = []
                for voice in voices {
                    // The existing API uses underscores. TTSKit uses hyphens
                    // and silently falls back to Ryan for an unknown raw value.
                    guard let speaker = Qwen3Speaker(rawValue: voice.replacingOccurrences(of: "_", with: "-")) else {
                        throw ProbeError("Unsupported SDK speaker: " + voice)
                    }
                    for i in 0..<count {
                        let recorder = ChunkRecorder(timeout: timeout, cancelAfter: results.isEmpty ? cancel : 0)
                        // The SDK can return normally on a token-ratio or KV
                        // limit. Only its pinned EOS stop reason proves a full
                        // utterance; intercept info logs without console I/O.
                        Logging.shared.logLevel = .info
                        Logging.shared.loggingCallback = { recorder.receiveSDKLog($0) }
                        let result = try await tts.generate(text: text, voice: speaker.rawValue, language: args.values["--language"] ?? "chinese",
                            options: parameters, callback: { recorder.receive($0) })
                        let seconds = monotonicNow() - recorder.start
                        let full = try pcm(result.audio)
                        guard result.sampleRate == 24000, !full.isEmpty, !recorder.invalid else { throw ProbeError("Invalid TTS output") }
                        let prefix = output.appendingPathComponent("\(voice)-\(i)")
                        try wav(full).write(to: prefix.appendingPathExtension("wav"))
                        try recorder.audio.write(to: prefix.appendingPathExtension("pcm"))
                        let eos = recorder.sdkStopReason?.hasPrefix("EOS token after ") == true
                        let row: [String: Any] = ["voice": voice, "sdk_voice": speaker.rawValue, "sdk_speaker_token": speaker.tokenID,
                            "status": recorder.stopReason ?? (eos ? "completed" : recorder.sdkStopReason == nil ? "unverified_completion" : "generation_limit"),
                            "eos_verified": eos, "sdk_stop_reason": recorder.sdkStopReason ?? "not_reported", "decoding_steps": result.timings.totalDecodingLoops,
                            "seconds": seconds, "audio_seconds": result.audioDuration, "rtf": seconds / result.audioDuration,
                            "first_audio_ms": (recorder.first ?? seconds) * 1000, "chunk_times_seconds": recorder.times, "chunk_bytes": recorder.byteCounts,
                            "stream_bytes": recorder.audio.count, "result_bytes": full.count, "stream_matches_result": recorder.audio == full,
                            "wav_file": prefix.appendingPathExtension("wav").path, "pcm_file": prefix.appendingPathExtension("pcm").path]
                        results.append(row)
                        try json(["sample": row])
                    }
                }
                try json(["status": results.allSatisfy { $0["status"] as? String == "completed" } ? "completed" : "incomplete",
                    "model": "qwen3-tts-1.7b-customvoice", "configured_compute_units": "cpuAndNeuralEngine", "ane_trace_verified": false,
                    "host": host(), "verification_ms": verificationMilliseconds, "text": text,
                    "language": args.values["--language"] ?? "chinese", "instructions": args.values["--instructions"] ?? "",
                    "seed": 42, "workers": 1, "max_tokens": 1200, "sample_rate": 24000,
                    "speech_decoder_mode": decoderMode.functionName, "multi_code_decoder_mode": multiCodeMode.rawValue,
                    "load_ms": loaded * 1000, "peak_rss_bytes": memory(), "results": results], to: args.values["--report"])
            }
        } catch {
            try? json(["status": "failed", "error": String(describing: error), "peak_rss_bytes": memory()], to: options?.values["--report"])
            exit(1)
        }
    }
}
