# CyberAudio：全 Swift 版本使用与部署

本指南对应 Swift 迁移源码。界面使用 SwiftUI／AppKit／AVFoundation，HTTP 使用 SwiftNIO，推理使用 MLX Swift 与 MLX Audio Swift；App 自带并直接管理唯一 `CyberAudioService` 子进程。安装、模型准备、HTTP 和推理均无需 Python。FFmpeg 仍是单独配置的原生可执行文件。

默认使用 Qwen3-ASR 1.7B 8-bit 和 Qwen3-TTS 1.7B CustomVoice 8-bit，也可在设置中选择这两个系列的 0.6B／1.7B、4-bit／8-bit 共八款固定型号。计算设备为 Apple GPU／Metal，选择、下载与存储迁移见[模型管理](MODELS.md)。

## 1. App 的运行方式

打开 App 后先检查模型和 FFmpeg，准备完成再加载、预热两个模型，接口随引擎启动。关闭工作台窗口后继续在菜单栏运行；从菜单栏可恢复工作台、打开设置、重启引擎或退出。退出或强制结束 App 会关闭其子进程和 API，模型不会作为系统守护服务继续运行。

首次安装可直接使用 App 的[准备界面](FIRST_RUN.md)，也可按第 12 节通过命令行准备。已有 Python 版用户按第 13 节升级。

## 2. 交付内容

```text
CyberAudio.app/Contents/
  MacOS/CyberAudio                 SwiftUI App
  Helpers/CyberAudioService        Swift HTTP + Qwen 推理
  Helpers/CyberAudioAssets         Swift 模型准备、下载、校验
  Helpers/*.bundle                固定依赖的资源
  Resources/mlx.metallib           固定并校验的 Metal 着色器
  Resources/offline.sb             禁止推理进程主动联网
  Resources/ThirdParty/            依赖许可证
  Frameworks/Sparkle.framework     更新框架
```

默认模型一对约 5.55GB，单独保存；App 不内置模型。本地打包使用 ad-hoc 签名，尚未进行 Developer ID 签名和 Apple 公证。源码构建不自动发布新版本。

源码已支持显式配置 Apple Development／Developer ID 签名与公证，要求和验收边界见[签名说明](SIGNING.md)。默认开发构建及既有稳定版的签名状态不因此改变。

## 3. 模型与设备

Apple Silicon、macOS 26 或以上，推荐 16GB 以上统一内存、至少 15GB 可用磁盘。Swift 源码构建需要 Swift 6.3 或更新工具链。

| 项目 | 固定版本 |
|---|---|
| ASR | `mlx-community/Qwen3-ASR-1.7B-8bit` @ `a8379a2e2f9e313c9292cdf1af4055ab56d50d55` |
| TTS | `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` @ `41d3337e8b7f2843a75841595fc14e4b9a7a4b96` |
| MLX Audio Swift | `927d4e30cf1dd3bcfdbf2cec8c9a6fd7edf4b4fe` |
| MLX Swift / MLX | 0.33.0 / 0.32.4 |
| SwiftNIO | 2.90.1 |

全部传递依赖见 `Native/Package.resolved`，八款型号的文件大小与 SHA-256 见 `Native/Sources/CyberAudioCore/ModelCatalog.lock.json`；原默认清单继续保存在 `Models.lock.json`。每次启动离线核对当前模型，不按最新版本下载。Tokenizer 由固定输入在本机生成，已有 tokenizer 必须与预期语义一致。

## 4. 服务与并发

App → `sandbox-exec` → `CyberAudioService` → SwiftNIO + 串行 Qwen 工作队列。FFmpeg 作为子进程把输入转换为 16kHz 单声道 PCM WAV。推理和解码继承禁止主动出站的沙箱。

默认监听 `127.0.0.1:8765`。STT 和 TTS 共用一个推理槽，忙时返回 429。断连、取消或超时只发出取消信号；生成和解码真正返回后才释放槽。TTS 逐步输出，每次写入完成后才继续生产，限制慢客户端的缓冲。

更新准备租约原子地暂停新推理，保留已有任务；租约每 30 秒需续期，取消等待恢复接收请求。Sparkle 同时等待界面录音、播放、听写识别、文本输出和剪贴板恢复完成。

## 5. 界面

工作台默认与最小内容尺寸为 560 × 440 pt，可放大。首次升级到紧凑界面时重置旧窗口尺寸，随后记住用户调整的大小。顶部切换“语音转文字”和“文字转语音”，并显示引擎状态与设置入口。主窗口没有外层滚动；文本编辑区可内部滚动、选择、撤销与中文输入，但始终不显示滚动条。

