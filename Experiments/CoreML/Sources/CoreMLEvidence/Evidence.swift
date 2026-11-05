import Foundation

public enum TextScore {
    public static func normalized(_ text: String) -> String {
        let value = text.precomposedStringWithCanonicalMapping.lowercased()
        return String(String.UnicodeScalarView(value.unicodeScalars.map {
            CharacterSet.alphanumerics.contains($0) ? $0 : Unicode.Scalar(32)!
        })).split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    public static func characterErrorRate(reference: String, hypothesis: String) -> Double {
        rate(Array(normalized(reference).filter { !$0.isWhitespace }), Array(normalized(hypothesis).filter { !$0.isWhitespace }))
    }

    public static func wordErrorRate(reference: String, hypothesis: String) -> Double {
        rate(normalized(reference).split(separator: " "), normalized(hypothesis).split(separator: " "))
    }

    private static func rate<T: Equatable>(_ reference: [T], _ hypothesis: [T]) -> Double {
        Double(distance(reference, hypothesis)) / Double(max(reference.count, 1))
    }

    fileprivate static func distance<T: Equatable>(_ reference: [T], _ hypothesis: [T]) -> Int {
        var row = Array(0...hypothesis.count)
        for (i, token) in reference.enumerated() {
            var next = [i + 1] + Array(repeating: 0, count: hypothesis.count)
            for (j, other) in hypothesis.enumerated() {
                next[j + 1] = min(next[j] + 1, row[j + 1] + 1, row[j] + (token == other ? 0 : 1))
            }
            row = next
        }
        return row[hypothesis.count]
    }
}

public struct ASRMeasurement: Codable {
    public let id: String
    public let repetition: Int
    public let audio_sha256: String
    public let text: String
    public let reference: String
    public let language: String
    public let milliseconds: Double
}

public struct ASRReport: Decodable {
    public let status: String
    public let model: String
    public let results: [ASRMeasurement]
}

public struct LanguageComparison: Encodable {
    public let samples: Int
    public let baselineCER: Double
    public let candidateCER: Double
    public let baselineWER: Double
    public let candidateWER: Double
}

public struct ASRComparison: Encodable {
    public let samples: Int
    public let languages: [String: LanguageComparison]
    public let accuracyNonRegressing: Bool
    public let baselineP95Milliseconds: Double
    public let candidateP95Milliseconds: Double

    public enum Invalid: Error { case unmatchedCorpus, invalidMeasurement }

    public init(baseline: [ASRMeasurement], candidate: [ASRMeasurement]) throws {
        func key(_ row: ASRMeasurement) -> String { row.id + "\0" + String(row.repetition) }
        guard !baseline.isEmpty, baseline.count == candidate.count,
              Set(baseline.map(key)).count == baseline.count,
              Set(candidate.map(key)).count == candidate.count else { throw Invalid.unmatchedCorpus }
        let indexed = Dictionary(uniqueKeysWithValues: candidate.map { (key($0), $0) })
        for row in baseline {
            guard let other = indexed[key(row)], other.audio_sha256 == row.audio_sha256,
                  other.reference == row.reference, other.language == row.language else { throw Invalid.unmatchedCorpus }
        }
        for row in baseline + candidate {
            guard ["zh", "en"].contains(row.language), !TextScore.normalized(row.reference).isEmpty,
                  !row.id.isEmpty, !row.audio_sha256.isEmpty, row.repetition >= 0,
                  row.milliseconds.isFinite, row.milliseconds > 0 else { throw Invalid.invalidMeasurement }
        }
        var groups: [String: LanguageComparison] = [:]
        for language in Set(baseline.map(\.language)) {
            let rows = baseline.filter { $0.language == language }
            var chars = 0, words = 0, baseChars = 0, candidateChars = 0, baseWords = 0, candidateWords = 0
            for row in rows {
                let other = indexed[key(row)]!
                let reference = TextScore.normalized(row.reference)
                let base = TextScore.normalized(row.text), next = TextScore.normalized(other.text)
                let referenceChars = Array(reference.filter { !$0.isWhitespace })
                let referenceWords = reference.split(separator: " ")
                chars += referenceChars.count; words += referenceWords.count
                baseChars += TextScore.distance(referenceChars, Array(base.filter { !$0.isWhitespace }))
                candidateChars += TextScore.distance(referenceChars, Array(next.filter { !$0.isWhitespace }))
                baseWords += TextScore.distance(referenceWords, base.split(separator: " "))
                candidateWords += TextScore.distance(referenceWords, next.split(separator: " "))
            }
            groups[language] = LanguageComparison(samples: rows.count,
                baselineCER: Double(baseChars) / Double(max(chars, 1)), candidateCER: Double(candidateChars) / Double(max(chars, 1)),
                baselineWER: Double(baseWords) / Double(max(words, 1)), candidateWER: Double(candidateWords) / Double(max(words, 1)))
        }
        samples = baseline.count
        languages = groups
        accuracyNonRegressing = groups.allSatisfy { language, scores in
            language == "zh" ? scores.candidateCER <= scores.baselineCER : scores.candidateWER <= scores.baselineWER
        }
        baselineP95Milliseconds = Statistics.percentile(baseline.map(\.milliseconds), 0.95)!
        candidateP95Milliseconds = Statistics.percentile(candidate.map(\.milliseconds), 0.95)!
    }
}

public enum Statistics {
    public static func percentile(_ samples: [Double], _ fraction: Double) -> Double? {
        guard !samples.isEmpty, samples.allSatisfy({ $0.isFinite && $0 >= 0 }), fraction > 0, fraction <= 1 else { return nil }
        return samples.sorted()[max(0, Int(ceil(Double(samples.count) * fraction)) - 1)]
    }
}

/// These booleans describe independently collected evidence; timing cannot imply
/// correct recognition, human listening, lower energy, or verified ANE execution.
public struct AcceptanceEvidence: Codable {
    public let realUtterances: Int
    public let includesMixedSpeech: Bool
    public let accuracyNonRegressing: Bool
    public let entitiesNonRegressing: Bool
    public let sttP95Milliseconds: Double
    public let ttsRealTimeFactor: Double
    public let nineVoicesListened: Bool
    public let energyLower: Bool?
    public let aneTraceVerified: Bool
    public let apiLifecyclePassed: Bool

    public var missing: [String] {
        var result: [String] = []
        if realUtterances < 100 { result.append("real_corpus_100") }
        if !includesMixedSpeech { result.append("mixed_speech") }
        if !accuracyNonRegressing { result.append("accuracy") }
        if !entitiesNonRegressing { result.append("entities") }
        if !sttP95Milliseconds.isFinite || sttP95Milliseconds <= 0 || sttP95Milliseconds > 3000 { result.append("stt_p95") }
        if !ttsRealTimeFactor.isFinite || ttsRealTimeFactor <= 0 || ttsRealTimeFactor >= 1 { result.append("tts_rtf") }
        if !nineVoicesListened { result.append("listening") }
        if energyLower != true { result.append("energy") }
        if !aneTraceVerified { result.append("ane_trace") }
        if !apiLifecyclePassed { result.append("api_lifecycle") }
        return result
    }

    public var accepted: Bool { missing.isEmpty }
}
