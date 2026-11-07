# R17：启动前校验服务端口

2026-11-07，从 `main@0f844cc` 续审。App 的 `ServiceConfiguration` 接受任意整数端口；旧 `startPrepared()` 直接启动 Helper，随后在就绪轮询中强制解包 URL。服务端已有的校验不能阻止 App 先启动和轮询，Foundation 对负数端口构造的就绪 URL 返回 nil。

[BackendController.swift](../../../../Sources/CyberAudio/BackendController.swift) 在读取配置后、发布配置及启动 Helper 前检查 `1...65535`，越界时进入启动失败状态并说明需要修正端口。

## 行为证据

[BackendLifecycleTests.swift](../../../../tests/BackendLifecycleTests.swift) 使用实际控制器、签名测试 bundle 和独立数据目录。新增用例在同一控制器上依次写入 `-1`、`0`、`65536`、`Int.max`，检查立即失败、未运行子进程、具体错误、配置／Token 原字节保留，以及异步让出后没有恢复运行。原五项停止、同步退出、重启、当前响应与端口冲突回归继续运行。

| 验证 | 结果 |
|---|---|
| 旧源码行为红测 | 编译成功，执行 exit 1；`-1` 未立即失败且 Helper 已运行，命中预定断言；原五项通过 |
| 修复后行为绿测 | 执行 exit 0；四种越界值及原五项生命周期回归全部通过 |
| 完整仓库检查 | `make check` exit 0，169.71 秒；37 项原生契约／0 失败／0 跳过、44 项工具、15 个原生渲染、六项配置／生命周期回归及全部行为／正式编译通过 |

[regressions.json](regressions.json) 记录红绿输出和实际编译源码摘要。红测在立即失败断言处退出；本次没有另行复现 App 崩溃。其他正数越界值可能仍能构造 Foundation URL，因此必须检查端口范围，不能仅用 URL 是否非空代替。

[checks.json](checks.json) 绑定完整检查结果与日志摘要，[review.json](review.json) 记录顺序代码复查，[source-sha256.json](source-sha256.json) 绑定实际验证的源码与检查入口。完整日志和临时测试 bundle 保留在任务私有目录。

此项是配置与启动行为修复。测试没有使用模型权重，也没有修改正式 App。短句 TTS 内容、严格 7200 秒 M4 负载、自然混说、真实录音／试听／跨应用听写和 Developer ID 公证／GUI 升级权限仍需各自完成验收。
