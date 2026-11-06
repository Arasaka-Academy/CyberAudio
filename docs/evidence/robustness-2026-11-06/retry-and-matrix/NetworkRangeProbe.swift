import Foundation
import CryptoKit

@main enum RangeProbe {
    static func main() async {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 45
        configuration.timeoutIntervalForResource = 55
        configuration.connectionProxyDictionary = ["HTTPEnable":1,"HTTPProxy":"127.0.0.1","HTTPPort":7890,
                                                     "HTTPSEnable":1,"HTTPSProxy":"127.0.0.1","HTTPSPort":7890]
        let session = URLSession(configuration:configuration)
        defer { session.invalidateAndCancel() }
        let source = URL(string:"https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/resolve/41d3337e8b7f2843a75841595fc14e4b9a7a4b96/model.safetensors?download=true")!
        var request = URLRequest(url:source,cachePolicy:.reloadIgnoringLocalCacheData)
        request.setValue("bytes=117440512-125829119",forHTTPHeaderField:"Range")
        request.setValue("identity",forHTTPHeaderField:"Accept-Encoding")
        let began = Date()
        var report: [String:Any] = ["transport":"URLSessionDownloadTask", "requested_range":"117440512-125829119",
                                  "maximum_resource_seconds":55,"tls_validation":"default"]
        do {
            let (temporary,response) = try await session.download(for:request)
            defer { try? FileManager.default.removeItem(at:temporary) }
            let data = try Data(contentsOf:temporary)
            report["bytes"] = data.count
            report["sha256"] = SHA256.hash(data:data).map { String(format:"%02x",$0) }.joined()
            if let http = response as? HTTPURLResponse {
                report["status"] = http.statusCode
                report["content_range"] = http.value(forHTTPHeaderField:"Content-Range")
            }
        } catch {
            let failure = error as NSError
            report["error_domain"] = failure.domain
            report["error_code"] = failure.code
            if let underlying = failure.userInfo[NSUnderlyingErrorKey] as? NSError {
                report["underlying_domain"] = underlying.domain
                report["underlying_code"] = underlying.code
            }
        }
        report["elapsed_seconds"] = Date().timeIntervalSince(began)
        if let data = try? JSONSerialization.data(withJSONObject:report,options:[.sortedKeys]) {
            print(String(decoding:data,as:UTF8.self))
        }
    }
}
