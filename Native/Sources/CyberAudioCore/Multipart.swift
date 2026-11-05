import Foundation

public struct MultipartPart {
    public let name: String
    public let filename: String?
    public let data: Data
}

public enum Multipart {
    public static func parse(_ body: Data, contentType: String) throws -> [MultipartPart] {
        let invalid = ServiceError(400, "invalid_request", "音频上传请求无效。")
        let content = contentType.split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }
        guard content.first?.lowercased() == "multipart/form-data",
              let boundaryField = content.dropFirst().first(where: { $0.hasPrefix("boundary=") }) else { throw invalid }
        let boundary = String(boundaryField.dropFirst(9)).trimmingCharacters(in: CharacterSet(charactersIn: "\""))
        guard (1...70).contains(boundary.utf8.count), boundary.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 && $0 != 34 }) else { throw invalid }
        let delimiter = Data(("--" + boundary).utf8), separator = Data(("\r\n--" + boundary).utf8)
        guard body.starts(with: delimiter) else { throw invalid }
        var offset = delimiter.count, parts: [MultipartPart] = []
        while true {
            guard offset + 2 <= body.count else { throw invalid }
            if body[offset..<offset+2] == Data("--".utf8) {
                guard body.count == offset + 2 || body[(offset+2)...] == Data("\r\n".utf8) else { throw invalid }
                return parts
            }
            guard body[offset..<offset+2] == Data("\r\n".utf8) else { throw invalid }
            offset += 2
            guard let headerEnd = body.range(of: Data("\r\n\r\n".utf8), in: offset..<min(offset+8192, body.count)),
                  let headers = String(data: body[offset..<headerEnd.lowerBound], encoding: .utf8) else { throw invalid }
            let dispositions = headers.components(separatedBy: "\r\n").filter { $0.lowercased().hasPrefix("content-disposition:") }
            guard dispositions.count == 1 else { throw invalid }
            let disposition = dispositions[0].dropFirst("content-disposition:".count).trimmingCharacters(in: .whitespaces)
            guard disposition.hasPrefix("form-data;") else { throw invalid }
            func attribute(_ key: String) -> String? {
                let pattern = "(?:^|;)\\s*" + key + "=\"([^\"\\r\\n]*)\""
                guard let regex = try? NSRegularExpression(pattern: pattern),
                      let match = regex.firstMatch(in: disposition, range: NSRange(disposition.startIndex..., in: disposition)),
                      let range = Range(match.range(at: 1), in: disposition) else { return nil }
                return String(disposition[range])
            }
            guard let name = attribute("name"), !name.isEmpty else { throw invalid }
            let dataStart = headerEnd.upperBound
            var search = dataStart
            var end: Range<Data.Index>?
            while let found = body.range(of: separator, in: search..<body.count) {
                let tail = found.upperBound
                if tail + 2 <= body.count, [Data("--".utf8), Data("\r\n".utf8)].contains(Data(body[tail..<tail+2])) {
                    end = found; break
                }
                search = tail
            }
            guard let end else { throw invalid }
            parts.append(MultipartPart(name: name, filename: attribute("filename"), data: body.subdata(in: dataStart..<end.lowerBound)))
            guard parts.filter({ $0.filename != nil }).count <= 1, parts.filter({ $0.filename == nil }).count <= 5 else { throw invalid }
            offset = end.upperBound
        }
    }
}
