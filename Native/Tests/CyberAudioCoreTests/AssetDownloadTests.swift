import Foundation
import CryptoKit
import Darwin
import CyberAudioCore

private let payload = Data("0123456789abcdefghijklmnopqrstuvwxyz".utf8)

extension ServiceTests {
    private func assetDownloadFixture() throws -> (URL, LockedAsset, URLSession) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("CyberAudio-download-test-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        addTeardownBlock { try? FileManager.default.removeItem(at:root) }
        let digest = SHA256.hash(data:payload).map { String(format:"%02x",$0) }.joined()
        let asset = try JSONDecoder().decode(LockedAsset.self,from:jsonData(["path":"asr/weight.bin","size":36,"sha256":digest]))
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3; config.timeoutIntervalForResource = 5
        let session = URLSession(configuration:config)
        addTeardownBlock { session.invalidateAndCancel() }
        return (root,asset,session)
    }
    private func fetch(_ asset: LockedAsset,_ root: URL,_ server: RangeServer,_ session: URLSession,restart: Bool = false) async throws {
        try await AssetDownloader.download(asset,from:server.url,to:root.appendingPathComponent("weight.bin"),
            staging:root.appendingPathComponent("downloads"),session:session,chunkBytes:8,restart:restart)
    }
    func testRangedAssetDownloadAndVerifiedReuse() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }
        try await fetch(asset,root,server,session)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload)
        XCTAssertEqual(server.ranges,["bytes=0-7","bytes=8-15","bytes=16-23","bytes=24-31","bytes=32-35"])
        try await fetch(asset,root,server,session)
        XCTAssertEqual(server.ranges.count,5,"Verified final assets must be reused without network requests")
    }
    func testInterruptedAssetDownloadResumes() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.interrupt = true
        do { try await fetch(asset,root,server,session); XCTFail("Truncated response accepted") } catch {}
        let files = try FileManager.default.contentsOfDirectory(at:root.appendingPathComponent("downloads"),includingPropertiesForKeys:nil)
        XCTAssertEqual(files.count,1)
        if let file = files.first { XCTAssertEqual(try Data(contentsOf:file),Data("01234567".utf8)) }
        let previous = server.ranges.count
        server.interrupt = false
        try await fetch(asset,root,server,session)
        XCTAssertEqual(server.ranges[previous],"bytes=8-15","Retry must reuse the last complete chunk")
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload)
    }
    func testTransientAssetInterruptionRecoversWithinDownload() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.interruptOnce = true
        try await fetch(asset,root,server,session)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload)
        XCTAssertEqual(server.ranges,["bytes=0-7","bytes=8-15","bytes=8-15","bytes=16-23","bytes=24-31","bytes=32-35"],
                       "A transient interruption must retry the unchanged range without appending unverified bytes")
    }
    func testTemporaryAssetServerFailureRecoversWithinDownload() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.unavailableOnce = true
        try await fetch(asset,root,server,session)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload)
        XCTAssertEqual(server.ranges,["bytes=0-7","bytes=8-15","bytes=8-15","bytes=16-23","bytes=24-31","bytes=32-35"])
    }
    func testPersistentAssetFailureStopsAfterBoundedRetries() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.unavailable = true
        do { try await fetch(asset,root,server,session); XCTFail("Unavailable server accepted") }
        catch { XCTAssertEqual((error as? ServiceError)?.code,"download_unavailable") }
        XCTAssertEqual(server.ranges,["bytes=0-7","bytes=8-15","bytes=8-15","bytes=8-15"],
                       "Each range has at most two automatic retries")
        let partial = root.appendingPathComponent("downloads/"+asset.sha256+".partial")
        XCTAssertEqual(try Data(contentsOf:partial),Data("01234567".utf8))
        XCTAssertFalse(FileManager.default.fileExists(atPath:root.appendingPathComponent("weight.bin").path))
    }
    func testAssetRetryRespectsServerDelayAndPermanentErrors() async throws {
        for retryAfter in ["120","Thu, 01 Jan 2099 00:00:00 GMT"] {
            let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
            defer { server.stop() }; server.unavailable = true; server.retryAfter = retryAfter
            do { try await fetch(asset,root,server,session); XCTFail("Unavailable server accepted") }
            catch { XCTAssertEqual((error as? ServiceError)?.code,"download_unavailable") }
            XCTAssertEqual(server.ranges,["bytes=0-7","bytes=8-15"],
                           "A server delay beyond the automatic retry budget must return control without an early retry")
        }
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.notFound = true
        do { try await fetch(asset,root,server,session); XCTFail("Missing asset accepted") } catch {}
        XCTAssertEqual(server.ranges,["bytes=0-7","bytes=8-15"],"Permanent HTTP errors must not be retried")
        let partial = root.appendingPathComponent("downloads/"+asset.sha256+".partial")
        XCTAssertEqual(try Data(contentsOf:partial),Data("01234567".utf8))
    }
    func testAssetCancellationInterruptsRetryDelay() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.interrupt = true
        let task = Task { try await self.fetch(asset,root,server,session) }
        let deadline = uptime()+3
        while server.ranges.count < 2 && uptime() < deadline { try await Task.sleep(nanoseconds:10_000_000) }
        try await Task.sleep(nanoseconds:150_000_000)
        let cancelled = uptime(); task.cancel()
        do { try await task.value; XCTFail("Cancelled retry completed") }
        catch { XCTAssertTrue(error is CancellationError,"A retry delay must report cancellation, not a network failure") }
        XCTAssertTrue(uptime()-cancelled < 0.75,"Cancelling must not wait for the retry backoff")
        XCTAssertEqual(server.ranges.count,2,"Cancellation must not initiate a retry")
        let partial = root.appendingPathComponent("downloads/"+asset.sha256+".partial")
        XCTAssertEqual(try Data(contentsOf:partial),Data("01234567".utf8))
    }
    func testIgnoredRangeReplacesOnlyVerifiedPartial() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.interrupt = true
        do { try await fetch(asset,root,server,session); XCTFail("Truncated response accepted") } catch {}
        server.interrupt = false; server.ignoreRange = true
        try await fetch(asset,root,server,session)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload,
                       "A complete 200 response must not be appended to a saved prefix")
    }
    func testAssetDownloadRejectsBadRangeAndDigest() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.wrongRange = true
        do { try await fetch(asset,root,server,session); XCTFail("Incorrect Content-Range accepted") } catch {}
        XCTAssertEqual(server.ranges.count,1,"Malformed ranges must fail without automatic retry")
        XCTAssertFalse(FileManager.default.fileExists(atPath:root.appendingPathComponent("weight.bin").path))
        server.wrongRange = false; server.corrupt = true
        do { try await fetch(asset,root,server,session); XCTFail("Wrong SHA-256 accepted") } catch {}
        XCTAssertFalse(FileManager.default.fileExists(atPath:root.appendingPathComponent("weight.bin").path))
        server.corrupt = false
        try await fetch(asset,root,server,session,restart:true)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload)
    }
    func testAssetDownloadCancellationPreservesProgress() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }; server.hold = true
        let task = Task { try await self.fetch(asset,root,server,session) }
        let deadline = uptime()+3
        while server.ranges.count < 2 && uptime() < deadline { try await Task.sleep(nanoseconds:10_000_000) }
        XCTAssertEqual(server.ranges.count,2)
        let cancelled = uptime(); task.cancel()
        do { try await task.value; XCTFail("Cancelled download completed") } catch {}
        XCTAssertTrue(uptime()-cancelled < 2,"Cancellation must interrupt an active network wait")
        server.hold = false; server.release.signal()
        try await fetch(asset,root,server,session)
        XCTAssertEqual(try Data(contentsOf:root.appendingPathComponent("weight.bin")),payload)
    }
    func testAssetDownloadPreservesFilesAndRejectsSymlinks() async throws {
        let (root,asset,session) = try assetDownloadFixture(),server = try RangeServer()
        defer { server.stop() }
        let target = root.appendingPathComponent("weight.bin"),outside = root.appendingPathComponent("outside")
        try Data("existing model".utf8).write(to:target)
        do { try await fetch(asset,root,server,session); XCTFail("Invalid existing final file overwritten") } catch {}
        XCTAssertEqual(try Data(contentsOf:target),Data("existing model".utf8))
        XCTAssertTrue(server.ranges.isEmpty)
        try FileManager.default.removeItem(at:target)
        try FileManager.default.createDirectory(at:outside,withIntermediateDirectories:true)
        try FileManager.default.createSymbolicLink(at:root.appendingPathComponent("downloads"),withDestinationURL:outside)
        do { try await fetch(asset,root,server,session); XCTFail("Symlink staging directory accepted") } catch {}
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath:outside.path).isEmpty)
        XCTAssertTrue(server.ranges.isEmpty)
    }
    func testPreparationReadinessAndDiskCapacity() throws {
        let (root,asset,_) = try assetDownloadFixture()
        let config = ServiceConfiguration(asr_dir:root.path,tts_dir:root.path,ffmpeg:"/opt/homebrew/bin/ffmpeg")
        let missing = try AssetPreparation.inspect(configuration:config,assets:[asset],
            staging:root.appendingPathComponent("downloads"),availableBytes:1)
        XCTAssertEqual(missing.missing_files,1)
        XCTAssertFalse(missing.models_ready)
        XCTAssertFalse(missing.can_download,"Insufficient storage must be detected before networking")
        let staging = root.appendingPathComponent("downloads")
        try FileManager.default.createDirectory(at:staging,withIntermediateDirectories:true)
        try Data(repeating:0,count:asset.size).write(to:staging.appendingPathComponent(asset.sha256+".partial"))
        let damagedPartial = try AssetPreparation.inspect(configuration:config,assets:[asset],staging:staging,availableBytes:1_000_000_000)
        XCTAssertEqual(damagedPartial.invalid_partials,[asset.path],"A complete corrupt partial must offer explicit restart")
        XCTAssertEqual(damagedPartial.resumable_bytes,0)
        try payload.write(to:root.appendingPathComponent("weight.bin"))
        let ready = try AssetPreparation.inspect(configuration:config,assets:[asset],
            staging:root.appendingPathComponent("downloads"),availableBytes:1)
        XCTAssertTrue(ready.models_ready)
        XCTAssertTrue(ready.ffmpeg_ready)
        XCTAssertEqual(ready.verified_bytes,36)
        XCTAssertEqual(ready.required_bytes,0,"An existing verified installation needs no download space")
        try Data("changed".utf8).write(to:root.appendingPathComponent("weight.bin"))
        let corrupt = try AssetPreparation.inspect(configuration:config,assets:[asset],
            staging:root.appendingPathComponent("downloads"),availableBytes:10_000_000_000)
        XCTAssertEqual(corrupt.invalid_files,["asr/weight.bin"])
        XCTAssertFalse(corrupt.can_download,"Preparation must preserve and report existing corrupt final files")
    }
    func testPreparationRejectsUnrelatedExecutable() throws {
        XCTAssertFalse(AssetPreparation.isFFmpeg("/does/not/exist"))
        XCTAssertFalse(AssetPreparation.isFFmpeg("/usr/bin/true"),"An executable is not necessarily FFmpeg")
        XCTAssertTrue(AssetPreparation.isFFmpeg("/opt/homebrew/bin/ffmpeg"))
    }
}

