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
- [ ] Accept a pristine installation downloading all model weights over the public network or real proxy. Actual cancellation/exit 130/resume passed; initial per-range retry Helper failed with connection loss at 14:18:00 UTC. Following successful 1MiB curl/URLSession probes and the adaptive-range fix, the exact final-App Assets failed at secure-connection validation at 14:41:40 UTC. Fourteen final files/112MiB partial and production preserved; all owned processes stopped. Complete download, all final hashes and fresh-root inference remain pending.

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
- [x] Add eight equal-RMS/order-controlled composites. Some restore both parts, others still omit or translate a language; volume mismatch alone does not explain all observations. Preserve candidate-only diagnostics without calling them natural mixed-speech acceptance.
- [ ] Resolve mixed-language omission/translation after root-cause diagnosis. Validate any change against the fixed corpus and natural mixed speech; controlled concatenation is not natural code-switch acceptance.
- [ ] Complete the original strict 7200-second M4/16GiB mixed workload gate. First run exited 1 after 3134.31 seconds/707 cycles; strict repeat also exited 1 after 4835.73 seconds/1229 cycles, with cycle 1230 explicitly failed/generation_limit. Both failures and all cleanup/preservation evidence remain. Diagnose before another repeat; do not weaken the criterion or select successful reruns.
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

## 10. Transient model download recovery — additional review finding 10

- [x] Reproduce a single interrupted range and temporary HTTP 503 ending the entire download; observe the new recovery/bounds/cancellation regressions fail before implementation.
- [x] Retry only the unchanged uncommitted range, at most twice, for known transient transport/HTTP errors. Keep backoff cancellable and respect bounded numeric/date Retry-After.
- [x] Preserve all range/length/digest checks, existing files and durable partials. Report only safe network categories, without raw URL/error userInfo or proxy information.
- [x] Run 33 native contracts, including cancellation during backoff, persistent failure bounds, long Retry-After and permanent HTTP errors.
- [x] Complete full checks and sequential review, then run the new actual Assets Helper on retained public partials. Complete checks passed; live runner 66573/child 66582 started at 14:06:14 UTC, bounded to 3600 seconds. Complete 23-file public download remains a separate unchecked gate in section 6.
- [x] Based on bounded probes (8MiB incomplete, same-start 1MiB complete with identical curl/URLSession SHA), reproduce degraded-transfer recovery. Commit f42bafe falls back once to 1MiB after exhausted timeout/connection-loss retries; durable prefix, cancellation, final SHA and error bounds preserved. Three regressions first yielded two failures, then all passed; HTTP/TLS failures do not resize. Complete public acceptance remains unchecked in section 6.

## 13. Independent subtalker sampling investigation

- [x] Compare the pinned Swift generation path with the recorded Qwen reference and fixed model generation_config. Keep talker temperature 0.7 and vary only private subtalker temperature from 0.7 to 0.9; do not change production defaults.
- [x] Complete six predeclared failure/control/repeat cases. Both seed-196 baseline token/PCM outputs match the original failure; both private candidate repetitions terminate at 4.88 seconds. All twelve actual ASR requests complete, with full text for both candidate repetitions and the control pair. Production SDK/App/Helper/config/Token preserved; jobs terminated.
- [x] Complete all 144 private candidates for the retained four-text/nine-voice/four-seed cohort. Reused the original 144 baseline generations; both groups completed 294 requests through the same final-App ASR. Candidate has zero no-EOS samples, 126/144 normalized exact versus 124/144, and 45 versus 91 total edits; eleven pairs worsen, including dates/names. No promotion. Evidence: subtalker-followup/.
- [ ] Consider any production sampling change only after broader content review, original strict target/runtime/corpus gates and human listening. A fixed seed or ASR roundtrip alone does not establish quality or stability.

## 14. Honor explicit English for dialect voices (R11)

