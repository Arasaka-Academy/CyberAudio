# CyberAudio 全 Swift 迁移与验证

运行时替换已实现：App 内置 Swift HTTP／推理 Helper，安装与模型准备使用 Swift 工具，保留原来的 Qwen3-ASR 1.7B 8-bit 与 Qwen3-TTS 1.7B CustomVoice 8-bit。App 不再打包或启动 Python、FastAPI、pip、venv。FFmpeg 仍为单独配置的原生工具。

此路线使用 **MLX Swift／Apple GPU／Metal**。此前 Core ML／SenseVoice／Whisper 的实验保留；用户随后选择继续使用 Qwen，因此本次没有迁移至 Core ML 或 ANE。

在 M4／16GiB 上，最终 App 的真实服务完成 100 条录音回归、9 音色生成、流式输出、取消恢复、更新排空与退出重启。中文 CER、英文 WER 与 Python 基线持平；HTTP P95 为 1.43 秒，基线为 1.27 秒。没有观察到加速，也没有采集能耗。

本报告主体记录首次隔离验证阶段：当时只构建、签名并验证测试安装包，正式 App、8765 端口的生产服务、原配置／Token、旧 Python 环境均保留，测试包为 0.2.0（4）。后续 **0.3.0（6）已发布并安装到 M4**，CI 正式包的回归与备份／安装证据见 [0.3.0 发布验收报告](releases/0.3.0-acceptance.md)；其中的 Helper 哈希和指标对应实际交付字节。

## 实现

```text
CyberAudio SwiftUI App
  └─ sandbox-exec + stdin 所有权管道
       └─ CyberAudioService
            ├─ SwiftNIO HTTP：127.0.0.1
            ├─ 单一串行推理工作队列
            │    ├─ Qwen3-ASR → MLX Swift / Metal
            │    └─ Qwen3-TTS → MLX Swift / Metal
            └─ FFmpeg 音频解码子进程

CyberAudioAssets prepare | download | verify
  └─ 原生配置、Token、固定资产下载、SHA-256 与 tokenizer 校验
```

主要源码为 [Native 包](../Native/Package.swift)、[HTTP](../Native/Sources/CyberAudioCore/HTTPService.swift)、[任务协调](../Native/Sources/CyberAudioCore/Coordinator.swift)、[Qwen 推理](../Native/Sources/CyberAudioInference/QwenEngine.swift)、[资产工具](../Native/Sources/CyberAudioCore/Assets.swift) 与 [App 进程管理](../Sources/CyberAudio/BackendController.swift)。[部署指南](GUIDE.md) 包含首次安装、API、代理、旧版升级与回退命令。

- 保留 Bearer 认证、模型／音色查询、STT、WAV／PCM TTS、流式 PCM、请求状态、更新准备／取消。模型接口返回真实仓库和固定 revision。
- 上传 20MiB、multipart 21MiB、JSON 16KiB；上传 30 秒、解码 25 秒、任务 120 秒；解码后最多 60 秒音频。最多 64 个连接，每个连接处理一个请求。
- STT／TTS 共用一个槽。断连或超时发出取消信号，工作线程、音频回调与解码器重置真正返回后才释放槽；Metal 单次操作可能不能立即打断。
- 流式生成同步等待每次网络写入，限制慢客户端的缓冲。首段成功前可返回 JSON 错误；首段后失败中断传输，不伪造完整结束。24kHz、单声道、PCM16；正常完成必须观测到 EOS，最多 1200 个 codec token／约 100 秒输出。
- 更新租约与推理准入共用锁，租约 30 秒、取消租约保留墓碑。App 沿用 Sparkle 的录音／播放／API 排空流程。
- 合并 main 时同步 0.2.1 的保护：认证后、读取上传体前原子检查更新状态；已接收音频请求持续计入更新等待，直到 HTTP 响应写出完成或断连，模型已空闲也不能提前重启。新增真实 TCP 的未读上传拒绝与慢速 WAV 发送测试，先复现失败，再验证修复后 19 项原生测试全部通过。
- Helper 使用与旧版相同的 `backend.lock`，检查父 PID、stdin EOF 和退出信号；App 与 Helper 均有退出上限。推理使用 App 内置禁止主动联网的沙箱，不依赖旧数据目录的沙箱文件。
- 日志不记录输入、音频或 Token；请求记录保留 64 条；临时目录按请求回收并跳过符号链接，日志按 5MiB 轮转。

