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
- [x] Validate workflow syntax and local equivalents (`actionlint`, complete `make check`, release App build).
- [ ] Verify remote CI after authorized branch delivery; this work remains local.

## 6. Recoverable first-run setup

Files: native asset downloader/tool, App setup controller/view, backend startup, setup and downloader tests, guide.

- [x] Inspect native asset and FFmpeg preparation contracts; implement native first-run status, model download progress, cancellation, retry, and resumable downloads.
- [x] Preserve valid files and configuration; detect insufficient space, missing FFmpeg, invalid assets, interrupted transfers, and unsupported proxies with actionable UI.
- [x] Exercise download restart, server Range behavior, cancellation, corrupt/changed partial data, and native UI rendering using controlled endpoints.
- [x] Build an isolated production App and verify preparation-to-ready using local pinned models and existing-installation startup. Verify actual cross-volume atomic publication using the bundled asset helper and a dedicated APFS image.
- [ ] Accept a pristine installation downloading all model weights over the public network or real proxy. A new M4 root is now downloading through a live existing HTTP proxy; actual 8MiB cancellation/exit 130/resume passed, but complete download, final hashes and ready remain pending.

## 7. Stable signing and upgrades

Files: packaging/signing scripts, release workflow, signing and upgrade documentation/tests.

- [x] Inspect available Developer ID/notarization capabilities without exposing private keys.
- [x] Implement configurable stable Developer ID signing, hardened runtime/required entitlements, notarization, and verification; validate ad-hoc mode and actual Apple Development signing separately. Distribution validation also requires a stapled ticket and Gatekeeper acceptance when reusing an App.
- [ ] With credentials available, verify signed/notarized artifacts and actual upgrade permission retention. Record a concrete external blocker if credentials or human authorization are required.

## 8. Real-use acceptance and final review

Files: isolated verification drivers, regression tests, acceptance report.

- [ ] Exercise actual cross-app dictation in TextEdit, Safari, and VS Code; validate focus changes, Unicode/Chinese IME, secure-field rejection, cancellation, clipboard recovery, and sleep/session recovery.
- [ ] Verify real microphone recording and human listening separately from synthetic or prerecorded audio.
- [x] Run complete isolated actual-model service acceptance: 16 STT format requests, nine TTS voices, complete streaming PCM, three disconnect/recovery rounds, update pause/resume, EOF, single ownership, sandbox, and App TERM/KILL/restart. All passed with production service preserved.
- [x] Complete the additional ten-minute mixed workload and inspect memory/recovery evidence.
- [x] Recover and verify all fixed 100-sample corpus audio. Repeat M1 and M4 actual-model comparison; M4 primary metrics match the historical Python baseline, and same-M1 installed old/new Helper outputs match 100/100. Preserve the initial cross-device differences separately.
- [x] Accept actual 60/60.1-second ASR limits, active HTTP ASR/nonstreaming TTS cancellation and same-instance recovery, long TTS completion/explicit generation limit, request cleanup and EOF using isolated real models.
- [x] Compare 30 controlled names/numbers/noise/composite/control inputs on same-M1 old/new Helpers. Reject the initial float/int generator error, verify corrected transforms against FFmpeg, and preserve all raw results. All 30 corrected outputs match; score only the 24 monolingual rows.
- [ ] Diagnose whole-language omission on the four corrected concatenated Chinese/English inputs without attributing it to a code regression prematurely. Validate any change against the fixed corpus and natural mixed speech; controlled concatenation is not natural code-switch acceptance.
- [ ] Finish the live 7200-second M4/16GiB mixed workload, inspect terminal exit and lifecycle cleanup, and retain actual memory/background-load evidence. Do not restart the matching live runner merely because SSH disconnects.
- [x] Review the final changed tree, run complete checks and package validation, and record every accepted requirement and remaining gate above.
- [ ] Mark the overall user goal complete only after the remaining required interactive, credential and target-device acceptance is actually performed.

## 9. Playback recovery — additional review finding 8

Files: `Sources/CyberAudio/AudioWorkspace.swift`, `tests/AudioWorkflowTests.swift`.

- [x] Reproduce stopped PCM playback retaining `playing` after an output configuration notification using the real AVFoundation engine and native HTTP fixture.
- [x] Observe only the current engine, hand off asynchronously from the audio thread, and remove the observer before stopping. Ignore stale queued engine/player callbacks.
- [x] Cancel partial generation without releasing update admission before the task/HTTP response drains; preserve a completed WAV for replay/export.
- [x] Handle current-player decoding and unsuccessful-completion errors; successful replay clears speech errors while retaining unrelated transcription errors.
- [x] Pass the integration regression for active/completed interruptions, retry, stale events, failure callbacks, and actual service update lease recovery.
- [x] Run complete checks, review observer/cancellation ownership, and record a separate new source/build/package snapshot while preserving the R1–R7 evidence.
- [ ] Perform physical output-device unplug/replug and sleep recovery in an unlocked interactive desktop session. Controlled notifications are not physical acceptance.

