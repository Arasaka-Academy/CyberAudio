import Foundation
import Darwin
import MLX

@main enum NativeOperatorProbe {
    static func equal(_ a: [Float], _ b: [Float]) -> Bool {
        zip(a, b).allSatisfy { $0.bitPattern == $1.bitPattern }
    }

    static func main() async throws {
        setbuf(stdout, nil)
        let args = CommandLine.arguments
        guard args.count == 4 else { fatalError("INPUT_SAFETENSORS METALLIB OUTPUT_JSON") }
        let outputURL = URL(fileURLWithPath: args[3])
        guard !FileManager.default.fileExists(atPath: outputURL.path) else { fatalError("Preserve existing evidence") }
        GPU.metallib = URL(fileURLWithPath: args[2])
        var results = [[String: Any]]()
        for device in [Device.cpu, Device.gpu] {
            let summary: [String: Any] = try await Device.withDefaultDevice(device) {
                let arrays = try MLX.loadArrays(url: URL(fileURLWithPath: args[1]))
                let rows = arrays["embedding_rows"]!, pads = arrays["pads"]!
                let native = arrays["native_next"]!, official = arrays["official_next"]!
                var frames = [[String: Any]]()
                var nativeMatches = 0, directMatches = 0, promotedMatches = 0
                for i in 0 ..< rows.dim(0) {
                    let embeddings = rows[i].reshaped(1, 16, 2048)
                    let pad = pads[i].reshaped(1, 1, 2048)
                    var sequential = embeddings[0..., 0 ..< 1, 0...]
                    for group in 1 ..< 16 {
                        sequential = sequential + embeddings[0..., group ..< (group + 1), 0...]
                    }
                    let direct = sum(embeddings, axis: 1, keepDims: true)
                    let promoted = sum(embeddings.asType(.float32), axis: 1, keepDims: true).asType(.bfloat16)
                    let nativeValues = (pad + sequential).asType(.float32).asArray(Float.self)
                    let directValues = (pad + direct).asType(.float32).asArray(Float.self)
                    let promotedValues = (pad + promoted).asType(.float32).asArray(Float.self)
                    let original = native[i].asType(.float32).asArray(Float.self)
                    let expected = official[i].asType(.float32).asArray(Float.self)
                    let a = equal(nativeValues, original), b = equal(directValues, expected), c = equal(promotedValues, expected)
                    nativeMatches += a ? 1 : 0; directMatches += b ? 1 : 0; promotedMatches += c ? 1 : 0
                    frames.append(["frame": i, "sequential_exact_native": a,
                        "bf16_sum_exact_official": b, "fp32_sum_exact_official": c])
                }
                return ["device": String(describing: device), "frames": frames,
                    "frame_count": rows.dim(0), "sequential_exact_native_frames": nativeMatches,
                    "bf16_sum_exact_official_frames": directMatches,
                    "fp32_sum_exact_official_frames": promotedMatches]
            }
            results.append(summary)
            print("OPERATOR", summary.filter { $0.key != "frames" })
        }
        let report: [String: Any] = ["status": "completed", "new_model_generations": 0, "results": results]
        try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: outputURL, options: .withoutOverwriting)
        guard results.allSatisfy({ $0["sequential_exact_native_frames"] as! Int == 367 && $0["fp32_sum_exact_official_frames"] as! Int == 367 }) else {
            fatalError("Native numerical verification failed; evidence preserved")
        }
    }
}
