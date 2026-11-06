# 2026-11-06 工程完善与验收证据

本目录对应 `codex/robustness`，审查基线 `96210dd`。问题、实现和剩余验收见[审查报告](../../reviews/2026-11-06.md)。这里只保留经过检查的结果、公开模型／合成语料元数据和源码摘要，不包含配置、Token、证书、私钥、完整日志或模型权重。

## 证据索引

| 文件 | 含义 |
|---|---|
| [checks.json](checks.json) | R1–R7 阶段完整 `make check`：28 项原生契约，29 项现役工具测试，14 个原生渲染，行为回归和实际推理 Helper 编译；工作流／entitlement／空白检查 |
| [build.json](build.json) | R1–R7 阶段设备、源码提交、本地 App 与两个 Helper／Metal 的大小和 SHA；明确本地 ad-hoc、未公证、未交付 |
| [source-sha256.json](source-sha256.json) | 本轮本仓库 App、Native、测试、工具、打包、锁文件与工作流的内容快照；不含下载的上游源码 |
| [native-service.json](native-service.json) | 最终真实模型／离线服务／格式／音色／断流／退出重启验收 |
| [synthetic-corpus.json](synthetic-corpus.json) | 本轮系统 `say` 中英文输入来源、参考文本及音频 SHA；原始录音为合成语音 |
| [development-signing.json](development-signing.json) | 最终 App 副本的实际 Apple Development 签名、强化运行时、内置工具启动结果；未公证 |
| [cross-volume.json](cross-volume.json) | 最终资源 Helper 在独立 APFS 镜像完成排他原子发布的结果 |
| [soak.json](soak.json) | 同一实际模型实例 602.69 秒、210 次 STT、210 次完整 TTS、42 次断流恢复、21 次更新暂停／恢复与逐轮内存指标 |
| [corpus-availability.json](corpus-availability.json) | 固定真人语料的本地缺失和两条固定数据源连接超时记录 |
| [first-run-light.png](first-run-light.png)、[first-run-dark.png](first-run-dark.png) | 受控准备状态在 560 × 440 pt 下的实际原生离屏渲染 |
| [package.json](package.json) | 本地 DMG 生成后的外部验收回执：映像／SHA、只读挂载、App 嵌套签名和指定文档／二进制逐字节检查 |
| [playback-recovery/README.md](playback-recovery/README.md) | R8 设备中断续审的独立回归、源码快照与后续构建证据；上列历史回执未被替换 |

完整原始记录保留在开发工作区 `.context/robustness/`，原始检查日志在 `/tmp/cyberaudio-robustness-*.log`，不会作为分发材料上传。

## 实际服务

硬件为 Apple M1 Max／32GiB，macOS 27.0.1。使用 R1–R7 阶段本地 App 的 Service，SHA 在 `build.json`、`development-signing.json` 与 `native-service.json` 中一致。测试 App 采用独立 bundle ID、数据目录和 18865 端口，前后确认 8765 正式服务实例身份一致。

验收包含 23 个模型资产与 tokenizer 校验、禁止主动出站网络、单所有者锁、中英文各八种输入格式、九音色 24kHz／单声道／PCM16 WAV、完整 PCM、三次断流取消与恢复、更新租约暂停／恢复、stdin EOF，以及真实 App 在活动推理中收到 TERM／KILL 后的子进程回收与重启。

复现驱动为 [verify_native.swift](../../../scripts/verify_native.swift)，需本地固定模型和带 SHA 的 `corpus.json`：

```bash
rtk proxy swiftc -parse-as-library -swift-version 5 -target arm64-apple-macos26.0 \
  scripts/verify_native.swift -o .context/verify-native
# 必须使用独立 bundle ID 的 App 副本、专用新目录与非正式端口。
rtk proxy .context/verify-native --app /absolute/path/to/isolated/CyberAudio.app \
  --root /absolute/path/to/new-data --models /absolute/path/to/models \
  --corpus /absolute/path/to/corpus --port 18865
```

测试会启动和终止指定 App，拒绝默认正式数据目录、8765、占用端口及没有验收标记的已有测试目录。进程参数禁用该次测试的全局听写和自动更新，不写正式 App 的偏好。

持续负载另用 18866 端口和全新目录，直接启动相同最终 Service 与离线沙箱。同一实例交替识别两条合成输入，轮换九个音色和 WAV／PCM，每五轮主动断流并检查 `cancelled`，每十轮验证更新暂停返回 503、取消租约后恢复。逐轮记录 TTS 的 Metal／RSS 指标和进程当前 RSS；最后检查实际 `tmp` 下没有 `request-*`，关闭 stdin 并等待进程退出，核对正式服务实例保持一致。测试无计划空闲间隔，但包含本地 HTTP、FFmpeg 和状态查询，不能视为纯 GPU 满载基准。

## 下载和签名

受控 HTTP 回归使用真实 socket 与 URLSession；SDK／推理性能不由这些下载测试证明。跨卷实测使用已验证的 1008 字节固定 ASR README 暂存文件、本机暂存卷、64MiB 独立 APFS 镜像和实际内置资源工具。两个卷的 device ID 不同。后续网络请求被本地 CONNECT 代理保持，再取消工具；代理不转发网络。摘要、最终文件、暂存消费、目标私有文件清理、Token 保留及镜像卸载均通过。

实际开发证书签名复制最终 App 并使用独立 bundle ID；签名由内到外执行，验证强化运行时和严格嵌套签名，并启动打包的 Assets 工具。证书姓名、团队、Keychain、签名凭据均未保存在本目录。

分发失败测试分别模拟公证拒绝、缺少 staple 票据与 Gatekeeper 拒绝，要求中止后续分发。它们验证失败处理，不是 Apple 真实公证的证据。

## 验收边界

- 本轮输入是 OS 合成语音，不用于估计真人 CER／WER 或主观音质。历史 FLEURS 100 条回归保留在前一日的证据目录，本机缺少原始录音；恢复固定数据源的中英文连接均超时，尚未重跑。
- 本轮没有从公网或真实代理下载全部模型权重，没有实测完整 SOCKS5 大文件下载。
- 桌面会话锁定，桌面自动化不可用。权限标志为允许不等于物理麦克风、跨应用输入、完整键盘导航、睡眠恢复或人耳试听通过。
- Apple Development 成功不等于 Developer ID、公证、稳定版升级或系统权限保留通过。本机没有 Developer ID Application 证书。
- 本分支没有推送，未跑远程 CI，未改动正式安装、更新 feed 或发布资产。
- Metal／RSS 指标口径不同，不能相加；预热后的短句速度不代表冷启动、内存压力、长期稳定性或能耗。
