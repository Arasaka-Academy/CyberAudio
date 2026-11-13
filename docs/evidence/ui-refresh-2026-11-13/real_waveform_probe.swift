import Foundation

@main
struct RealWaveformProbe {
    static func main() throws {
        var results: [[String: Any]] = []
        for path in CommandLine.arguments.dropFirst() {
            let waveform = try AudioWaveform.decodeWAV(Data(contentsOf: URL(fileURLWithPath: path)))
            guard waveform.duration > 0, !waveform.samples.isEmpty, waveform.samples.count <= 1026,
                  waveform.samples.min()! < 0, waveform.samples.max()! > 0 else { fatalError("Invalid measured waveform") }
            results.append(["file": URL(fileURLWithPath: path).lastPathComponent, "duration": waveform.duration,
                            "display_samples": waveform.samples.count, "min": waveform.samples.min()!, "max": waveform.samples.max()!])
        }
        print(String(data: try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
    }
}
