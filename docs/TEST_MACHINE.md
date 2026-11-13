# 测试机部署

自 2026-11-13 起，当前测试机器为 `codehubmac-mini.civet-beta.ts.net`，SSH 用户 `codehub`。硬件为 Apple M4、16GB 内存，系统为 macOS 27.2。凭据不写入仓库。

CyberAudio 安装到 `/Applications/CyberAudio.app`；安装包放在 `/Users/codehub/Downloads/CyberAudio-0.4.4/`。运行数据使用该用户的 `~/Library/Application Support/CyberAudio/`，由资产工具生成独立配置和 API Token，不复制其他机器的凭据。

0.4.4（11）接入已确认的 B「断讯 / Cutline」CyberAudio A 形应用图标、菜单栏小尺寸图形和字标。构建仍采用项目默认 ad-hoc 签名，模型独立于 DMG；未通过 Apple 公证。

## 安装与检查

```bash
ssh codehub@codehubmac-mini.civet-beta.ts.net
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/CyberAudio.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' /Applications/CyberAudio.app/Contents/Info.plist
codesign --verify --deep --strict /Applications/CyberAudio.app
/Applications/CyberAudio.app/Contents/Helpers/CyberAudioAssets status --json true
open /Applications/CyberAudio.app
```

默认 API 监听 `127.0.0.1:8765`。远程调试可通过 SSH 转发，例如 `ssh -L 18765:127.0.0.1:8765 codehub@codehubmac-mini.civet-beta.ts.net`；调用仍需测试机自身的 Bearer Token，见 [部署指南](GUIDE.md)。

首次麦克风、辅助功能与输入监控授权由 macOS 管理，见 [快捷听写](DICTATION.md)。应用安装、模型摘要、服务健康及合成／转写检查与实际麦克风、键盘输入和人工听感分别记录。

## 2026-11-13 部署验收

CyberAudio 0.4.4（11）已安装并启动，应用在 `codehub` 的图形会话中处于前台、未隐藏且完成启动；仅有一个 App 和一个由其直接管理的 `CyberAudioService`。应用、服务与资源工具均为 arm64，嵌套签名严格校验通过。远端五个图标资源的 SHA-256 与本项目源文件一致。

DMG：`CyberAudio-0.4.4-macOS-arm64.dmg`，已保存在本机 `dist/` 和测试机 `/Users/codehub/Downloads/CyberAudio-0.4.4/`。两端 SHA-256 一致：

```text
e409be5a8fd8f61be4f43c09cc17de323120281b069445f4dcee449205182760
```

- 内置资产工具验证 5,547,997,530 字节锁定模型文件，缺失和摘要错误均为 0，FFmpeg 可用。
- `/health`、`/ready`、带 Token 的 `/v1/models` 和 `/v1/audio/voices` 均返回 200；未带 Token 的 `/v1/models` 返回 401。模型 revision 与源码锁定值一致，9 个音色可查询。
- MLX Swift／Metal 实际预热完成，STT、TTS 均为 `ready`。
- 中文 TTS 使用 Vivian，将“你好，这是语音工作台的新版本测试。”合成为 24kHz 单声道 PCM16 WAV，时长 3.36 秒，耗时约 2.13 秒。
- 将该 WAV 提交 STT，约 0.65 秒返回完全相同的文字。这是一次接口往返验证，不代表完整语料准确率测试。
- 本地菜单栏 8 组深浅色／待机工作／1×2× 渲染检查通过；准备、更新、听写、音频工作流和布局回归通过；工具测试为 41 通过、3 跳过。

签名仍为 ad-hoc，未做 Apple 公证。真实麦克风、全局听写按键、目标输入框和扬声器人工试听尚未验收。远端会话未授予屏幕录制权限，未取得安装后界面截图；`design/icons/preview.png` 是设计展示板。

详细结果见 [部署报告](evidence/release-0.4.4-2026-11-13/deployment-summary.json) 和 [实际 API 结果](evidence/release-0.4.4-2026-11-13/runtime-smoke.json)。
