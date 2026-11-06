# R13 流式 TTS 晚期失败反馈与恢复

2026-11-07 续审，代码提交 `57c8152`。该修复保留真实失败原因、拒绝不完整音频并支持恢复，没有修复短句不结束或语音内容质量。

## 问题与实现

原 App 只在 PCM 字节迭代正常返回后查询请求状态，又把所有非完成记录归为“请缩短文本”；读取异常直接跳过查询。Coordinator 记录只有错误码，缺少与 WAV 响应一致的安全消息。

当前实现保存安全 message，未知底层 NSError 仍映射统一推理失败；类型明确的状态查询验证 16 位小写 hex 请求 ID、返回 ID 和 tts 类型。旧 Helper 只有 code 时提供中文兼容提示。独立临时 URLSession 对整个响应设置 3 秒上限，持续慢传输也不能延长诊断；正常 TLS 验证与取消保留。读取阶段异常时最多查询一次对应终态失败，查询不可用或没有对应失败则保留原错误。completed 记录不能使中断接收的部分 PCM 成为有效成品；用户取消、设备中断和播放器初始化失败不追加诊断。生成任务结束前保持更新等待。

## 回归证据

[真实红测确认](red-confirmation.json)记录 UI exit133 与原生 exit1／36 cases 中1 failure。UI 实际观察的是 URLSession 字节迭代正常返回、failed 记录被归为通用错误；该次没有观察到传输异常。原生红测确认缺少安全 message，不能把潜在异常分支描述为已在该次重现。

[完整检查](checks.json) exit 0／145.74 秒：36 原生契约、44 工具测试、14 个原生视图渲染，均无失败／跳过；全部行为分组和实际 App／Inference／Service／Assets 编译通过。包含 9 项真实 loopback 状态查询，覆盖消息、完成状态、旧协议、错误 ID／类型、未知状态、非法 ID、持续慢传输总截止与取消；3 类真实原生 HTTP 晚期故障 generation_limit／inference_timeout／inference_failed 保留与 WAV 一致的原因、不提供部分 WAV，并排空播放和更新等待。

先前依赖拉取与迁移模块缓存失败不是行为红测。[全量检查 setup 失败](check-setup-failure.json)为复制的 Git alternates 仍指向已删除旧工作区，工具阶段36通过／8 setup错误；[一次提前重试](check-interrupted-attempt.json)在修复生效前启动，已明确中止、exit-15，未加载模型。[修复回执](dependency-object-path-repair.json)只调整新私有副本的 Git 对象引用并恢复只读权限，SDK 源码与原 scratch 元数据保留。全部失败和中止回执保留，没有以它们充当功能红测或覆盖最终通过记录。

## 当前 App 与真实模型

[独立构建](build.json)使用新 bundle ID、目录和 18895 端口，ad-hoc 严格嵌套验签与真实 Assets prepare 通过。[原生报告](native-service.json)／[运行器](native-runner.json)均 passed、exit 0／172.33 秒：23 资产、16 格式、固定 100 条录音、9 音色、完整 PCM、3 轮断流恢复、更新暂停／恢复、EOF、单所有者锁、离线网络及实际 App 活动 TERM／KILL／重启。

[独立只读复核](independent-verification.json)确认同 M1 旧 Helper 的 100／100 原始 ASR 输出一致、九个 WAV 有效帧、最终组件／签名、冻结产物和清理边界；没有重新运行推理。

随后使用当前正式客户端源码和保留的私有 seed 196 旧 Helper，在 18896／独立目录进行[真实兼容验收](legacy-client.json)。实际客户端接收并播放部分 PCM，在 94.62 秒后显示“语音生成达到长度上限，请重试或分段生成。”；[客户端结果](legacy-client-result.json)确认 generation_limit 且旧记录没有 message 字段、没有可导出的部分 WAV，随后同一服务成功生成 76844 字节的 Serena WAV。原 Helper 摘要保留。观察到 AVFoundation 播放状态不等于人耳听辨或物理设备验收，这段时间也不是取消延迟。

## 保留与未通过项

[最终文件核验](verification.json)于 2026-11-06T23:27:38.537598+00:00 完成：97 个当前正式源码与构建一致，568 个原模型、8 个冻结产物、1357 个旧构建输入逐个摘要保持；生产实例／配置／Token 保留。所属模型进程、18895／18896 监听和临时请求已清理。QwenEngine、受校验 SDK 及默认 0.7 采样参数未变。

原严格M4 7200秒负载、短句TTS退化、自然混说／实体／英文数字质量、物理录音／试听／设备／睡眠／跨应用听写、公网完整下载和新目录推理、Developer ID／公证／实际GUI升级以及交付后的远程CI仍未通过。没有采用失败候选，没有安装、推送、PR或发布。

只归档明确白名单的JSON／源码和摘要，完整日志、权重、音频、配置、Token及二进制留在私有目录 `.context/robustness/stream-failure-20261107/`。脚本记录当时的私有路径，供审阅，不能从docs目录直接重做验收。[manifest.json](manifest.json)固定本目录文件摘要。
