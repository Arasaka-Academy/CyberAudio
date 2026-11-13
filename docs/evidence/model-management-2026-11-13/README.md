# 模型管理验收 · 2026-11-13

本轮编译、测试、模型下载、真实推理与原生渲染均通过 Tailscale 在 `codehubmac-mini` 执行。本机仅编辑源码、读取文件和查看远端生成的图片。

远端隔离工作目录：`/Users/codehub/Workspaces/CyberAudio-model-management`。设备为 Apple M4、16 GB，macOS 27.2、Swift 6.4.2。开发分支为 `codex/model-management`，起点为 `5a2c21a22901641b927164c720f3613de8823fb7`。本轮交付包括功能开发与验收。

## 自动检查与构建

| 检查 | 结果 | 证据 |
|---|---|---|
| `rtk proxy make check` | 通过；39 项原生契约测试，0 失败、0 跳过；54 项 Python 工具测试 | [完整日志](make-check.log) |
| 补充当前 ASR 请求契约 | 通过；省略 `model` 使用当前选择，显式其他 ASR 返回 `400 invalid_model` | [原生测试日志](native-contracts.log) |
| 模型控制器 | 关闭设置后保留下载进度，排队／取消／重试、后台下载保留引擎、停止状态仅保存选择、请求排空、候选提交前拒绝新请求、更新互斥、失败回退、迁移后继续同一下载均通过 | [完整日志中的 ModelManager](make-check.log) |
| App 打包 | App 与嵌套代码的 ad-hoc 签名检查、内置模型目录及状态命令 smoke check 通过 | [构建日志](app-build.log) |
| 源码空白检查 | 在远端对基线和交付源码、测试、脚本及文档运行 `git diff --no-index --check`，通过 | [结果](whitespace.json) |

最终 App 位于远端 `dist/CyberAudio.app`。真实推理使用该 App 内的 Assets、Service 和 `mlx.metallib`，摘要见 [运行产物摘要](runtime-artifacts.sha256)。最后一次界面修正后重新打包，三个推理产物的摘要保持一致。

模型控制器的故障、并发和界面状态测试使用隔离的原生 HTTP fixture。真实模型加载与推理由下面的独立矩阵验证。

## 真实模型矩阵

全部 8 款模型在 9 组真实推理组合中通过，见 [运行结果](runtime-summary.json)、[推理日志](runtime.log) 与 [下载日志](downloads.log)。完整目录检查的 8 款模型均为 `ready`，见 [模型状态](models-status.json)。

| ASR | TTS CustomVoice | 中英文识别、WAV／流式 PCM |
|---|---|---|
| 1.7B 8-bit | 1.7B 8-bit，默认回归 | 通过 |
| 0.6B 4-bit | 1.7B 8-bit | 通过 |
| 0.6B 8-bit | 1.7B 8-bit | 通过 |
| 1.7B 4-bit | 1.7B 8-bit | 通过 |
| 1.7B 8-bit | 0.6B 4-bit | 通过 |
| 1.7B 8-bit | 0.6B 8-bit | 通过 |
| 1.7B 8-bit | 1.7B 4-bit | 通过 |
| 0.6B 4-bit | 0.6B 8-bit，混合规格 | 通过 |
| 1.7B 4-bit | 1.7B 4-bit，混合规格 | 通过 |

每组在独立数据目录启动打包后的原生服务，使用固定模型目录并启用离线沙箱：中文 TTS 返回 24 kHz 单声道 WAV，英文 TTS 返回流式 PCM；每款 ASR 识别同一对由默认模型生成的中英文短音频。检查模型 ID、量化规格、revision、九种音色、认证、响应头与不支持表达指令时的明确错误。

各组的中文识别均为“你好，这是本地语音模型的测试。”，英文均为“Hello. This is a local speech model test.”。验收前后与最终读取时，远端已安装生产服务的 instance 相同。本轮 IPv4 SSH 曾中断，后台下载与推理继续完成，最终通过同一 Tailscale 节点的 IPv6 通道取回结果。

这是加载、输出格式和 API 推理检查；短合成音频不代表完整语料准确率、真实麦克风或人工听感验收。

## 存储与恢复

[存储验收结果](storage-summary.json) 中的 9 项场景全部通过。测试创建可丢弃的 512 MiB APFS 磁盘映像，并断言源和目标位于不同文件系统：

1. 跨文件系统复制与摘要校验，事务回退，以及提交标记后的退出恢复。
2. 保留目标冲突文件与原配置。
3. 空间不足时保留原模型、下载暂存和配置。
4. 目录权限错误时恢复原配置。
5. 复制过程中 SIGTERM 取消，退出码 `130`，再次准备可恢复。
6. 复制过程中 SIGKILL，退出码 `-9`，再次准备可恢复。
7. 在复制进度产生后暂停进程，强制卸载测试卷再继续；恢复原配置且不重建失联的挂载路径。
8. 候选目录断开后的回退及重新挂载恢复。
9. 删除未使用模型及其独有下载暂存，保留用户额外文件和当前选择。

这是可重复的磁盘映像卸载／重连检查；物理 USB 拔插未在本轮操作。

## 原生渲染

在远端以原生 `NSHostingView` 渲染最小窗口尺寸：工作台 7 种状态、设置 4 个已填充页签、模型页 5 种状态、准备页 5 种状态。模型设置内容在 `620 × 560` 布局中滚动，图片为 2 倍像素。长路径换行，下载和错误提示保留可用操作。

| 状态 | 远端生成的图片 |
|---|---|
| 浅色 | [models-light.png](screenshots/models-light.png) |
| 深色 | [models-dark.png](screenshots/models-dark.png) |
| 长路径与存储区域 | [models-long-path.png](screenshots/models-long-path.png) |
| 下载进度 | [models-downloading-dark.png](screenshots/models-downloading-dark.png) |
| 下载失败与重试 | [models-failed.png](screenshots/models-failed.png) |

图片中的模型状态和下载进度来自控制器 fixture；本轮没有把原生视图渲染等同于桌面点击或辅助功能自动化。

## 复现

以下命令在远端隔离工作目录内运行，`PATH` 包含 `.context/bootstrap/bin` 和 `/opt/homebrew/bin`：

```bash
rtk proxy make check
rtk proxy make app

rtk proxy .context/tool-tests-venv/bin/python scripts/validate_model_storage.py \
  --models "$PWD/.context/model-acceptance/models" \
  --output "$PWD/.context/model-storage-new-run" \
  --assets "$PWD/dist/CyberAudio.app/Contents/Helpers/CyberAudioAssets"

rtk proxy .context/tool-tests-venv/bin/python scripts/validate_model_variants.py \
  --models "$PWD/.context/model-acceptance/models" \
  --output "$PWD/.context/model-runtime-new-run" \
  --assets "$PWD/dist/CyberAudio.app/Contents/Helpers/CyberAudioAssets" \
  --service "$PWD/dist/CyberAudio.app/Contents/Helpers/CyberAudioService" \
  --metallib "$PWD/dist/CyberAudio.app/Contents/Resources/mlx.metallib"
```

先通过 Assets CLI 为统一目录准备全部固定模型；默认模型可从已有安装复用，并在真实服务加载前逐文件校验。下载方法和 API 契约见 [模型管理说明](../../MODELS.md)。证据仅包含日志、摘要和图片，不包含数据目录中的配置或 API Token。

静态审查按用户 `AGENTS.md` 在主线程依次进行，覆盖 API 兼容、下载源完整性、文件保护、事务恢复、异步请求准入和原生界面，见 [审查记录](review.json)。本轮源码与测试、脚本摘要见 [source-sha256.json](source-sha256.json)。
