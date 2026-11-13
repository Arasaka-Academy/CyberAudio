# Native interface dependencies

- Luminare 0.2.0, `d8fc61c831aff8a581deac00c89aa724cc32decd` (BSD 3-Clause).
- AudioKit Waveform 1.0.3, `357de1945a36e62a27f74a9b3b8f5e45904659d9` (MIT).
- SwiftUI Introspect 1.5.0 (MIT) and VariadicViews 1.0.0 (MIT), used by Luminare.
- Sparkle 2.10.1 (permissive license, included alongside this file).

All exact revisions are recorded in the root `Package.resolved`. Complete license
texts from each checkout are included in this directory.

`scripts/patch_waveform.py` checks the original Renderer.swift SHA-256 and adapts
only resource lookup: the packaged macOS app uses Contents/Resources, while native
SwiftPM looks beside the main bundle. A source shader fallback supports swift run.
`scripts/build_ui.sh` compiles the pinned shader to default.metallib for shipping;
the app does not require the Metal compiler or Xcode at runtime.
