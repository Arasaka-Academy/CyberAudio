# 固定真人语料与 M4 目标机验收

本阶段复用运行时修复 `e2f34df` 的实际 App／Helper，验收工具提交为 `56b7eca`。上级目录 R1–R7 和 `playback-recovery/` 的历史回执保留原值。新结果补齐固定真人语料和 M4 完整服务回归；长时间测试、完整公网下载、物理音频和正式分发分别记录。

## 已完成

| 文件 | 内容 |
|---|---|
| [corpus-recovery.json](corpus-recovery.json) | 从 M4 既有缓存恢复固定 100 条 FLEURS 录音；清单与仓库基线逐字节一致，所选 100 个 WAV 全部 SHA-256 通过 |
| [m4-native-service.json](m4-native-service.json) | M4／16GiB 完整实际服务回归，含 100 条 ASR、16 个格式请求、9 音色、PCM、取消、租约、EOF 与真实 App TERM／KILL／重启 |
| [m4-full-result.json](m4-full-result.json) | 目标机持久运行器的终态：215.38 秒、进程退出码 0、服务报告 passed |
| [m4-asr-comparison.json](m4-asr-comparison.json) | 与固定历史 Python 基线匹配 ID、音频摘要、参考、语言；主准确率指标不回退 |
| [m1-native-service.json](m1-native-service.json)、[m1-soak-smoke.json](m1-soak-smoke.json) | M1 完整服务及新 soak 模式的 75.84 秒冒烟验证；冒烟不计作两小时验收 |
| [m1-cross-device-comparison.json](m1-cross-device-comparison.json) | 保留首次跨设备比较的真实结果，包括中文 CER 小幅差异及 `accuracyNonRegressing: false` |
| [m1-installed-baseline.json](m1-installed-baseline.json)、[m1-same-device-comparison.json](m1-same-device-comparison.json) | 在同一 M1 隔离运行已安装旧 Helper；100／100 新旧输出逐字一致，主指标不回退 |
| [target-identity.json](target-identity.json) | 原五个组件与本地 R8 构建一致；测试副本更换 bundle ID 并通过严格嵌套验签；未替换生产 App |
| [preflight-before-after.json](preflight-before-after.json) | 元数据测试 App 证明旧驱动会先创建数据目录；新驱动在创建目录之前拒绝生产 bundle ID，并有 7 项永久 CLI 回归 |
| [checks.json](checks.json)、[source-sha256.json](source-sha256.json) | 完整检查：28 项原生契约、36 项工具测试、14 个渲染，全部行为组和实际 App／Helper 编译；97 个源码文件摘要 |
| [running-snapshot.json](running-snapshot.json) | 2026-11-06 11:47:37 UTC 的两项运行中任务快照；同时核对 PID、启动时间和命令，不代表任务完成 |

M4 是 Apple M4／16GiB、macOS 27.2（26B123）。中文 CER **7.9722704%**、英文 WER **3.3457249%** 与历史 M4 Python 主指标相同，原始输出 98／100 相同。HTTP P95 为 1549.41ms；后台负载和历史运行条件不同，未据此主张速度提升或回退。九个实际生成的 WAV 还独立解析为 24kHz、单声道、PCM16，帧数均大于零。

初次将 M1 新结果与历史 M4 比较，中文 CER 为 8.0300404%，英文 WER 为 3.2527881%；跨设备严格评分返回不回退失败。继续以同一 M1 的旧 0.4.3 Helper 为对照，新旧 100 条输出完全一致。两个报告均保留，避免把不同硬件的结果当成同设备修复回归。旧 Helper 为 `b784db35…d69193a`，本次为 `cc556b65…41a89`；完整摘要见 JSON。

FLEURS 与参考文本归属 Google／FLEURS（Conneau 等，2022），许可 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，[数据卡](https://huggingface.co/datasets/google/fleurs)，固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`。沿用前 50 条普通话、前 50 条美式英语，未按输出选择样本。所选录音合计 64,676,520 字节；本目录仅保存元数据、原参考和输出，不分发录音。缓存中的额外录音没有计入验收。

## 仍在运行

两项任务均在 M4 的专用缓存目录 `RobustnessValidation/20261106-e2f34df-target1/` 下，使用持久进程，SSH 断开不会重启或中止任务。它们不属于定时自动化。

- 两小时同实例混合负载：运行器 PID `53772`，启动时间 `2026-11-06 19:34:53 +0800`。快照中实际负载 714.37 秒、175 轮、35 次断流恢复、17 次更新暂停／恢复，仍为 `running`。终态需同时检查 `soak-result.json`、`data-soak/reports/soak.json` 和 `native-service.json` 的生命周期清理。
- 全新公网模型下载：运行器 PID `54552`，启动时间 `2026-11-06 19:44:01 +0800`。使用目标机已有显式 HTTP 代理，未修改系统代理。空目录初始缺少全部 23 文件，原模型未复用。保存 8MiB 分段后主动取消，Helper 退出 130，暂存和已完成文件保留；新进程从相同分段续传。快照持久暂存 150,994,944 字节，尚未完成总计 5,547,997,530 字节的下载与最终校验。

下载验证有一小时预算、3GiB 剩余磁盘保护和有限失败重试，记录独立尝试日志。代理端点是现场发现的既有可用服务，没有将其配置来源归因于用户。下载产生的 CPU／磁盘／网络活动属于两小时负载的背景条件；没有额外启动第三个 GPU 模型实例。

前台生产服务、单独验收服务和下载同时存在。`caffeinate -i` 仅在验收运行期间防止空闲睡眠，退出后释放；该测试不验证睡眠恢复。读取进程当前 RSS、进程峰值 RSS 和 Metal 指标的口径不同，不能相加，也不能用一条内存快照宣布无泄漏。

## 复现与边界

驱动要求独立 bundle ID、专用新目录、非生产端口，以及已校验的 `asr/`、`tts/` 和 `corpus.json`／WAV。测试会终止指定测试 App。示例中的 `7200` 是实际负载秒数，完整流程还包括准备、格式／音色和退出／重启检查。

```bash
rtk proxy swiftc -parse-as-library -swift-version 5 -target arm64-apple-macos26.0 \
  scripts/verify_native.swift -o .context/verify-native
rtk proxy .context/verify-native --app /absolute/path/to/isolated/CyberAudio.app \
  --root /absolute/path/to/new-data --models /absolute/path/to/models \
  --corpus /absolute/path/to/corpus --port 18867 --soak-seconds 7200
```

物理麦克风、人耳试听、真实拔插与睡眠、跨应用真人听写、Developer ID／公证、Sparkle GUI 升级和远程 CI 尚未完成。M4 没有可用 Apple Development 或 Developer ID Application 身份；本地 M1 的开发证书结果不能代替目标机分发证书。完整下载之后还须单独验证新模型目录进入 ready，且 FFmpeg 是目标机已安装版本，不能计作从零安装 FFmpeg。
