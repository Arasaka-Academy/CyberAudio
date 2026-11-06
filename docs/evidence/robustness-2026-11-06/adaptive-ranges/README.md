# 弱连接下的小分段恢复与最终构建验收

代码提交 **`f42bafe`**。此前同一路径 8MiB Range 未完成，而同起点 1MiB Range 在 curl／URLSession 均完整成功、SHA 一致，见[前一阶段探针](../retry-and-matrix/network-small-range-probes.json)。本次据此增加一次分段降级，没有更换模型、采样参数或现有代理配置。

## 修复与回归

大于 1MiB 的分段在耗尽两次超时／连接中断重试后，同一资产剩余部分改用 1MiB。持久前缀保持不动，小分段仍最多重试两次；持续失败会返回控制权。HTTP 错误、DNS／连接建立错误、TLS、范围／长度／摘要错误不触发缩小。取消、最终完整 SHA 和排他发布规则保留。

三个真实 socket／URLSession 回归验证：大分段反复截断后从正确前缀完成小分段，持续的小分段失败停止且保留前缀，以及 HTTP 503 保持原重试上限。先观察到两个预期失败，再全部通过，见[检查回执](checks.json)。随后完整 `make check` exit 0：**36 项原生契约／0 失败／0 跳过、39 项工具测试、14 个原生视图渲染**，所有准备／音频／听写／剪贴板／更新行为和实际 App／Inference／Service／Assets 编译通过。

## 最新字节的实际模型验收

隔离 App bundle ID `local.cyberaudio.validation.finalbytessmallrange`，独立数据目录／18882 端口。严格嵌套 ad-hoc 签名与 Assets prepare 通过；最终组件、输入二进制及锁文件摘要见[构建回执](build.json)，源码见[内容摘要](source-sha256.json)。原 `dist` App、DMG 和正式安装均保留。

[原生报告](native-service.json)和[运行器](native-runner.json)均 **passed，驱动 exit 0**，约 156.58 秒；实际 Service SHA `e9a9c159eb56150b07a59729c70e73b021282c07fc2331dcb468e8cdd98ad30e` 与签名后的候选一致。包含 23 模型资产校验、16 格式、固定 100 录音、9 音色、完整 PCM、断流取消／恢复、更新暂停／恢复、EOF 和活动 App TERM／KILL／重启。

[独立复核](independent-verification.json)再次检查全部录音 SHA／参考／语言、九个 WAV 完整帧、最终字节和签名、进程／监听／临时请求清理及生产保留；**100／100 原始识别结果与此前同 M1 旧版一致**。本次外层采集器正常通过，前一阶段的采集错误仍保留原值。没有重复运行同一模型负载以挑选成功结果。

## 实际公网结果仍失败

M4 使用从上述最终 App 提取的同一签名 Assets，SHA **`06698db17958ab5adc2da7d1a1d9b23cbc34c57b67e1b1fb5cf7e9fd0e7adb1c`**；资源 bundle、锁文件和传输摘要均复核。单次一小时有界下载于 **14:41:25 UTC** 开始，但在 **14:41:40 UTC 退出 1**，安全错误为“无法验证安全下载连接，已保留进度；请检查网络或代理后继续。”，见[终态](m4-download-terminal.json)。

14:43:48 UTC 复核 runner／Assets 均已退出，14 个最终文件共 2,469,536,771 字节、112MiB 分段、生产实例／配置／Token 保留。TLS 失败未触发分段降级，符合上述策略；不能把该错误具体归因为某个证书、代理或服务端。没有放宽 TLS 校验，也没有把受控恢复或单次 1MiB 探针当作完整下载成功。

因此，完整 **23 文件／5,547,997,530 字节** 的公网下载和新目录推理仍未验收。此前两小时 M4 负载 `generation_limit`、中英完整性、物理音频／桌面听写、Developer ID 公证／GUI 升级、远程 CI 也仍是各自独立的门槛。

采集源码副本只用于审阅与溯源，不直接在 docs 目录运行。模型、音频、原始日志与凭据留在私有目录，`SHA256SUMS.txt` 覆盖本阶段结构化证据与源码。
