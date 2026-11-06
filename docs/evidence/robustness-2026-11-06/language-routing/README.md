# R11 显式英文路由及最终 App 回归

代码提交 **`5479313`**。修复 eric／dylan 在显式英文下仍无条件套用四川／北京方言语言 ID 的问题；Qwen 参考实现只在中文／自动语言下覆盖。正式主／辅助采样温度继续为 0.7，没有采用前一阶段的私有候选。

## 实际模型红绿对照

[预定四个样本](plan.json)固定文本 `1234`、种子 31，分别选择两种音色和英文／中文。修复前同一音色的两种语言 token／PCM 完全相同，[两个边界断言均失败](behavior-red.json)。修复后使用实际生产 SDK 的 `generateManaged`，两种音色的英文输出均变化，中文方言 token／PCM 均与基线逐字节一致，[四项断言通过](behavior-green.json)。这验证语言选择边界，不证明听感或英文内容正确。

适配脚本校验固定上游原文，只允许已知旧 managed 摘要升级到当前精确补丁；不接受其他源码修改。实际命令的旧版本升级测试先失败，修复后与重复执行／篡改拒绝一起 4／4 通过，ASR 字节保持不变。[完整 make check](checks.json) exit 0：**36 原生契约／0 失败／0 跳过，40 工具测试／0 失败／0 跳过，14 个视图渲染**；全部行为分组及 App／Inference／Service／Assets 编译通过。

## 英文数字质量未接受

八个修复前后输出全部经最终 App 的同一 ASR 自动检测语言，见[完整原始回读](numeric-auto-asr.json)。旧英文与中文请求均被识别为中文；修复后中文请求保持原结果，而两个英文请求被自动识别为 Hindi／中文，没有确认英文输出正确。ASR 自身可能误判，不能仅凭回读认定实际发音语言；也不能把音频变化当成正确英文的证明。所有八个 WAV 的有效帧、采样格式、载荷与原 PCM 摘要独立匹配，仍需要英文数字专项质量和人耳对照。

## 最终签名字节的完整回归

独立 bundle ID `local.cyberaudio.validation.finalbyteslanguagerouting`，独立数据目录／18882 端口。[构建回执](build.json)记录最终 App／Service／Assets、Metal 和模型锁文件摘要；严格嵌套 ad-hoc 验签和资源 prepare 通过。最终 Service SHA `b33c26734ee4a18d48f9fa7efe5c018f88fa6ae7f4ce18f3e59d210c36893215`；Assets 与此前公网失败候选字节相同，为 `06698db17958ab5adc2da7d1a1d9b23cbc34c57b67e1b1fb5cf7e9fd0e7adb1c`。

[实际原生报告](native-service.json)与[外层运行器](native-runner.json)均 passed／exit 0，约 **164.53 秒**。包含 23 资产检查、16 个音频格式、固定 100 条录音、九音色 WAV、完整 PCM、三轮断流恢复、更新暂停／恢复、EOF、App 活动推理时 TERM／KILL 和重启。

[独立只读复核](independent-verification.json)没有重跑负载，确认 100 条录音摘要／参考／语言匹配，**100／100 原始 ASR 输出与同 M1 的旧版逐字一致**，九个 WAV 完整帧、最终签名和组件字节、进程／监听／临时请求清理、生产实例／配置／Token 及冻结 dist App／DMG 保留。额外八次 ASR 之后也正常退出并清理，见[追加字节复核](verification.json)。

完整服务回归通过不代表上面的英文数字质量通过。M4 严格两小时、自然混合语言、公网完整下载、物理麦克风／输出／桌面听写和 Developer ID 公证／GUI 升级门槛均仍保留。本阶段没有安装、推送、PR 或发布。

原音频、权重、Token 和完整原始日志保留在私有工作区。采集源码副本供审阅，不直接在 docs 中运行；`SHA256SUMS.txt` 对应本阶段文件。
