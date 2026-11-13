"""Adapt the pinned Waveform resource lookup for a packaged macOS app."""
import hashlib
from pathlib import Path
import stat

source = Path(__file__).resolve().parents[1] / ".build/checkouts/Waveform/Sources/Waveform/Renderer.swift"
original_hash = "4796544d7f89a143ac3cbd25633f2e4ad36d055e6bbad658d926abb60a12d948"
original = "        let library = try! device.makeDefaultLibrary(bundle: Bundle.module)"
replacement = """        // CyberAudio: native SwiftPM's Bundle.module only searches beside the
        // main bundle. App resources belong inside Contents/Resources instead.
        let resources = Bundle.main.resourceURL
            .flatMap { Bundle(url: $0.appendingPathComponent("Waveform_Waveform.bundle")) } ?? Bundle.module
        let library: MTLLibrary
        if let compiled = try? device.makeDefaultLibrary(bundle: resources) {
            library = compiled
        } else {
            // Also support swift run, where native SwiftPM copies Metal source.
            // Shipping builds always include a precompiled default.metallib.
            let source = try! String(contentsOf: resources.url(forResource: "Waveform", withExtension: "metal")!, encoding: .utf8)
            library = try! device.makeLibrary(source: source, options: nil)
        }"""
text = source.read_text()
baseline = text.replace(replacement, original)
if hashlib.sha256(baseline.encode()).hexdigest() != original_hash or baseline.count(original) != 1:
    raise SystemExit("Waveform 1.0.3 source changed; review the resource adapter before building")
patched = baseline.replace(original, replacement)
if text != patched:
    mode = stat.S_IMODE(source.stat().st_mode)
    source.chmod(mode | stat.S_IWUSR)
    try:
        source.write_text(patched)
    finally:
        source.chmod(mode)