- [x] Reproduce actual language selection with fixed input `1234` and seed 31 for eric/dylan: English and Chinese requests produce identical token/PCM bytes. Two expected behavior checks fail before the fix.
- [x] Limit dialect language override to Chinese/auto, retaining original sampling. Upgrade only the known previous managed SDK hash or the exact current patch; reject unrelated edits. Actual adapter-command regression first fails, then all four adapter tests pass.
- [x] Complete full checks and repeat the four actual-model cases through the newly built production managed SDK entry point. Full make check exit 0: 36/40/14; both English outputs change and Chinese dialect token/PCM bytes match the original baseline exactly.
- [x] Assemble and accept final signed `5479313` App bytes, fixed corpus and lifecycle; original native driver and outer runner pass in 164.53s. Independent verification confirms all 100 old/new M1 raw ASR outputs match, source/byte/signature/WAV/cleanup and production/frozen preservation.
- [ ] Accept English numeric pronunciation for eric/dylan. All eight pre/post outputs completed final-App ASR auto language detection, but corrected English outputs were detected as Hindi/Chinese; no English quality pass inferred from changed audio. Valid WAV payloads match original PCM; preserve every output and obtain specific content/listening evidence. Evidence: language-routing/.

## 15. Independent text-input layout investigation

- [x] Confirm current full-text prefill matches the retained official non-streaming branch; the upstream SDK file has no newer fix to adopt. Define a private sequential-text alternative from the reference without changing sampling, production SDK or shared objects.
- [x] Complete all ten predeclared paired generations and seventeen final-App ASR requests. Original failure token/PCM parity and both R11 English numeric baseline pairs match. Sequential text ends the known failure at 6.08 seconds with full roundtrip text, but the normal seed-31 control roundtrip regresses and English numbers remain unaccepted. No promotion or further full matrix/strict soak for this candidate.
- [x] Independently verify all ten token/PCM pairs, seventeen WAV payload references/thirteen unique WAVs, 96 current source hashes, owned process/group/listener cleanup and production/frozen preservation. Evidence: text-layout/.
- [x] Recheck desktop tooling: Orca executable/App absent; Cua Driver read-only health call returns Transport closed. No installation, service restart or permission prompts requested. Current lock state remains unknown; actual desktop/physical/listening acceptance is still unchecked.
- [ ] Resolve the original content/stability gates with a candidate that preserves normal controls and passes the existing broad target/corpus/lifecycle/listening requirements. The alternative text layout is not an accepted fix.

## 16. Preserve model files on SDK load failure (R12)

- [x] Trace the fixed SDK's destructive stale-tokenizer recovery. AssetStore normally rejects malformed assets first; a change after verification can still reach the SDK branch. Do not claim observed production loss.
- [x] Run the actual SDK against real weights linked into a disposable model root with a malformed speech tokenizer and a user-data sentinel. Baseline deletes the temporary directory and exits 2; original model files, production/config/Token and frozen artifacts remain intact.
- [x] Remove automatic whole-directory deletion through the hash-verified adapter; accept only the two known legacy managed digests or the exact current patch. Commit: b5607f2.
- [x] Observe the Foundation branch regression fail on deletion before the fix, then pass all eight adapter tests. Cover valid/missing tokenizer controls, both legacy upgrades, idempotency and tampering rejection.
- [x] Complete make check: 36 native contracts, 44 active tool tests, 14 renders, all behavior groups and actual App/Inference/Service/Assets compilation; no failures or skips, 260.40 seconds.
- [x] Repeat actual SDK loading after the fix: reject the malformed tokenizer while retaining the temporary root and original sentinel bytes; independently hash the original model files and frozen artifacts.
- [x] Verify the isolated current-byte App with the full fixed corpus, native service and real App lifecycle: native/runner exit 0, 189.06 seconds, 100/100 same-M1 old ASR outputs, independent byte/WAV/process/preservation checks passed. Sanitized evidence: model-preservation/.

## 17. Reference numerical parity investigation

