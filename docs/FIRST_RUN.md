# 首次启动与可恢复下载

打开 App 后先在后台检查固定模型和 FFmpeg。资源齐全时继续加载语音引擎；缺少资源时显示原生准备界面。模型共 5,547,997,530 字节，须主动点击下载，推理进程始终使用离线沙箱。

## 准备界面

1. 检查模型、FFmpeg 和磁盘空间。下载空间包含剩余文件、暂存副本及 256 MiB 余量。
2. 如果未找到 FFmpeg，安装后点“选择…”指定可执行文件，或点“重新检查”。Homebrew 用户可执行 `brew install ffmpeg`。
3. 选择下载源后点“下载本地模型”。默认使用 Hugging Face；连接困难时可选择 ModelScope。两者下载同一组固定文件，更换源会保留已完成的文件和进度。网络设置可填 `http://主机:端口` 或 `socks5://主机:端口`；不接受内嵌账号密码。
4. 下载可取消。已完成的文件和已写入暂存区的内容会保留，再次点击继续。当前尚未完整保存的网络响应可能需要重传。
5. 验证通过、FFmpeg 可用后自动进入工作台并启动引擎。

暂时的连接中断或下载服务繁忙会自动重试。若仍失败，界面保留进度；检查网络或代理后再次继续即可。取消也会立即中断重试等待。

完整暂存文件摘要异常时，界面提供“重新下载”，只重置下载器的暂存文件。已有最终模型文件校验失败时保留原文件，需先检查模型目录、备份异常文件并恢复固定版本，然后重新检查。

配置和 API Token 已存在时原样复用。FFmpeg 选择仅更新对应配置字段。App 退出会取消其准备工具，App 崩溃后工具会检测所属父进程消失并取消。自动更新等待准备工具结束后再继续；取消不会使迟到的准备结果启动引擎。

## 命令行

以下命令不打印 Token；把 App 路径替换为实际安装位置。

```bash
CYBERAUDIO_ASSETS='/Applications/CyberAudio.app/Contents/Helpers/CyberAudioAssets'
"$CYBERAUDIO_ASSETS" prepare
"$CYBERAUDIO_ASSETS" status --json true
"$CYBERAUDIO_ASSETS" download --json true
# Hugging Face 连接困难时显式选择另一固定下载源：
"$CYBERAUDIO_ASSETS" download --source modelscope --json true
"$CYBERAUDIO_ASSETS" set-ffmpeg --ffmpeg /opt/homebrew/bin/ffmpeg --json true
"$CYBERAUDIO_ASSETS" verify --json true
```

可用 `--root /绝对路径` 指定独立数据目录；`download --restart true` 从头下载未完成的资产，已验证的最终文件仍会复用。界面通过 JSON 行读取状态、进度和错误，单条记录最大 64 KiB；检查工具超时 180 秒，单次下载操作上限一小时，网络请求仍有独立截止时间。

`--source` 只接受 `huggingface`（默认）和 `modelscope`，不接受任意地址。ModelScope 使用两个固定仓库快照；全部 23 个文件的大小和 SHA-256 均与原有锁定清单一致。下载失败会返回错误，用户可更换源后继续，工具不会自动切换下载源。选择下载源不修改服务配置、Token 或模型版本。

下载器核对每个 HTTP Range、长度及最终 SHA-256。忽略 Range 的服务器可返回完整文件，通过校验后才发布。最终文件通过排他原子重命名发布；模型位于另一磁盘时先复制到目标磁盘上的暂存文件。错误不会覆盖已有最终文件。暂存目录为数据目录的 `tmp/downloads/`，同时只允许一个下载器持有准备锁。

## 验证边界

`make check` 使用受控本地 HTTP 服务验证断点续传、截断响应、取消、错误范围、摘要错误、忽略 Range、路径限制和最终文件保留；实际资产工具测试还验证配置、Token、FFmpeg 与父进程崩溃后的退出。控制器测试覆盖下载源与代理的参数传递、准备、失败重试、迟到结果、超时、更新等待和退出。原生离屏渲染覆盖 560 × 440 pt 下的五种准备状态，包括选择 ModelScope。

本轮另用最终 App 的资产工具和独立 APFS 镜像验证一个固定 README 的实际跨卷发布，包括摘要、暂存消费、目标临时文件清理和 Token 保留；结果见[工程验收](reviews/2026-11-06.md)。

2026-11-07 在 M4／16GiB 上显式选择 ModelScope，以正常 TLS、无代理完成一次实际续传：复用此前公网取得的 14 个最终文件和 176MiB 分段，补齐后全部 23 个文件／5,547,997,530 字节经独立摘要复核通过。当前签名 App 随后在新目录、仅链接这些下载文件，完成 100 条固定语料、9 个声音及完整服务／App 生命周期验收；M1 和 M4 各自与同设备旧报告的 100 条原始转写一致，见[下载与实际运行证据](evidence/robustness-2026-11-06/modelscope-download/README.md)。

此结果不说明原 Hugging Face 传输已修复，也不覆盖清空缓存重传、FFmpeg 从零安装、实际听感、完整桌面键盘导航或真实升级验收。
