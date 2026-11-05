# Core ML 原生推理验证

这是生产切换前的独立验证工具。`CyberAudioCoreMLProbe` 使用 Swift、Core ML、FluidAudio、WhisperKit 和 TTSKit，推理不启动 Python，也不访问生产配置或自动下载模型。现有 App、FastAPI 服务、部署和 MLX 模型仍用于基线比较。

先独立验证 STT／TTS 真实模型。仅在准确率、音色与表达、流式输出、取消、性能及能耗满足条件后，继续编写 App 内的原生 Helper／HTTP 服务。服务迁移完成后，还必须通过原有 API 与生命周期回归，才允许切换部署。

**本轮实测不满足替换条件，保留生产 MLX／Python 后端。** M4／16GiB 的 100 条公开真实录音、九音色及取消恢复记录见 [2026-11-05 验证报告](VALIDATION-2026-11-05.md)，逐条证据与离线复核命令见 [Results](Results/2026-11-05/README.md)。

普通 Whisper large-v3-turbo 也已完成两轮相同语料实测，准确率未达到现有 Qwen 的不退步条件，见 [Turbo 验证报告](WHISPER-TURBO-2026-11-05.md)。

**已确认的选择（2026-11-05）：** 用户决定继续使用现有 Qwen3-ASR 1.7B 8-bit 和 MLX／Python 后端。本轮 Core ML 评估到此结束，实验工具和测试证据保留；不进入原生 Helper／HTTP 服务迁移。

## 固定依赖和模型

需要 Apple Silicon、macOS 26 及以上、Swift 6.2 及以上。此最低版本只约束实验包，不更改主应用支持范围。

| 组件 | 固定版本 |
| --- | --- |
| FluidAudio | `ee78635d23d52379117554ba72358b65a0612447`，不启用默认 traits |
| Argmax WhisperKit／TTSKit | `9f99b5cb6a907730543823ad271b82532782a899` |
| Swift Argument Parser | `1.7.0` |
| SenseVoiceSmall | `FluidInference/sensevoice-small-coreml@74bdbe74b2258f25b5400db72f6cbfbef7c74ded`，FP16 编码器 |
| Qwen3-TTS | `argmaxinc/ttskit-coreml@065712357592bc0d194cf7cd91852c5cde3eb1b8`，12Hz／1.7B／CustomVoice |
| tokenizer | TTSKit 要求的 `Qwen/Qwen3-0.6B`，固定修订见 `assets.lock.json` |

模型锁包含 45 个文件、2,656,477,296 字节。大文件校验 LFS SHA256，小文件校验 Git blob SHA1。旧 MLX 权重不能用于本工具。

SenseVoice 预处理使用 CPU FP32，编码器固定 `.cpuAndNeuralEngine`。默认启用模型自身的数字／日期逆文本归一化（with-itn，embedding 14），以保留现有 API 的格式化数字；可用 `--text-norm without-itn` 复现 SDK 默认的口语文本模式（embedding 15）。模式写入报告，不进行参考文本驱动的后处理。TTS embedding 使用 CPU；解码器固定 CPU＋ANE，单 worker、不分句并发、随机种子 42、最多 1200 tokens。输出为 24kHz、单声道、PCM16 little-endian；每次生成保存 WAV 与实际逐步回调的 PCM，并逐字节比较两者。报告中的计算单元是**配置**，不能据此声称 ANE 实际运行或能耗下降。

## 构建和下载

在仓库根目录运行。下载与语料准备脚本仅供开发验收，Python 不属于原生推理运行时。

```sh
swift build -c release --package-path Experiments/CoreML --product CyberAudioCoreMLProbe
python3 scripts/prepare_coreml_models.py --root artifacts/coreml/models
```

如需代理，准备脚本接受 `--proxy http://127.0.0.1:7890`。下载支持断点续传；仅在长度和摘要均匹配后安装文件。可按 `--component asr` 或 `--component tts` 准备，再通过 `--verify-only` 离线检查。请勿将 `.partial` 和 `.ranges` 临时文件复制到部署。

