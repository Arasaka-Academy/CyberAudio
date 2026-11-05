import Foundation

func XCTAssertEqual<T: Equatable>(_ actual: T, _ expected: T) { precondition(actual == expected, "\(actual) != \(expected)") }
func XCTAssertNil<T>(_ value: T?) { precondition(value == nil) }
func XCTAssertTrue(_ value: Bool) { precondition(value) }
func XCTAssertFalse(_ value: Bool) { precondition(!value) }

@main
struct EvidenceTests {
    static func main() {
        let tests = EvidenceTests()
        tests.testCharacterErrorRateCountsChineseSubstitutionAndInsertion()
        tests.testWordErrorRateIgnoresCaseAndPunctuationButKeepsNumbers()
        tests.testInsertionsIntoSilenceAreNotScoredAsPerfectRecognition()
        tests.testPercentileUsesNearestRankAndRejectsInvalidSamples()
        tests.testGateRejectsMissingPhysicalEvidenceEvenWhenTimingPasses()
        tests.testGateRejectsAccuracyRegressionAndSmallSyntheticCorpus()
        tests.testGateRejectsNonRealtimeTTS()
        tests.testGateAcceptsOnlyCompleteEvidence()
        tests.testGateRejectsNonfiniteOrInvalidTiming()
        tests.testComparisonRejectsDifferentAudioAndMissingSamples()
        tests.testComparisonWeightsErrorsByReferenceLengthAndSeparatesLanguages()
        print("CoreMLEvidence: 11 behavioral scenarios passed")
    }
    func testCharacterErrorRateCountsChineseSubstitutionAndInsertion() {
        XCTAssertEqual(TextScore.characterErrorRate(reference: "今天开会", hypothesis: "今天开会了"), 0.25)
        XCTAssertEqual(TextScore.characterErrorRate(reference: "今天开会", hypothesis: "今天散会"), 0.25)
    }

    func testWordErrorRateIgnoresCaseAndPunctuationButKeepsNumbers() {
        XCTAssertEqual(TextScore.wordErrorRate(reference: "Room 2, please!", hypothesis: "room 3 please"), 1.0 / 3.0)
        XCTAssertEqual(TextScore.wordErrorRate(reference: "Hello, world!", hypothesis: "hello world"), 0)
    }

    func testInsertionsIntoSilenceAreNotScoredAsPerfectRecognition() {
        XCTAssertEqual(TextScore.characterErrorRate(reference: "", hypothesis: "你好"), 2)
    }

    func testPercentileUsesNearestRankAndRejectsInvalidSamples() {
        XCTAssertEqual(Statistics.percentile([1, 2, 3, 4, 5], 0.95), 5)
        XCTAssertNil(Statistics.percentile([], 0.95))
        XCTAssertNil(Statistics.percentile([Double.nan], 0.95))
    }

    func testGateRejectsMissingPhysicalEvidenceEvenWhenTimingPasses() {
        let evidence = AcceptanceEvidence(realUtterances: 100, includesMixedSpeech: true,
            accuracyNonRegressing: true, entitiesNonRegressing: true, sttP95Milliseconds: 500,
            ttsRealTimeFactor: 0.5, nineVoicesListened: false, energyLower: nil,
            aneTraceVerified: false, apiLifecyclePassed: false)
        XCTAssertFalse(evidence.accepted)
        XCTAssertTrue(evidence.missing.contains("energy"))
        XCTAssertTrue(evidence.missing.contains("listening"))
        XCTAssertTrue(evidence.missing.contains("ane_trace"))
    }

    func testGateRejectsAccuracyRegressionAndSmallSyntheticCorpus() {
        let evidence = AcceptanceEvidence(realUtterances: 5, includesMixedSpeech: false,
            accuracyNonRegressing: false, entitiesNonRegressing: true, sttP95Milliseconds: 500,
            ttsRealTimeFactor: 0.5, nineVoicesListened: true, energyLower: true,
            aneTraceVerified: true, apiLifecyclePassed: true)
        XCTAssertFalse(evidence.accepted)
        XCTAssertTrue(evidence.missing.contains("real_corpus_100"))
        XCTAssertTrue(evidence.missing.contains("accuracy"))
    }

    func testGateRejectsNonRealtimeTTS() {
        let evidence = AcceptanceEvidence(realUtterances: 100, includesMixedSpeech: true,
            accuracyNonRegressing: true, entitiesNonRegressing: true, sttP95Milliseconds: 500,
            ttsRealTimeFactor: 1.0, nineVoicesListened: true, energyLower: true,
            aneTraceVerified: true, apiLifecyclePassed: true)
        XCTAssertFalse(evidence.accepted)
        XCTAssertTrue(evidence.missing.contains("tts_rtf"))
    }

    func testGateAcceptsOnlyCompleteEvidence() {
        let evidence = AcceptanceEvidence(realUtterances: 100, includesMixedSpeech: true,
            accuracyNonRegressing: true, entitiesNonRegressing: true, sttP95Milliseconds: 3000,
            ttsRealTimeFactor: 0.99, nineVoicesListened: true, energyLower: true,
            aneTraceVerified: true, apiLifecyclePassed: true)
        XCTAssertTrue(evidence.accepted)
        XCTAssertTrue(evidence.missing.isEmpty)
    }

    func testGateRejectsNonfiniteOrInvalidTiming() {
        let evidence = AcceptanceEvidence(realUtterances: 100, includesMixedSpeech: true,
            accuracyNonRegressing: true, entitiesNonRegressing: true, sttP95Milliseconds: .nan,
            ttsRealTimeFactor: -1, nineVoicesListened: true, energyLower: true,
            aneTraceVerified: true, apiLifecyclePassed: true)
        XCTAssertFalse(evidence.accepted)
        XCTAssertTrue(evidence.missing.contains("stt_p95"))
        XCTAssertTrue(evidence.missing.contains("tts_rtf"))
    }

    func sample(_ id: String, _ reference: String, _ text: String, language: String = "zh", hash: String = "a") -> ASRMeasurement {
        ASRMeasurement(id: id, repetition: 0, audio_sha256: hash, text: text,
            reference: reference, language: language, milliseconds: 200)
    }

    func testComparisonRejectsDifferentAudioAndMissingSamples() {
        let baseline = [sample("one", "你好", "你好")]
        XCTAssertTrue((try? ASRComparison(baseline: baseline, candidate: [])) == nil)
        XCTAssertTrue((try? ASRComparison(baseline: baseline, candidate: [sample("one", "你好", "你好", hash: "b")])) == nil)
        XCTAssertTrue((try? ASRComparison(baseline: baseline, candidate: baseline + baseline)) == nil)
    }

    func testComparisonWeightsErrorsByReferenceLengthAndSeparatesLanguages() {
        let baseline = [sample("one", "今天开会", "今天开会"), sample("two", "你好", "你好"),
            sample("three", "Hello world", "hello world", language: "en")]
        let candidate = [sample("three", "Hello world", "hello world", language: "en"),
            sample("two", "你好", "你"), sample("one", "今天开会", "今天开会")]
        let comparison = try! ASRComparison(baseline: baseline, candidate: candidate)
        XCTAssertFalse(comparison.accuracyNonRegressing)
        XCTAssertEqual(comparison.languages["zh"]!.candidateCER, 1.0 / 6.0)
        XCTAssertEqual(comparison.languages["en"]!.candidateWER, 0)
        XCTAssertEqual(comparison.candidateP95Milliseconds, 200)
    }
}
