# CyberAudio Robustness Implementation Plan

> Execution: sequentially in the main chat, as required by the user's AGENTS.md. Continue the authorized implementation without another planning approval.

**Goal:** Fix all five findings from the 2026-11-06 review, then complete the recommended reliability, first-run, signing, and real-use improvements with evidence appropriate to each requirement.

**Architecture:** Keep the native macOS workspace and the serial Swift/MLX service. Make playback ownership explicit, bound the entire HTTP response lifecycle, and add cooperative ASR cancellation while retaining producer ownership. Reuse the native asset tool for recoverable setup instead of introducing a second model manager.

**Tech stack:** Swift 6.3+, macOS 26+, SwiftUI/AppKit/AVFoundation, SwiftNIO 2.90.1, pinned MLX Audio Swift/Metal, Python development tools, GitHub Actions.

**Specification:** The user's active goal and the five review findings in this chat. Existing behavior and acceptance boundaries: `docs/GUIDE.md`, `docs/DICTATION.md`, `docs/AUTOMATIC_UPDATES.md`, and `docs/NATIVE_SWIFT.md`.

## Global requirements

- Preserve unrelated worktrees, existing models, configuration, API Token, and installed production App while developing.
- Retain API compatibility, offline inference, authenticated loopback access, single model-slot ownership, and update drain safety.
- Do not count synthetic inference, rendered UI, signatures, or historical records as new physical audio, live upgrade, or energy evidence.
- Keep the compact native STT/TTS workspace and independent settings window.
- Run all shell commands through `rtk`; do not dispatch subagents.

## 1. Playback ownership — review finding 1

Files: `Sources/CyberAudio/AudioWorkspace.swift`, `tests/AudioWorkflowTests.swift`, `scripts/test_audio_workflow.sh`.

- [x] Extend the real AVFoundation/native-HTTP integration test: replay while the previous stream tail is queued; the new playback must remain busy and UpdateGate must wait.
- [x] Observe the new regression fail before changing production code.
- [x] Give playback a session identity invalidated before stopping old players; completion callbacks must match the current session/player.
- [x] Cover normal completion, repeated replay, explicit stop, and cancellation. Run the audio workflow and update tests.

## 2. HTTP send deadline — review finding 2

Files: `Native/Sources/CyberAudioCore/HTTPService.swift`, `Native/Tests/CyberAudioCoreTests/{Runner,ServiceTests}.swift`.

- [x] Add a real TCP test with an unread large WAV: it must block updates until the send deadline, then release the response without the client disconnecting.
- [x] Observe the failure, then retain an enforceable deadline through final flush. Distinguish queued response completion from completed transport.
- [x] Verify streaming backpressure, inference timeout/slot retention, disconnect cleanup, and normal response drain.

## 3. Cooperative ASR cancellation — review finding 3

Files: `scripts/patch_mlx_audio.swift`, `Native/Sources/CyberAudioInference/QwenEngine.swift`, SDK adapter tests and native verification.

- [x] Characterize the pinned ASR token/prefill loop and add a deterministic cancellation test for the actual adapter.
- [x] Add a hash-verified synchronous ASR adapter checking cancellation at token/prefill boundaries; retain the worker and model slot until generation returns.
- [x] Verify patch idempotency and refusal of modified upstream sources, compile the real inference product, and exercise cancellation/recovery using isolated real models when available.

## 4. Fresh test binaries and complete development checks — review findings 4 and 5

Files: `scripts/test_audio_workflow.sh`, `Makefile`, development test dependencies, `README.md`.

- [x] Always request an incremental fixture build before running integration tests.
- [x] Split active Python tool tests from legacy backend tests and run active tests in `make check` using a reproducible isolated environment.
- [x] Verify the complete test entry point without relying on the broken historical `.venv`.

## 5. Checks before merging and release

Files: `.github/workflows/`, build scripts, development documentation.

- [x] Add pull-request/main CI with pinned dependencies, native checks, active tool tests, and compilation of the real inference helper.
- [x] Reuse the same required checks in the release path; keep signing secrets out of untrusted PR jobs.
- [ ] Validate workflow syntax and local equivalents; verify remote CI if delivery is authorized/available.

## 6. Recoverable first-run setup

Files: native asset downloader/tool, App setup controller/view, backend startup, setup and downloader tests, guide.

- [ ] Inspect native asset and FFmpeg preparation contracts; implement native first-run status, model download progress, cancellation, retry, and resumable downloads.
- [ ] Preserve valid files and configuration; detect insufficient space, missing FFmpeg, invalid assets, interrupted transfers, and unsupported proxies with actionable UI.
- [ ] Exercise download restart, server Range behavior, cancellation, corrupt/changed partial data, and native UI rendering using controlled endpoints.
- [ ] Build an isolated production App and verify first-run-to-ready and existing-installation startup.

## 7. Stable signing and upgrades

Files: packaging/signing scripts, release workflow, signing and upgrade documentation/tests.

- [x] Inspect available Developer ID/notarization capabilities without exposing private keys.
- [ ] Implement configurable stable Developer ID signing, hardened runtime/required entitlements, notarization, and verification; validate ad-hoc development mode separately.
- [ ] With credentials available, verify signed/notarized artifacts and actual upgrade permission retention. Record a concrete external blocker if credentials or human authorization are required.

## 8. Real-use acceptance and final review

Files: isolated verification drivers, regression tests, acceptance report.

- [ ] Exercise actual cross-app dictation in TextEdit, Safari, and VS Code; validate focus changes, Unicode/Chinese IME, secure-field rejection, cancellation, clipboard recovery, and sleep/session recovery.
- [ ] Verify real microphone recording and human listening separately from synthetic or prerecorded audio.
- [ ] Run sustained isolated inference/cancellation/restart tests and the existing corpus comparisons; record measured limits rather than inferring performance or energy gains.
- [ ] Review the final changed tree, run complete checks and package validation, and audit every requirement above before marking the user goal complete.

## Evidence log

- Starting point: clean `main@96210dd`; development branch `codex/robustness`.
- Prior review reproduced stale playback callbacks and unbounded WAV flush using isolated probes. New regression tests must preserve these behaviors as permanent checks.
- Playback regression failed before the fix, then passed with the real AVFoundation/HTTP test chain, including update wait and stale delegate/replay checks.
- Unread WAV regression failed before the fix, then passed without client disconnect; 20 native contracts passed, zero skips.
- Real local ASR model on OS-synthesized speech: pre-cancel, cancellation at check 35 of 37, and same-instance recovery passed. The recorded 694 ms was time before the injected cancellation, not cancellation latency. No microphone/corpus accuracy claim.
- Complete `make check` passed, including 19 current development tool tests and compilation of the production inference/asset helpers. Logs: `/tmp/cyberaudio-robustness-check-1.log` and `/tmp/cyberaudio-robustness-asr-actual.log`.
- Signing inventory found an Apple Development identity, but no Developer ID Application identity. Production notarization and stable distribution/permission retention remain unverified.
