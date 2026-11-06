import AppKit
import ApplicationServices

struct TextInsertionTarget {
    let pid: pid_t
    let element: AXUIElement
    let selection: NSRange?
    let value: String?
    let editable: Bool
    let secure: Bool

    func matches(_ other: TextInsertionTarget) -> Bool {
        pid == other.pid && CFEqual(element, other.element) && editable && other.editable && !secure && !other.secure
            && (selection == nil || selection == other.selection) && (value == nil || value == other.value)
    }
}

@MainActor
protocol InsertionEnvironment {
    func focusedTarget() -> TextInsertionTarget?
    var canPostEvents: Bool { get }
    var modifiersReleased: Bool { get }
    func postPaste(to pid: pid_t) -> Bool
}

struct PasteboardContents: Equatable { let items: [[String: Data]] }
struct PasteboardSnapshot { let contents: PasteboardContents; let changeCount: Int }

@MainActor
final class PasteboardStore {
    static let ownerType = NSPasteboard.PasteboardType("local.cyberaudio.dictation.owner")
    let pasteboard: NSPasteboard
    init(_ pasteboard: NSPasteboard = .general) { self.pasteboard = pasteboard }
    func snapshot() -> PasteboardSnapshot? {
        let count = pasteboard.changeCount
        var items: [[String: Data]] = []
        for item in pasteboard.pasteboardItems ?? [] {
            var values: [String: Data] = [:]
            for type in item.types {
                guard let data = item.data(forType: type) else { return nil }
                values[type.rawValue] = data
            }
            items.append(values)
        }
        guard pasteboard.changeCount == count,
              !items.isEmpty || (pasteboard.types ?? []).isEmpty else { return nil }
        return PasteboardSnapshot(contents: PasteboardContents(items: items), changeCount: count)
    }
    func write(_ text: String, owner: String, restoring backup: PasteboardSnapshot) -> Int? {
        let item = NSPasteboardItem()
        guard item.setString(text, forType: .string), item.setString(owner, forType: Self.ownerType) else { return nil }
        guard pasteboard.changeCount == backup.changeCount else { return nil }
        let emptyCount = pasteboard.clearContents()
        guard pasteboard.writeObjects([item]) else {
            if pasteboard.changeCount == emptyCount { replace(with: backup.contents) }
            return nil
        }
        return pasteboard.changeCount
    }
    func restore(_ snapshot: PasteboardSnapshot, owner: String, changeCount: Int) {
        guard pasteboard.changeCount == changeCount, pasteboard.string(forType: Self.ownerType) == owner else { return }
        replace(with: snapshot.contents)
    }
    private func replace(with contents: PasteboardContents) {
        let items = contents.items.map { values in
            let item = NSPasteboardItem()
            for (type, data) in values { item.setData(data, forType: NSPasteboard.PasteboardType(type)) }
            return item
        }
        pasteboard.clearContents()
        if !items.isEmpty { pasteboard.writeObjects(items) }
    }
}

enum TextInsertionOutcome: Equatable {
    case inserted, sent, cancelled
    case retained(String)
}

