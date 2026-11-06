import AppKit
import ApplicationServices
import Foundation

// Read-only probes only: do not request TCC access or activate any application.
let session = CGSessionCopyCurrentDictionary() as? [String: Any]
let lockValue = session?["CGSSessionScreenIsLocked"] as? Bool
let state: [String: Any] = [
    "observed_at": ISO8601DateFormatter().string(from: Date()),
    "session_dictionary_available": session != nil,
    "screen_lock_key_present": lockValue != nil,
    "screen_locked": lockValue.map { $0 as Any } ?? NSNull(),
    "accessibility_trusted_for_this_probe": AXIsProcessTrusted(),
    "post_event_access_for_this_probe": CGPreflightPostEventAccess(),
    "requested_permissions": false,
    "activated_application": false,
    "scope": "Current probe process only; not the installed App or an end-to-end dictation result",
]
let data = try JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data([10]))
