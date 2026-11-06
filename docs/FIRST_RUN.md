# 首次启动与可恢复下载

打开 App 后先在后台检查固定模型和 FFmpeg。资源齐全时继续加载语音引擎；缺少资源时显示原生准备界面。模型共 5,547,997,530 字节，须主动点击下载，推理进程始终使用离线沙箱。

## 准备界面

1. 检查模型、FFmpeg 和磁盘空间。下载空间包含剩余文件、暂存副本及 256 MiB 余量。
2. 如果未找到 FFmpeg，安装后点“选择…”指定可执行文件，或点“重新检查”。Homebrew 用户可执行 `brew install ffmpeg`。
3. 点“下载本地模型”。网络设置可填 `http://主机:端口` 或 `socks5://主机:端口`；不接受内嵌账号密码。
4. 下载可取消。已完成的文件和已写入暂存区的内容会保留，再次点击继续。当前尚未完整保存的网络响应可能需要重传。
5. 验证通过、FFmpeg 可用后自动进入工作台并启动引擎。

完整暂存文件摘要异常时，界面提供“重新下载”，只重置下载器的暂存文件。已有最终模型文件校验失败时保留原文件，需先检查模型目录、备份异常文件并恢复固定版本，然后重新检查。

配置和 API Token 已存在时原样复用。FFmpeg 选择仅更新对应配置字段。App 退出会取消其准备工具，App 崩溃后工具会检测所属父进程消失并取消。自动更新等待准备工具结束后再继续；取消不会使迟到的准备结果启动引擎。

## 命令行

以下命令不打印 Token；把 App 路径替换为实际安装位置。

```bash
CYBERAUDIO_ASSETS='/Applications/CyberAudio.app/Contents/Helpers/CyberAudioAssets'
"$CYBERAUDIO_ASSETS" prepare
"$CYBERAUDIO_ASSETS" status --json true
"$CYBERAUDIO_ASSETS" download --json true
"$CYBERAUDIO_ASSETS" set-ffmpeg --ffmpeg /opt/homebrew/bin/ffmpeg --json true
"$CYBERAUDIO_ASSETS" verify --json true
```

可用 `--root /绝对路径` 指定独立数据目录；`download --restart true` 从头下载未完成的资产，已验证的最终文件仍会复用。界面通过 JSON 行读取状态、进度和错误，单条记录最大 64 KiB；检查工具超时 180 秒，单次下载操作上限一小时，网络请求仍有独立截止时间。

下载器核对每个 HTTP Range、长度及最终 SHA-256。忽略 Range 的服务器可返回完整文件，通过校验后才发布。最终文件通过排他原子重命名发布；模型位于另一磁盘时先复制到目标磁盘上的暂存文件。错误不会覆盖已有最终文件。暂存目录为数据目录的 `tmp/downloads/`，同时只允许一个下载器持有准备锁。

## 验证边界

`make check` 使用受控本地 HTTP 服务验证断点续传、截断响应、取消、错误范围、摘要错误、忽略 Range、路径限制和最终文件保留；实际资产工具测试还验证配置、Token、FFmpeg 与父进程崩溃后的退出。控制器测试覆盖准备、失败重试、迟到结果、超时、更新等待和退出。原生离屏渲染覆盖 560 × 440 pt 下的四种准备状态。

本轮另用最终 App 的资产工具和独立 APFS 镜像验证一个固定 README 的实际跨卷发布，包括摘要、暂存消费、目标临时文件清理和 Token 保留；结果见[工程验收](reviews/2026-11-06.md)。

这些检查不代表已从 Hugging Face 公网下载全部权重，也不替代真人语音、实际听感、完整桌面键盘导航或真实升级验收。
