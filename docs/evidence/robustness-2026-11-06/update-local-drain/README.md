# R16：更新握手后重新检查本地任务

2026-11-07，从 `main@67e13bf` 续审。`UpdateGate` 最初只在第一次异步引擎握手前等待本地任务。握手期间准备界面仍能启动下载；旧代码接收到 idle 后直接触发安装，忽略准备控制器仍持有子进程。

## 修复和行为回归

[UpdateGate.swift](../../../../Sources/CyberAudio/UpdateGate.swift) 在每一轮握手前等待当前本地任务，握手返回后再次检查。引擎和本地任务都空闲时，才在 MainActor 内设置允许终止并调用安装回调；检查与状态转换之间没有新的异步等待。

[PreparationTests.swift](../../../../tests/PreparationTests.swift) 使用真实 `PreparationController`、真实受控子进程和延迟返回的握手。在握手已开始后启动下载，随后返回 idle。旧源码在“下载仍忙时更新必须等待”的断言失败，修复后通过；取消下载仍保留 busy 归属，子进程退出后仅调用一次安装回调。全部原有准备行为与四组更新门闩回归仍通过。

| 验证 | 结果 |
|---|---|
| 行为红测 | 编译 exit 0，执行 exit -5，明确命中预定的提前安装断言；不是编译失败 |
| 行为绿测 | 准备回归与四组更新回归编译／执行均 exit 0，stderr 为空 |
| 完整仓库检查 | 完整 `make check` exit 0，572.60 秒；37 项原生契约／0 失败／0 跳过、44 项工具、15 个原生渲染，全部行为与正式 App／Service／Assets 编译通过 |
| 文件保护复核 | 98 个编译相关源文件仅预期的两项变化；1,350 个共享构建输入、1,026 项 metadata、六个冻结产物、既有 App 的 160 个文件／链接及生产实例／配置／Token 保留 |

首轮完整检查中，外置到隔离项目同级的 scratch 导致 SwiftPM 旧 native 执行方式传入不可从当前目录解析的 argv0。跨进程 tokenizer 测试因此找不到自身，37 项契约有 1 项失败。原退出／日志保留；仅把本任务构建目录移入隔离项目，并归档移动前的模块缓存后，再运行相同完整检查。没有修改测试、断言、选择或跳过条件。

## 证据和边界

[regressions.json](regressions.json) 保留红绿行为，[preservation.json](preservation.json) 记录保护复核；[checks.json](checks.json) 与 [review.json](review.json) 记录完整检查及四个视角的顺序审查；[source-sha256.json](source-sha256.json) 绑定已检查源码，[original-receipts.json](original-receipts.json) 关联全部红绿／初次失败／最终回执。生产原始记录、完整日志、配置、Token、内部路径及二进制仅保留私有记录。

本阶段安装回调只增加计数，没有安装更新或替换 App。没有启动模型质量候选、重跑严格 M4 负载或改变 SDK／采样参数。实际 Sparkle GUI 升级及权限保留、短句 TTS 内容、自然混说、原严格 7200 秒 M4、物理麦克风／人耳／桌面听写和 Developer ID 公证仍需各自验收。另见[空缓存预检阻塞](../empty-cache/README.md)。
