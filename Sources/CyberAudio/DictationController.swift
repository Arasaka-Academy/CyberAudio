import AppKit
import ApplicationServices
import AVFoundation
import Combine

@MainActor
final class DictationController: ObservableObject {
    enum Phase { case idle, armed, recording, transcribing, inserting }
    @Published private(set) var phase = Phase.idle
    @Published private(set) var enabled: Bool
    @Published private(set) var shortcut: DictationShortcut
    @Published private(set) var microphoneGranted = false
    @Published private(set) var accessibilityGranted = false
    @Published private(set) var inputMonitoringGranted = false
    @Published private(set) var listenerRunning = false
    @Published private(set) var configuringShortcut = false
    @Published private(set) var statusText = "按住快捷键开始听写"
    var blocksUpdate: Bool { phase != .idle || insertion.hasPendingClipboard }
    var available: Bool { enabled && microphoneGranted && accessibilityGranted && inputMonitoringGranted && listenerRunning && !configuringShortcut }
    var permissionSummary: String {
        if !enabled { return "快捷听写已暂停" }
        if !microphoneGranted || !accessibilityGranted || !inputMonitoringGranted { return "完成下方授权后即可使用" }
        return listenerRunning ? "按住 \(shortcut.display) 录音，松开后在原输入框输出" : "键盘监听未就绪，请重新开启快捷听写"
    }
    private let backend: BackendController
    private let audio: AudioWorkspace
    private let defaults: UserDefaults
    private let monitor = ShortcutMonitor()
    private let environment = SystemInsertionEnvironment()
    private lazy var insertion = TextInsertionService(environment: environment, clipboard: PasteboardStore())
    private let overlay = DictationOverlay()
    private var gesture = DictationGesture()
    private var session = DictationSession()
    private var target: TextInsertionTarget?
    private var armTask: Task<Void, Never>?
    private var startTask: Task<Bool, Never>?
    private var workTask: Task<Void, Never>?
    private var watcher: Task<Void, Never>?
    private var refreshTask: Task<Void, Never>?
    private var hideTask: Task<Void, Never>?
    private var observers: [NSObjectProtocol] = []
    private var workspaceObservers: [NSObjectProtocol] = []
    private var lockObservers: [NSObjectProtocol] = []
    private var focusObserver: AXObserver?
    private var resultText = ""
    private var started = false
    private var sessionActive = true
    private var awake = true
    private var screenLocked = false

    init(backend: BackendController, audio: AudioWorkspace, defaults: UserDefaults = .standard) {
        self.backend = backend; self.audio = audio; self.defaults = defaults
        enabled = defaults.object(forKey: "DictationEnabled") as? Bool ?? true
        if let data = defaults.data(forKey: "DictationShortcut"), let value = try? JSONDecoder().decode(DictationShortcut.self, from: data), value.valid {
            shortcut = value
        } else { shortcut = .rightOption }
        monitor.onInput = { [weak self] input, origin in self?.handle(input, originPID: origin) }
        monitor.onInteraction = { [weak self] in
            guard let self, self.phase != .idle else { return }
            self.session.invalidateTarget()
        }
        monitor.onLoss = { [weak self] in self?.cancel(); self?.gesture.reset() }
    }

