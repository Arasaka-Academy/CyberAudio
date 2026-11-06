# 输出设备恢复续审证据

对应 [审查报告 R8](../../../reviews/2026-11-06.md)。这是 R1–R7 修复提交后的追加修复；父目录已有的源码、构建、实际模型、持续负载和 DMG 回执保留原值，不用于证明新增 App 代码已经通过相同实际验收。

回归使用真实 `AVAudioEngine`／`AVAudioPlayer` 和原生 HTTP 测试服务。测试主动停止当前引擎、发送系统公开的配置通知，再检查播放／生成状态、成品音频、错误反馈、旧对象事件隔离及真实更新租约暂停／恢复。修复前在“停止的 PCM 引擎仍被标记为播放中”断言失败，修复后通过。

停止会先撤销观察者和播放会话，再释放音频对象。通知从投递线程异步转到主 actor，不让音频线程同步等待主线程停止引擎。取消生成仍由任务退出释放 `generating`，避免更新提前开始。完成的 WAV 可保存或重播；解码失败和失败完成会报告错误，成功重播只清除语音错误。

测试不访问物理麦克风，也不验证实际拔插、蓝牙路由切换、睡眠恢复或人耳音质。该阶段本机交互会话锁定。后续已完成固定真人语料和 M4 完整服务回归，新增证据见 [target-validation](../target-validation/README.md)；Developer ID、公证、跨应用听写、完整公网下载等当前状态见总报告。

复现：

```bash
rtk proxy env CYBERAUDIO_NATIVE_SCRATCH=/absolute/path/to/native-build \
  bash scripts/test_audio_workflow.sh
rtk proxy env CYBERAUDIO_NATIVE_SCRATCH=/absolute/path/to/native-build make check
```

单项原始日志为 `/tmp/cyberaudio-device-recovery-red.log` 和 `/tmp/cyberaudio-device-recovery-green.log`，完整检查日志为 `/tmp/cyberaudio-device-recovery-check.log`。本目录保存摘要与摘要值，不包含配置、Token、证书、模型或原始完整日志。

| 文件 | 含义 |
|---|---|
| [checks.json](checks.json) | 先失败后通过的回归记录、完整 `make check`、静态检查和顺序源码复查 |
| [source-sha256.json](source-sha256.json) | 与上一阶段相同范围的 96 文件快照，只有 AudioWorkspace 和音频集成测试内容改变 |
| [build.json](build.json) | `e2f34df` 后构建的 App、严格嵌套 ad-hoc 验签、资源 Helper 实际启动和各组件摘要 |
| [package.json](package.json) | 新本地 DMG 的外部回执，生成于映像之后，不嵌入其自身 |

完整检查再次通过：28 项原生契约、29 项现役工具测试、14 个原生渲染和全部行为回归。工作流 lint、entitlements 和空白检查通过。此前 App 已存档并逐项核对摘要，旧 DMG 保留；新 App 的主程序变化，Service、Assets helper、Metal 和 Info.plist 字节与此前实际模型验收的产物完全一致。未将此前 App 的真人操作或升级验收算作本次通过。
