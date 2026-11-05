---
title: CyberAudio：Mac 原生 STT／TTS App 安装、API 使用与部署运维指南
---

# CyberAudio：Mac 原生 STT／TTS App 安装、API 使用与部署运维指南

> 历史 Python 版本的安装与验收记录。当前 Swift 版本请使用 [现行指南](GUIDE.md)，不要继续按本文安装运行环境。

本文对应 2026 年 11 月 3 日的 CyberAudio 0.1.0（build 3）。项目源码位于 `/Users/zhubby/Repository/CyberAudio`。实际运行设备是 Mac mini：Apple M4、16GB 统一内存、macOS 27.2、arm64。

第 5 节界面操作已更新为 2026 年 11 月 5 日的本地 Liquid Glass 改造版；此次源码与本地 App 尚未按设备交付流程安装或发布。设备、模型和性能数据仍以原部署验收记录为准。

CyberAudio 是一个原生 SwiftUI Mac App，同时提供语音识别 STT、语音合成 TTS 和统一 API。它直接管理本地 Python／MLX Audio 推理子进程。界面、录音和播放使用 SwiftUI、AppKit、AVFoundation；模型推理仍使用 Python／MLX，并非全 Swift 模型实现。

## 1. 先理解 App 的运行方式

| 操作或状态 | 实际行为 |
| --- | --- |
| 打开 CyberAudio | 启动一个后端子进程，加载并预热两个模型 |
| Dock 与菜单栏 | App 不显示 Dock 图标；始终通过菜单栏入口打开主窗口或退出 |
| 模型就绪 | 原生窗口和 API 均可使用 STT／TTS |
| 点击窗口左上角关闭 | App 继续在菜单栏运行，API 保持可用 |
| 菜单栏打开主窗口 | 恢复操作界面，复用已加载模型 |
| “停止引擎” | App 保持打开，API 停止，模型内存释放 |
| “启动引擎”或“重启” | 由当前 App 重新启动自己的后端 |
| Cmd-Q 或“退出 CyberAudio” | 先清理录音／播放，再停止后端和两个模型 |
| App 被强制结束 | 父进程管道关闭，后端自停；另有父 PID 检查和 8 秒强制退出兜底 |
| App 异常退出后 | 不由系统服务自动拉起；再次打开 App 恢复 |
| 用户登录后自动打开 | 可在 App 的服务设置中启用登录项，或通过 macOS 登录项设置添加 |
| 用户注销／未登录 | 不承诺服务可用；这是跟随用户会话的 Mac App |
| 手动睡眠 | 推理和网络服务会暂停；应保持 Mac 在需要服务时清醒 |

旧的 `system/local.qwen3-asr` 已停用并移出 LaunchDaemons 目录。CyberAudio 不依赖 LaunchDaemon 或 LaunchAgent 来常驻后端。

Mac 开机后未输入密码的场景仍需区分 FileVault 预启动解锁与普通登录。保留 FileVault 时，App 无法在磁盘解锁前运行；App 的登录项也只在用户登录后生效。

2026 年 11 月 3 日再次核查目标机：`fdesetup status` 为 `FileVault is Off`，Tailscale 已由系统级 `tailscaled` 运行。网络进程与 CyberAudio App 有各自的生命周期；即使机器已可 SSH 连接，未登录并打开 App 时仍不会启动语音引擎。本次 App 迁移没有更改磁盘加密、自动登录或 Tailscale 的运行方式。新机器是否保留 FileVault 应按自己的数据保护需求决定，不能把关闭它当成安装 CyberAudio 的必需步骤。

## 2. 交付内容与入口

| 项目 | 当前配置 |
| --- | --- |
| App | `/Applications/CyberAudio.app` |
| 主要交互 | Mac 原生 SwiftUI 窗口，菜单栏入口 |
| 本机 API | `http://127.0.0.1:8765` |
| Tailscale HTTPS | `https://mac-mini.civet-beta.ts.net:8443` |
| STT | `POST /v1/audio/transcriptions` |
| TTS | `POST /v1/audio/speech` |
| 模型列表 | `GET /v1/models` |
| 音色列表 | `GET /v1/audio/voices` |
| 流式完成状态 | `GET /v1/requests/{request_id}` |
| 进程存活 | `GET /health` |
| 模型就绪 | `GET /ready` |
| API Token | `~/Library/Application Support/CyberAudio/config/api-token`，权限 0600 |

主交互入口是 Mac App。HTTPS 根路径是简短的 API 说明页，不是原来的浏览器录音工作台。远程设备通过同一个 HTTPS 地址调用 STT／TTS。

历史文档中的 `perseus.civet-beta.ts.net` 已过期，本次实际验证使用 `mac-mini.civet-beta.ts.net`。现有 HTTPS 443 → 本机 18080 的其他服务和 Funnel 配置保留；8443 → 8765 不启用 Funnel。

交付检查另发现：原有 443 入口返回 502，其本机 18080 端口没有监听。保留转发配置不等于原服务可用，本次未恢复这个无关后端；CyberAudio 的 8443 已独立验证正常。

## 3. 模型、版本和设备要求

