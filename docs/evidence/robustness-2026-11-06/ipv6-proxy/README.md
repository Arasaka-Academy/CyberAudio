# R18：IPv6 下载代理地址

2026-11-07，从 `main@c6440e1` 续审。输入合法的 `http://[::1]:端口` 或 `socks5://[::1]:端口` 时，`URLComponents.host` 保留 URI 方括号。旧代码将 `[::1]` 直接写入 CFNetwork 代理配置，代理未收到请求。

[Assets.swift](../../../../Native/Sources/CyberAudioCore/Assets.swift) 改用 `URL.host` 的主机表示，去除 IPv6 URI 方括号。原有协议／端口／凭据／路径限制继续执行，HTTP 和 SOCKS5 使用同一解析结果。

## 独立诊断与实际入口回归

独立原生 URLSession 探针分别将 `[::1]` 和 `::1` 交给系统，两个 IPv6 loopback 桩均不转发上游。前者未建立连接，后者捕获准确的 `CONNECT cyberaudio-proxy-test.invalid:443 HTTP/1.1`。两者却返回相同的 CFNetwork 310，必须用桩端观察判断是否正确路由，不能只检查错误类型。见 [probe.json](probe.json)。

[ServiceTests.swift](../../../../Native/Tests/CyberAudioCoreTests/ServiceTests.swift) 的新用例调用实际 `AssetStore.download`，使用独立临时根目录和 IPv6 socket。HTTP 桩验证 Hugging Face 的 CONNECT 目标；SOCKS5 桩完成方法选择，验证 ModelScope 的完整域名和 443 端口。两路均在观察后主动拒绝，不连接上游，并检查配置／Token 原字节保留且未发布未下载的资产。

| 验证 | 结果 |
|---|---|
| 旧源码红测 | 编译成功，执行 exit 1；38 项契约中新增 HTTP／SOCKS5 两个路由断言失败，原 37 项通过，0 跳过 |
| 修复后契约 | 38 项通过，0 失败、0 跳过；原 IPv4 代理和非法凭据／地址回归保留 |
| 完整仓库检查 | `make check` exit 0，160.83 秒；38 项原生契约、44 项工具、15 个原生渲染及全部行为／正式 App／Service／Assets 编译通过 |

[regressions.json](regressions.json) 记录红绿结果，[checks.json](checks.json) 绑定完整检查与日志摘要，[source-sha256.json](source-sha256.json) 绑定实际源码与入口，[review.json](review.json) 记录顺序复查。生产配置／Token／所有者锁的大小、mtime 和 SHA 全部保留。原始探针源码、完整日志和生产文件保护摘要仅留在任务私有目录。

此修复不解释旧 IPv4 代理的 CFNetwork 310，也不扩大重试或变更 TLS。代理拒绝回归不替代完整 SOCKS5 大文件、公网模型下载或首次安装验收。短句 TTS、严格 7200 秒 M4、自然混说、真实麦克风／试听／听写和 Developer ID／GUI 升级条件继续保留。
