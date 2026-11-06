import Foundation
import Darwin

/// Downloads fixed assets into a private staging area. Only complete, verified
/// files are published; a failed request leaves previously written bytes intact.
public enum AssetDownloader {
    public static func download(_ asset: LockedAsset, from source: URL, to target: URL,
                                staging: URL, session: URLSession, chunkBytes: Int = 8*1024*1024,
                                restart: Bool = false,
                                progress: @escaping @Sendable (Int64) -> Void = { _ in }) async throws {
        try validate(asset)
        guard chunkBytes > 0 else { throw invalidResponse }
        let reporter = DownloadProgress(total:Int64(asset.size),callback:progress)
        if let size = try regularSize(target) {
            guard size == asset.size, try AssetStore.digest(target) == asset.sha256 else {
                throw ServiceError(500,"invalid_assets","已有模型文件校验失败，文件已保留，请检查模型目录。")
            }
            reporter.report(Int64(asset.size)); return
        }
        try directory(staging)
        try FileManager.default.createDirectory(at:target.deletingLastPathComponent(),withIntermediateDirectories:true)
        let partial = staging.appendingPathComponent(asset.sha256+".partial")
        _ = try regularSize(partial)
        let fd = open(partial.path,O_CREAT|O_RDWR|O_NOFOLLOW,0o600)
        guard fd >= 0 else { throw ServiceError(500,"download_storage","无法打开下载暂存文件，请检查目录权限。") }
        let output = FileHandle(fileDescriptor:fd,closeOnDealloc:true)
        defer { try? output.close() }
        var info = stat()
        guard fstat(fd,&info) == 0,info.st_mode & S_IFMT == S_IFREG else { throw unsafePath }
        if restart { try output.truncate(atOffset:0) }
        var offset = try output.seekToEnd()
        guard offset <= UInt64(asset.size) else {
            throw ServiceError(500,"invalid_partial","下载暂存文件长度异常，请选择重新下载。")
        }
        reporter.report(Int64(offset))
        while offset < UInt64(asset.size) {
            try Task.checkCancellation()
            let end = min(UInt64(asset.size)-1,offset+UInt64(chunkBytes)-1)
            var request = URLRequest(url:source,cachePolicy:.reloadIgnoringLocalCacheData)
            request.setValue("bytes=\(offset)-\(end)",forHTTPHeaderField:"Range")
            request.setValue("identity",forHTTPHeaderField:"Accept-Encoding")
            let (temporary,response) = try await downloadChunk(request,session:session,offset:Int64(offset),
                fullSize:Int64(asset.size),chunkSize:Int64(end-offset+1),reporter:reporter)
            defer { try? FileManager.default.removeItem(at:temporary) }
            try Task.checkCancellation()
            guard let http = response as? HTTPURLResponse,let size = try regularSize(temporary) else { throw invalidResponse }
            if http.statusCode == 200 {
                // Some servers ignore Range. Validate the whole response and
                // publish it directly, rather than appending a duplicate prefix.
                guard size == asset.size,try AssetStore.digest(temporary) == asset.sha256 else { throw invalidResponse }
                try publish(temporary,to:target)
                try? FileManager.default.removeItem(at:partial)
                reporter.report(Int64(asset.size)); return
            }
            let expectedRange = "bytes \(offset)-\(end)/\(asset.size)"
            guard http.statusCode == 206,
                  http.value(forHTTPHeaderField:"Content-Range")?.trimmingCharacters(in:.whitespaces).lowercased() == expectedRange,
                  size == Int64(end-offset+1) else { throw invalidResponse }
            let input = try FileHandle(forReadingFrom:temporary)
            defer { try? input.close() }
            while let bytes = try input.read(upToCount:1024*1024),!bytes.isEmpty {
                try Task.checkCancellation()
                try output.write(contentsOf:bytes)
                offset += UInt64(bytes.count)
            }
            try output.synchronize()
            reporter.report(Int64(offset))
        }
        try Task.checkCancellation()
        guard try AssetStore.digest(partial) == asset.sha256 else {
            throw ServiceError(500,"invalid_partial","下载校验失败，模型文件未更改；请选择重新下载。")
        }
        try publish(partial,to:target)
        reporter.report(Int64(asset.size))
    }