| 组件 | 固定选择 |
| --- | --- |
| STT 模型 | `mlx-community/Qwen3-ASR-1.7B-8bit` |
| STT revision | `a8379a2e2f9e313c9292cdf1af4055ab56d50d55` |
| TTS 模型 | `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` |
| TTS revision | `41d3337e8b7f2843a75841595fc14e4b9a7a4b96` |
| MLX Audio | `0.6.3` |
| MLX／MLX Metal | `0.32.4` |
| Python | 最终环境 `3.13.18`；初次性能与连续测试进程为 `3.13.17` |
| 音频解码 | Homebrew FFmpeg |
| TTS 音频 | PCM16LE，24kHz，单声道；可包装成 WAV |
| 模型资产体积 | ASR 约 2.47GB；TTS 约 3.08GB；合计约 5.55GB |

8-bit 表示量化，不改变 1.7B 的模型规模名称，但可能影响识别与合成质量。不能把量化版结果等同于未量化模型的所有论文或官方指标。

运行要求是 Apple Silicon、macOS 26 或以上。当前固定 MLX wheel 使用 macOS 26 平台标签，不能仅根据 SwiftUI 的最低系统能力宣称旧 macOS 可运行完整推理栈。本次验证设备为 macOS 27.2；没有逐一验证所有 M1／M2／M3 型号。

16GB 适合本机单用户短句工作流；24GB 或以上更适合同时运行其他大型模型。建议至少保留 15GB、最好 20GB 可用磁盘，覆盖模型、Python 依赖、下载分片与更新回退。安装阶段的临时空间与平时运行不同。

实测进程新启动的加载与预热、短句延迟和内存见第 15 节及独立验收报告。本次没有清空系统文件缓存，不把进程重启数据称为磁盘冷缓存基准。GPU 活跃内存、GPU 分配峰值与进程 RSS 是不同统计，不能相加当成系统真实总占用。

## 4. 运行架构与并发规则

```text
SwiftUI 窗口／菜单栏
  ├─ AVAudioRecorder → 临时 WAV → STT API
  ├─ TTS API → WAV／PCM → AVAudioPlayer／AVAudioEngine
  └─ 直接管理唯一 Python 子进程
       └─ FastAPI（127.0.0.1:8765）
            ├─ 固定 ASR 模型
            ├─ 固定 TTS CustomVoice 模型
            └─ 单个专用执行线程＋全局推理槽

Tailscale 客户端 → HTTPS 8443 → 同一个 FastAPI
```

两个模型常驻在同一个后端进程，但每次只运行一个 STT 或 TTS。加载、预热和推理都在同一个专用线程上执行，避免 MLX stream 跨线程问题。请求进入忙碌状态时返回 429，不无限排队，不为每个请求重复加载模型。

客户端断开连接后，TTS 会在模型下一次输出音频块时检查取消标记。STT 已开始的 GPU 调用可能继续到该次推理结束；推理槽在工作真正完成前一直保留。客户端超时不等于 GPU 已立即停止。

原始 PCM 不承载 JSON 错误。若流式响应已经开始，后续错误会中止传输，并将请求状态记为 `failed` 或 `cancelled`。客户端应检查请求记录，确认 `completed` 后才保存为完整成品。请求记录只保留最近 64 条元数据，不存储输入文本与完整转写。

## 5. 原生界面使用

### 5.1 打开和确认就绪

在访达的“应用程序”中打开 CyberAudio。顶部状态会从“加载模型”变为“就绪”。主工作台顶部切换“语音转文字／文字转语音”，右上角齿轮、菜单栏的“设置…”或 `⌘,` 打开独立设置窗口，展示模型、API 地址与本次加载／预热耗时。首次加载可能受系统文件缓存和 Metal 编译影响，明显慢于后续请求。

启动失败时，先打开设置查看错误，再点击“打开日志”。常见原因包括缺少运行环境、模型文件不完整、8765 被其他程序占用，或 App／资产目录未按部署步骤准备。修复后点击“重启”。

### 5.2 语音转文字

1. 选择“语音转文字”。
2. 识别语言选择自动、中文或 English。
3. 点击“开始录音”。首次使用时，允许 macOS 的麦克风授权。
4. 说完点击“停止并识别”，结果出现在编辑区。
5. 点击“复制”复制文字；可直接在结果区修正。

录音会在约 59 秒处暂停，仍需点击“停止并识别”提交，以给服务端 60 秒上限留出余量。快捷键是 Cmd-Shift-R。

录音波形来自麦克风的实际输入电平。切换工作模式会保留文本、设置和任务；录音、识别、生成或播放期间，底部浮动操作栏持续提供停止或取消入口。识别／生成任务也可用 `⌘.` 取消。取消识别会停止客户端等待，已开始的 GPU 推理仍可能继续到该次调用结束。界面跟随系统浅色／深色，并适配降低透明度、减少动态效果与增强对比度设置。

也可点击“导入音频…”，选择 WAV、MP3、M4A、WebM 等常见格式。服务端以 FFmpeg 统一转换为 16kHz 单声道后识别。单文件最大 20MiB、最长 60 秒；压缩文件较小也可能因时长被拒绝。

若麦克风未授权，在“系统设置 → 隐私与安全性 → 麦克风”允许 CyberAudio，然后重试。Mac mini 的实际输入设备应在“系统设置 → 声音 → 输入”选择。程序能申请权限不代表该台 Mac 已连接可用麦克风。

### 5.3 文字转语音

1. 选择“文字转语音”，输入最多 500 个字符。
2. 选择预设音色与自动／中文／English。
3. 选填最多 200 个字符的表达指令，例如“自然、清晰、亲切地朗读”。
4. 点击“生成并播放”。
5. 完整生成后，可重播或“保存 WAV…”。

