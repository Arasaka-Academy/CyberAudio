# CyberAudio

原生 SwiftUI macOS 语音工作台。一个 App 同时提供本地 STT、TTS 和统一 HTTP API。

[完整 Notion 手册（部署、使用、API 与实测验收）](https://app.notion.com/p/CyberAudio-Mac-STT-TTS-App-API-40d1d9761945811081c6ddd41d45e2c8)。本地文档见 [部署指南](docs/GUIDE.md) 和 [验收报告](docs/VERIFICATION.md)。

- **STT**：Qwen3-ASR 1.7B 8-bit，中文／英文／混合语音，录音或导入文件。
- **TTS**：Qwen3-TTS 1.7B CustomVoice 8-bit，9 个预设音色，表达指令，WAV 与流式 PCM。
- **生命周期**：打开 App 加载模型；关闭窗口后继续在菜单栏运行；退出 App 停止 API、卸载模型。没有后台系统守护服务。
- **API**：保持 `/v1/audio/transcriptions`，新增 `/v1/audio/speech`，共用地址与 Bearer Token。

GUI 使用 SwiftUI／AppKit／AVFoundation；模型使用 App 直接管理的 Python／MLX Audio 子进程，**不是全 Swift 模型实现**。首次安装需要下载模型与准备独立运行环境。当前 `.app` 使用本机 ad-hoc 签名，未公证，不是包含所有资源的通用分发安装包。

## 设备

Apple Silicon Mac，macOS 26 或以上；推荐 16GB 以上统一内存、15GB 以上可用空间。已部署验证的设备是 Apple M4／16GB／macOS 27.2。模型固定 MLX Audio 0.6.3、MLX 0.32.4，完整依赖见 `requirements.lock.txt`。

## 目录

```text
Sources/CyberAudio/       SwiftUI 界面、音频、后端进程管理
backend/                 固定模型 FastAPI API 和针对性测试
scripts/                 模型准备、运行环境迁移、构建、实际模型验证
packaging/Info.plist      App 配置与麦克风权限说明
tests/ui_stub.py          仅用于界面测试的合成接口，不进入生产 App
docs/                    部署、API、运维与验收说明
```

运行资产位于 `~/Library/Application Support/CyberAudio/`，API Token 存在 `config/api-token`（0600），不进入仓库。默认监听 `127.0.0.1:8765`，远程访问由既有 Tailscale Serve 8443 转发；不对局域网或公网直接绑定。

## 构建

安装 Apple Command Line Tools 后：

```bash
bash scripts/build_app.sh
```

输出 `dist/CyberAudio.app`，其中仅包含原生可执行文件和后端源文件。完整首次安装、资产下载和迁移步骤见 [部署与使用指南](docs/GUIDE.md)。

## Release DMG 与下载

[Release DMG 工作流](https://github.com/Arasaka-Academy/CyberAudio/actions/workflows/release-dmg.yml) 仅在 **GitHub Release 发布**（`release.published`）时运行，包括预发布版本。普通 push、PR、单独推送 tag、保存 Release 草稿或编辑已发布的 Release 均不触发。

发布步骤：

1. 更新 `packaging/Info.plist` 中的 `CFBundleShortVersionString`，并递增 `CFBundleVersion`，提交并推送。
2. 在 GitHub 的 Releases 页面创建 Release，选择包含本工作流的提交，设置与 App 版本一致的 tag，例如 `v0.1.0`。预发布可使用 `v0.1.0-rc.1`，其 App 版本仍为 `0.1.0`。
3. 点击 Publish release，等待 `Release DMG` 工作流完成。
4. 打开该次工作流的 Summary，点击下载链接或底部的 Artifacts，下载并解压 `CyberAudio-v0.1.0-macOS-arm64`。

产物包括 `CyberAudio-0.1.0-macOS-arm64.dmg` 和同名 `.dmg.sha256` 文件。Actions artifact 保留 90 天，下载需要登录 GitHub。解压后可运行 `shasum -a 256 -c CyberAudio-0.1.0-macOS-arm64.dmg.sha256` 校验。

工作流使用 `macos-26` 的 ARM64 runner、固定 SHA 的官方 checkout／upload-artifact Actions，以及 `contents: read` 权限；从 Release tag 构建，版本不一致时失败。打包过程不下载模型。DMG 包含 App、Applications 快捷方式及部署资料；首次安装继续按指南准备 Python、FFmpeg 和模型。当前为 ad-hoc 签名包，未做 Apple 公证。

也可在 Apple Silicon Mac 上本地构建同款 DMG：

```bash
make dmg
# 指定 Release tag，并检查它与 Info.plist 的版本是否一致：
bash scripts/build_dmg.sh v0.1.0
```

## 开发检查

```bash
/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install fastapi==0.162.0 uvicorn==0.54.0 python-multipart==0.0.33 httpx pytest pytest-asyncio
make check
```

后端测试使用真实 FastAPI／FFmpeg 与受控推理替身，无需下载模型。实际 Metal 推理、网络访问、退出与崩溃行为的证据另见 [验收报告](docs/VERIFICATION.md)。测试目录、日志、模型、运行环境和生成音频均不提交。

## 调用

```bash
export CYBERAUDIO_BASE_URL='http://127.0.0.1:8765'
read -rs CYBERAUDIO_TOKEN
export CYBERAUDIO_TOKEN
curl --fail-with-body "$CYBERAUDIO_BASE_URL/v1/models" \
  -H "Authorization: Bearer $CYBERAUDIO_TOKEN"
```

完整请求参数、流式完成校验、错误处理和 Python 客户端示例见部署指南。STT／TTS 共用单个推理槽；忙时返回 429，调用方自行等待后重试。