## 固定资产与 SDK 适配

依赖固定在 [Native/Package.resolved](../Native/Package.resolved)，23 个所需模型文件共 5,547,997,530 字节，固定大小和 SHA-256 在 [Models.lock.json](../Native/Sources/CyberAudioCore/Models.lock.json)。两个原始模型 revision 与 Python 基线相同；不转换权重、不追踪移动的最新版。服务启动逐文件离线校验并验证本地 tokenizer 的语义。

MLX Audio Swift 固定 `927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe`，MLX Swift 0.33.0、MLX 0.32.4，SwiftNIO 2.90.1。[适配脚本](../scripts/patch_mlx_audio.swift) 先校验上游源码 SHA，再为 Qwen TTS 添加同步 `generateManaged`、三个取消检查边界，并仅在该入口取消 SDK 的文本长度启发式上限。服务自行执行 1200 token 上限与 EOS 检查；SDK 原有异步入口保持原行为。补丁可重复执行，并核对已修改源码。

该入口让 HTTP 工作队列实际拥有生成过程与 decoder reset，避免“消费者已取消、后台生产者仍在使用模型”时错误释放单任务槽。测试和构建不修改仓库外的 SDK。

CLT 缺少 Metal 编译器时，[资源脚本](../scripts/prepare_metal.sh) 从官方 MLX 0.32.4 wheel 仅提取匹配的 `mlx.metallib`；归档与着色器均固定并校验 SHA-256，不安装、运行或打包 Python。SDK、MLX、SwiftNIO 及传递依赖许可证随 App 分发，说明见 [THIRD_PARTY.md](../Native/THIRD_PARTY.md)。

原生下载工具支持 HTTP／SOCKS5 代理，拒绝 URL 中的凭据；正确文件复用，已有错误文件保留并报错。M4 实测经 HTTP 代理补下载缺失的 ASR README 后，23 个文件及两个 tokenizer 全部通过验证。此项验证了下载、代理、复用和校验路径，**没有重新下载全部 5.55GB 权重，也没有实测 SOCKS5 下载**。大文件暂不支持断点续传。

## M4 完整服务结果

最终回归为 2026-11-05 15:19:28–15:22:20 UTC，Apple M4／16GiB，macOS 27.2（26B123）。测试 App 使用独立 bundle ID、独立数据目录与 18765 端口。测试前后生产服务 `/health` 的实例身份相同。最终 Helper SHA-256 与本地 `dist/CyberAudio.app` 一致：

```text
573b250666d7722a312b3508a57ab275501c0d1451b6863290af3429152b530a
```

### STT

使用同一组固定 FLEURS 真人语料：普通话 50 条、美式英语 50 条，共 1010.48 秒，单条 4.32–19.12 秒。每次提交前核对录音 SHA-256，比较器核对 ID、参考文本、语言及摘要，并复用原评分实现。

| 指标 | Python 基线 | 最终 Swift HTTP 服务 |
|---|---:|---:|
| 成功请求 | 100/100 | 100/100 |
| 中文 CER | 7.9723% | 7.9723% |
| 英文 WER | 3.3457% | 3.3457% |
| 服务处理 P95 | 1271.55ms | 1428.75ms |
| 模型推理 P95 | 1244.59ms | 1326.60ms |
| 客户端 HTTP P95 | 1274.71ms | 1433.45ms |

CER／WER 是语料级微平均：NFC、转小写、非字母数字转空白；中文去空白后按字符评分，英文按空白分词；P95 使用 nearest rank。两次服务测试在同一台机器先后执行，未隔离后台负载，不作普遍性能结论。表中三种计时口径分别列出，不能混用。