- [x] Confirm the retained official/Python reference sources and formulate a teacher-forced input-embedding/forward-logit comparison independent of RNG. No sampling candidate is promoted.
- [x] Inspect existing runtimes without repairing them: the legacy .venv interpreter link is broken, an offline uv-managed Python 3.13 exists, but the queried MLX/reference packages are absent from the available runtimes/cache. One PyPI metadata request failed with No route to host; no dependency environment or installed runtime was changed.
- [x] Establish a separate hashed Python 3.13 reference runtime through the Tsinghua PyPI mirror after the original access path failed. MLX 0.32.4 matches the actual Swift header; installed mlx-audio 0.6.4 Qwen source matches the retained reference SHA exactly. Existing runtimes remain unchanged.
- [x] Build a private Swift diagnostic module and capture the five predeclared cases, at most 32 steps each. All 146 steps/151 tensor files/12,541,858 bytes retained; known seed-196 token and PCM prefixes match the historical failure exactly. Native runner exits 0 in 9.82 seconds.
- [x] Run the unmodified Python reference offline, forcing all sixteen captured codec IDs without RNG sampling. All 750 prepared/per-step tensor comparisons have identical shape/dtype/values, zero max/mean/RMS error; all 2190 auxiliary top-1 choices match. Reference runner exits 0 in 10.85 seconds.
- [x] Independently verify snapshots, raw codec IDs, PCM, original failure prefixes, 96 formal source hashes, 568 original model-directory files, eight frozen files, owned process-group cleanup and production instance/config/Token preservation. Retain the initial verifier U32 parser failure and corrected file-only verification without rerunning inference. Evidence: reference-parity/.
- [ ] Localize the remaining sampling/content/termination behavior and qualify any proposed change with complete generation, fixed content controls, strict original M4 workload and listening acceptance. Short teacher-forced forward parity alone does not close these gates.
- [ ] Establish a separate diagnostic reference environment, pin sources and packages, and compare identical inputs/codec vectors before attributing the TTS degeneration to an integration or model defect. Numerical parity has not been measured.

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
- First M4 strict soak failed at 3134.31 actual load seconds, 707 cycles, 141 disconnect recoveries and 70 update rounds; runner exited 1 at 12:27:55 UTC. The terminal request was not completed, but the old driver discarded its error code. Listener closed and request directories cleared; production instance/config/Token preserved. Do not infer crash, leak or an exact failure code from incomplete evidence.
- Commit `3d59609` preserves failed request JSON, HTTP status and workload/error context, plus explicit failed native report finalization. The controlled real-HTTP regression failed on missing `failed_request` before the fix; all eight targeted cases and all 37 active tools passed afterwards, no skips. Runtime/App/model parameters unchanged.
- Strict M4 repeat PID 59969, start `Fri Nov 6 20:48:05 2026`, root `data-soak-r2`, port 18876, same 7200-second criterion. Snapshot 12:50:42 UTC: 107.45 seconds/29 cycles, running. Original download runner stopped after four network failures; completion supervisor PID 55433 continues real resume, durable partial 1,468,006,400 bytes. Its old soak dependency will fail even if download succeeds; inspect public_download separately and wait for the current repeat to stop before any fresh model load.
- Eight additional equal-RMS/order composites completed on the candidate M1 Helper with exit 0 and verified source/output hashes. Some recover both languages, some still omit or translate; source loudness is not a complete explanation. These diagnostics and all terminal failures are archived in `docs/evidence/robustness-2026-11-06/extended-validation/`.
- A bounded M1 diagnostic completed 150 repetitions of the failed M4 cycle's short Chinese text/ono_anna/PCM in 532.43 seconds. No terminal failure reproduced in that window; output durations varied from 2.8 to 62.56 seconds, so completed does not establish content quality. Same-instance recovery, update pause/resume, cleanup, EOF exit 0 and production/config/Token preservation passed. Earlier 96-second PCM IncompleteRead and header-lookup collection failures remain recorded; the former has no terminal request code. Strict M4 acceptance remains pending.
- Follow-up R9 review found WAV error responses and interrupted PCM still threw before terminal-request collection. Both real-HTTP regressions failed on missing failed_request, then passed after response/transport observation and bounded terminal lookup. Commit `83f0ec3`; targeted 10 passed, active tools 39 passed/0 skipped. Runtime/App/models/strict criteria unchanged. The live M4 repeat remains on its original 3d59609 driver and is not restarted.
- Deterministic M1 TTS scan stopped at the first no-EOS seed 196, then repeated it and recovered with seed 0 (199 generations, 606.15s). Failure and repeat have identical 1200 tokens/96s PCM. A private service copy setting only seed 196 reproduced the identical PCM through actual HTTP, IncompleteRead and terminal failed/generation_limit; same-instance Serena/update/cleanup/EOF and production config/Token preservation passed. Original Helper and sources remain unchanged.
- Private SDK tracing preserves original failure tokens/PCM exactly and shows EOS was never filtered out in all 1200 steps. Temperatures 0.9 and 0.5 end for this seed but ASR roundtrips differ at initial words; no default changed or listening acceptance claimed. Additional 34+3 actual ASR calls cover TTS segments and auto/zh/en on eight composites; forced language does not resolve all omissions/translations. Evidence: model-diagnostics/.
- Original download completion supervisor ended failed at 13:16:19 UTC after three additional failed attempts. Terminal check preserves 2,206,203,904 partial bytes, five small verified files, production ready instance/config/Token, and confirms PIDs 55433/55496 absent. A normal-TLS public 8MiB Range probe succeeded; only then a new bounded Assets-only recovery PID 63337 (Fri Nov 6 21:28:30 2026) began, at most two one-hour attempts/7300s/3GiB reserve. No model loads; download success and fresh model acceptance remain separate gates. Evidence: download-terminal/.
- R10 commit `565eb1c` adds at most two cancellable retries per uncommitted range, bounded Retry-After and safe network error categories; new regressions first produced five expected assertions/errors. R8 follow-up `939050f` recognizes a stopped engine before tail completion clears ownership; deterministic callback-order regression first failed with exit 133, then passed. Latest complete `make check`: 33 native contracts/0 failures/0 skipped, 39 active tools, 14 renders, all behaviors and actual App/Helper compilation. Logs: `/tmp/cyberaudio-download-retry-check-final-20261106.log`.
- Strict M4 repeat terminated failed at 14:09:28 UTC, 4835.73 actual load seconds/1229 cycles/245 disconnect recoveries/122 update rounds. Cycle 1230 explicitly failed/generation_limit on ono_anna/PCM. Owned runner/driver/Helper gone, port 18876 closed, zero temporary requests, production preserved. Do not infer the first run's code or weaken the strict gate.
- Predeclared paired TTS cohort completed 288 generations in 908.02s, original failure parity intact; all outputs then passed through 294 actual ASR requests. Temperature 0.9 had zero no-EOS samples versus one for 0.7, but normalized exact roundtrip was 124/144 for both, with 12 candidate pairs having more edits and concrete omissions/unrelated text. Not population failure rates or human listening evidence; no default/candidate promotion. Evidence: retry-and-matrix/.
- Latest isolated `939050f` App/Service/Assets passed full actual native acceptance (driver exit 0): all 100 fixed corpus outputs match prior same-M1 old Helper, formats/nine voices/stream/cancel/update/EOF and active App TERM/KILL/restart. Outer collector then failed on wrong tts key; failed receipt and launched source preserved, source corrected to tts_voices. Independent read-only byte/report/audio/process/production verification passed without rerunning workload. Frozen dist App/DMGs and production/config/Token remain intact. Evidence: final-candidate/.
- Actual per-range retry candidate terminated failed at 14:18:00 UTC, exit 1, safe category connection lost. All owned processes absent at 14:22:12 UTC; fourteen final files/112MiB partial, production/config/Token preserved. Same-range 8MiB diagnostics through existing proxy: curl TLS failure, HTTP/1.1 partial 2.56MB/45s timeout, URLSession -1001 at 55s. Route responsibility unproven; no complete download/fresh inference gate accepted.
- Commit `f42bafe` adds one fallback to 1MiB for the current asset after exhausted large-range timeout/connection-loss retries. Three real-HTTP regressions first yielded two expected failures, then passed; persistent smaller ranges remain bounded and HTTP 503 does not resize. Complete `make check` exit 0: 36 native contracts/0 failures/0 skipped, 39 tools, 14 renders, all behavior groups and actual App/Helpers compiled. `/tmp/cyberaudio-small-range-check-20261106.log`.
- New final signed `f42bafe` candidate passed actual full native driver and outer runner, exit 0/156.58s. Independent byte/corpus/WAV/process/production checks passed; same-M1 old Helper raw outputs match 100/100. Exact final-App Assets SHA 06698db17958ab5adc2da7d1a1d9b23cbc34c57b67e1b1fb5cf7e9fd0e7adb1c ran on M4 at 14:41:25 UTC, exited 1 at 14:41:40 with safe secure-connection category. All owned processes stopped; production/config/Token and fourteen final files/112MiB partial preserved. Public download/fresh inference, strict soak, model quality and physical/credential gates remain unaccepted. Evidence: adaptive-ranges/.
- Latest local IOConsoleUsers inventory does not establish current lock state; the earlier locked-session note is historical. Physical/interactive acceptance remains incomplete. Fresh signing inventory has one Apple Development identity and zero Developer ID Application identities; target-device identity and automation inventories above describe their recorded snapshots.
- Independent subtalker-only followup completed six preliminary generations/12 ASR, then 144 new candidates and reused 144 original baselines/294 same-final-Helper ASR. Zero candidate no-EOS, longest14.88s, normalized exact126 versus124, edits45 versus91, but eleven pairs worse. Default sampling unchanged; original strict M4 gate remains unaccepted. All generation/ASR jobs ended, configs/Token preserved. Evidence: subtalker-followup/.
- Commit5479313 fixes explicit-English routing for eric/dylan with guarded legacy SDK upgrade. Actual red model checks fail twice; green production managed-entry checks pass four assertions with Chinese byte parity. Full check36/40/14 passes; final signed App/native driver/runner pass, with100/100 same-M1 old raw ASR outputs and independent byte verification. Eight extra auto-ASR outputs retain an English numeric quality gap (Hindi/Chinese detections); no listening acceptance claimed. All owned listeners/processes ended and production/frozen artifacts preserved. Evidence: language-routing/.
- After source1e98506, private text-layout build completed in138.13s; ten fixed paired generations completed in73.57s and seventeen final-App ASR requests in19.18s. Original failure and R11 numeric baseline bytes match. Sequential text ends seed196 at6.08s but normal control roundtrip worsens; no promotion. Independent verification at2026-11-06T16:00:51 UTC confirms96 formal source hashes, ten PCM/token pairs, seventeen WAV payload references/thirteen unique WAVs, owned PID25022/group and Helper25598 absent, port18886 closed, request cleanup and production/config/Token/frozen preservation. Desktop CLI absent and Cua transport closed; physical/interactive/listening and original target gates remain unchecked. Evidence: text-layout/.
- R12 commit b5607f2 removes destructive SDK recovery when speech_tokenizer is a file. The Foundation branch regression first failed on deletion, then eight adapter tests passed. Real-weight linked-root loader probes reproduce deletion with the previous SDK (exit 2) and preserve all user files with the fix (exit 0); original model manifest, production/config/Token and eight frozen files remain identical. Full make check exit 0/260.40 seconds: 36 native contracts, 44 active tools, 14 renders, no failures/skips. Current isolated signed App/native driver/runner exit 0 at16:27:48 UTC/189.06 seconds; independent check confirms100/100 same-M1 old ASR outputs, nine valid WAVs, source96 and process/listener/request cleanup. Evidence: model-preservation/. Original quality, strict M4, public download, physical/desktop and distribution gates remain unaccepted.
