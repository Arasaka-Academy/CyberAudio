# 引擎就绪轮询的进程归属（R15）

`b06dc7b` 修复停止／重启时旧 `/ready` 响应覆盖当前状态的问题。完整检查与隔离当前字节的 App 验证通过；短句内容、严格 M4 两小时、公网完整下载、现场与正式分发门槛继续保留。

## 复现和修复

网络响应完成后，其 MainActor continuation 可能仍在排队。原轮询只在发请求前检查取消，收到响应后直接修改 `status`、`busy` 和启动指标；停止时取消 Task 并不能撤回一个已完成的响应。

在产品代码修改前，用原 `BackendController` 的私有副本只增加两行可注入 URLSession 的构造入口。测试通过 URLProtocol 控制 `/ready` 的完成时序，并实际启动和停止原生测试 Helper。先阻塞 MainActor，等 URLSession 的任务完成，再停止／重启并释放 actor。三项回归失败，当前有效响应与当前端口冲突两个控制通过，见 [red.json](red.json)。

| 场景 | 旧实现的状态序列／结果 |
|---|---|
| 正常停止 | stopping → ready → stopped，停止中短暂恢复就绪 |
| 同步退出清理并完成停止 | stopping → stopped → ready，进程已不存在 |
| 同步清理后重新启动 | stopping → stopped → loading → failed，新实例被旧响应误判为端口冲突 |

正式轮询现在绑定创建它的 Process 和 instance，在成功返回后检查 Task 取消与进程归属；失败分支和下一轮也检查同样的归属。当前响应仍可更新 busy／启动指标，当前实例不符仍显示端口冲突。[五项回归](green.json)全部通过，且已加入 `scripts/test_audio_workflow.sh`，随 `make check` 必跑。

可在具备项目依赖的 checkout 中执行 `rtk proxy bash scripts/test_audio_workflow.sh`。它使用专用临时 App、数据目录和端口，结束后清理测试资源。受控 `/ready` 是 URLSession 边界回归，不能称为真实服务网络验收；后面的独立 App 门槛补足真实 HTTP／模型／子进程路径。

## 完整检查与当前 App

[make check](checks.json) exit 0，315.96 秒：36 项原生契约、0 失败／跳过，44 项工具测试、14 组渲染、全部行为组及正式 App／Inference／Service／Assets 编译。99 个源码／构建配置文件摘要在测试和验收间一致，六个冻结 App／DMG 文件保留。上游 SwiftPM 弃用与未处理 README 警告仍保留，未宣称整个构建无警告。

[当前隔离 App](build.json)采用独立 bundle ID、数据目录和 18901 端口，通过严格嵌套 ad-hoc 验签与 Assets prepare。完整原生驱动和外层运行器均通过，153.18 秒；包括固定 100 条语料、16 种 STT 格式、9 音色、完整 PCM、三轮断流恢复、更新暂停／恢复、EOF、单所有者、离线沙箱，以及实际 App 在推理中 TERM／KILL 后的 Helper 回收与再次启动。

[独立文件核验](verification.json)确认同 M1 旧 Helper 的 100／100 原始 ASR 文本相同，九个 WAV 的头部／帧数／摘要有效，打包字节与源码快照匹配；所属进程已退出、端口关闭、请求临时目录清空。正式服务的 instance、配置和 Token 摘要在前后相同，没有替换已安装 App。

## 失败记录与边界

[首次尝试](first-attempts.json)保留观察器未收到回调、测试可见性编译错误，以及共享待处理响应污染控制组的记录。只有按每例独立请求标识隔离后的三项行为失败／两个通过控制计入红测；修复前后测试源码 SHA 相同。完整日志、二进制和原始音频留在 `.context/robustness/readiness-ownership-20261107/`，本目录仅保留摘要与安全投影。

本次没有启动新的质量候选、采样矩阵、严格 M4 负载或公网续传。远端上轮记录为十四个最终文件和 176MiB 下载断点；本轮未连接 M4 或启动下载进程，不更新其终态。真实麦克风、人耳试听、跨应用／IME／物理设备／睡眠、Developer ID 公证和 GUI 升级仍未接受；总体目标未完成。