与 Python 的原始文本 98/100 相同；与先前直接 Swift 模型实验的原始文本 95/100 相同，差异包含标点及两个英文样本中的词语变化，详见比较 JSON。因此不能声称迁移后每条识别结果逐字不变。语料总体中文 CER／英文 WER 持平；数字／专名没有专项标注，不满足关键实体验收的证据要求。两种服务仍将相同 6 条中文录音标记为英语，正文识别与语言标签需分别判断。

另对一条中文、一条英文分别通过 WAV／WebM／MP3／M4A／FLAC／OGG／AIFF／AAC 提交，共 16 个格式请求全部成功；实际由配置的 FFmpeg 解码。

### TTS 与流式

最终服务预热后对每个音色生成一次，均传入表达指令、生成 EOS，并输出有效的 24kHz 单声道 PCM16 WAV。结果为短句，不代表长文本和主观音质验收。

| 音色 | 首段生成 | RTF | 音频长度 |
|---|---:|---:|---:|
| serena | 252.99ms | 0.645 | 1.84s |
| vivian | 218.75ms | 0.622 | 2.08s |
| uncle_fu | 216.23ms | 0.615 | 2.32s |
| ryan | 218.10ms | 0.604 | 3.12s |
| aiden | 217.70ms | 0.611 | 2.72s |
| ono_anna | 217.66ms | 0.615 | 2.32s |
| sohee | 216.84ms | 0.619 | 1.92s |
| eric | 210.96ms | 0.614 | 2.32s |
| dylan | 217.61ms | 0.624 | 1.92s |

完整 PCM 流收到 107,520 字节，客户端观测 7 次数据回调、首数据 218.04ms，并查询到 `completed`。回调次数是客户端网络观测，不等同于 codec 块数或网络包数。三次首段后断连均进入 `cancelled`，模型槽排空约 41–42ms，同一实例后续生成均返回 200。第一次同时执行更新准备：新请求返回 503，取消更新租约后恢复。

以上“首段生成”是工作线程从开始请求至第一个音频回调的计时，WAV 响应会在生成完整后返回。流式“首数据”才是客户端测得的首包时间。App 启动已显式预热；此前未预热模型的首段可达 3.78 秒，不能把预热后数据作为完整冷启动。

### 启动、内存与进程所有权

该次启动模型文件校验 3.09 秒、双模型加载 2.85 秒、预热 2.91 秒；未重启机器或清空系统缓存。启动后 MLX Metal 活跃内存 5,578,423,720 字节、Metal 峰值 6,273,318,916 字节，进程峰值 RSS 3,456,204,800 字节。这些计数口径不同，不能相加，也不证明全系统内存节省；目标机同时保留生产服务。

关闭 Helper stdin 后约 193ms 停止。真实 SwiftUI App 在真实 TTS 生成期间分别收到 TERM 和 KILL，两次直接子进程均确认为 `CyberAudioService`，App／监听停止、Helper 被回收，下次启动恢复 ready。记录中的 App 停止耗时分别约 278ms／76ms；Helper 回收是另行轮询确认，不能把该数值解释成完整模型卸载耗时。等待后没有服务复活。出站网络探针在 App 同一沙箱中返回 EPERM。

## 自动检查与打包

`make check` 已通过：17 项原生行为测试、4 项更新状态机测试、11 项原评分／验收工具测试，以及 shell 语法、plist 和 App Release 编译。17 项覆盖真实 SwiftNIO／FFmpeg、严格参数、认证、分块限额、互斥、租约、取消／超时后保留槽、慢客户端、错误隐私、半途流失败、清理、已有配置／Token 保留、跨进程／语言环境 tokenizer 校验与真实代理失败路径。它们使用受控推理替身，不代替上面的真实 Qwen 回归。

