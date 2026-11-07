// Small assertion harness for macOS Command Line Tools, which do not ship XCTest.
// Every failed assertion is counted and makes this executable exit nonzero.
import Foundation
import Darwin
import CyberAudioCore

private let failureLock = NSLock()
private var failures = 0
func XCTFail(_ message: String,file: StaticString = #filePath,line: UInt = #line) {
    failureLock.lock(); failures += 1; failureLock.unlock()
    fputs("FAIL \(file):\(line): \(message)\n",stderr)
}
func XCTAssertTrue(_ value: @autoclosure () throws -> Bool,_ message: String = "",file: StaticString = #filePath,line: UInt = #line) {
    do { if try !value() { XCTFail("Expected true. "+message,file:file,line:line) } }
    catch { XCTFail("Unexpected error: \(error)",file:file,line:line) }
}
func XCTAssertFalse(_ value: @autoclosure () throws -> Bool,_ message: String = "",file: StaticString = #filePath,line: UInt = #line) {
    XCTAssertTrue(try !value(),message,file:file,line:line)
}
func XCTAssertEqual<T: Equatable>(_ a: @autoclosure () throws -> T,_ b: @autoclosure () throws -> T,_ message: String = "",file: StaticString = #filePath,line: UInt = #line) {
    do { let left = try a(),right = try b(); if left != right { XCTFail("\(left) != \(right). "+message,file:file,line:line) } }
    catch { XCTFail("Unexpected error: \(error)",file:file,line:line) }
}
func XCTAssertThrowsError<T>(_ value: @autoclosure () throws -> T,file: StaticString = #filePath,line: UInt = #line,_ handler: ((Error)->Void)? = nil) {
    do { _ = try value(); XCTFail("Expected an error",file:file,line:line) } catch { handler?(error) }
}
func XCTAssertNotNil<T>(_ value: T?,file: StaticString = #filePath,line: UInt = #line) { XCTAssertTrue(value != nil,file:file,line:line) }
struct XCTSkip: Error { let message: String; init(_ message: String) { self.message = message } }
private struct UnwrapFailure: Error {}
func XCTUnwrap<T>(_ value: T?,file: StaticString = #filePath,line: UInt = #line) throws -> T {
    guard let value else { XCTFail("Required value is nil",file:file,line:line); throw UnwrapFailure() }; return value
}
final class Expectation {
    let semaphore = DispatchSemaphore(value:0)
    func fulfill() { semaphore.signal() }
}
class XCTestCase {
    private var teardowns: [()->Void] = []
    func addTeardownBlock(_ block: @escaping ()->Void) { teardowns.append(block) }
    func tearDown() { for work in teardowns.reversed() { work() } }
    func expectation(description: String) -> Expectation { Expectation() }
    func wait(for items: [Expectation],timeout: Double) {
        for item in items { XCTAssertEqual(item.semaphore.wait(timeout:.now()+timeout),.success) }
    }
}

@main enum ContractTests {
    static func main() async {
        signal(SIGPIPE,SIG_IGN)
        if CommandLine.arguments.count >= 3, CommandLine.arguments[1] == "--check-tokenizer" {
            do { _ = try AssetStore.prepareTokenizer(directory:URL(fileURLWithPath:CommandLine.arguments[2]),asr:false) }
            catch { exit(1) }
            return
        }
        let cases: [(String,(ServiceTests) async throws -> Void)] = [
            ("strict speech",{ try $0.testStrictSpeechOptionsAndAliases() }),
            ("binary multipart",{ try $0.testMultipartBinaryBoundaryAndLimits() }),
            ("atomic update admission",{ try $0.testUpdateLeasesAndAdmissionAreAtomic() }),
            ("update lease expiry",{ try $0.testAbandonedLeaseExpiresAndCancelledLeaseCannotRenew() }),
            ("producer cancellation join",{ try $0.testCancelledWorkerKeepsSlotUntilProducerReturns() }),
            ("timeout slot retention",{ try $0.testTimeoutKeepsSlotAndRecordsFailure() }),
            ("HTTP auth WAV PCM and records",{ try await $0.testActualHTTPAuthenticationWAVStreamingAndStatuses() }),
            ("FFmpeg formats and limits",{ try await $0.testActualFFmpegDecodeSilenceLongAudioAndCleanup() }),
            ("chunked limit and duplicate auth",{ try $0.testRawHTTPChunkedLimitAndDuplicateAuthorization() }),
            ("private and partial failures",{ try await $0.testPrivateFailureAndLateStreamingFailure() }),
            ("HTTP disconnect update drain and recovery",{ try await $0.testHTTPDisconnectJoinsProducerAndUpdateCanPauseAdmission() }),
            ("HTTP timeout retains producer",{ try $0.testHTTPTimeoutRespondsBeforeProducerReturns() }),
            ("slow HTTP reader bounds producer",{ try $0.testSlowHTTPReaderBoundsProducerAndDisconnectRecovers() }),
            ("update rejects unread audio bodies",{ try $0.testUpdateRejectsAudioAtHeadersAfterAuthentication() }),
            ("update waits for WAV response flush",{ try $0.testUpdateWaitsForCompletedModelResponseToFlush() }),
            ("unread WAV send deadline releases update drain",{ try $0.testUnreadWAVExpiresWithoutClientDisconnect() }),
            ("health and scoped cleanup",{ try await $0.testLoadingFailureHealthAndScopedCleanup() }),
            ("native setup preservation",{ try $0.testNativeAssetSetupPreservesExistingTokenAndConfiguration() }),
            ("tokenizer process and locale stability",{ try $0.testTokenizerComparisonAcrossProcessesAndLocales() }),
            ("native proxy download preserves failed assets",{ try await $0.testNativeDownloadUsesProxyAndPreservesAssetsOnFailure() }),
            ("model download sources pin repositories and nested paths",{ try $0.testModelDownloadSourcesPinRepositoriesAndNestedPaths() }),
            ("ranged asset download and reuse",{ try await $0.testRangedAssetDownloadAndVerifiedReuse() }),
            ("interrupted download resumes completed chunks",{ try await $0.testInterruptedAssetDownloadResumes() }),
            ("transient asset interruption retries unchanged range",{ try await $0.testTransientAssetInterruptionRecoversWithinDownload() }),
            ("temporary asset server failure recovers",{ try await $0.testTemporaryAssetServerFailureRecoversWithinDownload() }),
            ("persistent asset failures stop after bounded retries",{ try await $0.testPersistentAssetFailureStopsAfterBoundedRetries() }),
            ("interrupted large ranges fall back without losing prefix",{ try await $0.testInterruptedLargeRangeFallsBackWithoutLosingPrefix() }),
            ("persistent small range failure remains bounded",{ try await $0.testPersistentSmallRangeFailureRemainsBounded() }),
            ("HTTP unavailability does not reduce range",{ try await $0.testTemporaryHTTPFailureDoesNotReduceRange() }),
            ("asset retry respects server delay and permanent errors",{ try await $0.testAssetRetryRespectsServerDelayAndPermanentErrors() }),
            ("asset cancellation interrupts retry delay",{ try await $0.testAssetCancellationInterruptsRetryDelay() }),
            ("ignored ranges never append a full response",{ try await $0.testIgnoredRangeReplacesOnlyVerifiedPartial() }),
            ("download rejects bad ranges and checksums",{ try await $0.testAssetDownloadRejectsBadRangeAndDigest() }),
            ("asset cancellation preserves resumable progress",{ try await $0.testAssetDownloadCancellationPreservesProgress() }),
            ("asset download preserves final files and rejects symlinks",{ try await $0.testAssetDownloadPreservesFilesAndRejectsSymlinks() }),
            ("preparation readiness and disk capacity",{ try $0.testPreparationReadinessAndDiskCapacity() }),
            ("preparation checks actual FFmpeg identity",{ try $0.testPreparationRejectsUnrelatedExecutable() })
        ]
        var skipped = 0
        for (name,work) in cases {
            let test = ServiceTests(),before = failures
            do { try await work(test) }
            catch let skip as XCTSkip { skipped += 1; print("SKIP \(name): \(skip.message)") }
            catch { XCTFail("\(name): \(error)") }
            test.tearDown()
            if failures == before { print("PASS \(name)") }
        }
        print("Native contracts: \(cases.count) cases, \(failures) failures, \(skipped) skipped")
        if failures != 0 { exit(1) }
    }
}
