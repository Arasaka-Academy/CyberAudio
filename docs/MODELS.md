# 模型管理

在“设置 → 模型”分别选择语音识别与语音合成模型。每个型号按需下载，下载结束后点击“设为使用”；后台下载不会自动切换当前模型。旧配置继续使用两款 1.7B 8-bit，并复用原模型目录、配置和 Token。

| 用途 | 规格 | API 型号 ID |
|---|---|---|
| Qwen3-ASR | 0.6B 4-bit | `qwen3-asr-0.6b-4bit` |
| Qwen3-ASR | 0.6B 8-bit | `qwen3-asr-0.6b-8bit` |
| Qwen3-ASR | 1.7B 4-bit | `qwen3-asr-1.7b-4bit` |
| Qwen3-ASR | 1.7B 8-bit，默认 | `qwen3-asr-1.7b` |
| Qwen3-TTS CustomVoice | 0.6B 4-bit | `qwen3-tts-0.6b-4bit` |
| Qwen3-TTS CustomVoice | 0.6B 8-bit | `qwen3-tts-0.6b-8bit` |
| Qwen3-TTS CustomVoice | 1.7B 4-bit | `qwen3-tts-1.7b-4bit` |
| Qwen3-TTS CustomVoice | 1.7B 8-bit，默认 | `qwen3-tts-1.7b` |

所有 TTS 型号保留九种音色。0.6B TTS 不支持表达指令，工作台根据模型能力禁用该控件，合成请求清空旧指令；API 对非空 `instructions` 返回 `400 unsupported_instructions`。

## 下载与切换

模型页显示规格、下载大小、安装及使用状态。支持下载、继续、重试、取消、排队和删除未使用模型；下载百分比、字节数、当前文件与校验阶段由 App 持有，关闭设置窗口后继续下载，重新打开仍可查看进度。首次准备与模型页共用同一个准备工具。

可选择 Hugging Face、ModelScope 和可选 HTTP／SOCKS5 代理。每个型号只提供已核验的源；当前新增六款型号使用 ModelScope，默认两款支持两个源。仓库 revision、每个文件的大小和 SHA-256 固定在 [统一模型目录](../Native/Sources/CyberAudioCore/ModelCatalog.lock.json)，不会跟随上游最新版。

下载器保留内容寻址的暂存文件，只发布完整且摘要正确的文件；可复用其他已安装型号中摘要相同的文件。当前未完整保存的 HTTP 分段在取消后可能重传。最终文件有冲突或校验失败时保留原文件并提示检查。

删除未使用模型会清理其已登记文件与独有下载暂存，保留其他型号共用的下载暂存和用户额外文件。

“设为使用”先等待录音、听写、播放及已接收的外部 API 响应结束，再停止引擎、保存候选选择并加载。候选引擎在配置确认前继续拒绝新音频请求。加载失败会恢复原始配置并重新加载原模型；引擎原本停止时只保存选择。切换、迁移及应用更新共用维护准入，普通下载不重启引擎。

## 存储位置

默认统一目录为数据目录下的 `models/`。模型页显示目录、已登记文件及下载暂存的占用、剩余空间，可在 Finder 中打开，或通过“更改位置…”迁移到另一目录或外置盘。

迁移时暂停当前下载，复制已登记模型、生成的 tokenizer 和可恢复下载，逐文件检查大小与 SHA-256，随后切换配置；引擎确认可用后才清理原位置的已登记文件，并继续暂停的下载。目录中的用户额外文件保留。目标冲突文件不会被覆盖。

恢复记录保存在数据目录 `config/model-change.json`。取消、空间不足、权限错误、断盘或意外退出时保留原配置及可恢复记录；再次准备时恢复未确认操作。已经写入提交标记的操作继续清理，避免恢复到已经部分清理的旧目录。磁盘断开时不重建同名 `/Volumes/...` 路径。

旧配置的 `asr_dir`、`tts_dir` 会继续生效。新字段为 `asr_model_id`、`tts_model_id`、`models_dir` 与 `model_directories`，由模型管理维护。

## Assets CLI 与 API

```bash
CYBERAUDIO_ASSETS='/Applications/CyberAudio.app/Contents/Helpers/CyberAudioAssets'
"$CYBERAUDIO_ASSETS" catalog --json true
"$CYBERAUDIO_ASSETS" models --json true
"$CYBERAUDIO_ASSETS" status --model qwen3-asr-0.6b-4bit --json true
"$CYBERAUDIO_ASSETS" download --model qwen3-asr-0.6b-4bit --source modelscope --json true
"$CYBERAUDIO_ASSETS" verify --model qwen3-asr-0.6b-4bit --json true
```

使用 `--root /绝对路径` 可操作独立数据目录。下载及校验进度事件包含 `model_id`。无 `--model` 的 `status`、`download` 与 `verify` 继续面向当前选中的一对型号。

`/v1/models` 只返回当前启用的真实型号、固定 revision、量化规格和能力；`/v1/audio/voices` 返回当前 TTS 型号及 `supports_instructions`。省略请求的 `model` 时使用当前选择；显式指定其他型号返回 `400 invalid_model`。WAV／PCM 的 `X-Model` 响应头同样使用真实 TTS 型号。两个默认 API ID 保持兼容。

本轮验收与复现命令见 [模型管理验收](evidence/model-management-2026-11-13/README.md)。
