# M4 公网下载路径恢复与中断终态

2026-11-07 追加，基线 `main@d8e7ad8`。本轮没有修改产品源码、模型参数、系统代理或 TLS 信任设置。目标仍是同一固定 Hugging Face 修订、23 文件／5,547,997,530 字节，以及完成后的新目录推理；本轮未达到该门槛。

## 路径对照和实际范围校验

原 M4 显式代理的原生 1MiB 探针仍返回 `NSURLErrorDomain -1200`。四个固定 HEAD 条件在两台机器分别执行，均保留默认 TLS、无重试：本机经现有代理的 Hugging Face／Apple HTTP 和 SOCKS5H 全部 200；M4 的 Hugging Face 两种协议均 curl 35／SSL_ERROR_SYSCALL，Apple 两种均 200。本机直连 HF 超时，Apple 200。结果只约束这些具体路径和时间，未归因为某个代理实现、证书或源站。

本轮通过专用 SSH 反向转发，请求将 M4 `127.0.0.1:17890` 转到本机既有 `127.0.0.1:7890`。只替换原 Swift 探针的两个代理端口常量，保持 URL、范围和默认 TLS。实际 URLSession 返回 **206、1,048,576 字节、Content-Range `bytes 117440512-118489087/2393308931`**，耗时 6.194 秒，SHA-256 为 `995c2090456f47cfe6a14a697a24f15fdef57f3c99c0280e9618cf4fc13cb4da`，与原模型对应字节一致；生产和文件清单前后不变，见 [forwarded-native-range.json](forwarded-native-range.json)。

先前用 netstat 列表作为监听存在的前置条件，失败发生在网络探针之前。改用实际 socket 检查后完成上述请求；缺少 lsof／netstat 输出不能单独证明端口关闭，也不把 TCP 建连视为完整 HTTP／TLS 成功。

## 同一下载器的真实续传终态

沿用签名 Assets SHA `06698db17958ab5adc2da7d1a1d9b23cbc34c57b67e1b1fb5cf7e9fd0e7adb1c`、原目录和 `--proxy http://127.0.0.1:17890`，不重新开始分段。预定上限 3600 秒、3GiB 磁盘余量、仅一次 CLI 调用。

运行在 **03:40:14 UTC** 开始；本机下载 SSH 与反向转发随后均因 keepalive 超时 exit 255。实际远端运行器在 **03:56:52 UTC** 写出 failed 终态，下载子进程 **exit 1**，安全错误为 `download_unavailable`／“下载中断，已保留进度；请检查网络或代理后继续。”，不是程序成功或完整下载成功。总过程约 998.38 秒；见 [download-terminal.json](download-terminal.json) 和 [terminal-events.json](terminal-events.json)。

独立 SSH 终态检查确认：14 个最终文件仍为 **2,469,536,771 字节**、缺少 9 文件，持久 TTS 分段从 **117,440,512 增至 184,549,376 字节**，增加 64MiB。运行器重新核对原 14 文件 SHA／mtime 及原 112MiB 前缀 SHA，均保留；生产 ready／idle 的同一实例、配置和 Token 摘要不变。03:57:17 UTC 未见所属下载／推理进程。

本机 SSH 退出后，远端该转发的 sshd 会话仍独占 17890 监听。核对其 UID、PID／启动时间／命令以及唯一监听后，仅向该会话发 TERM。03:59:27 UTC 确认进程、监听均消失，17890 连接返回 errno 61，生产仍保留；原共享 7890 代理未停止。详见 [preservation-and-cleanup.json](preservation-and-cleanup.json)。

## 验收包准备与排除的解释

当前 `d8e7ad8` App 已在本机增量构建、隔离组装并通过严格嵌套 ad-hoc 验签和 Assets prepare，未安装。为准备后续推理，将其与 M4 原测试包逐文件比较：160 个文件／链接中 155 个相同，差量压缩包 21,589,067 字节。整包传输达到 240 秒预算；差量传输 SSH keepalive 超时；保留的 9,400,320 字节差量与本地前缀 SHA 一致，但一次 SFTP 续传又在 banner 交换阶段超时。目标包没有完成校验或运行，原 App 和两份部分归档均保留，所属归档文件无打开的传输句柄。不能将本机签名结果算作 M4 新模型验收或 Developer ID 分发通过。

三次 Tailscale 探针均经 DERP，未报告直连；该观察本身不能证明吞吐瓶颈的唯一原因。两机 HF DNS 答案不同，但在 M4 原代理上仅改 CONNECT 目标地址、保留原 HTTPS 主机／SNI／默认 TLS 的两个 HEAD 条件仍同样失败。因此没有更改 DNS、系统代理或推理代码。新的网络原始日志、地址、完整归档与模型仍留在私有目录。

本轮只新增网络／文件／构建证据，没有重跑完整 make check 或模型质量矩阵。本轮构建清单中 98 项源码／构建配置摘要及 6 项既有冻结构建产物均保持一致。此前 main 的 CI 记录保持原验收时间；此次文档交付 CI 另行核验。短句 TTS／原严格 M4 7200 秒负载、自然混说质量、物理录音／试听／桌面听写、Developer ID 公证／GUI 升级、完整公网下载和 FFmpeg 从零安装仍分别待验。

下一次应先取得可持续传输的证据，保留本轮 176MiB 断点；全部 23 个固定哈希通过后，再启动隔离的新目录推理。历史失败不改写，不从一次范围成功推断完整安装成功。[summary.json](summary.json)记录本阶段接受边界。

本目录从 `.context/robustness/public-download-refresh-20261107/` 选择性导出。Swift 探针和下载监督脚本带有当时固定路径，仅用于审阅和溯源；不应从 docs 目录直接重跑。配置、Token、模型、二进制、原始网络地址和完整日志均未入 Git。`SHA256SUMS.txt` 覆盖此目录的公开文件。