勾选“流式播放”时，接收 PCM 的同时开始播放；取消勾选时等待完整 WAV，再播放。流式文本在请求开始前一次提交，并不支持一边追加文本一边生成。首个声音仍需等待文本预处理和模型产生首段音频。

“取消生成”停止接收和播放，并触发服务端取消。对于慢客户端，界面限制提前排入播放队列的音频量，服务端也限制流式缓冲。流式连接异常、生成超时或达到长度上限时，不将部分音频作为成功 WAV 保存。

本版没有声音克隆、参考音频上传、唤醒词、实时字幕、自动 VAD、长篇有声书队列或系统级语音输入法。TTS 的 `CustomVoice` 指固定预设音色；表达指令用于情绪／风格，效果需根据实际听感判断。

### 5.4 预设音色

| API 音色 ID | 使用建议 |
| --- | --- |
| `serena` | 默认中文音色，可先用于中性朗读 |
| `vivian` | 中文另一种女声音色 |
| `uncle_fu` | 中文男声音色 |
| `ryan` | 英文男声音色，英文示例默认使用 |
| `aiden` | 另一种英文男声音色 |
| `ono_anna` | 预设日文音色；本版接口语言选项仍限定中／英／自动 |
| `sohee` | 预设韩文音色；跨语言效果需试听 |
| `eric` | 预设中文地域音色，具体表达可试听 |
| `dylan` | 另一种预设中文地域音色，可试听比较 |

接口可查询当前完整列表。只验证了报告中列出的音色与文本，没有对 9 个音色的所有语言／指令组合进行主观评分。

## 6. API 的共通约定

`/v1/*` 均使用 `Authorization: Bearer <API Token>`。`/health` 与 `/ready` 不要求 Token；它们只暴露运行状态、固定模型及资源指标，不返回文本或 Token。令牌复用原有 STT 凭据，但它与 Mac 登录密码无关。

在 App 的设置窗口“API 连接”中点击“复制 Token”。也可由本机管理员从权限受限文件读取。不要把 Token 放在 URL、Git、Notion 或截图中。App 复制 Token 会写入系统剪贴板，应在可信本机环境中使用。

下列 shell 示例适用于 macOS 默认 zsh。先设置 URL，再在隐藏输入中粘贴 Token，按回车确认：

```bash
export CYBERAUDIO_BASE_URL='https://mac-mini.civet-beta.ts.net:8443'
read -rs 'CYBERAUDIO_TOKEN?API Token: '
echo
export CYBERAUDIO_TOKEN
```

在同一台 Mac 本机调用时，可将 URL 改成 `http://127.0.0.1:8765`。远程客户端需要加入 Tailscale 网络；API Token 不会替代 Tailscale 网络授权。

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/models" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"

curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/audio/voices" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"
```

这里提供的是 OpenAI 风格的兼容子集，不承诺支持 OpenAI 音频 API 的全部参数、模型名、字幕格式或所有 SDK 行为。

## 7. STT 请求与返回

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/audio/transcriptions" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN" \
  -F 'file=@./speech.wav' \
  -F 'model=qwen3-asr-1.7b' \
  -F 'language=auto' \
  -F 'response_format=json'
```

| 字段 | 取值与限制 |
| --- | --- |
| `file` | 必填音频文件，20MiB 以内、60 秒以内 |
| `model` | 可省略；`qwen3-asr-1.7b` 或已部署模型仓库名 |
| `language` | `auto`、`zh`、`en`；兼容 `Chinese`／`English` |
| `response_format` | 仅 `json` |

返回保留原 STT 的字段：

```json
{
  "text": "甚至出现交易几乎停滞的情况。",
  "language": "zh",
  "model": "qwen3-asr-1.7b",
  "repository": "mlx-community/Qwen3-ASR-1.7B-8bit",
  "revision": "a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
  "duration": 4.2039,
  "inference_ms": 420.0,
  "processing_ms": 503.0,
  "request_id": "example-request-id",
  "no_speech": false,
  "metal_peak_bytes": 6430357008,
  "metal_active_bytes": 5635025622,
  "process_peak_rss_bytes": 3751133184
}
```

以上数值为结构示例，具体数值以响应和验收报告为准。`inference_ms` 是识别耗时；`processing_ms` 还包含服务端音频转换，不包括客户端上传和公网／Tailscale 往返。

`no_speech=true` 只用于检测到精确数字静音的情况，不是通用 VAD。环境噪声和实际无语音片段仍可能进入模型；不能依赖该字段作为可靠的“是否有人说话”判断。

## 8. TTS：WAV 与非流式 PCM

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/audio/speech" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-tts-1.7b","input":"你好，欢迎使用 CyberAudio。","voice":"serena","language":"chinese","instructions":"自然、清晰地朗读。","response_format":"wav","stream":false}' \
  --output speech.wav

