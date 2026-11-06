import AppKit
import ApplicationServices

@MainActor
private final class InputEnvironment: InsertionEnvironment {
    var target = TextInsertionTarget(pid: 99999, element: AXUIElementCreateApplication(99999),
                                    selection: NSRange(location: 2, length: 2), value: "前文旧文后文", editable: true, secure: false)
    var canPostEvents = true
    var modifiersReleased = true
    var posts = 0
    var onPaste: (() -> Void)?
    func focusedTarget() -> TextInsertionTarget? { target }
    func postPaste(to pid: pid_t) -> Bool { posts += 1; onPaste?(); return pid == 99999 }
    func replaceValue(_ text: String) {
        target = TextInsertionTarget(pid: target.pid, element: target.element,
                                    selection: NSRange(location: 2 + (text as NSString).length, length: 0),
                                    value: "前文\(text)后文", editable: true, secure: false)
    }
}

@main
@MainActor
struct TextInsertionTests {
    struct Failure: Error { let message: String }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw Failure(message: message) }
    }
    static func main() async throws {
        let board = NSPasteboard(name: .init("CyberAudio-tests-\(UUID())"))
        defer { board.releaseGlobally() }
        let store = PasteboardStore(board)
        let original = NSPasteboardItem()
        original.setString("原来的剪贴板", forType: .string)
        original.setData(Data([1, 2, 3, 0, 255]), forType: .init("test.binary"))
        let extra = NSPasteboardItem()
        extra.setString("另一个剪贴板条目", forType: .string)
        extra.setData(Data("{\\rtf1 original}".utf8), forType: .rtf)
        board.clearContents(); board.writeObjects([original, extra])
        let backup = store.snapshot()!
        let environment = InputEnvironment()
        let service = TextInsertionService(environment: environment, clipboard: store)
        environment.onPaste = {
            if board.string(forType: .string) == "你好 Hello 123" { environment.replaceValue("你好 Hello 123") }
        }
        let success = await service.insert("你好 Hello 123", into: environment.target, stillValid: { true })
        try require(success == .inserted, "A normal paste replaces the selected range with the exact Unicode text")
        try require(environment.posts == 1, "Only one paste is sent; Return is never posted")
        try require(store.snapshot()?.contents == backup.contents, "All original clipboard formats are restored")

        let oldTarget = environment.target
        environment.target = TextInsertionTarget(pid: 123, element: AXUIElementCreateApplication(123), selection: nil,
                                                value: nil, editable: true, secure: false)
        let switched = await service.insert("不得写入", into: oldTarget, stillValid: { true })
        try require(isRetained(switched) && environment.posts == 1 && store.snapshot()?.contents == backup.contents, "Changed application must not receive text or mutate the clipboard")

        let other = InputEnvironment()
        let second = TextInsertionService(environment: other, clipboard: store)
        other.onPaste = {
            other.replaceValue("新的结果")
            board.clearContents(); board.setString("用户刚复制的内容", forType: .string)
        }
        _ = await second.insert("新的结果", into: other.target, stillValid: { true })
        try require(board.string(forType: .string) == "用户刚复制的内容", "Restoration must never overwrite a user's newer copy")

        let resets = backup.contents.items.map { values in
            let item = NSPasteboardItem()
            for (type, data) in values { item.setData(data, forType: .init(type)) }
            return item
        }
        board.clearContents(); board.writeObjects(resets)
        let cancelledEnvironment = InputEnvironment()
        let cancellable = TextInsertionService(environment: cancelledEnvironment, clipboard: store)
        let pasteStartedAt = ProcessInfo.processInfo.systemUptime
        let task = Task { await cancellable.insert("取消中的文本", into: cancelledEnvironment.target, stillValid: { true }) }
        while cancelledEnvironment.posts == 0 { await Task.yield() }
        task.cancel()
        try await Task.sleep(for: .milliseconds(50))
        try require(cancellable.hasPendingClipboard && board.string(forType: .string) == "取消中的文本", "Cancellation must preserve the 500 ms paste delivery window")
        let result = await task.value
        try require(result == .cancelled && store.snapshot()?.contents == backup.contents, "Cancellation still restores the clipboard before finishing")
        try require(ProcessInfo.processInfo.systemUptime - pasteStartedAt >= 0.5, "Cancellation cannot shorten the delivery lease")

        let drainingEnvironment = InputEnvironment()
        let draining = TextInsertionService(environment: drainingEnvironment, clipboard: store)
        var prepared = false, installed = false, preparedWhileBusy = false
        var contentsAtPreparation: PasteboardContents?
        let gate = UpdateGate(audioBusy: { draining.hasPendingClipboard }, prepare: { _ in
            prepared = true
            preparedWhileBusy = draining.hasPendingClipboard
            contentsAtPreparation = store.snapshot()?.contents
            return true
        }, resume: { _ in })
        let insertionTask = Task { await draining.insert("更新前完成输入", into: drainingEnvironment.target, stillValid: { true }) }
        while drainingEnvironment.posts == 0 { await Task.yield() }
        gate.begin { installed = true }
        try await Task.sleep(for: .milliseconds(50))
        try require(!prepared && !installed, "Update preparation must wait for the clipboard delivery lease")
        _ = await insertionTask.value
        let deadline = Date().addingTimeInterval(3)
        while !installed && Date() < deadline { try await Task.sleep(for: .milliseconds(10)) }
        try require(installed && !preparedWhileBusy && contentsAtPreparation == backup.contents, "Updates start only after clipboard restoration has finished")

        let unavailable = InputEnvironment()
        unavailable.canPostEvents = false
        let guarded = TextInsertionService(environment: unavailable, clipboard: store)
        let denied = await guarded.insert("权限不足", into: unavailable.target, stillValid: { true })
        try require(isRetained(denied) && unavailable.posts == 0 && store.snapshot()?.contents == backup.contents, "Permission denial preserves both text and clipboard")
        unavailable.canPostEvents = true
        unavailable.modifiersReleased = false
        let held = await guarded.insert("仍有修饰键", into: unavailable.target, stillValid: { true })
        try require(isRetained(held) && unavailable.posts == 0, "Held modifiers must not produce a modified paste")
        unavailable.modifiersReleased = true
        let invalid = await guarded.insert("已经失焦", into: unavailable.target, stillValid: { false })
        try require(isRetained(invalid) && unavailable.posts == 0, "Sticky focus invalidation cannot be reversed by returning to the app")
        let empty = await guarded.insert("  \n", into: unavailable.target, stillValid: { true })
        try require(isRetained(empty) && unavailable.posts == 0, "Empty recognition results must not paste or press Return")

        for secure in [true, false] {
            let target = TextInsertionTarget(pid: unavailable.target.pid, element: unavailable.target.element,
                                             selection: nil, value: nil, editable: secure, secure: secure)
            let refused = await guarded.insert("不应输入", into: target, stillValid: { true })
            try require(isRetained(refused) && unavailable.posts == 0, "Secure and read-only controls must retain the result")
        }
        let sameApp = InputEnvironment()
        let anotherControl = TextInsertionTarget(pid: sameApp.target.pid, element: AXUIElementCreateSystemWide(),
                                                 selection: sameApp.target.selection, value: sameApp.target.value, editable: true, secure: false)
        let sameAppService = TextInsertionService(environment: sameApp, clipboard: store)
        let moved = await sameAppService.insert("另一个输入框", into: anotherControl, stillValid: { true })
        try require(isRetained(moved) && sameApp.posts == 0, "A different control in the same app must not receive text")
        let movedSelection = TextInsertionTarget(pid: sameApp.target.pid, element: sameApp.target.element,
                                                 selection: NSRange(location: 0, length: 0), value: sameApp.target.value, editable: true, secure: false)
        let selectionResult = await sameAppService.insert("选区已变", into: movedSelection, stillValid: { true })
        try require(isRetained(selectionResult) && sameApp.posts == 0, "Changing only the selection also prevents automatic insertion")

        let oldSnapshot = store.snapshot()!
        board.clearContents(); board.setString("在准备期间复制的新内容", forType: .string)
        let staleWrite = store.write("旧结果", owner: "stale", restoring: oldSnapshot)
        try require(staleWrite == nil && board.string(forType: .string) == "在准备期间复制的新内容", "A stale clipboard snapshot cannot overwrite a newer copy")

        let exitingEnvironment = InputEnvironment()
        let exiting = TextInsertionService(environment: exitingEnvironment, clipboard: store)
        let beforeExit = store.snapshot()!
        let exitTask = Task { await exiting.insert("退出时仍在等待", into: exitingEnvironment.target, stillValid: { true }) }
        while exitingEnvironment.posts == 0 { await Task.yield() }
        try require(exiting.hasPendingClipboard, "Clipboard ownership remains busy during the delivery window")
        exiting.restorePendingClipboard()
        try require(!exiting.hasPendingClipboard && store.snapshot()!.contents == beforeExit.contents, "Termination restores an owned clipboard synchronously")
        exitTask.cancel(); _ = await exitTask.value
        print("TextInsertion: clipboard, Unicode selection, focus, permission, cancellation, update drain and termination checks passed")
    }
    static func isRetained(_ result: TextInsertionOutcome) -> Bool { if case .retained = result { return true }; return false }
}