语音转文字保留手动录音、导入、语言选择、复制和清空。文字转语音保留音色、语言、生成、播放和 WAV 保存；表达指令与流式播放选项在“语音选项”弹出面板中。0.6B TTS 根据模型能力禁用表达指令。错误、加载和更新等待使用固定状态区，较长错误可打开“详情”。设置分为“听写”“模型”“服务”“通用与更新”四个分页，模型页使用滚动布局。

全局快捷听写默认按住右 Option 150 ms 后录音，松开后识别，并向原输入框发送一次普通粘贴；不自动回车或发送消息。首次使用先在“设置 → 听写”完成麦克风、辅助功能和输入监控授权，可修改组合键、恢复默认或暂停功能。录音与识别过程通过屏幕底部浮层反馈，关闭主窗口后仍可使用。Escape 取消，录音最长 59 秒；右 Option 配合其他按键时取消本次快捷录音并保留正常键盘输入。

如果识别期间切换应用、输入框或可观察的选区，本次结果保留在工作台，浮层提供复制入口。返回原输入框也不会恢复本次自动输入。密码框、不可编辑控件、权限不足、仍按住修饰键或无法完整保存剪贴板时，只保留结果。主窗口手动录音和文件导入仅用于预览、编辑与复制。完整行为和验收说明见 [快捷听写与紧凑界面](DICTATION.md)。

真实麦克风、扬声器与全部音色的主观音质仍需人工验收。合成音频或 API 成功不等同于完成这些验收。

## 6. API 共通约定

`/v1/*` 和 `/internal/*` 需要唯一的 `Authorization: Bearer <Token>`。`/health`、`/ready` 与 `/` 可匿名访问。Token 保存在数据目录 `config/api-token`，权限 0600；可在设置中复制，不应放入 URL、截图或 Git。

在 macOS zsh 中：

```bash
export CYBERAUDIO_BASE_URL='http://127.0.0.1:8765'
read -rs 'CYBERAUDIO_TOKEN?API Token: '
echo
export CYBERAUDIO_TOKEN
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/models" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"
```

`/v1/models` 返回真实模型 ID、仓库、revision 与量化规格。`/v1/audio/voices` 返回 9 个音色：`serena`、`vivian`、`uncle_fu`、`ryan`、`aiden`、`ono_anna`、`sohee`、`eric`、`dylan`。默认 `serena`。

这是 OpenAI 风格的兼容子集；只支持本文列出的模型、参数与输出格式。HTTP 每个连接处理一个请求，响应后关闭连接；客户端可自动重新建立连接。请求记录最多保留 64 条，重启后清空。

## 7. STT

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/audio/transcriptions" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN" \
  -F 'file=@./speech.webm' -F 'language=auto' -F 'response_format=json'
```

只接收一个 `file`；文件最多 20MiB，整个 multipart 请求最多 21MiB，解码后最长 60 秒。支持 WAV、MP3、M4A、WebM、OGG、FLAC、AAC、AIFF；实际解码能力取决于配置的 FFmpeg。上传截止 30 秒、解码截止 25 秒、整个推理任务截止 120 秒。

语言可用 `auto`、`zh`、`en`、`Chinese`、`English`。输出为 JSON，包含 `text`、`language`、`model`、`repository`、`revision`、`duration`、`processing_ms`、`inference_ms`、`request_id`、`no_speech`。全零静音直接返回空文本，不调用模型。自动语言判断存在已记录的模型误判，不能把标签等同于准确率。

## 8. TTS WAV 与 PCM

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/audio/speech" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"input":"你好，这是本地语音服务。","voice":"serena","language":"chinese","instructions":"语气温和自然。","response_format":"wav","stream":false}' \
  -o speech.wav
```

`input` 必须是 1–500 个 Unicode 字符的字符串，去空白后不能为空；`instructions` 最多 200 字符。语言支持 `auto`、`chinese`、`english` 及 `zh`／`en`／`Chinese`／`English` 别名。音色不区分大小写。未知字段和错误类型会拒绝。

非流式支持 `wav`（默认）或 `pcm`，均为 24kHz、单声道、PCM16 小端。响应头含 `X-Request-ID`、`X-Model`、`X-Processing-Ms`、`X-First-Audio-Ms`、`X-Audio-Duration`。PCM 没有 WAV 文件头。

## 9. 流式 PCM 与取消

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/audio/speech" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"input":"这段音频会逐步输出。","response_format":"pcm","stream":true}' \
  -D speech.headers -o speech.pcm
```

只有 `response_format=pcm` 支持 `stream=true`。首段音频生成成功后才发送 200；之后的错误会中止 HTTP 分块传输，不发送正常结束块。客户端必须检查 HTTP 流完整结束，并用 `X-Request-ID` 查询：

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/requests/REQUEST_ID" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"
```