afplay speech.wav
```

| 字段 | 行为 |
| --- | --- |
| `model` | 默认 `qwen3-tts-1.7b`，仅允许当前固定模型及其仓库名 |
| `input` | 必填非空文本，最多 500 个 Unicode 字符 |
| `voice` | 默认 `serena`，9 个固定音色之一，不区分大小写 |
| `language` | 默认 `auto`；支持 `chinese`／`english`，也接受 `zh`／`en` |
| `instructions` | 默认空字符串，最多 200 字符 |
| `response_format` | `wav` 或 `pcm`，默认 `wav` |
| `stream` | 严格布尔值，默认 `false`；为 `true` 时格式必须是 `pcm` |

目前不提供 MP3／Opus 输出，不接受 `speed`、参考音频或任意模型路径。未知字段会返回错误，避免参数看似生效、实际被忽略。

成功响应是二进制音频，包含以下头部：

```text
X-Request-ID: <请求编号>
X-Model: qwen3-tts-1.7b
X-Audio-Sample-Rate: 24000
X-Audio-Channels: 1
X-Audio-Format: pcm_s16le
X-Processing-Ms: <服务端总处理时间>
X-First-Audio-Ms: <模型产生首段音频的时间>
X-Audio-Duration: <音频秒数>
```

非流式 WAV 要等完整生成后返回，因此 `X-First-Audio-Ms` 很低并不等于客户端已经开始播放。WAV 的采样格式由 WAV 头描述；`X-Audio-Format` 描述其中的 PCM 编码。

单次生成最多使用 1200 个音频 token，推理时间预算 120 秒。到达上限会报错，不把截断内容包装成成功结果。长文本建议按语义主动分成更短句子，调用方串行处理；不要仅按 500 字符上限估算必然成功的时长。

## 9. TTS：流式 PCM 与完成校验

```bash
curl --fail-with-body --no-buffer \
  "$CYBERAUDIO_BASE_URL/v1/audio/speech" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"input":"Hello, welcome to CyberAudio.","voice":"ryan","language":"english","response_format":"pcm","stream":true}' \
  --dump-header response-headers.txt \
  --output speech.pcm
```

该文件是原始 PCM，没有 WAV 头。应以 signed 16-bit little-endian、24000Hz、单声道解释。播放或转换示例：

```bash
ffplay -f s16le -ar 24000 -ac 1 speech.pcm

ffmpeg -f s16le -ar 24000 -ac 1 -i speech.pcm speech.wav
```

浏览器或第三方播放器通常不能把原始 PCM 当作完整 WAV 直接打开。要边接收边播放，客户端必须实现 PCM 缓冲播放；CyberAudio 的原生界面使用 AVAudioEngine 实现这一点。

从响应头取出 `X-Request-ID` 后，检查请求状态：

```bash
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/requests/REQUEST_ID" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"
```

```json
{
  "request_id": "REQUEST_ID",
  "kind": "tts",
  "status": "completed",
  "duration": 6.08,
  "first_audio_ms": 250.0,
  "processing_ms": 3820.0
}
```

`running` 表示尚未结束，`completed` 才代表成功；`failed`／`cancelled` 表示不能作为完整成品。状态记录会因进程重启或超出最近 64 条而过期，404 不表示生成成功。需要长期保存任务状态的业务，应由调用方实现持久任务记录。

## 10. Python 客户端示例

下例使用标准库生成 WAV，无需安装 MLX。客户端只需安装 `httpx`。Token 从环境变量读取，适合放入自己的密钥配置；不要把真实 Token 写进源码。

```python
import io
import os
import wave
from pathlib import Path
import httpx

base = os.environ.get("CYBERAUDIO_BASE_URL", "http://127.0.0.1:8765")
token = os.environ["CYBERAUDIO_TOKEN"]
headers = {"Authorization": f"Bearer {token}"}