历史 Python／开发发布工具的 45 项测试亦通过；这些文件保留在源码中，不进入 App。新更新测试 fixture 已能构建两个使用 `CyberAudioFixture` 的原生 App 并签名本地清单／归档，但**尚未重新执行完整的 Sparkle GUI 下载、安装与重启轮次**；[自动更新指南](AUTOMATIC_UPDATES.md) 将历史 Python fixture 的完整轮次单独标记。

`build_app.sh` 打包两个 arm64 Helper、资源 bundle、Metal、Sparkle 和许可证，转换并签名 SwiftPM 的资源 bundle，执行严格递归签名验证及资源查询 smoke。检查 App 没有 `.py`／`.pyc`／`.venv`／`libpython` 文件，也没有 Helper 的 Python 动态链接。最终 App 本地占用约 286MiB；DMG 另行校验压缩映像与 SHA-256。

构建需要 Swift 6.3+。当前使用 CLT 和已验证的匹配着色器构建；未接受完整 Xcode 许可。SwiftPM native build system／skip-update 的弃用提示及上游 README 资源提示保留。尚未执行这次源码的 GitHub Actions、Developer ID 签名／公证或正式发布；CI runner 也必须提供符合要求的 Swift 工具链。

## 证据与复现

- [最终服务逐条结果](evidence/native-swift-2026-11-05/native-service.json)：100 条 ASR、格式、音色、流式、取消、生命周期、版本与 Helper 摘要。
- [同语料对比](evidence/native-swift-2026-11-05/asr-comparison.json)：准确率、三种延迟及与先前 Swift 输出的差异。
- [代理补下载后的资产校验](evidence/native-swift-2026-11-05/proxy-assets-verified.json)。
- [先前独立 Qwen 实验](../Experiments/QwenSwift/VALIDATION-2026-11-05.md) 与其 Python 基线、两轮直接 Swift 推理记录。

从仓库构建检查与验收驱动：

```bash
make check
make app
bash scripts/build_dmg.sh 0.3.0 --reuse-app
swiftc -parse-as-library -swift-version 5 -target arm64-apple-macos26.0 \
  scripts/verify_native.swift -o .context/verify-native
# 使用隔离 App bundle ID、全新测试目录与非生产端口；模型、语料须事先准备。
.context/verify-native --app /absolute/path/to/isolated/CyberAudio.app \
  --root /absolute/path/to/new-test-data --models /absolute/path/to/models \
  --corpus /absolute/path/to/corpus --port 18765 --asr-count 100
```

`--models` 下须有 `asr/`、`tts/`；`--corpus` 下为固定 `corpus.json` 和 WAV。驱动拒绝默认生产目录、8765 端口、无专用标记的已有数据目录和占用端口；它会写入测试配置、启动测试 App、在生成中退出该 App，因此只能用于隔离副本。旧 Python 生命周期脚本仍为历史工具，不能用于这套隔离验收。

重算已保留的报告：

```bash
swiftc -parse-as-library -swift-version 5 \
  Experiments/CoreML/Sources/CoreMLEvidence/Evidence.swift scripts/compare_native.swift \
  -o .context/compare-native
.context/compare-native \
  Experiments/QwenSwift/Results/2026-11-05/m4-qwen-python-baseline.json \
  Experiments/QwenSwift/Results/2026-11-05/m4-qwen-swift-run1.json \
  docs/evidence/native-swift-2026-11-05/native-service.json \
  .context/native-asr-comparison.json
```

当前剩余验收为：真实麦克风与扬声器、九音色及表达指令主观试听、长文输出、中英混说与专名／数字专项语料、长期内存与能耗、原生 fixture 的完整 Sparkle GUI 安装轮次、正式签名／公证与发布网络。这些不由现有 API 成功或短句 RTF 推导。

FLEURS 与参考文本归属 Google／FLEURS（Conneau 等，2022），许可 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，[数据卡](https://huggingface.co/datasets/google/fleurs)，固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`。本目录保留参考文本与模型输出，不分发录音；参考文本未修改。