    private static func downloadChunk(_ request: URLRequest,session: URLSession,offset: Int64,
                                      fullSize: Int64,chunkSize: Int64,reporter: DownloadProgress) async throws -> (URL,URLResponse) {
        // Retry only this uncommitted range. A failed attempt never changes the
        // durable prefix, and cancellation also interrupts the bounded backoff.
        for attempt in 0...2 {
            try Task.checkCancellation()
            let delegate = ChunkProgress(offset:offset,fullSize:fullSize,chunkSize:chunkSize,reporter:reporter)
            let result: (URL,URLResponse)
            do { result = try await session.download(for:request,delegate:delegate) }
            catch {
                if Task.isCancelled || error is CancellationError { throw CancellationError() }
                if delegate.exceeded { throw invalidResponse }
                let retryable: Set<URLError.Code> = [.timedOut,.networkConnectionLost,.cannotConnectToHost,
                                                    .dnsLookupFailed,.notConnectedToInternet]
                guard attempt < 2,let code = (error as? URLError)?.code,retryable.contains(code) else {
                    throw unavailable(error)
                }
                try await Task.sleep(nanoseconds:UInt64(1 << attempt)*1_000_000_000)
                continue
            }
            if let http = result.1 as? HTTPURLResponse,[408,429,500,502,503,504].contains(http.statusCode) {
                try? FileManager.default.removeItem(at:result.0)
                guard attempt < 2,let delay = retryDelay(http,attempt:attempt) else {
                    throw ServiceError(502,"download_unavailable","下载服务暂时不可用，已保留进度；请稍后继续。")
                }
                try await Task.sleep(nanoseconds:delay)
                continue
            }
            return result
        }
        throw ServiceError.inference
    }
    private static func retryDelay(_ response: HTTPURLResponse,attempt: Int) -> UInt64? {
        let backoff = Double(1 << attempt)
        guard let header = response.value(forHTTPHeaderField:"Retry-After")?.trimmingCharacters(in:.whitespaces) else {
            return UInt64(backoff*1_000_000_000)
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier:"en_US_POSIX"); formatter.timeZone = TimeZone(secondsFromGMT:0)
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        let seconds = Double(header) ?? formatter.date(from:header)?.timeIntervalSinceNow ?? backoff
        guard seconds.isFinite,seconds <= 30 else { return nil }
        return UInt64(max(backoff,seconds)*1_000_000_000)
    }
    private static func unavailable(_ error: Error) -> ServiceError {
        let reason: String
        switch (error as? URLError)?.code {
        case .timedOut: reason = "下载连接超时"
        case .networkConnectionLost: reason = "下载连接中断"
        case .cannotConnectToHost: reason = "无法建立下载连接"
        case .cannotFindHost,.dnsLookupFailed: reason = "无法解析下载地址"
        case .notConnectedToInternet: reason = "网络不可用"
        case .secureConnectionFailed,.serverCertificateHasBadDate,.serverCertificateUntrusted,
             .serverCertificateHasUnknownRoot,.serverCertificateNotYetValid: reason = "无法验证安全下载连接"
        default: reason = "下载中断"
        }
        // Do not expose localizedDescription/userInfo: redirects may carry
        // signed credentials, and proxy addresses can contain private data.
        return ServiceError(502,"download_unavailable",reason+"，已保留进度；请检查网络或代理后继续。")
    }
    static var unsafePath: ServiceError { ServiceError(500,"unsafe_download_path","下载路径包含非普通文件或符号链接，请检查暂存目录。") }
    private static func publish(_ source: URL,to target: URL) throws {
        // RENAME_EXCL atomically publishes without replacing a final file that
        // appeared during verification. An external model volume needs a local
        // staging copy before the same atomic operation.
        if renamex_np(source.path,target.path,UInt32(RENAME_EXCL)) == 0 { return }
        guard errno == EXDEV else { throw ServiceError(500,"download_storage","无法保存已验证的模型，已有文件已保留。") }
        let sibling = target.deletingLastPathComponent().appendingPathComponent(".cyberaudio-\(UUID()).download")
        defer { try? FileManager.default.removeItem(at:sibling) }
        let descriptor = open(sibling.path,O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0o600)
        guard descriptor >= 0 else { throw unsafePath }
        let output = FileHandle(fileDescriptor:descriptor,closeOnDealloc:true)
        let input = try FileHandle(forReadingFrom:source)
        defer { try? input.close(); try? output.close() }
        while let chunk = try input.read(upToCount:1024*1024),!chunk.isEmpty {
            try Task.checkCancellation()
            try output.write(contentsOf:chunk)
        }
        try output.synchronize()
        try Task.checkCancellation()
        guard renamex_np(sibling.path,target.path,UInt32(RENAME_EXCL)) == 0 else {
            throw ServiceError(500,"download_storage","无法保存已验证的模型，已有文件已保留。")
        }
        try? FileManager.default.removeItem(at:source)
    }
    static func validate(_ asset: LockedAsset) throws {
        guard asset.size > 0, asset.sha256.count == 64,
              asset.sha256.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
            throw ServiceError(400,"invalid_asset","下载资产信息无效。")
        }
    }
    private static var invalidResponse: ServiceError { ServiceError(502,"download_failed","下载响应长度、范围或摘要不匹配，已保留先前进度。") }
    static func regularSize(_ url: URL) throws -> Int64? {
        var info = stat()
        if lstat(url.path,&info) != 0 {
            if errno == ENOENT { return nil }
            throw ServiceError(500,"download_storage","无法检查下载文件，请检查目录权限。")
        }
        guard info.st_mode & S_IFMT == S_IFREG else { throw unsafePath }
        return info.st_size
    }
    private static func directory(_ url: URL) throws {
        var info = stat()
        if lstat(url.path,&info) == 0 {
            guard info.st_mode & S_IFMT == S_IFDIR else { throw unsafePath }
        } else {
            guard errno == ENOENT else { throw unsafePath }
            try FileManager.default.createDirectory(at:url,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        }
    }
}

