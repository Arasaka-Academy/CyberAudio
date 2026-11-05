# Native Swift service evidence, 2026-11-05

The final isolated App was tested on Apple M4, 16GiB, macOS 27.2 (26B123),
2026-11-05 15:19:28–15:22:20 UTC. The service helper SHA-256 matches the helper
in the final local `dist/CyberAudio.app`:

```text
573b250666d7722a312b3508a57ab275501c0d1451b6863290af3429152b530a
```

| File | Evidence |
|---|---|
| `native-service.json` | Real Qwen HTTP run: 100 recordings with actual audio hashes, 16 format requests, 9 voices, complete PCM stream, 3 disconnect/recovery rounds, update admission, owned-helper shutdown and two real App launches |
| `asr-comparison.json` | Recomputed corpus-level CER/WER and separate processing/inference/HTTP P95 against the original Python baseline; differences from the earlier direct Swift model experiment |
| `proxy-assets-verified.json` | Native HTTP-proxy download of the one missing pinned README, reuse/verification of the other assets, all 23 required files and both tokenizers verified |
| `checks.txt` | Final `make check` output: 17 native cases, 4 update-gate scenarios, 11 scoring/evidence scenarios, shell/plist validation and App release build |
| `packaging.json` | Final DMG size/hash, image verification, readonly mount, strict App signatures and helper identity matching the real model test |
| `SHA256SUMS` | Checksums of these result files |

The production health instance was present and unchanged before/after the real
run (`production_instance_checked=true`). The test used port 18765, a separate
bundle ID and cache data root. It did not replace `/Applications`, write the
production configuration/token, or remove the Python runtime.

The JSON has no credentials, authorization headers, production paths or user
recordings. Request/instance identifiers are ephemeral IDs from the isolated test.
The test-generated TTS audio is retained in the isolated target's reports directory,
outside this evidence folder. The `packets` field in `stream_complete` counts
client data callbacks; it is not a count of physical network packets or codec frames.

Reproduction and limitations are in [the migration report](../../NATIVE_SWIFT.md).
The comparator is `scripts/compare_native.swift`, compiled together with the
original `Experiments/CoreML/Sources/CoreMLEvidence/Evidence.swift`.

The corpus contains 50 Mandarin and 50 US English human recordings from
[Google/FLEURS](https://huggingface.co/datasets/google/fleurs), Conneau et al. (2022),
revision `168de341b3db6859a9bac1c50a2ef5e3b47647e0`, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Reference text is unmodified; recordings are not redistributed here. There is
no dedicated code-switch or entity annotation set. Output success/EOS does not
establish human listening, style adherence, lower energy or ANE execution.
