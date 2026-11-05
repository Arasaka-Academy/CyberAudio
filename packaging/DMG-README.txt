CyberAudio — 本地语音识别与合成

适用设备：Apple Silicon Mac，macOS 26 或以上；建议 16GB 以上统一内存。

1. 将 CyberAudio.app 拖到 Applications（应用程序）文件夹。
   更新时先退出正在运行的 CyberAudio，再替换旧 App。

2. 首次安装需要 FFmpeg 和固定版本的模型；推理与 HTTP 服务均使用 Swift，无需 Python。
   请按照 Deployment/GUIDE.md 第 12.1–12.4 节运行 App 内的 CyberAudioAssets 工具，
   依次准备配置、下载并校验模型。源码构建步骤可跳过。
   从旧版升级会保留配置、Token、模型和旧运行环境，默认不删除任何旧数据。
   模型文件合计约 5.55GB，建议预留至少 15GB 可用磁盘。

3. 从“应用程序”打开 CyberAudio，等待 STT 与 TTS 就绪。
   关闭窗口后继续在菜单栏运行；退出 App 会停止接口与模型。

此版本采用 ad-hoc 签名，未做 Developer ID 签名与 Apple 公证。
若 macOS 阻止打开，请在“系统设置 → 隐私与安全性”检查该 App 的打开许可。

源码与最新说明：https://github.com/Arasaka-Academy/CyberAudio