    func start() {
        guard !started else { return }; started = true
        observers.append(NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshPermissions() }
        })
        for name in [NSWorkspace.willSleepNotification, NSWorkspace.sessionDidResignActiveNotification,
                     NSWorkspace.didWakeNotification, NSWorkspace.sessionDidBecomeActiveNotification] {
            workspaceObservers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self else { return }
                    if name == NSWorkspace.willSleepNotification { self.awake = false }
                    if name == NSWorkspace.didWakeNotification { self.awake = true }
                    if name == NSWorkspace.sessionDidResignActiveNotification { self.sessionActive = false }
                    if name == NSWorkspace.sessionDidBecomeActiveNotification { self.sessionActive = true }
                    self.refreshPermissions()
                }
            })
        }
        // Screen lock is distinct from fast user switching on macOS.
        for locked in [true, false] {
            let name = Notification.Name(locked ? "com.apple.screenIsLocked" : "com.apple.screenIsUnlocked")
            lockObservers.append(DistributedNotificationCenter.default().addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.screenLocked = locked; self?.refreshPermissions() }
            })
        }
        workspaceObservers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.session.invalidateTarget() }
        })
        refreshPermissions()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard !Task.isCancelled else { return }
                self?.refreshPermissions()
            }
        }
    }

    func refreshPermissions() {
        if let state = CGSessionCopyCurrentDictionary() as? [String: Any] {
            screenLocked = state["CGSSessionScreenIsLocked"] as? Bool ?? false
        }
        microphoneGranted = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        accessibilityGranted = AXIsProcessTrusted() && CGPreflightPostEventAccess()
        inputMonitoringGranted = CGPreflightListenEventAccess()
        if enabled && microphoneGranted && accessibilityGranted && inputMonitoringGranted && !configuringShortcut && sessionActive && awake && !screenLocked {
            listenerRunning = monitor.start(shortcut: shortcut)
        } else {
            if blocksUpdate { cancel() }
            monitor.stop(); listenerRunning = false; gesture.reset()
        }
    }

    func setEnabled(_ value: Bool) {
        enabled = value; defaults.set(value, forKey: "DictationEnabled")
        refreshPermissions()
    }
    func setShortcut(_ value: DictationShortcut) {
        guard value.valid else { return }
        cancel(); monitor.stop(); gesture.reset()
        shortcut = value
        defaults.set(try? JSONEncoder().encode(value), forKey: "DictationShortcut")
        refreshPermissions()
    }
    func configureShortcut(_ active: Bool) {
        configuringShortcut = active
        if active { cancel(); gesture.reset() }
        refreshPermissions()
    }
    func requestMicrophone() async { _ = await AVCaptureDevice.requestAccess(for: .audio); refreshPermissions() }
    func requestAccessibility() {
        _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
        openPrivacy("Accessibility")
    }
    func requestInputMonitoring() { _ = CGRequestListenEventAccess(); openPrivacy("ListenEvent") }
    func openPrivacy(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_\(pane)") { NSWorkspace.shared.open(url) }
    }

    private func handle(_ input: ShortcutMatcher.Input, originPID: pid_t?) {
        switch input {
        case .press:
            guard available, !blocksUpdate else { return }
            guard backend.ready else {
                showFeedback("听写引擎尚未就绪", detail: "请在主窗口检查服务状态")
                return
            }
            guard !backend.busy, !audio.cannotStart else {
                showFeedback("正在处理其他音频", detail: "请稍后再按住快捷键")
                return
            }
            apply(gesture.press(at: ProcessInfo.processInfo.systemUptime), originPID: originPID)
        case .release: apply(gesture.release())
        case .interrupt: apply(gesture.interrupt())
        }
    }
    private func apply(_ actions: [DictationGesture.Action], originPID: pid_t? = nil) {
        for action in actions {
            switch action {
            case .arm: arm(originPID: originPID)
            case .record: record()
            case .submit: submit()
            case .cancel: cancel(showFeedback: phase != .armed)
            }
        }
    }
    func arm(originPID: pid_t?) {
        hideTask?.cancel()
        let id = session.begin()
        resultText = ""
        phase = .armed; statusText = "准备听写，继续按住快捷键开始录音"
        monitor.consumeEscape = true
        updateOverlay()
        target = environment.focusedTarget()
        if target == nil || target?.pid != originPID { session.invalidateTarget() }
        installFocusObserver()
        armTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(150))
            guard !Task.isCancelled, let self, self.session.id == id else { return }
            self.apply(self.gesture.advance(to: ProcessInfo.processInfo.systemUptime))
        }
        watcher = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard !Task.isCancelled, let self, self.session.id == id else { return }
                if self.phase != .inserting, self.session.canInsert(id), let target = self.target {
                    if let current = self.environment.focusedTarget(), target.matches(current) {} else { self.session.invalidateTarget() }
                }
                if self.phase == .recording && self.audio.recordingSeconds >= 59 { self.apply(self.gesture.limitReached()) }
                self.updateOverlay()
            }
        }
    }
    private func record() {
        guard let id = session.id, phase == .armed else { return }
        phase = .recording; statusText = "正在聆听，松开快捷键结束"
        updateOverlay()
        startTask = Task { [weak self] in
            guard let self else { return false }
            let success = await self.audio.beginRecording(requestPermission: false)
            guard self.session.id == id, !Task.isCancelled else { return false }
            if !success { self.finish(id, message: self.audio.errorMessage ?? "暂时无法开始录音", copy: false) }
            return success
        }
    }
    private func submit() {
        guard let id = session.id, phase == .recording else { return }
        phase = .transcribing; statusText = "正在本机识别语音…"
        updateOverlay()
        let pending = startTask
        workTask = Task { [weak self] in
            guard let self else { return }
            let recordingStarted = await pending?.value ?? self.audio.recording
            guard self.session.id == id else { return }
            guard recordingStarted, !Task.isCancelled else { self.finish(id, message: "已取消", copy: false); return }
            let outcome = await self.audio.stopAndTranscribe(using: self.backend, sessionID: id)
            guard self.session.id == id else { return }
            guard !Task.isCancelled else { self.finish(id, message: "已取消", copy: false); return }
            switch outcome {
            case .success(let completedID, let result) where completedID == id:
                self.resultText = result.text
                guard !result.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    self.finish(id, message: "未识别到语音", copy: false); return
                }
                guard self.session.canInsert(id), let target = self.target else {
                    self.finish(id, message: "输入位置已改变，结果已保留", copy: true); return
                }
                self.phase = .inserting; self.statusText = "正在输入文本…"; self.updateOverlay()
                let delivered = await self.insertion.insert(result.text, into: target, stillValid: { [weak self] in self?.session.canInsert(id) == true })
                switch delivered {
                case .inserted: self.finish(id, message: "已输入", copy: false)
                case .sent: self.finish(id, message: "粘贴已发出，请检查输入位置", copy: true)
                case .cancelled: self.finish(id, message: "已取消，结果已保留", copy: true)
                case .retained(let message): self.finish(id, message: message, copy: true)
                }
            case .failed(_, let message): self.finish(id, message: message, copy: false)
            default: self.finish(id, message: "已取消", copy: false)
            }
        }
    }

    func cancel(showFeedback: Bool = true) {
        guard let id = session.id else { overlay.hide(); return }
        _ = gesture.interrupt()
        session.invalidateTarget()
        armTask?.cancel(); startTask?.cancel()
        audio.cancelRecording()
        if workTask != nil {
            audio.cancelTranscription(); workTask?.cancel()
            // insert() owns clipboard restoration; stay busy until its defer has run.
        } else { finish(id, message: showFeedback ? "已取消" : "", copy: false) }
    }

    private func finish(_ id: UUID, message: String, copy: Bool) {
        guard session.id == id else { return }
        session.finish(id); phase = .idle
        armTask?.cancel(); watcher?.cancel()
        startTask = nil; workTask = nil; target = nil
        removeFocusObserver()
        monitor.consumeEscape = false
        statusText = message.isEmpty ? "按住快捷键开始听写" : message
        guard started, enabled, sessionActive, awake, !screenLocked, !configuringShortcut, !message.isEmpty else {
            overlay.hide(); return
        }
        showFeedback(message, detail: copy ? "可复制，或在主窗口查看" : "CyberAudio", copy: copy)
    }

    private func showFeedback(_ message: String, detail: String, copy: Bool = false) {
        statusText = message
        overlay.show(title: message, detail: detail, levels: [], seconds: nil,
                     cancel: nil, copy: copy ? { [weak self] in self?.copyResult() } : nil)
        hideTask?.cancel()
        hideTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(copy ? 5 : 1.5))
            guard !Task.isCancelled else { return }; self?.overlay.hide()
        }
    }

    private func updateOverlay() {
        guard phase != .idle else { return }
        let title = phase == .armed ? "准备听写" : phase == .recording ? "正在聆听" : phase == .inserting ? "正在输入" : "正在识别"
        let detail = phase == .armed ? "继续按住 \(shortcut.display) 开始录音 · Esc 取消"
            : phase == .recording ? "松开 \(shortcut.display) 结束 · Esc 取消" : "在这台 Mac 本地处理"
        overlay.show(title: title, detail: detail,
                     levels: phase == .recording ? audio.recordingLevels : [],
                     seconds: phase == .recording ? audio.recordingSeconds : nil,
                     cancel: { [weak self] in self?.cancel() }, copy: nil)
    }
    private func copyResult() {
        guard !resultText.isEmpty else { return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(resultText, forType: .string)
        overlay.hide()
    }
    private func installFocusObserver() {
        removeFocusObserver()
        guard let target else { return }
        var observer: AXObserver?
        guard AXObserverCreate(target.pid, { _, _, notification, context in
            guard let context else { return }
            let controller = Unmanaged<DictationController>.fromOpaque(context).takeUnretainedValue()
            MainActor.assumeIsolated {
                // Our own paste changes selection and value after the final target check.
                let textChanged = [kAXSelectedTextChangedNotification, kAXValueChangedNotification].contains(notification as String)
                if controller.phase != .inserting || !textChanged { controller.session.invalidateTarget() }
            }
        }, &observer) == .success, let observer else { return }
        focusObserver = observer
        let app = AXUIElementCreateApplication(target.pid)
        for name in [kAXFocusedUIElementChangedNotification, kAXFocusedWindowChangedNotification] {
            AXObserverAddNotification(observer, app, name as CFString, Unmanaged.passUnretained(self).toOpaque())
        }
        for name in [kAXSelectedTextChangedNotification, kAXValueChangedNotification] {
            AXObserverAddNotification(observer, target.element, name as CFString, Unmanaged.passUnretained(self).toOpaque())
        }
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .commonModes)
    }
    private func removeFocusObserver() {
        if let focusObserver { CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(focusObserver), .commonModes) }
        focusObserver = nil
    }
    func cleanup() {
        cancel(showFeedback: false)
        armTask?.cancel(); startTask?.cancel(); workTask?.cancel(); watcher?.cancel(); refreshTask?.cancel(); hideTask?.cancel()
        insertion.restorePendingClipboard()
        monitor.stop(); removeFocusObserver(); overlay.hide()
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        for observer in workspaceObservers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        for observer in lockObservers { DistributedNotificationCenter.default().removeObserver(observer) }
        observers.removeAll(); workspaceObservers.removeAll(); lockObservers.removeAll()
        listenerRunning = false; started = false
    }
}