仅当记录 `status=completed` 时视为完成；`running`、`cancelled` 和 `failed` 都不能当作完整音频。生成必须遇到 EOS，最多 1200 个 codec token、约 100 秒输出；达到上限返回 `generation_limit`，流式已输出的部分不能当作完整结果。

失败记录包含 `code` 和安全的 `message`；较旧 Helper 可能只有 `code`。App 在流读取结束或读取异常时有界查询当前请求状态，保留实际的长度上限、超时或推理失败原因。主动取消不触发额外诊断；状态不可用时保留原错误，未完整接收的音频不提供 WAV 导出。

取消时关闭 HTTP 连接；工作线程在模型的取消边界停止并重置解码器。某些单次 Metal 操作无法立即中断，这期间仍保持忙状态。客户端停止等待不会提前释放模型槽。

## 10. Swift 客户端

```swift
import Foundation
var request = URLRequest(url: URL(string: "http://127.0.0.1:8765/v1/audio/speech")!)
request.httpMethod = "POST"
request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
request.setValue("application/json", forHTTPHeaderField: "Content-Type")
request.httpBody = try JSONSerialization.data(withJSONObject: [
    "input": "原生 Swift 语音服务。", "response_format": "pcm", "stream": true
])
let (bytes, response) = try await URLSession.shared.bytes(for: request)
guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return }
for try await byte in bytes { /* 缓冲后按 PCM16 / 24000Hz / mono 播放 */ }
// 完整读完后，查询 X-Request-ID 对应记录并核对 completed。
```

客户端应处理 429 的退避重试。取消 Swift Task 后也应取消对应 URLSession 请求。

流读取抛错时，也可在非主动取消的前提下有界查询同一请求的失败记录以显示原因；即使该记录为 `completed`，也不能把已中断接收的 PCM 当作完整成品。

## 11. 错误与隐私

错误统一为 `{"error":{"code":"...","message":"..."}}`。常见状态：400 参数／音频无效、401 Token 错误、404 接口／记录不存在、413 上传或时长超限、422 生成未完整结束、429 忙、500 推理或资源错误、503 未就绪／更新准备、504 推理超时。401 包含 Bearer challenge；429 与更新等待包含 `Retry-After: 2`。

日志不记录输入文字、上传内容、Token 或内部错误路径。临时 `request-*` 目录在解码完成或失败后清理，启动时清理遗留的同类目录，跳过符号链接。`service.log` 每 5MiB 轮转，最多保留 4 个历史文件。

## 12. 首次安装

### 12.1 系统工具

将 App 复制到 `/Applications`；安装 FFmpeg：

```bash
brew install ffmpeg
/opt/homebrew/bin/ffmpeg -version
```

使用已构建 App 时无需 Python、pip、venv、Swift 编译器或完整 Xcode。源码构建需要 Apple Command Line Tools 与 Swift 6.3+；运行 `bash scripts/build_app.sh`，输出 `dist/CyberAudio.app`。

### 12.2 准备配置与 Token

```bash
export CYBERAUDIO_DATA="$HOME/Library/Application Support/CyberAudio"
export CYBERAUDIO_ASSETS='/Applications/CyberAudio.app/Contents/Helpers/CyberAudioAssets'
"$CYBERAUDIO_ASSETS" prepare --root "$CYBERAUDIO_DATA" \
  --ffmpeg /opt/homebrew/bin/ffmpeg --port 8765
```

已有配置和 Token 原样保留。`--port`、`--ffmpeg`、`--https-url` 只在首次创建配置时生效；后续修改 `config/service.json` 再重启。工具不打印 Token。

### 12.3 下载固定模型

```bash
"$CYBERAUDIO_ASSETS" download --root "$CYBERAUDIO_DATA"
# 网络需要代理时，仅下载命令可指定：
"$CYBERAUDIO_ASSETS" download --root "$CYBERAUDIO_DATA" --proxy http://127.0.0.1:7890
# 也可显式选择 ModelScope，继续复用已完成的进度：
"$CYBERAUDIO_ASSETS" download --root "$CYBERAUDIO_DATA" --source modelscope
```

只有此命令需要联网，可用无凭据的 HTTP 或 SOCKS5 代理；每个文件均校验大小和 SHA-256。默认下载源是 Hugging Face，`--source modelscope` 使用包含相同锁定文件的固定 ModelScope 快照，不改变模型或摘要要求，也不会在失败后自动换源。正确文件会复用，已有摘要错误的文件会保留并报错，不自动覆盖。下载按 Range 分段保存进度，取消或失败后可再次运行，复用已完成文件与持久分段；最终完整摘要通过后才发布模型文件。临时网络错误会有界重试，大分段反复超时或中断后可降为 1MiB，TLS／摘要等错误不会绕过验证。单次网络无响应 60 秒或响应超过 30 分钟会失败。需要重新开始异常分段时，按[首次准备说明](FIRST_RUN.md)使用显式重新下载选项。

