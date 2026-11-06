import AppKit
import Combine
import OSLog
import Sparkle

/// Keeps Sparkle's native UI and intercepts every user-driven relaunch before quitting.
@MainActor
private final class AudioUpdateUserDriver: SPUStandardUserDriver {
    weak var owner: UpdaterController?
    private var expected: UInt64 = 0
    private var received: UInt64 = 0

    override func showUpdateFound(with appcastItem: SUAppcastItem, state: SPUUserUpdateState,
                                  reply: @escaping (SPUUserUpdateChoice) -> Void) {
        super.showUpdateFound(with: appcastItem, state: state) { [weak self] choice in
            if choice == .install, state.stage == .installing, let owner = self?.owner {
                owner.installWhenIdle({ reply(.install) }, cancelled: { reply(.dismiss) })
            } else {
                if choice == .skip { self?.owner?.cancelPreparedUpdate() }
                reply(choice)
            }
        }
    }

    override func showReady(toInstallAndRelaunch reply: @escaping (SPUUserUpdateChoice) -> Void) {
        owner?.markReady()
        super.showReady(toInstallAndRelaunch: { [weak self] choice in
            if choice == .install, let owner = self?.owner {
                owner.installWhenIdle({ reply(.install) }, cancelled: { [weak owner] in
                    owner?.cancelPreparedUpdate()
                    reply(.skip)
                })
            } else {
                if choice == .skip { self?.owner?.cancelPreparedUpdate() }
                reply(choice)
            }
        })
    }

    override func showDownloadDidReceiveExpectedContentLength(_ expectedContentLength: UInt64) {
        expected = expectedContentLength
        received = 0
        super.showDownloadDidReceiveExpectedContentLength(expectedContentLength)
    }

    override func showDownloadDidReceiveData(ofLength length: UInt64) {
        received += length
        owner?.downloadProgress = expected > 0 ? min(1, Double(received) / Double(expected)) : nil
        super.showDownloadDidReceiveData(ofLength: length)
    }
}

@MainActor
final class UpdaterController: NSObject, ObservableObject, SPUUpdaterDelegate, @preconcurrency SPUStandardUserDriverDelegate {
    @Published private(set) var status = "自动保持最新版本"
    @Published private(set) var availableVersion: String?
    @Published private(set) var errorMessage: String?
    @Published private(set) var canCheck = false
    @Published private(set) var downloading = false
    @Published private(set) var pendingInstallation = false
    @Published var downloadProgress: Double?
    let gate: UpdateGate
    private var updater: SPUUpdater!
    private var driver: AudioUpdateUserDriver!
    private var observations: [NSKeyValueObservation] = []
    private var subscriptions = Set<AnyCancellable>()
    private var automaticInstall: (() -> Void)?
    private var confirmingInstallation = false
    private var installationAlert: NSAlert?
    private var started = false
    private let backend: BackendController
    private let log = Logger(subsystem: Bundle.main.bundleIdentifier ?? "local.cyberaudio.mac", category: "updates")

