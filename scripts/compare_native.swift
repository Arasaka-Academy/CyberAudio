// Compile together with Experiments/CoreML/Sources/CoreMLEvidence/Evidence.swift.
// Reuse the original corpus matching, normalization, edit counts and percentile.
import Foundation

private struct NativeReport: Decodable {
    let status: String
    let asr_corpus: [Row]
    let os: String?
    let physical_memory_bytes: UInt64?
    struct Row: Decodable {
        let id: String
        let reference: String
        let language: String
        let audio_sha256: String
        let http_ms: Double
        let result: Result
    }
    struct Result: Decodable {
        let text: String
        let language: String?
        let processing_ms: Double
        let inference_ms: Double
    }
}

@main private enum CompareNative {
    static func main() throws {
        let paths = Array(CommandLine.arguments.dropFirst())
        guard paths.count == 4 else {
            fputs("Usage: compare-native python-baseline.json swift-model.json native-service.json output.json\n",stderr)
            exit(64)
        }
        let decoder = JSONDecoder()
        let baselineData = try Data(contentsOf:URL(fileURLWithPath:paths[0]))
        let baseline = try decoder.decode(ASRReport.self,from:baselineData)
        let previous = try decoder.decode(ASRReport.self,from:Data(contentsOf:URL(fileURLWithPath:paths[1])))
        let native = try decoder.decode(NativeReport.self,from:Data(contentsOf:URL(fileURLWithPath:paths[2])))
        guard baseline.status == "completed",previous.status == "completed",native.status == "passed",
              baseline.results.count == 100,native.asr_corpus.count == 100 else { throw ASRComparison.Invalid.unmatchedCorpus }
        func measurement(_ row: NativeReport.Row,_ ms: Double) -> ASRMeasurement {
            ASRMeasurement(id:row.id,repetition:0,audio_sha256:row.audio_sha256,text:row.result.text,
                reference:row.reference,language:row.language,milliseconds:ms)
        }
        let candidate = native.asr_corpus.map { measurement($0,$0.result.processing_ms) }
        let processing = try ASRComparison(baseline:baseline.results,candidate:candidate)
        // This also rejects different bytes, references, IDs, languages or repetitions.
        _ = try ASRComparison(baseline:previous.results,candidate:candidate)
        let previousByID = Dictionary(uniqueKeysWithValues:previous.results.map { ($0.id,$0) })
        let baselineByID = Dictionary(uniqueKeysWithValues:baseline.results.map { ($0.id,$0) })
        let raw = try JSONSerialization.jsonObject(with:baselineData) as! [String:Any]
        let originalRows = raw["results"] as! [[String:Any]]
        let baseInference = originalRows.map { ($0["response"] as! [String:Any])["inference_ms"] as! Double }
        let baseHTTP = originalRows.map { $0["http_milliseconds"] as! Double }
        var environment: [String:Any] = ["os":native.os ?? "not recorded"]
        if let memory = native.physical_memory_bytes { environment["physical_memory_bytes"] = memory }
        var report: [String:Any] = [
            "samples":candidate.count,
            "same_audio_hashes_and_references":true,
            "raw_text_matches_previous_swift":candidate.filter { $0.text == previousByID[$0.id]!.text }.count,
            "raw_text_matches_python":candidate.filter { $0.text == baselineByID[$0.id]!.text }.count,
            "differences_from_previous_swift":candidate.compactMap { row -> [String:Any]? in
                let prior = previousByID[row.id]!.text
                guard prior != row.text else { return nil }
                return ["id":row.id,"previous":prior,"native":row.text,
                    "normalized_text_equal":TextScore.normalized(prior) == TextScore.normalized(row.text)]
            },
            "detected_language_mismatches":native.asr_corpus.filter { $0.result.language != $0.language }.map(\.id),
            "latency_ms":[
                "python_processing_p95":Statistics.percentile(baseline.results.map(\.milliseconds),0.95)!,
                "native_processing_p95":Statistics.percentile(candidate.map(\.milliseconds),0.95)!,
                "python_inference_p95":Statistics.percentile(baseInference,0.95)!,
                "native_inference_p95":Statistics.percentile(native.asr_corpus.map { $0.result.inference_ms },0.95)!,
                "python_http_p95":Statistics.percentile(baseHTTP,0.95)!,
                "native_http_p95":Statistics.percentile(native.asr_corpus.map(\.http_ms),0.95)!
            ],
            "scoring":"NFC lowercase; punctuation to spaces; aggregate character/word edits; nearest-rank P95",
            "baseline_environment":["platform":raw["platform"] as? String ?? "not recorded"],
            "candidate_environment":environment,
            "limitations":["50 Mandarin and 50 English public FLEURS recordings; no dedicated code-switch or entity labels",
                "Hardware and background load must be checked separately; latency observations do not establish speedup or energy gains"]
        ]
        report["accuracy_and_processing"] = try JSONSerialization.jsonObject(with:JSONEncoder().encode(processing))
        let output = try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys,.withoutEscapingSlashes])
        try output.write(to:URL(fileURLWithPath:paths[3]),options:.atomic)
        print(String(decoding:output,as:UTF8.self))
    }
}
