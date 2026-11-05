import Foundation
import Darwin

public enum AudioFile {
    public static func wav(_ pcm: Data) -> Data {
        var data = Data()
        func ascii(_ text: String) { data.append(contentsOf: text.utf8) }
        func u16(_ number: UInt16) { var value = number.littleEndian; withUnsafeBytes(of: &value) { data.append(contentsOf: $0) } }
        func u32(_ number: UInt32) { var value = number.littleEndian; withUnsafeBytes(of: &value) { data.append(contentsOf: $0) } }
        ascii("RIFF"); u32(UInt32(pcm.count+36)); ascii("WAVEfmt "); u32(16); u16(1); u16(1)
        u32(24000); u32(48000); u16(2); u16(16); ascii("data"); u32(UInt32(pcm.count)); data.append(pcm)
        return data
    }
    public static func inspectNormalizedWAV(_ data: Data) throws -> (duration: Double, silent: Bool) {
        let invalid = ServiceError(400, "invalid_audio", "音频数据无效。")
        func number(_ offset: Int, _ bytes: Int) throws -> Int {
            guard offset >= 0, offset + bytes <= data.count else { throw invalid }
            return (0..<bytes).reduce(0) { $0 | Int(data[offset+$1]) << (8*$1) }
        }
        guard data.count >= 12, data.prefix(4) == Data("RIFF".utf8), data[8..<12] == Data("WAVE".utf8) else { throw invalid }
        var offset = 12, format = false, samples: Data?
        while offset+8 <= data.count {
            let length = try number(offset+4,4), start = offset+8
            guard length <= data.count-start else { throw invalid }
            let tag = data[offset..<offset+4]
            if tag == Data("fmt ".utf8) {
                guard length >= 16, try number(start,2) == 1, try number(start+2,2) == 1,
                      try number(start+4,4) == 16000, try number(start+14,2) == 16 else { throw invalid }
                format = true
            }
            if tag == Data("data".utf8) { samples = data.subdata(in: start..<start+length) }
            offset = start+length+(length%2)
        }
        guard format, let samples, samples.count%2 == 0 else { throw invalid }
        guard !samples.isEmpty else { throw ServiceError(400, "empty_audio", "音频为空。") }
        let duration = Double(samples.count)/32000
        guard duration <= 60 else { throw ServiceError(413, "audio_too_long", "音频不能超过 60 秒。") }
        return (duration, samples.allSatisfy { $0 == 0 })
    }
    public static func decode(ffmpeg: String, source: URL, destination: URL, cancellation: CancellationToken) throws -> (duration: Double, silent: Bool) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: ffmpeg)
        process.arguments = ["-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "wav,mp3,mov,matroska,webm,ogg,flac,aac,aiff", "-i", source.path,
            "-map", "0:a:0", "-vn", "-t", "60.1", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", destination.path]
        process.standardInput = FileHandle.nullDevice; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { throw ServiceError(500, "decoder_unavailable", "FFmpeg 未安装或无法启动。") }
        let deadline = uptime()+25
        do {
            while process.isRunning {
                try cancellation.check()
                guard uptime() < deadline else { throw ServiceError(400, "decode_timeout", "音频解码超时。") }
                Thread.sleep(forTimeInterval: 0.02)
            }
        } catch {
            process.terminate()
            let stop = uptime()+1
            while process.isRunning && uptime() < stop { Thread.sleep(forTimeInterval: 0.01) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit(); throw error
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw ServiceError(400, "invalid_audio", "无法读取音频，请上传 WAV、MP3、M4A 或 WebM。") }
        return try inspectNormalizedWAV(Data(contentsOf: destination))
    }
    public static func cleanup(_ directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        for entry in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isDirectoryKey,.isSymbolicLinkKey]) where entry.lastPathComponent.hasPrefix("request-") {
            let attributes = try entry.resourceValues(forKeys: [.isDirectoryKey,.isSymbolicLinkKey])
            if attributes.isDirectory == true && attributes.isSymbolicLink != true { try FileManager.default.removeItem(at: entry) }
        }
    }
}
