# Luminare 设置与音频波形验收

本轮基于 `bda3b2bdab313698dd2e7d5fd96e141eaa10a594`，开发分支为 `codex/luminare-waveform`。所有依赖解析、编译、测试、打包和原生渲染均经 Tailscale 在 `codehubmac-mini` 执行；本机只编辑源码、进行 Git 操作和读取远端生成的产物。

远端隔离目录：`/Users/codehub/Workspaces/CyberAudio-model-management`。设备为 Apple M4、16 GB，macOS 27.2、Swift 6.4.2。未向正式安装应用部署本轮产物。

## 实现

- 固定 Luminare 0.2.0 与 AudioKit Waveform 1.0.3；传递依赖固定在根目录 `Package.resolved`，完整许可证随 App 打包。
- 设置保持 `620 × 560`，使用侧栏导航、统一卡片、开关和操作按钮。模型页保留滚动、下载进度、失败重试、选择、存储与网络设置。
- 工作台保持 `560 × 440` 最小尺寸和原生文本编辑器。录音波形来自麦克风电平历史；合成波形来自实际 PCM/WAV 峰值，播放位置来自 AVFoundation。
- 波形按新增数据增量处理，显示数据有固定上限；完整 WAV 在后台解析，取消、清空和退出会取消分析并拒绝迟到结果。
- 主操作按钮采用原生有边框样式，改善深色模式下的文字对比。减少透明度、增强对比和减少动态效果沿用原生偏好。

`scripts/patch_waveform.py` 校验固定上游 Renderer.swift 的摘要，只调整资源查找与源码着色器回退。`scripts/build_ui.sh` 为分发包编译 `default.metallib`，并使用完整 Xcode 的 SwiftUI 宏插件。运行 App 无需 Xcode 或 Metal 编译器。

## 远程验收

| 检查 | 结果与证据 |
|---|---|
| `rtk proxy make check` | 通过：39 项原生契约，0 失败、0 跳过；54 项工具测试；[完整日志](make-check.txt) |
| 听写与音频 | 授权取消、输入框与剪贴板、IME/撤销、真实 HTTP fixture、PCM/WAV、重播归属、设备变化、请求排空与更新等待通过 |
| 波形 | 120 秒流的内存上限、瞬态峰值、流式/WAV 一致性、带填充元数据的 WAV、损坏输入、取消与迟到分析结果通过 |
| 模型控制器 | 页面关闭后下载继续、排队/取消/重试、后台下载保留当前引擎、切换回退、维护互斥和迁移恢复回归通过 |
| 原生渲染 | 10 种工作台、7 种设置、6 种模型、5 种首次准备状态，共 28 张；另有 1 张打包资源 GPU 渲染，共 [29 张图片](screenshots) |
| 打包资源 | 将构建目录的 Waveform 资源移走，签名后的隔离 App 仍可加载包内预编译着色器，并完成真实 AudioKit Metal 绘制 |
| `rtk proxy make app` | 通过 App 与嵌套代码的 ad-hoc 签名、Assets 目录/状态命令、架构及无 Python 运行时检查；[日志](app-build.txt) |
| 源码与保护 | 在远端检查源码空白、本地与远端源码一致性、包内许可证，以及正式 App/配置摘要与 8765 实例不变；[回执](checks.json)、[空白检查](whitespace.json) |

本轮 Native Helper 重新链接，新包另做默认 1.7B 8-bit 组合的真实中英文识别、WAV 与流式 PCM 检查，均通过，见 [默认组合结果](default-runtime.json)。同一份产品波形解析器对这两段真实模型输出的时长、非静音峰值和显示采样上限检查通过，见 [波形结果](real-waveform.json)。完整八款矩阵仍保留在[模型管理验收](../model-management-2026-11-13/README.md)，不作为本轮新二进制的矩阵验收。

## 预览

| 界面 | 图片 |
|---|---|
| 听写设置 | [浅色](screenshots/settings-0.png)、[深色](screenshots/settings-0-dark.png) |
| 模型管理 | [浅色](screenshots/models-light.png)、[深色](screenshots/models-dark.png) |
| 模型状态 | [下载中](screenshots/models-downloading-dark.png)、[失败](screenshots/models-failed.png)、[长路径](screenshots/models-long-path.png) |
| 辅助显示 | [减少透明度、减少动态效果、增强对比](screenshots/models-accessible.png) |
| 服务与通用 | [服务](screenshots/settings-1.png)、[更新](screenshots/settings-2-dark.png) |
| 播放 | [WAV 波形](screenshots/tts-waveform-light.png)、[播放进度](screenshots/tts-playing-dark.png)、[流式状态](screenshots/tts-streaming-dark.png) |
| 打包 GPU 资源 | [包内着色器绘制](screenshots/packaged-waveform.png) |

图片来自原生 NSHostingView；Metal 图层从渲染器自身队列读取 GPU 像素后合成。模型状态来自控制器 fixture，工作台截图中的音频为可重复的 PCM 测试信号。辅助显示状态只在测试进程内覆盖 NSWorkspace getter，并断言 SwiftUI 读到对应偏好，没有修改用户系统设置。视图渲染不等于桌面点击自动化、真实麦克风或人工听感验收。

## 复现

在远端隔离目录运行，`PATH` 包含 `.context/bootstrap/bin` 与 `/opt/homebrew/bin`：

```bash
export CYBERAUDIO_LAYOUT_OUTPUT="$PWD/.context/design-refresh/layout"
rtk proxy make check
rtk proxy make app

# 对最终包补充默认组合与真实音频波形检查
rtk proxy python3 docs/evidence/ui-refresh-2026-11-13/default_acceptance.py
rtk proxy swiftc -swift-version 5 -parse-as-library \
  Sources/CyberAudio/AudioWaveform.swift \
  docs/evidence/ui-refresh-2026-11-13/real_waveform_probe.swift \
  -o .context/design-refresh/real-waveform-probe
rtk proxy .context/design-refresh/real-waveform-probe \
  .context/design-refresh/default-runtime/zh.wav \
  .context/design-refresh/default-runtime/en.wav
```

完整 Xcode 需要 Metal Toolchain；本次在远端通过 `xcodebuild -downloadComponent MetalToolchain` 准备。上游 SwiftPM 弃用、上游资源声明提示，以及测试进程的系统沙箱提示保留在日志中；最终 `make check` 与 `make app` 出口均为 0。

最终 App 位于远端 `dist/CyberAudio.app`。[源文件摘要](source-sha256.json) 对应本轮改动的编译输入，[产物摘要](app-sha256.json) 对应最终打包字节。顺序审查见 [review.json](review.json)。
