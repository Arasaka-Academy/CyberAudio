# Swift Qwen：M4 实测结果

**保留现有 Qwen 模型、去掉推理阶段 Python，已通过独立模型验证。** Swift 的中文识别错误率与当前 Python 相同，英文少一个词错误；延迟接近。九音色均能逐块输出，预热后合成速度快于播放速度。建议下一步验证原生 Helper 与现有 HTTP 协议，再决定生产替换。

本次使用 **Swift＋MLX／Metal GPU**。它没有验证 Core ML／ANE，也没有证明更省电或更省内存。生产 App、Python 服务、配置及模型文件未改动；测试结束时原服务仍为同一实例、ready、空闲。

## 识别结果

目标机为 Apple M4／16GiB、macOS 27.2（26B123）。复用此前固定的 100 条 FLEURS 真人录音：普通话 50 条、英语 50 条，共 1,010.48 秒，单条 4.32–19.12 秒。本轮重新采集生产 Python 基线，随后串行运行两个独立的 Swift 进程。所有原生推理均在禁止网络的 sandbox 中执行。

| 指标 | 当前 Python Qwen | Swift 第 1 轮 | Swift 第 2 轮 |
|---|---:|---:|---:|
| 完成录音 | 100/100 | 100/100 | 100/100 |
| 中文 CER，越低越好 | 7.9723% | 7.9723% | 7.9723% |
| 英文 WER，越低越好 | 3.3457% | 3.2528% | 3.2528% |
| 请求处理 P50 | 829.54ms | 869.42ms | 861.52ms |
| 请求处理 P95 | 1,271.55ms | 1,304.47ms | 1,338.44ms |
| 最大请求处理时间 | 4,536.84ms | 1,575.02ms | 1,509.44ms |
| 100 条处理时间之和 | 90.09s | 91.01s | 90.73s |
| 与参考语言标签一致 | 94/100 | 94/100 | 94/100 |

两轮 Swift 的原始识别文本、检测语言均 **100/100 一致**。每轮与 Python 的原始文本 97 条相同，评分规范化后 98 条相同。中文 50 条文本与 Python 全部一致。英文 WER 从 36/1,076 个词错误变成 35/1,076；这是小样本中的一个词差异，不能据此宣称普遍提高准确率。

两个有评分差异的英文样本为 `en-010-1776` 和 `en-046-1710`：前者 Swift 正确识别了 “Through the night”，后者参考文本中的专名 “METI” 两种实现均未正确识别。专名、数字没有独立标注，不能作专项验收。两种实现还将同样 6 条中文录音的语言标签返回为英语，尽管正文主要为中文；这个现有问题没有因 Swift 迁移而消失。

CER／WER 为语料级微平均，复用原生评分器：Unicode NFC、转小写、将非字母数字字符变为空白；中文按去空白后的字符评分，英文按空白分词，保留数字。P50／P95 使用 nearest rank。比较器核对 ID、重复序号、录音 SHA256、参考文本及语言，拒绝不完整、未配对结果。保留报告在本机重新评分，与目标机两轮结果一致。

延迟主表中，Python 是服务返回的 `processing_ms`，包含音频规范化与推理；Swift 是从本地 WAV 解码到生成结果的墙钟时间。两者都不包含模型加载，但输入处理路径不同。次要的模型计时 P95 分别为 Python **1,244.59ms**、Swift **1,304.16／1,338.15ms**。本轮 Swift P95 比 Python 高约 2.6%／5.3%，仍低于此前的 3 秒目标，没有观察到速度提升。

Python 基线首条录音耗时 4.54 秒，服务当时已驻留；Swift 每次运行前显式预热。两者不构成同等条件下的冷启动对比，不能用最大值宣称 Swift 大幅加速。

## 加载与内存

首次独立 Swift ASR 进程的资产校验为 1,318.89ms、模型加载 2,655.48ms、一秒静音预热 3,678.85ms；随后同一中文录音三次为 **1,073.20／707.02／706.18ms**。这是本次首次模型加载记录，先前已经运行过 GPU 算术 smoke，也未清空系统文件缓存，不是重启后的完整冷启动。

两轮完整 ASR 的模型加载为 1,220.91／1,141.78ms，预热为 647.02／506.80ms；每次还有 1,296.11／1,131.67ms 的资产摘要校验。正式服务需要分别考虑资产校验、模型加载、预热以及首请求，不能将单条识别时间当成启动时间。

