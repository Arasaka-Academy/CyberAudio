# 最新隔离 App 的实际验收

本阶段重新组装 **`939050f`** 对应的 App、Service 和 Assets，使用独立 bundle ID `local.cyberaudio.validation.final939050f` 和独立数据目录／18882 端口。当前 `dist/CyberAudio.app` 与既有 DMG、正式安装、配置／Token 均保留，没有安装、推送或发布。

[构建回执](build.json)记录最终签名后的每个组件、输入二进制、Metal、锁文件、驱动与保留产物的 SHA。[源码摘要](source-sha256.json)固定本仓库源码内容，原始构建回执 SHA 由[独立验证](independent-verification.json)关联。严格嵌套 ad-hoc 验签和 Assets prepare 通过；没有 Developer ID 公证。

## 原生验收结果

[原生报告](native-service.json)为 **passed，驱动 exit 0**，运行于 M1 Max／32GiB，2026-11-06 14:18:17–14:20:56 UTC。该报告的 Service SHA 与当前候选逐字节匹配。

- 全部 23 个固定模型资产校验、离线出站拒绝、单所有者锁和鉴权检查。
- 16 个中英文音频格式请求、固定 100 条真人录音。独立复核全部录音 SHA、文本参考与语言，并与此前同一 M1 的旧版 0.4.3 结果比较，**100／100 原始输出逐字相同**。
- 九音色 WAV、完整流式 PCM、三轮真实断流取消／恢复，以及更新暂停、拒绝新请求和租约恢复。
- stdin EOF 后退出；实际 App 在活动推理中分别收到 TERM／KILL 后 Helper 回收，再次启动。两轮 Helper 均消失，没有复活或残留监听。
- 独立重新读取全部九个 WAV 的帧，验证单声道／24kHz／PCM16、帧数及 SHA；所有所属进程已退出、18882 已关闭、临时请求目录为零，生产实例和配置／Token 摘要保持一致。

本轮没有更改模型或采样参数；运行耗时约 160.94 秒，不是跨设备速度或能耗对照，也不覆盖已失败的 M4 两小时门槛、公网下载、物理麦克风／试听／拔插／睡眠、跨应用听写或公证 GUI 升级。

## 采集器错误与独立复核

外层 Python 采集器在原生驱动已经退出 0 后，读取不存在的 `tts` 键；实际字段为 `tts_voices`。因此[原始外层回执](collector-failed.json)保留 **failed／KeyError**，没有改写成通过，也没有重跑模型负载。

[独立复核回执](independent-verification.json)重新验证原生报告、退出码、组件 SHA、全部录音和 WAV、取消／生命周期结果、进程／端口／目录清理及正式服务保留，结果为 `verified_native_run`。它明确区分原生通过和采集器失败。已保留[实际启动的采集器](run-final-candidate-launched.py)、[修正副本](run-final-candidate-corrected.py)及[只读验证源码](verify-final-candidate-result.py)。源码副本只用于审阅与溯源，不直接在 docs 目录运行。
