# CyberAudio

原生 SwiftUI macOS 语音工作台。一个 App 同时提供本地 STT、TTS 和统一 HTTP API。

当前文档见 [部署指南](docs/GUIDE.md) 和 [Swift 迁移报告](docs/NATIVE_SWIFT.md)。[Notion 手册](https://app.notion.com/p/CyberAudio-Mac-STT-TTS-App-API-40d1d9761945811081c6ddd41d45e2c8) 与 [旧验收报告](docs/VERIFICATION.md) 保留历史 Python 版本记录。

- **STT**：Qwen3-ASR 1.7B 8-bit，中文／英文／混合语音，录音或导入文件。
- **TTS**：Qwen3-TTS 1.7B CustomVoice 8-bit，9 个预设音色，表达指令，WAV 与流式 PCM。
- **界面**：原生 Liquid Glass 工作台，顶部切换录音／朗读模式，独立设置窗口，跟随系统浅色／深色；跨模式保留任务和停止／取消入口。
- **生命周期**：打开 App 加载模型；仅在菜单栏显示，不显示 Dock 图标；关闭窗口后继续运行，可从菜单栏恢复主窗口；退出 App 停止 API、卸载模型。没有后台系统守护服务。
- **更新**：Sparkle 2.10.1，每天检查稳定版并自动下载；确认重启或主动退出时安装，等待音频和 API 任务完成。首次需手动安装 0.2.0（4），详见 [自动更新与发布](docs/AUTOMATIC_UPDATES.md)。
- **API**：保持 `/v1/audio/transcriptions`，新增 `/v1/audio/speech`，共用地址与 Bearer Token。

运行时已改为全 Swift：SwiftUI／AppKit／AVFoundation 界面、SwiftNIO HTTP、MLX Swift／MLX Audio Swift 推理。App 直接管理内置 Helper，无需 Python、pip 或 venv。首次安装只需 FFmpeg 和固定模型资产。计算使用 Apple GPU／Metal，未切换 Core ML 或 ANE。当前 `.app` 使用 ad-hoc 签名，未公证，模型单独下载。

## 设备

Apple Silicon Mac，macOS 26 或以上；推荐 16GB 以上统一内存、15GB 以上可用空间。验证设备是 Apple M4／16GB／macOS 27.2。MLX Swift 0.33.0、MLX 0.32.4，完整 Swift 依赖见 `Native/Package.resolved`。源码构建需要 Swift 6.3+。

## 目录

```text
Sources/CyberAudio/       SwiftUI 界面、音频、后端进程管理
Native/                  Swift HTTP、Qwen 推理、资产工具、协议测试
backend/                 历史 Python API 与回归测试，不进入 App
scripts/                 原生构建、Metal 准备、隔离验收及历史开发工具
packaging/Info.plist      App 配置与麦克风权限说明
Native/.../CyberAudioFixture/  界面／更新测试的原生合成接口，不进入生产 App
docs/                    部署、API、运维与验收说明
```

运行资产位于 `~/Library/Application Support/CyberAudio/`，API Token 存在 `config/api-token`（0600），不进入仓库。默认监听 `127.0.0.1:8765`，远程访问由既有 Tailscale Serve 8443 转发；不对局域网或公网直接绑定。

## 构建

安装 Apple Command Line Tools 后：

```bash
bash scripts/build_app.sh
```

输出 `dist/CyberAudio.app`，包含 App、原生推理 Helper、资产工具、Metal 资源和 Sparkle。首次安装、下载和迁移见 [部署指南](docs/GUIDE.md)，验证边界见 [Swift 迁移报告](docs/NATIVE_SWIFT.md)。

## Release 与自动更新

[GitHub Releases](https://github.com/Arasaka-Academy/CyberAudio/releases) 提供 DMG、完整 App 更新 ZIP、SHA-256 校验与签名 appcast。稳定更新地址固定为 [appcast.xml](https://arasaka-academy.github.io/CyberAudio/appcast.xml)。

发布工作流仅由 `release.published` 触发。它从 tag 构建一次 App，产出 DMG 和 ZIP，签名并验证清单，上传并重新下载校验所有资产，最后部署 Pages 清单。预发布不进入稳定通道，旧版本不能覆盖新清单；失败时原清单继续生效。

```bash
make check app
make dmg
# 需先配置与 App 公钥匹配的 Keychain 签名密钥：
make update
```

首次安装需准备 FFmpeg 和模型。当前 App 为 ad-hoc 签名，未做 Apple 公证。密钥配置、版本规则、工作流重试及验收边界见 [自动更新与发布指南](docs/AUTOMATIC_UPDATES.md)。

## 开发检查

```bash
make check
make legacy-test # 可选：现有 .venv 中的历史 Python 与发布工具测试
```

原生协议测试使用真实 SwiftNIO／FFmpeg 与受控推理替身，无需下载模型，Command Line Tools 即可运行。真实模型隔离验收使用 `scripts/verify_native.swift`；证据见 [Swift 迁移报告](docs/NATIVE_SWIFT.md)。历史 Python 测试和发布编排用于开发，不进入 App。

## 调用

```bash
export CYBERAUDIO_BASE_URL='http://127.0.0.1:8765'
read -rs CYBERAUDIO_TOKEN
export CYBERAUDIO_TOKEN
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/models" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"
```

完整请求参数、流式完成校验、错误处理和 Swift 客户端示例见部署指南。STT／TTS 共用单个推理槽；忙时返回 429，调用方自行等待后重试。
