import AppKit
import Darwin
import SwiftUI

@main @MainActor
struct ModelManagerTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message:message) }
    }
    static func until(_ condition: () -> Bool, _ message: String) async throws {
        let deadline = Date().addingTimeInterval(15)
        while !condition() {
            try require(Date() < deadline,message)
            try await Task.sleep(for:.milliseconds(10))
        }
    }
    static func availablePort() throws -> Int {
        let fd = socket(AF_INET,SOCK_STREAM,0)
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to:&address) {
            $0.withMemoryRebound(to:sockaddr.self,capacity:1) { Darwin.bind(fd,$0,socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        var size = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to:&address) {
            $0.withMemoryRebound(to:sockaddr.self,capacity:1) { getsockname(fd,$0,&size) }
        }
        try require(fd >= 0 && bound == 0 && named == 0,"Cannot allocate isolated port")
        return Int(UInt16(bigEndian:address.sin_port))
    }
    static func main() async throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let root = URL(fileURLWithPath:CommandLine.arguments[2],isDirectory:true)
        let helper = try ModelManagementFixture.create(root:root,port:availablePort())
        let backend = BackendController(root:root)
        let audio = AudioWorkspace(recordingsDirectory:root.appendingPathComponent("recordings"))
        let defaults = UserDefaults(suiteName:"CyberAudio-ModelTests-\(UUID())")!
        defaults.set(false,forKey:"DictationEnabled")
        let dictation = DictationController(backend:backend,audio:audio,defaults:defaults)
        var starts = 0
        let preparation = PreparationController(root:root,helper:helper,onReady:{ starts += 1 })
        let models = ModelManagerController(backend:backend,audio:audio,dictation:dictation,preparation:preparation,defaults:defaults)
        defer { models.shutdown(); preparation.shutdown(); dictation.cleanup(); audio.cleanup(); backend.stopForTermination() }
        func flag(_ name: String) throws { try Data().write(to:root.appendingPathComponent(name)) }
        func remove(_ name: String) { try? FileManager.default.removeItem(at:root.appendingPathComponent(name)) }
        func model(_ id: String) throws -> ManagedModel {
            guard let value = models.snapshot?.models.first(where:{ $0.id == id }) else { throw Failure(message:"Missing fixture model \(id)") }
            return value
        }
        func options(_ value: [String:Any]) throws {
            try JSONSerialization.data(withJSONObject:value).write(to:root.appendingPathComponent("config/ui-fixture.json"),options:.atomic)
        }
        func identity() async throws -> String {
            let (data,_) = try await URLSession.shared.data(from:URL(string:backend.localURL+"/ready")!)
            return (try JSONSerialization.jsonObject(with:data) as! [String:Any])["instance"] as! String
        }
        func configuration() throws -> Data { try Data(contentsOf:root.appendingPathComponent("config/service.json")) }
        let token = try Data(contentsOf:root.appendingPathComponent("config/api-token"))
        preparation.check(startWhenReady:false)
        try await until({ preparation.ready && !preparation.busy },"Preparation did not become ready")
        models.refresh()
        try await until({ models.snapshot != nil && !models.busy },"Catalog did not load")
        try require(models.message.isEmpty,"Completed checks must clear the transient checking message")
        try require(starts == 0 && !backend.running,"Inspecting models must not start a stopped engine")
        backend.startPrepared()
        try await until({ backend.ready },"Fixture engine did not start")
        let originalInstance = try await identity(), original = try configuration()

        // Settings ownership is independent of the lifetime of its SwiftUI view.
        models.downloadSource = .modelScope
        try flag("hold-download")
        models.download(try model("qwen3-asr-0.6b-8bit"))
        try await until({ preparation.completedBytes > 0 },"Background progress was not published")
        let window = NSWindow(contentRect:NSRect(x:0,y:0,width:620,height:560),styleMask:[.titled],backing:.buffered,defer:false)
        window.contentView = NSHostingView(rootView:ModelSettingsView().environmentObject(models).environmentObject(backend).environmentObject(preparation))
        window.contentView = nil
        try await Task.sleep(for:.milliseconds(80))
        try require(models.operation == .downloading && preparation.ready && backend.ready,
            "Closing settings must preserve progress and the ready workspace")
        let continuedInstance = try await identity(), unchanged = try configuration()
        try require(continuedInstance == originalInstance && unchanged == original,"An inactive download must preserve the running engine and selection")
        models.download(try model("qwen3-tts-0.6b-8bit"))
        try require(models.queuedModelIDs == ["qwen3-tts-0.6b-8bit"],"A second model should queue behind the owned helper")
        models.cancel()
        try await until({ !models.busy },"Cancelled download did not release helper ownership")
        remove("hold-download")
        try require(preparation.ready && models.queuedModelIDs.isEmpty && starts == 0,"Cancellation must preserve first-run readiness without restarting")
        try flag("fail-download")
        models.download(try model("qwen3-asr-0.6b-8bit"))
        try await until({ !models.busy },"Failed download did not settle")
        try require(models.modelErrors["qwen3-asr-0.6b-8bit"] != nil && preparation.ready && backend.ready,"A download error must remain local to that model")
        remove("fail-download")

        await backend.stop()
        models.activate(try model("qwen3-tts-0.6b-4bit"))
        try await until({ !models.busy },"Stopped-engine selection did not finish")
        try require(!backend.running && backend.configuration?.ttsModelID == "qwen3-tts-0.6b-4bit" && !backend.supportsInstructions,
            "Stopped-engine activation only saves selection and catalog capabilities")
        backend.startPrepared()
        try await until({ backend.ready },"Selected fixture did not start")

        // Keep a real HTTP response admitted while activation starts. The
        // existing authenticated maintenance lease must drain it before restart.
        try options(["hold":true])
        let request = try backend.request(path:"/v1/audio/speech",method:"POST",body:Data(#"{"input":"排空测试","response_format":"wav"}"#.utf8),contentType:"application/json")
        let response = Task { try await backend.session.data(for:request) }
        let reports = root.appendingPathComponent("reports/ui-requests.jsonl")
        try await until({ (try? String(contentsOf:reports,encoding:.utf8).contains("started")) == true },"External request was not admitted")
        let beforeDrain = try configuration(), beforeDrainInstance = try await identity()
        try flag("hold-commit")
        models.activate(try model("qwen3-asr-0.6b-4bit"))
        try await until({ audio.managingModels },"Model maintenance did not acquire admission")
        var updated = false, updateSawSelection = false
        let gate = UpdateGate(audioBusy:{ audio.managingModels || preparation.busy },
            prepare:{ try await backend.prepareForUpdate(lease:$0) },resume:{ await backend.cancelUpdatePreparation(lease:$0) })
        gate.begin { updated = true; updateSawSelection = backend.configuration?.asrModelID == "qwen3-asr-0.6b-4bit" }
        try await Task.sleep(for:.milliseconds(250))
        let duringDrain = try configuration(), duringDrainInstance = try await identity()
        try require(!updated && gate.waiting && duringDrain == beforeDrain && duringDrainInstance == beforeDrainInstance,
            "Switching and application updates must wait for the admitted response")
        try options([:])
        let (audioData,httpResponse) = try await response.value
        try require((httpResponse as? HTTPURLResponse)?.statusCode == 200 && audioData.starts(with:Data("RIFF".utf8)),"Draining must preserve the original response")
        try await until({ backend.ready && backend.configuration?.asrModelID == "qwen3-asr-0.6b-4bit" && preparation.busy && !models.cancellable },
            "Candidate did not reach its transactional commit")
        let candidateRequest = try backend.request(path:"/v1/audio/speech",method:"POST",body:Data(#"{"input":"确认期间不得接收请求"}"#.utf8),contentType:"application/json")
        let (_,candidateResponse) = try await backend.session.data(for:candidateRequest)
        try require((candidateResponse as? HTTPURLResponse)?.statusCode == 503 && !updated,
            "A ready candidate must retain maintenance admission until the configuration is committed")
        remove("hold-commit")
        try await until({ !models.busy && updated },"Activation and queued update did not complete in order")
        try require(models.error == nil && updateSawSelection && backend.ready,"The update may only enter after successful model activation")
        gate.reset()
        try await Task.sleep(for:.milliseconds(100))

        // Local recording admission is retained until the user finishes or
        // cancels the switch; cancellation leaves the current engine alive.
        let beforeCancel = try configuration(), beforeCancelInstance = try await identity()
        audio.recording = true
        models.activate(try model("qwen3-asr-1.7b"))
        try await until({ audio.managingModels },"Recording wait did not begin")
        models.cancel()
        try await until({ !models.busy },"Cancelled activation did not settle")
        audio.recording = false
        let afterCancel = try configuration(), afterCancelInstance = try await identity()
        try require(beforeCancel == afterCancel && beforeCancelInstance == afterCancelInstance && !audio.managingModels,
            "A cancelled recording wait must preserve configuration and engine")

        // The candidate really starts, fails readiness, and is replaced by the
        // original synthetic engine using the byte-exact original config.
        try options(["fail_model_id":"qwen3-asr-1.7b"])
        let beforeFailure = try configuration()
        models.activate(try model("qwen3-asr-1.7b"))
        try await until({ !models.busy },"Failed candidate did not roll back")
        let afterFailure = try configuration(), preservedToken = try Data(contentsOf:root.appendingPathComponent("config/api-token"))
        try require(models.error != nil && backend.ready && backend.configuration?.asrModelID == "qwen3-asr-0.6b-4bit" && beforeFailure == afterFailure,
            "A failed candidate must restore the original configuration and reload the original model")
        try require(preservedToken == token,"Model changes must preserve the API credential")
        try options([:])
        try flag("hold-download")
        models.download(try model("qwen3-tts-0.6b-8bit"))
        try await until({ preparation.completedBytes > 0 },"Migration fixture download did not start")
        let movedDirectory = root.appendingPathComponent("new-model-storage")
        models.move(to:movedDirectory)
        try await until({ backend.configuration?.models_dir == movedDirectory.path && models.operation == .downloading && preparation.completedBytes > 0 },
            "Storage migration did not resume the paused download")
        try require(preparation.ready && backend.ready && !audio.managingModels && models.currentModelID == "qwen3-tts-0.6b-8bit",
            "Migration must resume the same background model after confirming the selected engine")
        remove("hold-download")
        try await until({ !models.busy },"Resumed background download did not finish")
        try require(models.error == nil && models.snapshot?.models.first(where:{ $0.id == "qwen3-tts-0.6b-8bit" })?.state == "ready",
            "The resumed model must finish without becoming active")
        print("ModelManager: shared progress after settings closure, queue/cancel/retry, inactive engine preservation, stopped selection, capabilities, real HTTP drain, candidate admission, update exclusion, candidate rollback and download resumption after migration passed")
    }
}
