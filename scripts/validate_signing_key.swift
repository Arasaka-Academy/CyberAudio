import CryptoKit
import Foundation

// Only the public key is compared. Never print the secret or put it in argv.
let plistURL = URL(fileURLWithPath: CommandLine.arguments[1])
let info = try PropertyListSerialization.propertyList(from: Data(contentsOf: plistURL), format: nil) as! [String: Any]
guard let encoded = ProcessInfo.processInfo.environment["SPARKLE_EDDSA_PRIVATE_KEY"],
      let seed = Data(base64Encoded: encoded.trimmingCharacters(in: .whitespacesAndNewlines)),
      seed.count == 32,
      let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: seed),
      key.publicKey.rawRepresentation.base64EncodedString() == info["SUPublicEDKey"] as? String else {
    fputs("Sparkle signing key does not match the application's public key.\n", stderr)
    exit(1)
}
print("Sparkle signing key matches the application public key")