## Evidence log

- Starting point: clean `main@96210dd`; development branch `codex/robustness`.
- Prior review reproduced stale playback callbacks and unbounded WAV flush using isolated probes. New regression tests must preserve these behaviors as permanent checks.
- Playback regression failed before the fix, then passed with the real AVFoundation/HTTP test chain, including update wait and stale delegate/replay checks.
- Unread WAV regression failed before the fix, then passed without client disconnect; 20 native contracts passed, zero skips.
- Real local ASR model on OS-synthesized speech: pre-cancel, cancellation at check 35 of 37, and same-instance recovery passed. The recorded 694 ms was time before the injected cancellation, not cancellation latency. No microphone/corpus accuracy claim.
- Complete `make check` passed, including 19 current development tool tests and compilation of the production inference/asset helpers. Logs: `/tmp/cyberaudio-robustness-check-1.log` and `/tmp/cyberaudio-robustness-asr-actual.log`.
- Signing inventory found an Apple Development identity, but no Developer ID Application identity. Production notarization and stable distribution/permission retention remain unverified.
- Final complete check log: `/tmp/cyberaudio-robustness-check-final.log`; 28 native contracts, 14 native renders, preparation/recording/playback/dictation/update regressions, 27 tool tests, production helper compilation and release App compilation all passed. Two later signing reuse regressions require the final tool check below.
- The actual service initially reproduced Cocoa 256 / POSIX 20 for symlink model roots after successful file verification. Resolving the roots before SDK directory loading fixed actual ASR/TTS load/warmup; full service acceptance then passed. Report: `.context/robustness/service-acceptance-2/reports/native-service.json`.
- Exact final App copy signed with an available Apple Development certificate: strict nested signatures, hardened runtime and bundled Assets helper smoke all passed. `.context/robustness/development-signing-final.json`; this is not Developer ID or notarization evidence.
- Cross-volume publication used the final bundled Assets helper, a preverified fixed README partial on the data volume and a separate 64MiB APFS image. Published bytes/hash, consumed partial, private sibling cleanup, subsequent cancellation and unchanged Token passed; image detached. `.context/robustness/cross-volume-acceptance/report.json`.
- Closing review found reused distribution Apps could pass signature checks without a notarization ticket. Two fail-closed regressions reproduced the omission; distribution verification now also validates the ticket and Gatekeeper assessment.
- Desktop acceptance is unavailable while the session is locked at loginwindow; Orca is absent and the available desktop tool transport is closed. Permission flags alone do not prove cross-app dictation or physical audio.
- Report and sanitized evidence are retained in `docs/reviews/2026-11-06.md` and `docs/evidence/robustness-2026-11-06/`. Pending external acceptance remains explicit; the overall user goal is not marked complete.
- After the final signing correction, complete `make check` passed again: 28 native contracts, 29 development tool tests, 14 native renders and all behavioral/build checks. Log: `/tmp/cyberaudio-robustness-check-accepted.log`; workflow lint, entitlement validation and current ad-hoc App verification also passed.
- The fixed 100-sample FLEURS manifest is present but none of its audio files are local. Bounded attempts to reach both immutable Parquet sources failed with curl 28 connection timeouts; no HTTP/HTTPS/SOCKS system proxy is enabled. The corpus comparison is a recorded acceptance gap, not a result inferred from synthetic speech.
- Ten-minute actual-model mixed load passed on one instance: 602.69 seconds, 210 STT, 210 complete TTS, 42 disconnect/cancellation recovery rounds, 21 update pause/resume rounds. Metal active bytes first/last 5,578,423,756 / 5,578,423,828, maximum 5,581,437,472; no cumulative growth observed in this window. Actual `tmp` contains no request directories after completion. EOF stop 152ms; production instance preserved. `docs/evidence/robustness-2026-11-06/soak.json`.
- Local DMG `CyberAudio-0.4.3-robustness.local-macOS-arm64.dmg` built without replacing an existing release artifact. Image and SHA checks passed; a read-only mount verified nested signatures, exact App/Helper/Metal bytes and bundled preparation/signing/review/evidence documents, then detached. `docs/evidence/robustness-2026-11-06/package.json`. This is an ad-hoc, unnotarized local artifact, not an installed or published release.
- Local implementation commits: `0cf9e40` runtime/review fixes, `7fb6b75` recoverable first-run preparation, `b730c64` certificate signing/notarization. Final sequential review and a 96-file source-content snapshot are retained with the evidence.
- Additional playback recovery regression failed before the fix with “A stopped PCM engine must not remain marked as playing after a route change” (exit 133), then passed using the current AVFoundation objects and actual fixture HTTP update lease. Logs: `/tmp/cyberaudio-device-recovery-red.log` and `/tmp/cyberaudio-device-recovery-green.log`. Physical audio acceptance remains pending.
- Playback recovery fix committed locally as `e2f34df`. Complete checks passed again: 28 native contracts, 29 active tool tests, 14 native renders, all behavioral groups, actual helper/App compilation, workflow lint and entitlements. `/tmp/cyberaudio-device-recovery-check.log`.
- The new App passed strict nested ad-hoc verification and bundled Assets smoke. Service, Assets, Metal and Info.plist hashes match the previous actual-model acceptance artifacts; the main App binary changed. Previous App archived with five verified component hashes; previous DMG retained.
- New local `CyberAudio-0.4.3-robustness.playback-macOS-arm64.dmg`: 108,027,599 bytes; SHA-256 `de4589039a1c42922e5cb12909804fdae1faad4cb3ddf88dfbf1bbc3c36699d7`. Image/sidecar checks, read-only mounting, nested signatures, component bytes and 12 documentation/evidence files passed; volume detached. Separate checks/source/build/external package receipts are in `docs/evidence/robustness-2026-11-06/playback-recovery/`.
- Subsequent recovery supersedes the earlier corpus-availability gap: the existing M4 corpus cache supplied all selected WAVs, manifest SHA `dc67d7b52bea55f7ea6fcd0934e0460811699ad510c0befe9f938c60c84ef7eb`, exactly matching the repository manifest. All 100 audio hashes passed; selected audio totals 64,676,520 bytes. Raw recordings remain outside Git.
- M1 full actual-model acceptance passed with the recovered corpus. Initial M1 vs historical M4 scoring showed Chinese CER 8.0300404% vs 7.9722704%; an isolated installed 0.4.3 Helper on the same M1 then matched all 100 new outputs exactly. Both comparisons and raw results are retained.
- M4/16GiB/macOS 27.2 full actual-model acceptance passed, exit 0, 215.38 seconds. Chinese CER 7.9722704% and English WER 3.3457249% match historical M4 Python primary metrics. 16 format requests, nine independently parsed WAVs, streaming/cancellation/update/EOF, actual App TERM/KILL/restart and production preservation passed.
- Acceptance tooling commit `56b7eca` adds safe preflight, bounded round-robin corpus soak, actual helper RSS/Metal metrics and accurate comparison environment metadata. Complete checks passed with 28 native contracts, 36 tool tests, 14 native renders, all workflows and App/Helper compilation; log `/tmp/cyberaudio-target-validation-check-20261106.log`.
- Two-hour M4 runner PID 53772, start `Fri Nov 6 19:34:53 2026`, remains live. Snapshot at 11:47:37 UTC: 714.37 actual load seconds, 175 cycles, 35 disconnect recoveries and 17 update rounds. This snapshot is not terminal acceptance.
- A separate public-download runner PID 54552, start `Fri Nov 6 19:44:01 2026`, uses a new root and existing explicit HTTP proxy, preserving production configuration/models. Actual cancellation at a durable 8MiB prefix exited 130 and a new process resumed the same prefix. All 23 final hashes and fresh-root readiness remain pending; the wrapper is bounded to one hour with a 3GiB disk reserve.
- M4 has an unlocked desktop but no usable desktop automation driver and no Apple Development/Developer ID Application identity. Physical microphone/listening and credential gates remain. New completed results and explicitly running snapshots are separate in `docs/evidence/robustness-2026-11-06/target-validation/`.
- Actual M1 boundary driver exited 0 in 88.06 seconds. Active ASR and nonstreaming TTS disconnect-to-idle observations were about 299/364ms, with same-instance recovery. A 417-scalar English paragraph produced 31.68 seconds of WAV; 500-scalar Chinese returned explicit `generation_limit`. No physical listening claim.
- Corrected controlled cohort completed both old/new runs with exit 0 and 30/30 exact output matches. Source WAVs are IEEE float32, not signed Int32; the initial private generator error was caught by independent FFmpeg comparison, and those invalid quality results are excluded but preserved. Four corrected composites omit an entire language in both versions, a pending quality limitation. Completed evidence and original collection sources are in `docs/evidence/robustness-2026-11-06/quality-boundaries/`.
