import Foundation
import CryptoKit
import Darwin
import MLX
import MLXRandom

@main enum SamplingParityProbe {
    struct Failure: Error { let message: String }
    static func sha(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func main() {
        setbuf(stdout, nil)
        do { try run() }
        catch { fputs("Sampler capture failed: \(error)\n", stderr); exit(1) }
    }
    static func run() throws {
        let args = CommandLine.arguments
        guard args.count == 5 else { throw Failure(message: "CAPTURE_ROOT METALLIB CONFIG NEW_OUTPUT_ROOT") }
        let fm = FileManager.default
        let captures = URL(fileURLWithPath: args[1], isDirectory: true)
        let root = URL(fileURLWithPath: args[4], isDirectory: true)
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Output exists") }
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        GPU.metallib = URL(fileURLWithPath: args[2])
        let input = try JSONSerialization.jsonObject(with: Data(contentsOf: captures.appendingPathComponent("report.json"))) as! [String: Any]
        let configuration = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[3]))) as! [String: Any]
        let config = configuration["talker_config"] as! [String: Any]
        let eos = config["codec_eos_token_id"] as! Int, vocab = config["vocab_size"] as! Int
        let suppressed = (vocab-1024..<vocab).filter { $0 != eos }
        let start = ProcessInfo.processInfo.systemUptime
        var report: [String: Any] = ["status": "running", "source_report_sha256": sha(try Data(contentsOf: captures.appendingPathComponent("report.json")))]
        var results = [[String: Any]](), calls = 0
        func save() throws {
            report["results"] = results; report["calls"] = calls
            report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - start
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        try save()
        do {
            for captured in input["results"] as! [[String: Any]] {
                let item = captured["case"] as! [String: Any]
                let id = captured["id"] as! String
                MLXRandom.seed(UInt64(item["seed"] as! Int))
                let directory = root.appendingPathComponent(id, isDirectory: true)
                try fm.createDirectory(at: directory, withIntermediateDirectories: false)
                var history = [Int](), steps = [[String: Any]]()
                for metadata in captured["steps"] as! [[String: Any]] {
                    guard ProcessInfo.processInfo.systemUptime - start < 120 else { throw Failure(message: "Budget") }
                    let index = metadata["step"] as! Int
                    let path = captures.appendingPathComponent(id).appendingPathComponent(metadata["file"] as! String)
                    guard sha(try Data(contentsOf: path)) == metadata["sha256"] as! String else { throw Failure(message: "Capture changed") }
                    let arrays = try MLX.loadArrays(url: path)
                    let expected = arrays["codes"]!.asType(Int32.self).asArray(Int32.self).map(Int.init)
                    var filtered = [MLXArray](), codes = [Int]()
                    for group in 0..<16 {
                        let logits = group == 0 ? arrays["logits"]! : arrays["code_logits"]![0..., (group-1)..<group, 0...]
                        let token = sampleToken(logits, temperature: 0.7, topP: 1, topK: 50,
                            repetitionPenalty: group == 0 ? 1.05 : 1,
                            generatedTokens: group == 0 ? history : nil,
                            suppressTokens: group == 0 ? suppressed : nil,
                            eosTokenId: group == 0 ? eos : nil,
                            onFiltered: { filtered.append($0) })
                        eval(token)
                        let value = Int(token.item(Int32.self))
                        guard value == expected[group] else { throw Failure(message: "Native RNG/trajectory differs: \(id) step \(index) group \(group), expected \(expected[group]), actual \(value)") }
                        codes.append(value); calls += 1
                    }
                    let file = String(format: "filtered-%03d.safetensors", index)
                    let output = directory.appendingPathComponent(file)
                    try MLX.save(arrays: ["main": filtered[0], "auxiliary": stacked(Array(filtered.dropFirst()), axis: 1)], url: output)
                    steps.append(["step": index, "file": file, "sha256": sha(try Data(contentsOf: output)), "codes": codes])
                    history.append(codes[0])
                }
                results.append(["id": id, "steps": steps, "all_codes_match": true])
                try save(); Memory.clearCache()
                print("SAMPLED", id, steps.count, "steps")
            }
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        try save()
    }
    static func sampleToken(
        _ logits: MLXArray,
        temperature: Float = 0.9,
        topP: Float = 1.0,
        topK: Int = 50,
        repetitionPenalty: Float = 1.0,
        generatedTokens: [Int]? = nil,
        suppressTokens: [Int]? = nil,
        eosTokenId: Int? = nil,
        minP: Float = 0.0,
        onFiltered: (MLXArray) -> Void
    ) -> MLXArray {
        var logitsSlice = logits[0..., (-1)..., 0...].squeezed(axis: 1) // [batch, vocab_size]

        // Suppress tokens by setting to -inf
        if let suppress = suppressTokens, !suppress.isEmpty {
            let suppressArr = MLXArray(suppress.map { Int32($0) }).reshaped(1, -1)
            let negInf = MLXArray.full([1, suppress.count], values: MLXArray(-Float.infinity), dtype: logitsSlice.dtype)
            logitsSlice = putAlong(logitsSlice, suppressArr, values: negInf, axis: -1)
        }

        // Repetition penalty
        if let tokens = generatedTokens, !tokens.isEmpty, repetitionPenalty != 1.0 {
            let unique = Array(Set(tokens)).filter { $0 < logitsSlice.dim(-1) }
            if !unique.isEmpty {
                let tokenIds = MLXArray(unique.map { Int32($0) }).reshaped(1, -1)
                let selected = takeAlong(logitsSlice, tokenIds, axis: -1)
                let penalized = which(
                    selected .< 0,
                    selected * repetitionPenalty,
                    selected / repetitionPenalty
                )
                logitsSlice = putAlong(logitsSlice, tokenIds, values: penalized, axis: -1)
            }
        }

        // Greedy if temperature 0
        if temperature <= 0 {
            return argMax(logitsSlice, axis: -1, keepDims: true)
        }

        // Preserve EOS logit so top-k/top-p/min-p do not permanently suppress it.
        let eosLogit: MLXArray? = if let eosTokenId, eosTokenId >= 0, eosTokenId < logitsSlice.dim(-1) {
            logitsSlice[0..., eosTokenId ..< (eosTokenId + 1)]
        } else {
            nil
        }

        // Apply top-k filtering (match mlx_lm.apply_top_k ordering and masking semantics)
        var filteredLogits = logitsSlice
        let vocabSize = logitsSlice.dim(-1)
        if topK > 0, topK < vocabSize {
            let kth = min(topK - 1, max(vocabSize - 1, 0))
            if kth >= 0 {
                let maskIdx = argPartition(-logitsSlice, kth: kth, axis: -1)[0..., topK...]
                let negInf = MLXArray.full(maskIdx.shape, values: MLXArray(-Float.infinity), dtype: logitsSlice.dtype)
                filteredLogits = putAlong(filteredLogits, maskIdx, values: negInf, axis: -1)
            }
        }

        // Apply top-p (nucleus) sampling
        if topP > 0, topP < 1.0 {
            let probs = softmax(filteredLogits, axis: -1)

            // Sort in ASCENDING order (like Python)
            let sortedIndices = argSort(filteredLogits, axis: -1)
            let sortedProbs = takeAlong(probs, sortedIndices, axis: -1)

            // Cumulative probabilities
            let cumProbs = cumsum(sortedProbs, axis: -1)

            // Rearrange cumulative probs back to original order
            // Create inverse index mapping using putAlong
            let vocabSize = sortedIndices.dim(-1)
            let arangeIndices = MLXArray(0 ..< vocabSize).reshaped(1, -1).asType(Int32.self)
            let zeros = MLXArray.zeros(sortedIndices.shape, type: Int32.self)
            let inverseIndices = putAlong(zeros, sortedIndices, values: arangeIndices, axis: -1)
            let cumProbsOrigOrder = takeAlong(cumProbs, inverseIndices, axis: -1)

            // Mask tokens where cumulative prob > (1 - top_p)
            // Keep tokens that are in the top_p nucleus
            let threshold = 1.0 - topP
            let mask = cumProbsOrigOrder .> threshold
            let negInf = MLXArray.full(filteredLogits.shape, values: MLXArray(-Float.infinity), dtype: filteredLogits.dtype)
            filteredLogits = which(mask, filteredLogits, negInf)
        }

        // Apply min-p sampling behavior (default kept at 0.0 for now)
        if minP > 0.0 {
            let scaledMinP = Float(log(Double(minP)))
            // Indices sorted in descending order (like Python `argsort(-logits)`)
            let sortedIndices = argSort(-filteredLogits, axis: -1)
            let sortedLogits = takeAlong(filteredLogits, sortedIndices, axis: -1)
            let topLogits = sortedLogits[0..., 0 ..< 1]
            let scaledMinPArray = MLXArray.full(
                topLogits.shape,
                values: MLXArray(scaledMinP),
                dtype: sortedLogits.dtype
            ) + topLogits
            let removeMask = sortedLogits .< scaledMinPArray
            let negInf = MLXArray.full(sortedLogits.shape, values: MLXArray(-Float.infinity), dtype: sortedLogits.dtype)
            let filteredSortedLogits = which(removeMask, negInf, sortedLogits)

            let invArange = MLXArray(0 ..< vocabSize).reshaped(1, -1).asType(Int32.self)
            let inverseIndices = putAlong(MLXArray.zeros(sortedIndices.shape, type: Int32.self), sortedIndices, values: invArange, axis: -1)
            filteredLogits = takeAlong(filteredSortedLogits, inverseIndices, axis: -1)
        }

        if let eosLogit, let eosTokenId {
            let eosIdx = MLXArray([Int32(eosTokenId)]).reshaped(1, 1)
            filteredLogits = putAlong(filteredLogits, eosIdx, values: eosLogit, axis: -1)
        }

        // Sample with temperature
        onFiltered(filteredLogits / temperature)
        let token = categorical(filteredLogits / temperature)
        return token.reshaped(1, 1)
    }


}
