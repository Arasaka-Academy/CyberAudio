CyberAudio — 本地语音识别与合成

适用设备：Apple Silicon Mac，macOS 26 或以上；建议 16GB 以上统一内存。

1. 将 CyberAudio.app 拖到 Applications（应用程序）文件夹。
   更新时先退出正在运行的 CyberAudio，再替换旧 App。

2. 首次安装需要准备 Homebrew Python 3.13、FFmpeg、独立 Python 环境和模型。
   请按照 Deployment/GUIDE.md 第 12.1–12.4 节操作。
   Deployment 中附带固定依赖锁和模型下载、配置及资产校验脚本。
   可将 Deployment 复制到自己的工作目录后执行；使用 DMG 内的 App 可跳过指南中的源码构建步骤。
   模型文件合计约 5.55GB，建议预留至少 15GB 可用磁盘。

3. 从“应用程序”打开 CyberAudio，等待 STT 与 TTS 就绪。
   关闭窗口后继续在菜单栏运行；退出 App 会停止接口与模型。

此版本采用 ad-hoc 签名，未做 Developer ID 签名与 Apple 公证。
若 macOS 阻止打开，请在“系统设置 → 隐私与安全性”检查该 App 的打开许可。

源码与最新说明：https://github.com/Arasaka-Academy/CyberAudio