### 12.4 离线校验与启动

```bash
"$CYBERAUDIO_ASSETS" verify --root "$CYBERAUDIO_DATA"
open -a CyberAudio
curl --fail-with-body http://127.0.0.1:8765/ready
```

首次预热可能较慢；`status=ready` 表示两个模型都已加载。报告在 `reports/native-assets-verified.json`。数据目录包含 `config`（配置、Token、进程锁）、`models`、`logs`、`reports`、`tmp`。

### 12.5 源码构建与测试

```bash
make check
make app
make dmg
```

脚本固定依赖并核对 SDK 适配补丁，打包匹配的 Metal 资源，对 Helper 和 App 签名并验证。SwiftPM native build system 的弃用提示和上游未处理 README 提示暂时保留。旧 Python 测试与发布编排是开发工具，不进入 App；可另行运行 `make legacy-test`。

### 12.6 远程 HTTPS

继续使用现有 Tailscale Serve 等可信反向代理转发本机端口；迁移不修改其配置。远程 URL 可写入 `https_url` 供 App 显示。网络访问权限和 API Token 都必须满足，不能直接把本机服务绑定到公网。

## 13. 从 Python 版本升级

1. 保留旧 App 与数据备份，退出旧 App，确认旧监听关闭。
2. 用新 App 内的 `CyberAudioAssets prepare`、`verify` 检查同一数据目录。原模型路径、FFmpeg 路径、端口、HTTPS URL 和 Token 都会复用。
3. 若缺少固定文件，运行 `download` 补齐；摘要错误或 tokenizer 不匹配会报错，先检查资产，不要随意删除现有文件。
4. 替换并启动 App，确认 `/ready`、模型身份、真实 STT 与 TTS。

旧 `runtime/.venv`、Python 基础解释器、旧清单和模型不会自动删除。新 App 只启动内置 Swift Helper。旧 `config/offline.sb` 保留；实际执行的策略来自 App 内的 `Resources/offline.sb`。

需要回退时退出新 App，再恢复旧 App 与兼容的 Python 环境；不要同时启动两个版本。旧安装方法见 [历史 Python 指南](GUIDE-PYTHON-LEGACY.md)。

## 14. 运维与更新

日常检查 `/health`、`/ready`、`logs/service.log`。重启引擎会停止旧 Helper、关闭监听，再建立新实例。退出时等待取消和模型清理；App 与 Helper 都有停止上限。强制退出仍由管道 EOF 与父 PID 监测回收 Helper。

Sparkle 签名、稳定通道与数据保留见 [自动更新指南](AUTOMATIC_UPDATES.md)。模型／SDK 升级必须重新固定版本、校验与测试。卸载／清理旧环境前自行确认备份和回退需要。

## 15. 验收证据

Swift Qwen 独立对比见 [模型验证报告](../Experiments/QwenSwift/VALIDATION-2026-11-05.md)。完整服务的协议、打包和生命周期证据见 [迁移报告](NATIVE_SWIFT.md)。旧 Python 数据见 [历史验收报告](VERIFICATION.md)，不能当作新服务结果。

未测能耗，未验证 ANE，不承诺去掉 Python 就更省内存。自动化不能替代麦克风、播放和主观音质试听。

## 16. 排障

| 现象 | 检查 |
|---|---|
| 无法启动／资源校验失败 | App 是否完整，`CyberAudioAssets verify` 是否通过，查看 `service.log` 错误代码 |
| 未就绪 | 模型是否齐全、FFmpeg 是否可执行、首次预热是否完成 |
| 端口占用／ownership failed | 同一数据目录是否已有 Helper，旧 App 是否仍在监听 |
| 取消后暂时仍忙 | 等待当前 Metal 操作与生成清理，不要并发重试 |
| 有 PCM 但请求失败 | 查记录与 HTTP 完成状态；截断音频不能当完整结果 |
| 下载失败 | 检查网络后重试；已验证的完整文件会复用 |
| 系统升级后退出 | 检查固定 SDK、Metal 与 sandbox-exec 兼容性，保留旧 App 回退 |

## 17. 参考

- [MLX Audio Swift](https://github.com/Neywa-Labs/mlx-audio-swift)
- [MLX Swift](https://github.com/ml-explore/mlx-swift)
- [SwiftNIO](https://github.com/apple/swift-nio)
- [适配补丁与许可证](../Native/THIRD_PARTY.md)

——文档结束——