private final class RangeServer: @unchecked Sendable {
    private let lock = NSLock(),done = DispatchSemaphore(value:0)
    let release = DispatchSemaphore(value:0)
    private var stopping = false
    private var requests: [String] = []
    private var flags = [String:Bool]()
    private var retryAfterValue = "0"
    private let fd: Int32
    let url: URL
    var ranges: [String] { lock.lock(); defer { lock.unlock() }; return requests }
    private func flag(_ name: String) -> Bool { lock.lock(); defer { lock.unlock() }; return flags[name] ?? false }
    private func set(_ name: String,_ value: Bool) { lock.lock(); flags[name] = value; lock.unlock() }
    var interrupt: Bool { get { flag("interrupt") } set { set("interrupt",newValue) } }
    var interruptOnce: Bool { get { flag("interruptOnce") } set { set("interruptOnce",newValue) } }
    var unavailableOnce: Bool { get { flag("unavailableOnce") } set { set("unavailableOnce",newValue) } }
    var unavailable: Bool { get { flag("unavailable") } set { set("unavailable",newValue) } }
    var notFound: Bool { get { flag("notFound") } set { set("notFound",newValue) } }
    var retryAfter: String {
        get { lock.lock(); defer { lock.unlock() }; return retryAfterValue }
        set { lock.lock(); retryAfterValue = newValue; lock.unlock() }
    }
    var ignoreRange: Bool { get { flag("ignore") } set { set("ignore",newValue) } }
    var wrongRange: Bool { get { flag("wrong") } set { set("wrong",newValue) } }
    var corrupt: Bool { get { flag("corrupt") } set { set("corrupt",newValue) } }
    var hold: Bool { get { flag("hold") } set { set("hold",newValue) } }
    init() throws {
        let listener = socket(AF_INET,SOCK_STREAM,0)
        guard listener >= 0 else { throw ServiceError.inference }
        fd = listener
        var addr = sockaddr_in(); addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size); addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to:&addr) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { bind(listener,$0,socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        guard bound == 0,listen(listener,8) == 0 else { close(listener); throw ServiceError.inference }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to:&addr) { $0.withMemoryRebound(to:sockaddr.self,capacity:1) { getsockname(listener,$0,&length) } }
        url = URL(string:"http://127.0.0.1:\(UInt16(bigEndian:addr.sin_port))/asset")!
        DispatchQueue.global().async { self.run() }
    }
    func stop() {
        lock.lock(); let alreadyStopped = stopping; stopping = true; lock.unlock()
        guard !alreadyStopped else { return }
        release.signal(); shutdown(fd,SHUT_RDWR)
        _ = done.wait(timeout:.now()+3); close(fd)
    }
    private func run() {
        defer { done.signal() }
        while true {
            lock.lock(); let stopped = stopping; lock.unlock(); if stopped { return }
            var descriptor = pollfd(fd:fd,events:Int16(POLLIN),revents:0)
            guard poll(&descriptor,1,100) > 0 else { continue }
            lock.lock(); let stop = stopping; lock.unlock(); if stop { return }
            let peer = accept(fd,nil,nil); if peer < 0 { continue }
            respond(peer); close(peer)
        }
    }
    private func respond(_ peer: Int32) {
        var timeout = timeval(tv_sec:3,tv_usec:0)
        setsockopt(peer,SOL_SOCKET,SO_RCVTIMEO,&timeout,socklen_t(MemoryLayout<timeval>.size))
        var bytes = Data(),buffer = [UInt8](repeating:0,count:4096)
        while bytes.count < 16384 && bytes.range(of:Data("\r\n\r\n".utf8)) == nil {
            let count = recv(peer,&buffer,buffer.count,0); if count <= 0 { return }; bytes.append(contentsOf:buffer.prefix(count))
        }
        let line = String(decoding:bytes,as:UTF8.self).components(separatedBy:"\r\n").first { $0.lowercased().hasPrefix("range:") }
        guard let range = line?.split(separator:":",maxSplits:1).last?.trimmingCharacters(in:.whitespaces),
              range.hasPrefix("bytes=") else { return }
        let parts = range.dropFirst(6).split(separator:"-").compactMap { Int($0) }
        guard parts.count == 2,parts[0] >= 0,parts[1] < payload.count else { return }
        lock.lock(); requests.append(range); let number = requests.count,mode = flags; lock.unlock()
        if mode["notFound"] == true && number >= 2 {
            write(Data("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".utf8),to:peer)
            return
        }
        if (mode["unavailable"] == true && number >= 2) || (mode["unavailableOnce"] == true && number == 2) {
            write(Data("HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: \(retryAfter)\r\nConnection: close\r\n\r\n".utf8),to:peer)
            return
        }
        var data = mode["ignore"] == true ? payload : payload.subdata(in:parts[0]..<(parts[1]+1))
        if mode["corrupt"] == true && parts[0] == 0 { data[0] ^= 1 }
        let contentRange = mode["ignore"] == true ? "" : "Content-Range: bytes \(parts[0]+(mode["wrong"] == true ? 1 : 0))-\(parts[1])/36\r\n"
        write(Data("HTTP/1.1 \(mode["ignore"] == true ? "200 OK" : "206 Partial Content")\r\nContent-Length: \(data.count)\r\n\(contentRange)Connection: close\r\n\r\n".utf8),to:peer)
        if mode["hold"] == true && number >= 2 { _ = release.wait(timeout:.now()+5); return }
        if (mode["interrupt"] == true && number >= 2) || (mode["interruptOnce"] == true && number == 2) { data = Data(data.prefix(data.count/2)) }
        write(data,to:peer)
    }
    private func write(_ data: Data,to peer: Int32) {
        data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = send(peer,bytes.baseAddress!.advanced(by:offset),bytes.count-offset,0)
                if count <= 0 { return }; offset += count
            }
        }
    }
}
