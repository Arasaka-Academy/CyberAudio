# Recoverable native first-run preparation

The user authorized the review recommendations and continued development. This specification implements the native first-run/download recommendation within the existing compact macOS application.

## User flow

Opening an existing installation checks preparation and starts the existing service. A new or incomplete installation shows a 560 × 440 pt native preparation screen. It explains the local model download size, shows model/FFmpeg readiness, offers a download-and-start action, cancellation, retry, optional proxy, and FFmpeg file selection/install instructions. Downloading requires a user action. Existing valid assets, configuration, and Token are reused.

Preparing configuration and checking files use the bundled CyberAudioAssets tool. Inference stays in the offline sandbox. Native preparation is the only network-enabled path. The UI decodes bounded JSON status/progress records from its owned helper, and an explicit state prevents stale helper completions from starting the engine after cancellation or shutdown.

The update gate waits while preparation owns a helper or is finalizing configuration. Quit cancels only the preparation child owned by this App. Engine start/restart entry points run preparation checks when preparation is required.

## Downloads

Each fixed, SHA-256-pinned asset downloads in bounded HTTP ranges into an App-owned staging directory. Requests use URLSession download tasks so even a server that ignores Range writes its response to disk rather than allocating an entire model in memory. Completed chunks survive cancellation and network errors. A matching complete download is atomically moved into place only after exact size and digest validation.

Require correct Content-Range and length on 206 responses. A 200 response may provide the complete verified asset; never append it to an existing prefix. Reject unexpected status, malformed ranges, excessive size, invalid digest, non-regular staging/target files, and concurrent preparation owners. Preserve existing final files. An explicit restart action resets only this downloader's partial assets, never model/configuration/user files.

Report cumulative bytes, total bytes, current fixed asset path, and whether verification is running. Progress is monotonic within an attempt. Validate disk capacity before network transfers, including staging/headroom. Proxy configuration keeps the existing HTTP/SOCKS5 restrictions and rejects embedded credentials.

## Typed tool boundary

Add status/progress JSON records containing model bytes/files, missing/corrupt state, FFmpeg availability/path, and disk capacity. Do not emit Token, input audio/text, private signed download URLs, or raw environment values. Keep existing prepare/download/verify commands compatible, with explicit JSON progress and restart flags for the App.

## Acceptance

- Real controlled HTTP endpoints exercise ranged success, interrupted-chunk retry, cancellation/retry, ignored Range, wrong Content-Range, bad digest/restart, existing-file preservation, and unsafe paths.
- Existing proxy, tokenizer, configuration, and token-preservation contracts continue to pass.
- App controller tests exercise first-run, reuse, progress, failed setup/retry, cancel, late process completion, FFmpeg selection, update waiting, and quit.
- Native renders cover preparation, progress, failure, and FFmpeg-missing at the minimum window size with keyboard-accessible actions.
- Build an isolated App and verify preparation-to-ready using controlled small assets as well as reuse of local production model bytes. Keep real microphone/listening and public full-weight download evidence separate.