private final class DownloadProgress: @unchecked Sendable {
    private let lock = NSLock()
    private let total: Int64
    private let callback: @Sendable (Int64) -> Void
    private var last: Int64 = -1
    init(total: Int64,callback: @escaping @Sendable (Int64)->Void) { self.total = total; self.callback = callback }
    func report(_ bytes: Int64) {
        lock.lock(); defer { lock.unlock() }
        let value = min(total,max(0,bytes))
        guard value > last else { return }
        last = value; callback(value)
    }
}

private final class ChunkProgress: NSObject,URLSessionDownloadDelegate,@unchecked Sendable {
    let offset: Int64
    let fullSize: Int64,chunkSize: Int64
    let reporter: DownloadProgress
    private let lock = NSLock()
    private var oversized = false
    var exceeded: Bool { lock.lock(); defer { lock.unlock() }; return oversized }
    init(offset: Int64,fullSize: Int64,chunkSize: Int64,reporter: DownloadProgress) {
        self.offset = offset; self.fullSize = fullSize; self.chunkSize = chunkSize; self.reporter = reporter
    }
    func urlSession(_ session: URLSession,downloadTask: URLSessionDownloadTask,didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64,totalBytesExpectedToWrite: Int64) {
        let ranged = (downloadTask.response as? HTTPURLResponse)?.statusCode == 206
        let limit = ranged ? chunkSize : fullSize
        if totalBytesWritten > limit || totalBytesExpectedToWrite > limit {
            lock.lock(); oversized = true; lock.unlock(); downloadTask.cancel(); return
        }
        let prefix = ranged ? offset : 0
        reporter.report(prefix+totalBytesWritten)
    }
    func urlSession(_ session: URLSession,downloadTask: URLSessionDownloadTask,didFinishDownloadingTo location: URL) {}
}