with httpx.Client(base_url=base, headers=headers, timeout=150) as client:
    with Path("speech.wav").open("rb") as source:
        result = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("speech.wav", source, "audio/wav")},
            data={"model": "qwen3-asr-1.7b", "language": "auto"},
        )
    result.raise_for_status()
    print(result.json()["text"])

    pcm = bytearray()
    with client.stream("POST", "/v1/audio/speech", json={
        "model": "qwen3-tts-1.7b",
        "input": "你好，统一语音接口已经准备好了。",
        "voice": "serena", "language": "chinese",
        "response_format": "pcm", "stream": True,
    }) as response:
        response.raise_for_status()
        request_id = response.headers["x-request-id"]
        for chunk in response.iter_bytes():
            pcm.extend(chunk)
            # 在此把 chunk 送入自己的实时 PCM 播放缓冲。
    completed = client.get(f"/v1/requests/{request_id}")
    completed.raise_for_status()
    if completed.json()["status"] != "completed":
        raise RuntimeError("生成未完整结束，放弃保存成品")
    if not pcm or len(pcm) % 2:
        raise RuntimeError("PCM 数据不完整")
    with wave.open("generated.wav", "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
```

生产调用方应为 429 设置有限重试和退避，遵守 `Retry-After: 2`。对长 TTS 不要设置三秒级客户端读超时；识别与合成耗时不同。网络中断后，应先确认前一个请求已结束，再重试，避免重复生成。

## 11. 错误、限制与隐私

| HTTP 状态 | 常见含义 |
| --- | --- |
| 400 | JSON／multipart 损坏、语言／音色／格式不支持、文本或参数超限、音频无效 |
| 401 | Token 缺失或错误 |
| 404 | 请求状态不存在／过期，或接口路径错误 |
| 408 | 上传未在时间预算内完成 |
| 413 | 音频文件／请求体过大，或音频超过 60 秒 |
| 422 | TTS 达到生成 token 上限，建议缩短文本 |
| 429 | STT 或 TTS 正在处理，共用推理槽 |
| 499 | 客户端取消；连接已经断开时通常无法再收到此 JSON |
| 500 | 推理失败或模型没有有效输出 |
| 503 | 模型正在加载或加载失败 |
| 504 | 处理超过时间预算 |

```json
{"error":{"code":"busy","message":"STT／TTS 正在处理，请稍后重试。"}}
```

身份验证在 multipart／JSON 解析前进行，支持带长度与分块传输的请求体上限。服务没有跨域开放策略、任意模型下载、模型删除或远程配置写接口。

上传文件进入权限受限的临时目录，处理结束后删除；服务启动时清理自己遗留的请求目录。原生麦克风临时 WAV 在正常提交／退出时删除，下次启动也清理其专属临时录音目录。异常断电／强制结束可能使临时文件暂时留到下一次清理，不能把它描述成安全擦除。

默认日志记录请求编号、类别、耗时、音频时长和错误类型，不保存完整输入文本、完整转写或录音。GUI 当前结果存在进程内存中，不提供历史数据库。验收脚本会有意保存公开测试文本、参考音频结果和合成样本到 `reports/`；这些是测试产物，与日常请求的默认保留策略不同。

后端以普通用户运行。运行期使用本地资产、离线环境变量及 `sandbox-exec` 的禁止外联策略。该策略经当前 macOS 实测；`sandbox-exec` 属于旧系统工具，未来系统升级若移除或改变它，需要重新验证，不能静默退回联网加载。

## 12. 从空白机器安装

这是一套面向开发者／自用 Mac 的部署方式。`.app` 未包含 Python 和模型，采用本机 ad-hoc 签名，未做 Developer ID 签名、公证、DMG 安装器、自动更新或 App Store 打包。

### 12.1 系统与工具

完成 macOS 首次设置，创建日常用户并登录。安装 Tailscale 并加入自己的网络。将睡眠策略设为符合服务时段要求，保持 Mac 在需要 API 时清醒；不必为了此 App 关闭 FileVault、SIP 或 Gatekeeper。

```bash
xcode-select --install
xcode-select -p
swift --version
uname -m
sw_vers
```

安装 Homebrew 后准备依赖：

```bash
brew install python@3.13 ffmpeg
/opt/homebrew/opt/python@3.13/bin/python3.13 --version
/opt/homebrew/bin/ffmpeg -version
```

不要依赖 SSH 环境中模糊的 `python3`，它可能仍指向 Apple 的旧 Python。目标机实际出现过这种路径差异，因此部署命令显式使用 Homebrew Python 3.13。虚拟环境仍依赖基础解释器，不能卸载它引用的 `python@3.13`。本次验收期间曾发现基础解释器被移除，已恢复为 3.13.18 并重新检查依赖；对应基准环境的区别见验收报告。

### 12.2 源码与独立 Python 环境

将 CyberAudio 源码放到自己的目录。以下以当前仓库路径为例：

```bash
cd /Users/zhubby/Repository/CyberAudio
export CYBERAUDIO_DATA="$HOME/Library/Application Support/CyberAudio"
mkdir -p "$CYBERAUDIO_DATA/runtime"
/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv \
  "$CYBERAUDIO_DATA/runtime/.venv"
"$CYBERAUDIO_DATA/runtime/.venv/bin/python" -m pip install \
  -r requirements.lock.txt
```

如果下载必须走已有的本机代理，只为安装／下载命令传入代理。例如目标机已有代理端口 7890；其他机器请换成自己的配置：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 \
HTTP_PROXY=http://127.0.0.1:7890 \
  "$CYBERAUDIO_DATA/runtime/.venv/bin/python" -m pip install \
  -r requirements.lock.txt
```

安装完成保存版本并验证 GPU：

```bash
"$CYBERAUDIO_DATA/runtime/.venv/bin/python" -m pip freeze \
  > "$CYBERAUDIO_DATA/runtime/requirements.installed.txt"

"$CYBERAUDIO_DATA/runtime/.venv/bin/python" -c \
  'import mlx.core as mx; print(mx.metal.is_available()); x=mx.ones((256,256)); mx.eval(x@x); print(mx.get_peak_memory())'
```

必须看到 Metal 可用。失败时先检查芯片架构、系统版本和 wheel 版本，不要把 CPU 回退当成通过了本方案。

### 12.3 下载两个固定 revision

```bash
/opt/homebrew/opt/python@3.13/bin/python3.13 scripts/prepare_models.py \
  --model asr \
  --root "$CYBERAUDIO_DATA/models/Qwen3-ASR-1.7B-8bit" \
  --proxy http://127.0.0.1:7890

/opt/homebrew/opt/python@3.13/bin/python3.13 scripts/prepare_models.py \
  --model tts \
  --root "$CYBERAUDIO_DATA/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit" \
  --proxy http://127.0.0.1:7890
```

不需要代理时，省略 `--proxy`。脚本固定仓库与 revision，分片续传大文件，验证 LFS 权重 SHA256，并为所有文件保存校验清单。看到 `All model files verified` 才代表该模型下载完成；存在若干 `.parts` 文件不代表模型可用。脚本不会输出重定向下载地址到日志。

### 12.4 生成配置与 Token

```bash
/opt/homebrew/opt/python@3.13/bin/python3.13 scripts/prepare_runtime.py \
  --root "$CYBERAUDIO_DATA" \
  --https-url 'https://YOUR-MAC.YOUR-TAILNET.ts.net:8443'

/opt/homebrew/opt/python@3.13/bin/python3.13 scripts/verify_assets.py \
  --root "$CYBERAUDIO_DATA"
```

配置生成器不会覆盖已有 Token 和 `service.json`。需要调整端口、FFmpeg 或 HTTPS 显示地址时，在 App 停止后编辑配置，再重启引擎。配置中的 HTTPS 地址是展示用途；实际转发由 Tailscale Serve 决定。

目录结构如下：

```text
~/Library/Application Support/CyberAudio/
  config/
    service.json           模型目录、FFmpeg、端口和 HTTPS 地址
    api-token              独立 API 凭据，0600
    offline.sb             运行期禁止外联
    backend.lock           后端单实例文件锁
  runtime/.venv/           独立 Python 环境
  runtime/requirements.installed.txt
  models/
    Qwen3-ASR-1.7B-8bit/
    Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/
  logs/service.log         轮换日志
  tmp/request-*/           请求临时音频，正常处理后删除
  reports/                 验收产物
```

### 12.5 构建和安装 App

```bash
bash scripts/build_app.sh
codesign --verify --deep --strict dist/CyberAudio.app
cp -R dist/CyberAudio.app /Applications/
```

更新已有 App 时先退出它，并备份旧 App 后再替换，不要覆盖正在运行的可执行文件。当前源码可使用 Command Line Tools 构建，不要求安装完整 Xcode。

从访达打开 App，等到就绪。当前工程用 `ObservableObject` 管理界面状态，以避开本次命令行 SDK 缺少 SwiftUI 新 `@State` 宏插件的问题；构建已实际通过。

### 12.6 配置远程 HTTPS

先确认本机就绪，再检查已有 Serve 配置：

```bash
curl --fail http://127.0.0.1:8765/ready
tailscale serve status --json
tailscale serve --bg --https=8443 http://127.0.0.1:8765
tailscale serve status --json
```

在另一台 Tailscale 设备访问对应域名。首次启用 HTTPS／Serve 可能需要按 Tailscale 管理控制台的提示启用能力。只新增需要的 8443 转发，不运行 `tailscale serve reset`，也不把 8765 改为 `0.0.0.0`。

本次目标机已经有正确转发，因此迁移中保留原配置。没有新增 8444，也没有为 TTS 单独开放另一个端口。

## 13. 从旧 Qwen3-ASR 服务迁移

本次采用“新 App 在临时 8766 端口验证 → 停用旧服务 → 切回 8765”的流程。旧服务在新模型和 App 准备期间继续工作。

1. 将原 ASR 模型用 APFS copy-on-write 克隆到新资产目录，保留原文件供回退。
2. 从原已验证环境克隆独立 venv，修正入口路径，再核对全部依赖版本。运行环境没有继续引用旧 venv 的 site-packages。
3. 复用旧 Token，保存在新目录；Mac 登录密码不进入配置或文档。
4. 下载并校验固定 TTS 模型。
5. 新 App 用 8766 完成真实模型、API 和音频格式验证。
6. 退出新 App，停用并卸载旧 LaunchDaemon，归档旧 plist。
7. 新配置切到 8765，替换 App 后重新打开。
8. 核验 Tailscale 8443、生命周期、离线边界和稳定性。

同机 venv 迁移脚本只适用于同架构、兼容 Python 的已验证环境，不适合复制到另一种系统／架构。命令示例：

```bash
/opt/homebrew/opt/python@3.13/bin/python3.13 scripts/clone_runtime.py \
  --source /Users/zhubby/Services/qwen3-asr/current/.venv \
  --destination "$CYBERAUDIO_DATA/runtime/.venv"
```

目标目录存在时默认拒绝覆盖。`--repair-existing` 只用于明确修复入口脚本。路径含空格时，console entrypoint 使用 shell trampoline；日常维护统一用 `python -m pip`／`python -m pytest` 更稳妥。

旧启动项归档位置：

```text
/Library/Application Support/CyberAudio/Migration/local.qwen3-asr.plist
```

停用旧启动项需要管理员权限。以下是本次迁移操作的等价步骤，**只应针对确实存在的旧 Qwen3-ASR 服务执行**：

```bash
sudo launchctl disable system/local.qwen3-asr
sudo launchctl bootout system/local.qwen3-asr
sudo mkdir -p '/Library/Application Support/CyberAudio/Migration'
sudo mv /Library/LaunchDaemons/local.qwen3-asr.plist \
  '/Library/Application Support/CyberAudio/Migration/local.qwen3-asr.plist'
```

本次已执行。当前旧 plist 不在 LaunchDaemons 目录，旧 label 不再加载。原模型、环境与数据仍保留在 `~/Services/qwen3-asr`，不与新 App 同时抢占 8765。

## 14. 登录启动、运维、升级与回退

### 14.1 登录时打开

在独立设置窗口的“登录启动”中打开“登录时打开 CyberAudio”。App 使用 `SMAppService.mainApp`；macOS 若要求批准，在“系统设置 → 通用 → 登录项与扩展”完成。

由于当前是 ad-hoc 自用构建，登录项是否可注册受 macOS 策略、签名和安装路径影响。若显示注册失败，可在系统登录项界面手动添加 `/Applications/CyberAudio.app`。需要重新登录才能验证自动打开；本次没有用真实注销／重启测试代替用户当前会话。

注册登录项的意义是“登录后打开这个 App”，不是创建独立后台服务。要取消自动打开，关闭 App 开关或移除系统登录项。即使设置了自动打开，手动退出 App 后仍应停止 API，不由守护进程把它拉起。

### 14.2 日常检查

```bash
curl --fail http://127.0.0.1:8765/health
curl --fail http://127.0.0.1:8765/ready
tail -n 80 "$HOME/Library/Application Support/CyberAudio/logs/service.log"
tailscale serve status --json
```

`/health` 只证明进程能够回应；`/ready` 为 200 且状态为 `ready` 才表示两个模型加载完成。`busy=true` 是正在工作，不是服务损坏。App 未打开或引擎已停止时，连接失败／HTTPS 502 是生命周期的预期结果。

日志单文件上限 5MiB，保留 4 份旧日志。不要把日常请求正文加入调试日志。需要报告故障时，优先提供请求编号、发生时间、HTTP 状态和错误 code。

### 14.3 停止、重启和退出

优先使用 App 的“停止引擎”“重启”与“退出 CyberAudio”。关闭主窗口继续运行；Cmd-Q 才退出。出现异常时，不应通过 `launchctl kickstart` 启动旧服务来“修复”新 App，否则会重新引入端口冲突。

远程管理中如需终止 App，先用精确进程路径确认唯一 PID，再向该 PID 发送 TERM。当前 App 将 TERM 转为正常清理退出；这是本次远程生命周期验收的方式。不要用宽泛的 `pkill python`。

```bash
pgrep -fl '^/Applications/CyberAudio.app/Contents/MacOS/CyberAudio$'
kill -TERM APP_PID
```

强制结束仅用于确认正常退出失败或模拟故障。后端有父管道和 PID 检查，App 崩溃时应自行关闭；在下一次启动会清理遗留请求临时目录。

### 14.4 升级

从 0.2.0（4）起支持 Sparkle 应用内更新；从更早版本升级需先手动安装一次。默认每天检查并下载稳定版，在设置的“软件更新”中关闭或主动检查。运行中确认“安装并重启”，或主动退出时安装；重启前保存内存中的文字和音频。更新等待当前音频／API 推理结束，等待时可取消；新推理请求返回 `503 updating`。完整流程、数据保留和验证记录见 [自动更新与发布指南](AUTOMATIC_UPDATES.md)。以下手动流程仍适用于首次接入和回退。

1. 保存当前 App、源码版本／文件校验、配置和依赖锁。
2. 在新目录准备模型／依赖；不要直接在正在运行的 venv 执行无版本约束升级。
3. 构建新 App，先使用隔离数据目录及其他端口验证。
4. 退出旧 App，再替换 `/Applications/CyberAudio.app`。
5. 启动新 App，测试两个接口、流式完成状态和退出行为。
6. 通过后保留上一版直到完成使用验收。

App 支持 `--data-dir /absolute/path` 或 `CYBERAUDIO_HOME` 指定隔离资产目录，适合开发测试。隔离目录中的配置必须使用不同端口，Token、临时目录和模型记录也应独立。

### 14.5 回退到旧 STT

先完全退出 CyberAudio，确认 8765 已关闭，再恢复原 LaunchDaemon：

```bash
sudo cp '/Library/Application Support/CyberAudio/Migration/local.qwen3-asr.plist' \
  /Library/LaunchDaemons/local.qwen3-asr.plist
sudo chown root:wheel /Library/LaunchDaemons/local.qwen3-asr.plist
sudo chmod 644 /Library/LaunchDaemons/local.qwen3-asr.plist
sudo launchctl enable system/local.qwen3-asr
sudo launchctl bootstrap system /Library/LaunchDaemons/local.qwen3-asr.plist
curl --fail http://127.0.0.1:8765/ready
```

这会恢复原 STT，TTS 将不可用。不要同时启用旧 LaunchDaemon 与新 App。本次保留并检查了回退资产，但未实际执行旧服务回退；基础 Python 也发生过修复，恢复旧服务时需要重新验证它的解释器路径和依赖。只在确认新 App 需要撤销时执行。

### 14.6 备份与卸载

优先备份源码、配置、Token 和依赖锁，Token 的备份也必须限制访问。模型可按固定 revision 重新下载，是否完整备份取决于网络和恢复时长。备份前停止引擎，避免把正在写入的临时文件当成需要保存的数据。

卸载时退出 App、取消登录项，先将 App 和专属数据目录移入可恢复的备份／废纸篓。确认其他客户端不再依赖此服务后，才移除专属 8443 转发：

```bash
tailscale serve --https=8443 off
```

保留无关的 443 服务。不要删除整个 Tailscale 配置，也不要因为卸载 CyberAudio 删除仍用于回退的旧 ASR 资产。

## 15. 本次验收如何解读

固定资产已经过校验：ASR 11 个文件、TTS 13 个文件，revision 与第 3 节一致。运行日志确认使用 Metal，两个模型在同一后端进程中就绪。

最终 build 2／Python 3.13.18 预热后的 10 秒中英文短句测试为 20 个串行请求，本机 API 端到端 P50 为约 0.659 秒、P95 约 0.665 秒。该端到端包含本机 HTTP 与音频转换，不包括真实麦克风采集、句尾等待和远程网络；样本集很小，不能外推为所有录音的 P95。

同轮正式验收中，中文 WAV 5.68 秒音频生成耗时约 3.378 秒；英文 WAV 4.16 秒音频约 2.456 秒；混合文本流式 PCM 5.76 秒音频的本机首段约 247 毫秒、完整接收约 3.263 秒。RTF 定义为生成墙钟时间／输出音频时长，分别约 0.595、0.590、0.567；小于 1 表示该次样本生成快于播放。TTS 使用采样，重复请求的音频长度与耗时会变化。

此前 build 1／Python 3.13.17 完成了 30 分钟有间隔混合负载：80 个周期、160 个 STT／TTS 成功请求，周期错误为 0，采样的 Metal 活跃内存变化约 64KiB。build 2 的后端文件与其相同，原生 App 修改了登录项状态控制。最终 build 2 另在真实 TTS 生成中完成正常退出和强制结束测试，监听约 111ms 关闭、模型子进程结束，重新打开后恢复就绪。完整负载和统计边界见验收报告。

ASR 在公开中文真实语音样本及人工预设的中／英合成参考文本上完成验证。中英混合示例把 `and` 规范化成了“和”，并有标点／空格变化，不能称为严格逐字完全一致。数字与人名样本通过不代表真实用户的口音、距离或噪声条件已验收。

TTS 回灌到 ASR 的中文／英文内容可辨识，专名存在 `Cyber Audio` 空格和 `Mac Mini` 大小写规范化。回灌是内容一致性的辅助检查，不代替真人试听、自然度／音色相似度评分，也不能排除两个模型共有的偏差。

本次后端针对性测试为 24 项，使用真实 FastAPI、FFmpeg 和可控推理替身，覆盖认证、限制、兼容响应、全局互斥、取消、流式完整性与临时文件清理。它们与真实模型测试是不同层级的证据。

原生界面已在管理 Mac 构建，并使用受控测试接口检查布局、侧栏导航、文件对话框导入、结果显示与复制、WAV 播放及 PCM 播放。保存对话框能够打开，但写文件流程尚未完成，不能称为保存成功。目标机 App 的真实模型和进程生命周期通过远程验证。目标机未授予界面自动化的辅助功能／屏幕录制权限，因此不能把远程 API 验证描述成在目标机逐按钮完成了全部 GUI 验收。

还需由你在实际设备上确认：麦克风采音、扬声器听感、目标机文件导入与保存流程、9 个音色的使用偏好、手机客户端，以及启用登录项后的真实重新登录／重启。稳定性、HTTPS 与完整运行指标以随项目交付的 `docs/VERIFICATION.md` 最终报告为准。

## 16. 常见排障

| 现象 | 检查与处理 |
| --- | --- |
| 打开 App 后一直加载 | 看 `/ready` 和日志；检查模型下载是否完成、可用内存、Metal 初始化 |
| “端口被其他服务占用” | 确认旧 ASR LaunchDaemon 已停用；检查是否手动运行了第二个后端 |
| 退出 App 后 API 仍响应 | 核对响应中的 `application`／`instance`，确认不是旧服务或其他进程 |
| HTTPS 502 | 先检查本机 API；App 关闭时后端本来就不存在 |
| HTTPS 域名打不开 | 检查两端 Tailscale 在线、域名变化、Serve 配置与网络策略 |
| 401 | 重新复制当前 Token，检查是否包含换行、是否误用了 Mac 登录密码 |
| 429 | 一个 STT／TTS 已在工作；等待后重试，不要重复启动模型 |
| 音频能播放却不能识别 | 检查时长、编码与 FFmpeg；先转成 16kHz 单声道 WAV 缩小问题范围 |
| 录音权限失败 | macOS 麦克风授权、输入设备、App 签名／路径是否变化 |
| TTS 发音／音色不合适 | 调整音色、语言、标点和简短表达指令，分句后比较实际听感 |
| 流式 PCM 打不开 | 按 PCM16LE／24kHz／单声道解码，或转换为 WAV |
| 生成开头正常但文件不完整 | 检查网络异常和请求完成状态，缩短文本，拒绝保存 failed／cancelled 成品 |
| 重启后不自动出现 | 确认已解锁 FileVault、用户已登录、登录项已批准，不能用服务状态代替启动验收 |
| 系统升级后后端立即退出 | 检查 Python／MLX wheel、`sandbox-exec` 与系统版本兼容，使用锁定环境回退 |

## 17. 官方参考与后续方向

- [Qwen3-ASR 官方项目](https://github.com/QwenLM/Qwen3-ASR)
- [Qwen3-TTS 官方项目](https://github.com/QwenLM/Qwen3-TTS)
- [MLX Audio](https://github.com/Blaizzy/mlx-audio)
- [固定 ASR 模型仓库](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit/tree/a8379a2e2f9e313c9292cdf1af4055ab56d50d55)
- [固定 TTS 模型仓库](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/tree/41d3337e8b7f2843a75841595fc14e4b9a7a4b96)
- [Apple SwiftUI](https://developer.apple.com/documentation/swiftui)
- [Apple SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)
- [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve)
- [此前的 macOS 首次开机到服务器配置教程](https://app.notion.com/p/macOS-Mac-mini-40c1d976194581359f1bd4066be0c27b)

下一阶段可分别评估 Developer ID 签名与公证、首开资源安装器、菜单栏快捷录音、自动 VAD、长文任务队列、更多语言、声音克隆、CyberOS／CM5 客户端接入。它们需要独立的需求与验收，不应从本版的统一接口和原生界面推断已经支持。

文档结束标记：CYBERAUDIO-GUIDE-END-20261103。
