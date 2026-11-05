# Native runtime dependencies

The application uses Swift + MLX on the Apple GPU. It does not use Core ML or ANE.
FFmpeg is an external executable, configured separately by the user.

- MLX Audio Swift: `927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe` (MIT).
- MLX Swift 0.33.0: `7ede4fd6d05f63305c1c0ebf4f8c1909a637c574` (MIT).
- MLX 0.32.4: `36969c094354086e15d2a327a9c50feb20b9cf11` (MIT).
- SwiftNIO 2.90.1: `3eea09220e07d34ace722221cbda90306f48c86c` (Apache 2.0).
- Other transitive revisions are fixed in `Native/Package.resolved`.

`scripts/patch_mlx_audio.swift` verifies the original Qwen3TTS source SHA-256,
adds a synchronous `generateManaged` adapter, and checks service cancellation at
three existing generation boundaries. The adapter removes the upstream heuristic
text-length token cap only for managed calls; the service enforces 1200 tokens and
requires EOS. Audio callbacks run on the inference worker and apply bounded HTTP
backpressure. Returning from this method joins the producer and its decoder reset.
The script also verifies already patched source. No other SDK code is changed.

The native tokenizer conversion in `CyberAudioCore/Assets.swift` follows the fixed
upstream Qwen tokenizer implementation. Copyright (c) 2025 Prince Canuma;
the complete upstream MIT license is included as `mlx-audio-swift-LICENSE`.

When Apple's Metal compiler is unavailable, `scripts/prepare_metal.sh` extracts
only `mlx/lib/mlx.metallib` from the official `mlx_metal` 0.32.4 distribution.
Both archive and shader hashes are fixed and checked. No Python code, interpreter,
package manager or runtime is installed or shipped. The MLX MIT notice is included.

The App's `Contents/Resources/ThirdParty` contains dependency licenses. Qwen model
assets are downloaded separately from their pinned repositories; model license
declarations and provenance remain in their pinned model cards. See `Models.lock.json` for
the exact required inference files, sizes and hashes.