```sh
PROBE_DIR="$(swift build -c release --package-path Experiments/CoreML --show-bin-path)"
PROBE="$PROBE_DIR/CyberAudioCoreMLProbe"
"$PROBE" verify --models artifacts/coreml/models --lock Experiments/CoreML/assets.lock.json
"$PROBE" asr --models artifacts/coreml/models --lock Experiments/CoreML/assets.lock.json \
  --audio /absolute/path/recording.wav --repeat 3 --report artifacts/coreml/reports/asr.json
"$PROBE" tts --models artifacts/coreml/models --lock Experiments/CoreML/assets.lock.json \
  --text '今天下午三点十五分，请提醒我开会。' --voice all --language chinese \
  --instructions '请用自然、轻快的语气说话。' --output artifacts/coreml/voices \
  --report artifacts/coreml/reports/tts.json
```

原生程序在每次加载前再次验证对应组件的资产，不调用 SDK 下载／自动恢复入口。离线验收可使用 macOS `sandbox-exec` 的禁止网络策略运行它；编译缓存仍需允许本地写入。

取消与恢复在同一 TTS 实例中验证：

```sh
"$PROBE" tts --models artifacts/coreml/models --lock Experiments/CoreML/assets.lock.json \
  --text '这是用于验证流式取消后再次生成的真实模型测试。' --voice serena --language chinese \
  --repeat 2 --cancel-after-chunks 2 --output artifacts/coreml/cancel \
  --report artifacts/coreml/reports/cancel.json
```

只取消第一次生成，第二次必须完整结束。回调发生时才记录首包和分块时间；取消／超时／达到 token、音频比例或 KV 上限都不能算作完整输出。SDK 正常返回不能证明读完：程序通过固定 SDK 的日志回调采集 EOS／限制结束原因，只有确认 EOS 才标记 `completed`。试听仍需人工完成，机器对比音频字节不能替代试听。

CLI 沿用现有接口的九个音色名，将 `uncle_fu`／`ono_anna` 显式映射为 SDK 的 `uncle-fu`／`ono-anna`，并验证 SDK 枚举，避免未知名称静默回退到 Ryan。报告保存实际 SDK 音色及 speaker token。

性能测试可选择 `--decoder-mode latency`（默认，每次约 80ms 音频）或 `--decoder-mode throughput`（每次约 320ms 音频），均保留逐步回调。`--multi-code-mode stepped`（默认）和 `fused` 选择已锁定的同一 multifunction 模型中的函数。报告记录两项配置、逐块字节数和到达时间，不能把不同配置的结果混写为一次测量。

## 100 条公开真实录音