    init(backend: BackendController, audio: AudioWorkspace, dictation: DictationController,
         preparationBusy: @escaping () -> Bool = { false }) {
        self.backend = backend
        gate = UpdateGate(audioBusy: { audio.working || audio.playing || dictation.blocksUpdate || preparationBusy() },
                          prepare: { try await backend.prepareForUpdate(lease: $0) },
                          resume: { await backend.cancelUpdatePreparation(lease: $0) })
        super.init()
        driver = AudioUpdateUserDriver(hostBundle: .main, delegate: self)
        driver.owner = self
        updater = SPUUpdater(hostBundle: .main, applicationBundle: .main, userDriver: driver, delegate: self)
        gate.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }.store(in: &subscriptions)
        gate.$waiting.combineLatest(gate.$terminationAllowed).sink { waiting, allowed in
            audio.preparingUpdate = waiting || allowed
        }.store(in: &subscriptions)
        observations = [
            updater.observe(\.canCheckForUpdates, options: [.initial, .new]) { [weak self] updater, _ in
                Task { @MainActor in self?.canCheck = updater.canCheckForUpdates }
            },
            updater.observe(\.automaticallyChecksForUpdates) { [weak self] _, _ in self?.objectWillChange.send() },
            updater.observe(\.automaticallyDownloadsUpdates) { [weak self] _, _ in self?.objectWillChange.send() },
            updater.observe(\.lastUpdateCheckDate) { [weak self] _, _ in self?.objectWillChange.send() }
        ]
    }

    var automaticChecks: Bool {
        get { updater.automaticallyChecksForUpdates }
        set { updater.automaticallyChecksForUpdates = newValue }
    }
    var automaticDownloads: Bool {
        get { updater.automaticallyDownloadsUpdates }
        set { updater.automaticallyDownloadsUpdates = newValue }
    }
    var lastCheck: Date? { updater.lastUpdateCheckDate }
    var version: String {
        let info = Bundle.main.infoDictionary ?? [:]
        return "\(info["CFBundleShortVersionString"] as? String ?? "—")（\(info["CFBundleVersion"] as? String ?? "—")）"
    }
    var menuTitle: String {
        if gate.waiting { return "取消等待更新" }
        return pendingInstallation ? "安装更新并重启…" : (availableVersion == nil ? "检查更新…" : "查看更新…")
    }
    var canPerformAction: Bool { !confirmingInstallation && (gate.waiting || automaticInstall != nil || canCheck) }
    var displayStatus: String { gate.waiting || gate.terminationAllowed || gate.failure != nil ? gate.message : status }

    func start() {
        guard !started else { return }
        started = true
        do { try updater.start() }
        catch { errorMessage = "更新器无法启动：\(error.localizedDescription)"; status = "更新暂不可用" }
    }

    func checkForUpdates() {
        if gate.waiting { gate.cancel(); return }
        errorMessage = nil
        if let automaticInstall {
            log.notice("Requesting confirmation before update relaunch")
            let alert = NSAlert()
            alert.messageText = "安装更新并重启 CyberAudio？"
            alert.informativeText = "请先复制需要的文字并保存音频。当前任务结束后再重启；模型、配置与运行环境会保留。"
            alert.addButton(withTitle: "安装并重启")
            alert.addButton(withTitle: "稍后")
            NSApp.activate(ignoringOtherApps: true)
            installationAlert = alert
            confirmingInstallation = true
            objectWillChange.send()
            let confirmed: (NSApplication.ModalResponse) -> Void = { [weak self] response in
                guard let self else { return }
                self.confirmingInstallation = false
                self.installationAlert = nil
                self.objectWillChange.send()
                self.log.notice("Update confirmation response: \(response.rawValue)")
                if response == .alertFirstButtonReturn { self.installWhenIdle(automaticInstall) }
            }
            // Present after the initiating menu/accessibility action has returned.
            DispatchQueue.main.async {
                let window = NSApp.keyWindow ?? NSApp.mainWindow
                    ?? NSApp.windows.first { $0.isVisible && $0.canBecomeKey }
                if let window { alert.beginSheetModal(for: window, completionHandler: confirmed) }
                else { confirmed(alert.runModal()) }
            }
        } else if canCheck {
            NSApp.activate(ignoringOtherApps: true)
            updater.checkForUpdates()
        }
    }

    func installWhenIdle(_ action: @escaping () -> Void, cancelled: @escaping () -> Void = {}) {
        log.notice("Waiting for audio and inference before update installation")
        gate.begin(install: { [backend] in
            backend.preserveRootForUpdateRelaunch()
            action()
        }, cancelled: cancelled)
    }

    func cancelPreparedUpdate() {
        pendingInstallation = false
        automaticInstall = nil
        availableVersion = nil
        status = "已取消此次更新，可重新检查"
    }

    func markReady() {
        pendingInstallation = true
        downloading = false
        downloadProgress = nil
        status = "更新已就绪，确认重启或退出时安装"
    }

    var supportsGentleScheduledUpdateReminders: Bool { true }
    func standardUserDriverShouldHandleShowingScheduledUpdate(_ update: SUAppcastItem, andInImmediateFocus immediateFocus: Bool) -> Bool { false }
    func standardUserDriverWillHandleShowingUpdate(_ handleShowingUpdate: Bool, forUpdate update: SUAppcastItem, state: SPUUserUpdateState) {
        availableVersion = update.displayVersionString
        if !pendingInstallation { status = "发现新版本 \(update.displayVersionString)" }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        availableVersion = item.displayVersionString
        status = "发现新版本 \(item.displayVersionString)"
    }
    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: Error) {
        status = "当前已是最新的兼容版本"
        errorMessage = nil
        availableVersion = nil
    }
    func updater(_ updater: SPUUpdater, willDownloadUpdate item: SUAppcastItem, with request: NSMutableURLRequest) {
        downloading = true
        downloadProgress = nil
        status = "正在下载 \(item.displayVersionString)…"
    }
    func updater(_ updater: SPUUpdater, didDownloadUpdate item: SUAppcastItem) {
        downloading = false
        status = "正在验证更新…"
    }
    func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem, immediateInstallationBlock: @escaping () -> Void) -> Bool {
        automaticInstall = immediateInstallationBlock
        markReady()
        return true
    }
    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem, untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        log.notice("Sparkle requested relaunch; task gate allowed: \(self.gate.terminationAllowed)")
        guard !gate.terminationAllowed else { return false }
        installWhenIdle(installHandler)
        return true
    }
    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        downloading = false
        if (error as NSError).code == 1001 { return } // Sparkle's no-update result is informational.
        pendingInstallation = false
        automaticInstall = nil
        errorMessage = error.localizedDescription
        status = "更新未完成，可重试"
        UserDefaults.standard.removeObject(forKey: "PendingUpdateDataDirectory")
        gate.reset()
    }
}
