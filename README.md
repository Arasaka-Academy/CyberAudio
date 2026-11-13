<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="design/icons/CyberAudio-wordmark.svg">
    <img src="design/icons/CyberAudio-wordmark-black.svg" width="360" alt="CyberAudio">
  </picture>
</p>

<p align="center">
  <strong>Local speech to text, text to speech, and dictation for macOS.</strong>
</p>

A native SwiftUI workspace with Liquid Glass. Inference runs locally with MLX and Metal, without a Python runtime.

- **Transcribe:** Record or import Chinese, English, or mixed speech with Qwen3-ASR 1.7B (8-bit).
- **Speak:** Nine voices, expression controls, WAV export, and streaming PCM with Qwen3-TTS CustomVoice 1.7B (8-bit).
- **Dictate:** Hold Right Option to record; release to transcribe and paste into the original input field.
- **Integrate:** A local HTTP API with Bearer authentication at `http://127.0.0.1:8765`.

## Get started

Requires Apple Silicon and macOS 26+. We recommend 16 GB of unified memory and 15 GB of free disk space.

1. Download a DMG from [Releases](https://github.com/Arasaka-Academy/CyberAudio/releases) and copy CyberAudio to Applications.
2. Install FFmpeg: `brew install ffmpeg`.
3. Open CyberAudio and download the models (about 5.55 GB) from Hugging Face or ModelScope.

Closing the window keeps the app in the menu bar. Quitting stops the API and unloads the models.

Current packages are ad-hoc signed and not notarized.

## Build

Requires Swift 6.3+ and Apple Command Line Tools. Tests also use [uv](https://docs.astral.sh/uv/).

```sh
make app    # Build dist/CyberAudio.app
make check  # Run native, UI, and tool regressions
make dmg    # Create a DMG
```

## Documentation

Detailed guides (Chinese): [Setup & API](docs/GUIDE.md) · [First launch](docs/FIRST_RUN.md) · [Dictation](docs/DICTATION.md) · [Updates](docs/AUTOMATIC_UPDATES.md) · [Signing](docs/SIGNING.md) · [Test deployment](docs/TEST_MACHINE.md).
