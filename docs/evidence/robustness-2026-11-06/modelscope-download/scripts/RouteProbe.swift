import Foundation
import CryptoKit

private final class BodyLimit: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    let maximum: Int64
    init(_ maximum: Int64) { self.maximum = maximum }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        if totalBytesWritten > maximum || totalBytesExpectedToWrite > maximum {
            downloadTask.cancel()
        }
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {}
}

@main enum RouteProbe {
    static func main() async {
        let source = URL(string: CommandLine.arguments[1])!
        let start = Int64(CommandLine.arguments[2])!
        let length = Int64(CommandLine.arguments[3])!
        let limit = Double(CommandLine.arguments[4])!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = min(30, limit)
        configuration.timeoutIntervalForResource = limit
        configuration.connectionProxyDictionary = [
            "HTTPEnable": 0, "HTTPSEnable": 0, "SOCKSEnable": 0]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: source, cachePolicy: .reloadIgnoringLocalCacheData)
        if start >= 0 {
            request.setValue("bytes=\(start)-\(start + length - 1)", forHTTPHeaderField: "Range")
        }
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        let began = ProcessInfo.processInfo.systemUptime
        var result: [String: Any] = ["tls_validation": "default", "proxy": "disabled",
                                   "transport": "URLSessionDownloadTask", "maximum_bytes": length,
                                   "maximum_resource_seconds": limit]
        do {
            let (temporary, response) = try await session.download(for: request, delegate: BodyLimit(length))
            defer { try? FileManager.default.removeItem(at: temporary) }
            let handle = try FileHandle(forReadingFrom: temporary)
            defer { try? handle.close() }
            let bytes = try handle.read(upToCount: Int(length) + 1) ?? Data()
            guard bytes.count <= length else { throw URLError(.dataLengthExceedsMaximum) }
            result["bytes"] = bytes.count
            result["sha256"] = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
            if let http = response as? HTTPURLResponse {
                result["status"] = http.statusCode
                result["content_range"] = http.value(forHTTPHeaderField: "Content-Range")
                result["final_host"] = http.url?.host
            }
        } catch {
            let failure = error as NSError
            result["error_domain"] = failure.domain
            result["error_code"] = failure.code
        }
        result["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - began
        if let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
            print(String(decoding: data, as: UTF8.self))
        }
    }
}
