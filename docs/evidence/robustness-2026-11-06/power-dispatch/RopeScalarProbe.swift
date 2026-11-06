import Foundation
import Darwin
import MLX

@main enum RopeScalarProbe {
    static func main() async {
        do {
            let args = CommandLine.arguments
            guard args.count == 4 else { exit(2) }
            GPU.metallib = URL(fileURLWithPath: args[1])
            let output = URL(fileURLWithPath: args[2])
            guard !FileManager.default.fileExists(atPath: output.path) else { exit(3) }
            try await Device.withDefaultDevice(.gpu) {
                let base: Float = 1_000_000
                let dim = 128
                let indices = MLXArray(stride(from: 0, to: dim, by: 2)).asType(.float32)
                let exponent = indices / Float(dim)
                let power = MLXArray(base).pow(exponent)
                let inverse = 1.0 / power
                let arrays = ["indices": indices, "exponent": exponent, "power": power, "inverse": inverse]
                MLX.eval(indices, exponent, power, inverse)
                try MLX.save(arrays: arrays, url: output)
                let original = try MLX.loadArrays(url: URL(fileURLWithPath: args[3]))["inv_freq"]!
                guard inverse.asArray(Float.self) == original.asArray(Float.self) else {
                    fputs("Standalone native formula does not reproduce captured frequency\n", stderr); exit(4)
                }
                print("Standalone original Swift formula reproduces every captured frequency value")
            }
        } catch { fputs("\(error)\n", stderr); exit(1) }
    }
}
