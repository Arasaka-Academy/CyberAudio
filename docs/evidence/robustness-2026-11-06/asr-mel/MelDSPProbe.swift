import Foundation
import Darwin
import CryptoKit
import MLX
import MLXAudioCore

@main enum MelDSPProbe {
    struct Failure: Error { let message: String }
    static func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func main() {
        setbuf(stdout, nil)
        do { try run() } catch { fputs("Mel DSP capture: \(error)\n", stderr); exit(1) }
    }
    static func run() throws {
        let args = CommandLine.arguments
        guard args.count == 5 else { throw Failure(message: "METALLIB PLAN CONSTANTS NEW_OUTPUT") }
        let fm = FileManager.default
        let root = URL(fileURLWithPath: args[4], isDirectory: true)
        guard !fm.fileExists(atPath: root.path) else { throw Failure(message: "Preserve earlier output") }
        let planData = try Data(contentsOf: URL(fileURLWithPath: args[2]))
        let plan = try JSONSerialization.jsonObject(with: planData) as! [String: Any]
        try fm.createDirectory(at: root, withIntermediateDirectories: false)
        GPU.metallib = URL(fileURLWithPath: args[1]); Memory.cacheLimit = 128 * 1024 * 1024
        let began = ProcessInfo.processInfo.systemUptime
        var rows = [[String: Any]]()
        var report: [String: Any] = ["status": "running", "model_loaded": false, "asr_generations": 0, "plan_sha256": sha(planData)]
        func save() throws {
            report["results"] = rows; report["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("report.json"), options: .atomic)
        }
        func norm(_ power: MLXArray) -> MLXArray {
            var value = MLX.maximum(power, MLXArray(Float(1e-10)))
            value = MLX.log10(value)
            value = MLX.maximum(value, value.max() - MLXArray(Float(8)))
            return (value + MLXArray(Float(4))) / MLXArray(Float(4))
        }
        try save()
        do {
            try Device.withDefaultDevice(.gpu) {
                let reference = try MLX.loadArrays(url: URL(fileURLWithPath: args[3]))
                let window = hanningWindow(size: 400, periodic: true)
                let filters = melFilters(sampleRate: 16000, nFft: 400, nMels: 128, norm: "slaney", melScale: .slaney)
                try MLX.save(arrays: ["window": window, "filters": filters], url: root.appendingPathComponent("constants.safetensors"))
                var totalBytes = 0
                for item in plan["cases"] as! [[String: Any]] {
                    let id = item["id"] as! String
                    let source = URL(fileURLWithPath: item["native_file"] as! String)
                    guard sha(try Data(contentsOf: source)) == item["native_sha256"] as! String else { throw Failure(message: "Input changed") }
                    let original = try MLX.loadArrays(url: source)
                    let audio = original["audio"]!
                    let baseline = computeMelSpectrogram(audio: audio, sampleRate: 16000, nFft: 400, hopLength: 160, nMels: 128, melScale: .slaney, hannPeriodic: true)
                    guard baseline.transposed(1, 0).expandedDimensions(axis: 0).asArray(Float.self) == original["mel"]!.asArray(Float.self) else { throw Failure(message: "Original public Mel did not reproduce") }
                    let spectrum = stft(audio: audio, window: window, nFft: 400, hopLength: 160)
                    let kept = spectrum[0..<(spectrum.dim(0) - 1)]
                    let power = MLX.abs(kept).square(); MLX.eval(power)
                    let melPower = MLX.matmul(power, filters); MLX.eval(melPower)
                    let decomposed = norm(melPower)
                    guard decomposed.asArray(Float.self) == baseline.asArray(Float.self) else { throw Failure(message: "Native decomposition does not reproduce") }
                    let refSpectrum = stft(audio: audio, window: reference["window"]!, nFft: 400, hopLength: 160)
                    let refKept = refSpectrum[0..<(refSpectrum.dim(0) - 1)]
                    let refPower = MLX.abs(refKept).square(); MLX.eval(refPower)
                    let windowOnly = MLX.matmul(refPower, filters); MLX.eval(windowOnly)
                    let filtersOnly = MLX.matmul(power, reference["filters"]!); MLX.eval(filtersOnly)
                    let both = MLX.matmul(refPower, reference["filters"]!); MLX.eval(both)
                    let arrays = ["fft_real": kept.realPart(), "fft_imag": kept.imaginaryPart(), "power": power,
                                  "mel_power": melPower, "mel": decomposed, "reference_window_power": refPower,
                                  "reference_window_mel": norm(windowOnly), "reference_filters_mel": norm(filtersOnly),
                                  "reference_constants_mel": norm(both)]
                    let file = root.appendingPathComponent(id + ".safetensors")
                    try MLX.save(arrays: arrays, url: file)
                    let data = try Data(contentsOf: file); totalBytes += data.count
                    guard totalBytes < plan["snapshot_budget_bytes"] as! Int,
                          ProcessInfo.processInfo.systemUptime - began < 180 else { throw Failure(message: "Diagnostic budget") }
                    rows.append(["id": id, "file": file.lastPathComponent, "bytes": data.count, "sha256": sha(data),
                                 "original_public_mel_exact": true, "original_decomposition_exact": true])
                    try save(); Memory.clearCache(); print("MEL", id)
                }
            }
            let constantsData = try Data(contentsOf: root.appendingPathComponent("constants.safetensors"))
            report["constants_sha256"] = sha(constantsData); report["constants_bytes"] = constantsData.count
            report["status"] = "diagnostic_completed"
        } catch { report["status"] = "failed"; report["error"] = String(describing: error); try save(); throw error }
        try save()
    }
}
