# Qwen 的 Swift／MLX 独立验证

本实验检验在保留 Qwen3-ASR 1.7B 8-bit 和 Qwen3-TTS 1.7B CustomVoice 8-bit 的情况下移除运行时 Python。生产 App／服务不接入本实验。Python 仅用于开发阶段准备资产和采集现有服务基线，原生推理由 `CyberAudioQwenSwiftProbe` 完成。

**M4 实测已完成：** 两轮各 100 条识别均完成，中文 CER 7.9723%，英文 WER 3.2528%，P95 1.30／1.34 秒；两轮文本一致。九音色均生成 EOS，预热后 RTF 0.604–0.620。首次未预热的 TTS 首块为 3.78 秒，取消排空、人工试听及生产集成尚未验收。详见 [验证报告](VALIDATION-2026-11-05.md) 和 [保留证据](Results/2026-11-05/README.md)。

MLX Audio Swift 固定为 `927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe`，MLX Swift 为 0.33.0（`7ede4fd6d05f63305c1c0ebf4f8c1909a637c574`），底层 MLX 子模块为 0.32.4（`36969c094354086e15d2a327a9c50feb20b9cf11`）。所有传递依赖见 `Package.resolved`。实验需要 Swift 6.3 及以上、Apple Silicon、macOS 26 及以上。

## 构建与准备

正常的完整 Xcode 环境应按上游要求构建并携带 Metal 着色器。本实验机器仅有 CLT，改用 SwiftPM `native` 构建系统编译 Swift／C++，运行时通过 MLX 的公开 `GPU.metallib` 入口加载匹配底层 MLX 0.32.4 的官方预编译着色器。SDK 推理源码不修改。

```sh
swift build --build-system native -c release --package-path Experiments/QwenSwift \
  --product CyberAudioQwenSwiftProbe
```

实验着色器来自官方 PyPI `mlx-metal 0.32.4` 的 macOS 26 arm64 wheel，仅提取 `mlx/lib/mlx.metallib`，不安装或调用 Python 包。发行包 SHA256 为 `514df92d41a286895d2564b4b18f4da45096bd7a4a0c47e1042f345ad5a7f936`，着色器 SHA256 为 `c6d7a3facb83568f6e4cb2cf4a8c736ee29302571a48343cb9a3a4d1cd3e3893`，大小 214,156,448 字节。正式 App 构建仍需独立验证完整 Xcode 的打包流程。

在模型所在 Mac 上运行准备脚本，使用 APFS clone 副本及逐文件 SHA256，避免 Swift tokenizer 准备操作写入生产模型目录：

```sh
python3 scripts/prepare_qwen_swift_assets.py \
  --service-root "$HOME/Library/Application Support/CyberAudio" \
  --destination /absolute/path/qwen-swift-models
```

它核对当前固定模型的生产 manifest／下载 provenance、权重摘要及复制后每个文件的摘要。已有错误副本会被拒绝，不自动覆盖。Swift 每次加载前再次验证对应组件，并直接使用 `fromModelDirectory`。首次加载可能在副本中生成 `tokenizer.json`，其实际摘要单独写入报告。

## 离线识别与配对比较

`PROBE` 为构建后的原生可执行文件，`MODELS` 为上述隔离副本根目录，`METALLIB` 为已校验的预编译着色器路径。

```sh
sandbox-exec -p '(version 1)(allow default)(deny network*)' "$PROBE" smoke \
  --metallib "$METALLIB" --report /absolute/path/gpu-smoke.json
sandbox-exec -p '(version 1)(allow default)(deny network*)' "$PROBE" asr \
  --metallib "$METALLIB" --models "$MODELS" --lock "$MODELS/assets.lock.json" \
  --corpus artifacts/coreml/corpus/corpus.json --report artifacts/qwen-swift/asr.json
```

复用 Core ML 实验的同一批 100 条 FLEURS 真人录音。ASR 使用自动语言、temperature 0、max tokens 1024、无额外 repetition penalty；加载后用一秒静音、中文、16 tokens 单独预热，与生产入口一致。报告区分资产校验、加载、预热、文件识别墙钟时间、模型自身耗时和进程／Metal 内存。逐条包含参考文本、录音 SHA256 和实际检测语言，不根据参考文本修正输出。

原生 Core ML 实验工具的 `compare` 子命令仅依赖通用报告字段，可以直接重算 Swift Qwen 与现有 Python Qwen 的 CER／WER、P95，拒绝不完整或未配对的样本。生产基线继续使用 `scripts/benchmark_coreml_baseline.py`。

## 流式语音合成

```sh
sandbox-exec -p '(version 1)(allow default)(deny network*)' "$PROBE" tts \
  --metallib "$METALLIB" --models "$MODELS" --lock "$MODELS/assets.lock.json" \
  --voice all --repeat 2 --output artifacts/qwen-swift/voices \
  --report artifacts/qwen-swift/voices.json
```

TTS 保持 1.7B CustomVoice、24kHz 单声道 PCM16，逐块写入 PCM 和 WAV，记录首块、所有分块及 RTF。九音色名称直接核对模型 `spk_id`；Swift API 通过 `voice = "speaker, instruction"` 传递音色和表达指令。参数 temperature 0.7、seed 42、top-k 50、top-p 1、repetition penalty 1.05、最多 1200 tokens、流式间隔 0.32 秒。SDK 还有内部文本长度上限，正常返回不能证明完整生成；必须在真实 token 回调中观测到模型配置的 EOS，才标为 `completed`。

使用 `--cancel-after-chunks 2 --repeat 2` 只取消第一条，随后同一模型实例再次生成。SDK 的流终止会取消 producer，但当前接口不提供 producer join；报告明确记录这一局限，并在取消后等待 2 秒再验证恢复。这个测试不等价于服务层已能立即确认任务排空。`--timeout` 是事件检查时的上限，独立验证运行器还应设置进程总超时。

生成 WAV／PCM 及 token 回调可以验证渐进输出、长度和停止原因；九音色、发音及表达效果仍需人工试听。原生 HTTP、断连、更新排空及 App 生命周期不属于该模型实验。

FLEURS 及使用的参考文本来自 Google／FLEURS（Conneau 等，2022），许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。固定语料和归属信息见 [现有语料记录](../CoreML/Results/2026-11-05/README.md)及 [数据卡](https://huggingface.co/datasets/google/fleurs)。
