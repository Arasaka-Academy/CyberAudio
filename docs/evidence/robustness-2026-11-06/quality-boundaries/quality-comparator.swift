import Foundation

private struct QualityRun: Decodable {
    let status: String
    let helper_sha256: String
    let corpus_sha256: String
    let production_preserved: Bool
    let configuration_and_token_preserved: Bool
    let temporary_request_cleanup: Bool
    let owned_helper_exit_code: Int
    let results: [ASRMeasurement]
}

@main enum CompareQuality {
    static func main() throws {
        let paths = Array(CommandLine.arguments.dropFirst())
        guard paths.count == 3 else { exit(64) }
        let decoder = JSONDecoder()
        let baseline = try decoder.decode(QualityRun.self, from: Data(contentsOf: URL(fileURLWithPath: paths[0])))
        let candidate = try decoder.decode(QualityRun.self, from: Data(contentsOf: URL(fileURLWithPath: paths[1])))
        for run in [baseline, candidate] {
            guard run.status == "completed", run.production_preserved,
                  run.configuration_and_token_preserved, run.temporary_request_cleanup,
                  run.owned_helper_exit_code == 0, run.results.count == 30,
                  Set(run.results.map(\.id)).count == 30 else { exit(1) }
        }
        guard baseline.corpus_sha256 == candidate.corpus_sha256 else { exit(1) }
        let indexed = Dictionary(uniqueKeysWithValues: baseline.results.map { ($0.id, $0) })
        var differences: [[String: Any]] = []
        var separate: [[String: Any]] = []
        for row in candidate.results {
            guard let original = indexed[row.id], original.audio_sha256 == row.audio_sha256,
                  original.reference == row.reference, original.language == row.language,
                  original.repetition == row.repetition else { exit(1) }
            if original.text != row.text {
                differences.append(["id": row.id, "reference": row.reference,
                                    "baseline": original.text, "candidate": row.text])
            }
            if !["zh", "en"].contains(row.language) {
                separate.append(["id": row.id, "language": row.language, "reference": row.reference,
                                 "baseline": original.text, "candidate": row.text,
                                 "raw_text_match": original.text == row.text])
            }
        }
        let old = baseline.results.filter { ["zh", "en"].contains($0.language) }
        let new = candidate.results.filter { ["zh", "en"].contains($0.language) }
        guard old.count == 24, new.count == 24, separate.count == 6 else { exit(1) }
        func json(_ comparison: ASRComparison) throws -> Any {
            try JSONSerialization.jsonObject(with: JSONEncoder().encode(comparison))
        }
        var conditions: [String: Any] = [:]
        for condition in ["clean", "snr20", "snr10"] {
            let first = old.filter { $0.id.hasSuffix("-" + condition) }
            let second = new.filter { $0.id.hasSuffix("-" + condition) }
            guard first.count == 8, second.count == 8 else { exit(1) }
            conditions[condition] = try json(ASRComparison(baseline: first, candidate: second))
        }
        let result: [String: Any] = [
            "status": "completed", "scope": "Same M1 Max; installed 0.4.3 and current Helper run sequentially",
            "corpus_sha256": candidate.corpus_sha256,
            "baseline_helper_sha256": baseline.helper_sha256, "candidate_helper_sha256": candidate.helper_sha256,
            "samples": 30, "strict_id_hash_reference_language_repetition_match": true,
            "raw_text_matches": 30 - differences.count, "differences": differences,
            "monolingual_accuracy": try json(ASRComparison(baseline: old, candidate: new)),
            "conditions": conditions, "composites_and_controls": separate,
            "limitations": "Eight public human recordings transformed to 24 monolingual inputs; four concatenated composites and two controls. No natural code-switch, entity labels, physical microphone, room noise, human listening, speedup or energy claim."
        ]
        let output = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        try output.write(to: URL(fileURLWithPath: paths[2]), options: .atomic)
        print(String(decoding: output, as: UTF8.self))
    }
}
