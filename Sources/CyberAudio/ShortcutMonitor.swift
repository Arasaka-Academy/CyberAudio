import AppKit
import ApplicationServices

@MainActor
final class ShortcutMonitor {
    var onInput: ((ShortcutMatcher.Input, pid_t?) -> Void)?
    var onInteraction: (() -> Void)?
    var onLoss: (() -> Void)?
    var consumeEscape = false
    private var matcher = ShortcutMatcher()
    private var swallowed = Set<UInt16>()
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var watchdog: Task<Void, Never>?
    private var waitingForRelease = false
    var running: Bool { tap != nil }

    func start(shortcut: DictationShortcut) -> Bool {
        if running { return true }
        matcher = ShortcutMatcher(shortcut: shortcut)
        let events: [CGEventType] = [.keyDown, .keyUp, .flagsChanged, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        let mask: CGEventMask = events
            .reduce(0) { $0 | (1 << $1.rawValue) }
        guard let port = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                          options: .defaultTap, eventsOfInterest: mask, callback: { _, type, event, context in
            guard let context else { return Unmanaged.passUnretained(event) }
            return MainActor.assumeIsolated {
                Unmanaged<ShortcutMonitor>.fromOpaque(context).takeUnretainedValue().receive(type, event)
            }
        }, userInfo: Unmanaged.passUnretained(self).toOpaque()) else { return false }
        tap = port
        source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, port, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: port, enable: true)
        waitingForRelease = physicallyHeld
        watchdog = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard !Task.isCancelled, let self else { return }
                if self.waitingForRelease {
                    if !self.physicallyHeld { self.waitingForRelease = false }
                } else if let input = self.matcher.reconcile(physicallyHeld: self.physicallyHeld) {
                    self.onInput?(input, nil)
                }
            }
        }
        return true
    }

    func stop() {
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        if let tap { CFMachPortInvalidate(tap) }
        source = nil; tap = nil
        watchdog?.cancel(); watchdog = nil
        swallowed.removeAll()
        matcher = ShortcutMatcher(shortcut: matcher.shortcut)
        consumeEscape = false
    }

    private var physicallyHeld: Bool {
        // HID state is independent of key events swallowed by our session tap.
        if matcher.shortcut.kind == .rightOption { return CGEventSource.keyState(.hidSystemState, key: 61) }
        let flags = CGEventSource.flagsState(.hidSystemState).rawValue & DictationShortcut.modifierMask
        return CGEventSource.keyState(.hidSystemState, key: matcher.shortcut.keyCode)
            && flags & matcher.shortcut.modifiers == matcher.shortcut.modifiers
    }

    private func receive(_ type: CGEventType, _ event: CGEvent) -> Unmanaged<CGEvent>? {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            matcher = ShortcutMatcher(shortcut: matcher.shortcut)
            waitingForRelease = physicallyHeld
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            DispatchQueue.main.async { [weak self] in self?.onLoss?() }
            return Unmanaged.passUnretained(event)
        }
        if event.getIntegerValueField(.eventSourceUserData) == SystemInsertionEnvironment.eventTag { return Unmanaged.passUnretained(event) }
        let code = UInt16(event.getIntegerValueField(.keyboardEventKeycode))
        if waitingForRelease {
            if !physicallyHeld { waitingForRelease = false }
            if type == .keyUp, swallowed.remove(code) != nil { return nil }
            return Unmanaged.passUnretained(event)
        }
        var consume = false
        var input: ShortcutMatcher.Input?
        switch type {
        case .keyDown:
            if code == 53, consumeEscape {
                swallowed.insert(code)
                DispatchQueue.main.async { [weak self] in self?.onLoss?() }
                return nil
            }
            input = matcher.handle(.down(code, event.flags.rawValue, event.getIntegerValueField(.keyboardEventAutorepeat) != 0))
            if matcher.shortcut.kind == .combination, code == matcher.shortcut.keyCode,
               event.flags.rawValue & DictationShortcut.modifierMask == matcher.shortcut.modifiers {
                swallowed.insert(code); consume = true
            } else {
                DispatchQueue.main.async { [weak self] in self?.onInteraction?() }
            }
        case .keyUp:
            consume = swallowed.remove(code) != nil
            input = matcher.handle(.up(code))
        case .flagsChanged:
            // Device-specific flags distinguish right release even when left Option remains down.
            let rightDown = event.flags.rawValue & 0x40 != 0
            input = matcher.handle(.flags(code, event.flags.rawValue, rightDown))
        case .leftMouseDown, .rightMouseDown, .otherMouseDown:
            if matcher.held { input = .interrupt }
            DispatchQueue.main.async { [weak self] in self?.onInteraction?() }
        default: break
        }
        if let input {
            let origin = input == .press ? NSWorkspace.shared.frontmostApplication?.processIdentifier : nil
            DispatchQueue.main.async { [weak self] in self?.onInput?(input, origin) }
        }
        return consume ? nil : Unmanaged.passUnretained(event)
    }
}