| 进程／场景 | 进程峰值 RSS | MLX Metal 峰值 |
|---|---:|---:|
| Swift ASR 第 1 轮 | 2.24GiB | 3.20GiB |
| Swift ASR 第 2 轮 | 3.02GiB | 3.20GiB |
| Swift TTS 九音色 | 2.45GiB | 3.43GiB |
| Swift TTS 取消／恢复 | 4.09GiB | 3.43GiB |
| Swift TTS 英文 | 4.14GiB | 3.43GiB |
| 当前 Python 服务，本轮观察到的进程历史峰值 | 1.36GiB | 6.18GiB |

**这些数值不能直接用于判断整体内存节省。** Swift 每个进程只加载 ASR 或 TTS；生产服务同时驻留两者，且其计数是进程生命周期峰值。RSS 与 Metal 为不同口径，不能相加。Swift 最终 `metal_active_bytes` 很小，是报告采集时模型已被作用域／ARC 释放，不能当成驻留需求。

目标机同时保留生产服务及其他应用。主测试前后 `vm.swapusage` 的已用量为 **5,204.50M → 8,570.50M**；补充音频内容校验结束后为 8,554.50M。测试时存在明显内存压力，这次没有隔离系统负载、双模型驻留峰值或长时间服务内存表现。没有 Instruments／能耗数据，不判断 ANE 或省电效果。

## 语音合成结果

使用原有 **Qwen3-TTS 1.7B CustomVoice 8-bit**，24kHz 单声道 PCM16；九音色各生成两次。中文输入为“今天下午三点十五分，请提醒我开会。”，表达指令为“请用自然、轻快的语气说话。”。参数为 temperature 0.7、seed 42、top-k 50、top-p 1、repetition penalty 1.05、max tokens 1200、流式间隔 0.32 秒；全部串行执行。

| 音色 | 两次均观测到 EOS | 第二次首块 | 第二次 RTF | 音频长度 |
|---|---|---:|---:|---:|
| serena | 是 | 244.98ms | 0.619 | 2.96s |
| vivian | 是 | 242.31ms | 0.611 | 3.36s |
| uncle_fu | 是 | 241.92ms | 0.604 | 3.76s |
| ryan | 是 | 241.55ms | 0.620 | 2.64s |
| aiden | 是 | 243.48ms | 0.612 | 3.20s |
| ono_anna | 是 | 241.89ms | 0.613 | 3.04s |
| sohee | 是 | 242.45ms | 0.608 | 3.20s |
| eric | 是 | 242.05ms | 0.616 | 2.96s |
| dylan | 是 | 241.24ms | 0.614 | 2.96s |

18/18 次均输出 9–12 个实际音频块，并在真实 token 回调中观测到配置的 EOS 2150。固定 seed 的九组重复生成，其 token 序列和 PCM SHA256 均一致。首次未预热的 Serena 首块 **3,781.59ms**、整句 5,586.25ms、RTF **1.887**；后续 17 次首块 241.24–244.98ms、RTF **0.604–0.620**。RTF 小于 1 表示生成速度快于音频播放，首请求仍需预热或加载提示。

首次 TTS 进程的模型加载为 2,483.24ms，另有 1,450.75ms 的摘要校验，均不包含在以上首块计时中。补充的 Ryan 英文两次均自然生成 EOS，音频各 3.44 秒，首块 423.58／242.78ms，RTF 0.662／0.610。该英文测试是新的进程，不能当成九音色进程的连续预热结果。

验证器检查了 **22 份** WAV／PCM：24kHz、单声道、16-bit、WAV 数据区与 PCM 完全相同，摘要正确，字节数等于所有块之和，块时间递增。将其中 **21 份完整输出**交给离线 Swift Qwen ASR，识别文本均与输入逐字一致。这是机器内容检查，不能代替人工试听音质、音色相似性、语气或分块边界听感。

表达指令已通过 SDK 的 CustomVoice `speaker, instruction` 入口传入，未做人工或对照试听，不能宣称语气遵从已验收。SDK 还有 `min(maxTokens, max(75, 文本 token 数 × 6))` 的内部上限；本次短句均生成 EOS，不代表长文本没有截断风险。