用户没有现成录音集，使用 FLEURS 测试集前 50 条普通话、前 50 条美式英语真人录音。选择规则固定，未按模型结果筛选。数据来源：`google/fleurs@168de341b3db6859a9bac1c50a2ef5e3b47647e0`，FLEURS 数据许可为 CC BY 4.0；保留数据集及原始来源归属信息，见其 [数据卡](https://huggingface.co/datasets/google/fleurs)。

```sh
python3 -m venv .context/corpus
.context/corpus/bin/python -m pip install pyarrow==26.0.1
.context/corpus/bin/python scripts/prepare_coreml_corpus.py --root artifacts/coreml/corpus
```

输出含参考文本、录音 SHA256、不可变来源、`provenance.json`。Parquet 音频字典很大，读取前 50 条仍需下载几乎全部音频列，因此脚本直接准备完整的固定版本源文件（约 1.02GiB），校验官方 LFS SHA256 后才提取。该语料没有专门的中英混说覆盖，也没有人工数字／专名标签，不能替代这些验收。

在目标 Mac 上顺序执行，避免两个引擎同时推理影响延迟。基线脚本从现有配置私下读取令牌，不输出令牌，不改配置、不停止服务。它写入逐条检查点，任何失败都会标记不完整。原生程序读取完全相同的 WAV 文件。

```sh
python3 scripts/benchmark_coreml_baseline.py \
  --root "$HOME/Library/Application Support/CyberAudio" \
  --corpus artifacts/coreml/corpus/corpus.json --report artifacts/coreml/reports/baseline.json
"$PROBE" asr --models artifacts/coreml/models --lock Experiments/CoreML/assets.lock.json \
  --corpus artifacts/coreml/corpus/corpus.json --report artifacts/coreml/reports/candidate.json
"$PROBE" compare --baseline artifacts/coreml/reports/baseline.json \
  --candidate artifacts/coreml/reports/candidate.json --report artifacts/coreml/reports/comparison.json
```

比较工具拒绝缺样本、重复样本、音频摘要／参考文本／语言标签不匹配及无效耗时。文本进行 Unicode 规范化、忽略大小写和标点，保留数字；中文按总参考字符数汇总 CER，英文按总参考词数汇总 WER。主指标必须分别不退步。P95 使用 nearest-rank。基线延迟为服务端 `processing_ms`（含 FFmpeg 解码），原生为 `transcribe(audioURL:)`（含 SDK 解码），两者都不含冷加载；HTTP 额外开销另存，编解码路径差异应在解释结果时保留。

## 普通 Whisper large-v3-turbo

用户选择的是 `openai_whisper-large-v3-v20240930_turbo` 普通版。独立锁文件
`whisper-turbo.lock.json` 固定 Argmax 模型修订
`0f63a7800b00dd0226abd051b906c246e1907482` 和 OpenAI tokenizer 修订
`41f01f3fe87f28c78e2fbf8b568835947dd65ed9`。模型文件 1,638,464,446 字节；
加上 tokenizer 共 29 个文件、1,641,461,408 字节。

**已完成 M4／16GiB 两轮 100 条实测。** 中文 CER 9.5898%、英文 WER 4.7398%，P95 931.48／923.41ms；两轮原始文本逐条一致。准确率未达到现有 Qwen 的不退步条件，详见 [普通 Turbo 实测报告](WHISPER-TURBO-2026-11-05.md) 和 [逐条证据](Results/2026-11-05-whisper-turbo/README.md)。

```sh
python3 scripts/prepare_coreml_models.py --lock Experiments/CoreML/whisper-turbo.lock.json \
  --root artifacts/coreml/models-whisper-turbo
"$PROBE" asr --engine whisper-turbo --models artifacts/coreml/models-whisper-turbo \
  --lock Experiments/CoreML/whisper-turbo.lock.json \
  --corpus artifacts/coreml/corpus/corpus.json --report artifacts/coreml/reports/whisper-turbo.json
```

使用同一固定 Argmax SDK 的 WhisperKit，显式本地模型及 tokenizer 路径、`download: false`。
与 SenseVoice 一样，在禁止网络的 sandbox 下验证。Mel 在 CPU 上运行，编码器和解码器配置
CPU＋ANE；这仍只代表计算单元配置。开启预热，首次加载时间包含编译、预热和 tokenizer 加载。

识别为 `transcribe`、自动语言检测、单 worker、无时间戳输出，temperature 0，最多 5 次温度回退。
每条保存检测语言、回退次数、SDK 编码／解码分阶段耗时；主延迟仍是调用原生文件识别的墙钟时间，
包含音频读取和语言检测。报告保留模型原始文本，不根据参考文本转换简繁或改写数字。
`--engine sensevoice` 是默认值，`--text-norm` 仅对 SenseVoice 有效。

## 验收门槛和测试

`gate --evidence evidence.json --report gate.json` 输出缺失项，不满足条件时退出码为 2。证据 JSON 使用 `AcceptanceEvidence` 的字段名称。门槛包括 100 条真实录音、混说覆盖、准确率及实体正确率不退步、STT P95 ≤3000ms、TTS RTF <1、九音色人工试听、重复测量能耗下降、ANE 实际跟踪，以及 API／生命周期回归。该命令是最终切换检查，API 回归在服务实现后完成。

```sh
bash scripts/test_coreml_evidence.sh
.venv/bin/python -m pytest -q tests/test_coreml_assets.py
```

能耗测量需要目标机可用的 Instruments／`xctrace` 或获得权限的 `powermetrics`，记录冷编译、暖请求和对照空闲状态；固定语料、重复测量并记录环境。运行时配置 `.cpuAndNeuralEngine`、CPU 占用或 RSS 变化均不能代替实际 ANE 跟踪及能耗证据。

## 本次验证的构建差异

本地 CLT 能构建本工具，但完整 Xcode 的许可尚未接受，未代用户接受。固定 FluidAudio 的 Swift 6.2 manifest 即使关闭默认 traits，SwiftPM 仍尝试下载禁用的 Nemo 二进制。早期 smoke 构建在忽略的本地 SDK 副本里移除了该禁用声明，Swift 推理源码保持原样。随后下载并校验官方 Nemo 归档，预填本任务的 SwiftPM 缓存，已完成**未修改 SDK 的固定修订构建**。Git 镜像也仅使用官方固定提交的本地缓存，配置不提交到仓库。最终测试以未修改 SDK 的构建为准，早期 smoke 结果单独标明。