@MainActor
final class TextInsertionService {
    let environment: InsertionEnvironment
    let clipboard: PasteboardStore
    private struct PendingClipboard { let backup: PasteboardSnapshot; let owner: String; let changeCount: Int }
    private var pendingClipboard: PendingClipboard?
    var hasPendingClipboard: Bool { pendingClipboard != nil }
    init(environment: InsertionEnvironment, clipboard: PasteboardStore) {
        self.environment = environment
        self.clipboard = clipboard
    }
    func insert(_ text: String, into target: TextInsertionTarget, stillValid: () -> Bool) async -> TextInsertionOutcome {
        if Task.isCancelled { return .cancelled }
        guard !hasPendingClipboard else { return .retained("正在完成上次输入，结果已保留") }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return .retained("未识别到语音") }
        guard environment.canPostEvents else { return .retained("请先开启辅助功能权限") }
        guard target.editable, !target.secure, stillValid(),
              let current = environment.focusedTarget(), target.matches(current) else {
            return .retained("输入位置已改变，结果已保留")
        }
        guard environment.modifiersReleased else { return .retained("请松开快捷键，结果已保留") }
        guard let backup = clipboard.snapshot() else { return .retained("剪贴板暂不可用，结果已保留") }
        guard stillValid(), let latest = environment.focusedTarget(), target.matches(latest), !Task.isCancelled else {
            return .retained("输入位置已改变，结果已保留")
        }
        let owner = UUID().uuidString
        guard let count = clipboard.write(text, owner: owner, restoring: backup) else { return .retained("无法准备输入，结果已保留") }
        pendingClipboard = PendingClipboard(backup: backup, owner: owner, changeCount: count)
        defer { if pendingClipboard?.owner == owner { restorePendingClipboard() } }
        guard stillValid(), !Task.isCancelled, environment.modifiersReleased,
              let final = environment.focusedTarget(), target.matches(final) else {
            return .retained("输入位置已改变，结果已保留")
        }
        guard environment.postPaste(to: target.pid) else { return .retained("无法自动输入，结果已保留") }
        // Once Cmd+V is posted, the target still needs time to read the clipboard.
        // Cancellation must not restore it early or let an update interrupt delivery.
        await Task.detached { try? await Task.sleep(for: .milliseconds(500)) }.value
        if Task.isCancelled { return .cancelled }
        // A posted event is not proof of insertion. Verify where the control exposes text;
        // unobservable editors get a neutral "sent" status and are never retried.
        guard let before = target.value, let selection = target.selection,
              selection.location <= (before as NSString).length,
              selection.length <= (before as NSString).length - selection.location,
              let after = environment.focusedTarget(), after.pid == target.pid,
              CFEqual(after.element, target.element), let value = after.value else { return .sent }
        let expected = (before as NSString).replacingCharacters(in: selection, with: text)
        return value == expected ? .inserted : .retained("无法确认输入，结果已保留")
    }
    // App termination cannot wait for an asynchronous task's defer to run.
    func restorePendingClipboard() {
        guard let pending = pendingClipboard else { return }
        pendingClipboard = nil
        clipboard.restore(pending.backup, owner: pending.owner, changeCount: pending.changeCount)
    }
}

@MainActor
final class SystemInsertionEnvironment: InsertionEnvironment {
    // Identifies only our paste events, so the shortcut listener ignores them.
    static let eventTag: Int64 = 0x4359424552415544
    var canPostEvents: Bool { AXIsProcessTrusted() && CGPreflightPostEventAccess() }
    var modifiersReleased: Bool {
        CGEventSource.flagsState(.combinedSessionState).intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).isEmpty
    }
    func focusedTarget() -> TextInsertionTarget? {
        guard AXIsProcessTrusted(), let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return nil }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.05)
        guard let raw = attribute(app, kAXFocusedUIElementAttribute), CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
        let element = unsafeBitCast(raw, to: AXUIElement.self)
        AXUIElementSetMessagingTimeout(element, 0.05)
        let role = attribute(element, kAXRoleAttribute) as? String ?? ""
        let subrole = attribute(element, kAXSubroleAttribute) as? String ?? ""
        let secure = subrole == kAXSecureTextFieldSubrole || subrole.lowercased().contains("secure")
        guard !secure else { return TextInsertionTarget(pid: pid, element: element, selection: nil, value: nil, editable: false, secure: true) }
        var settable = DarwinBoolean(false)
        let canSetValue = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable) == .success && settable.boolValue
        let editable = [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole].contains(role)
            && (attribute(element, kAXEnabledAttribute) as? Bool ?? true)
            && (attribute(element, kAXIsEditableAttribute) as? Bool ?? canSetValue)
        var selection: NSRange?
        if let rawRange = attribute(element, kAXSelectedTextRangeAttribute), CFGetTypeID(rawRange) == AXValueGetTypeID() {
            var range = CFRange()
            if AXValueGetValue(unsafeBitCast(rawRange, to: AXValue.self), .cfRange, &range), range.location >= 0, range.length >= 0 {
                selection = NSRange(location: range.location, length: range.length)
            }
        }
        let value = attribute(element, kAXValueAttribute) as? String
        return TextInsertionTarget(pid: pid, element: element, selection: selection, value: value, editable: editable, secure: secure)
    }
    func postPaste(to pid: pid_t) -> Bool {
        guard canPostEvents, let source = CGEventSource(stateID: .privateState),
              let down = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false) else { return false }
        for event in [down, up] {
            event.flags = .maskCommand
            event.setIntegerValueField(.eventSourceUserData, value: Self.eventTag)
            event.postToPid(pid)
        }
        return true
    }
    private func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }
}