取消测试在收到两个音频块后取消消费者，保存的部分音频为 0.64 秒；等待 2 秒后，在同一模型实例上再次生成，完整输出 EOS，PCM 与未取消时的 Serena 相同。**SDK 没有暴露 producer join，本次没有证明取消立即排空。** 这还不能替代 HTTP 断连、更新等待、退出等服务验收。

本机试听文件保存在忽略的 `artifacts/qwen-swift/audio/`，不随结果 JSON 分发。示例：[Serena 中文](../../artifacts/qwen-swift/audio/voices/serena-1.wav)、[Ryan 英文](../../artifacts/qwen-swift/audio/english/ryan-1.wav)。其余音色按 `voices/<voice>-1.wav` 命名。

## 版本、离线与复现

- MLX Audio Swift：[`927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe`](https://github.com/Neywa-Labs/mlx-audio-swift/tree/927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe)，SDK 推理源码未修改。
- MLX Swift 0.33.0：`7ede4fd6d05f63305c1c0ebf4f8c1909a637c574`；底层 MLX 0.32.4：`36969c094354086e15d2a327a9c50feb20b9cf11`。传递依赖见 [Package.resolved](Package.resolved)。
- ASR：[`mlx-community/Qwen3-ASR-1.7B-8bit`](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit/tree/a8379a2e2f9e313c9292cdf1af4055ab56d50d55)，修订 `a8379a2e2f9e313c9292cdf1af4055ab56d50d55`。
- TTS：[`mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit`](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/tree/41d3337e8b7f2843a75841595fc14e4b9a7a4b96)，修订 `41d3337e8b7f2843a75841595fc14e4b9a7a4b96`。

通过 APFS clone 在独立目录复用生产资产，24 个原始文件共 5,547,999,722 字节，核对固定权重 provenance 与所有文件 SHA256；没有重新下载或转换模型。Swift 生成的 tokenizer 只写入隔离副本。最后重新校验生产模型：原有 10 个 ASR 文件、14 个 TTS 文件的摘要和文件集合未改变，生产目录均未出现 `tokenizer.json`。

本机 M1 Max 用 Swift CLT 6.4 编译 Release 二进制，再传到 M4 验证，两端二进制 SHA256 相同：`15302b13c764a506607c4a7f582df9971760ec19b2680e78b94d8219b8e2cad0`。两台机器的原生 GPU smoke 均通过。`otool -L` 未发现 Python／virtualenv 链接；Swift probe 直接加载本地权重，网络被 sandbox 禁止。Python 仅用于开发阶段的资产准备、任务编排、现有服务基线采集与证据整理。

机器缺少完整 Xcode 的 Metal 编译器，使用 SwiftPM `--build-system native` 完成 Swift／C++ Release 构建（347.96 秒、exit 0），通过公开 `GPU.metallib` 入口加载**匹配 MLX 0.32.4** 的官方预编译着色器。它来自 [PyPI 官方 wheel](https://files.pythonhosted.org/packages/2a/d6/4340ed6e184f71693d7f531c9ae55d677451351245760d71f8dd714dcbe5/mlx_metal-0.32.4-py3-none-macosx_26_0_arm64.whl) 的 `mlx/lib/mlx.metallib`，仅提取资源，不安装或调用 Python 包。wheel 与着色器摘要见 [metallib.json](Results/2026-11-05/metallib.json)。未接受 Xcode 许可或更改开发工具配置。

该构建路径有 native 构建系统弃用及上游 README 资源警告。它证明当前二进制能原生运行；完整 Xcode 构建、资源打包、签名、分发仍需独立验证。本实验最低系统为 macOS 26，与当前 App 声明一致。

结构化原始结果、汇总、资产摘要、脱敏说明和重算命令见 [证据目录](Results/2026-11-05/README.md)。资产准备脚本的 4 个隔离／完整性测试、全套 45 个 Python 测试、原生评分工具的 11 个行为场景均通过；两个保留报告也已本机重新评分。模型实验未覆盖 API／生命周期、人工试听、中英混说、专名数字专项、长文 TTS、双模型驻留和能耗；生产替换尚未验收。

FLEURS 及参考文本归属 Google／FLEURS（Conneau 等，2022），许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。[数据卡](https://huggingface.co/datasets/google/fleurs)，固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`，参考文本未修改。
