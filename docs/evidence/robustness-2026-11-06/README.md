# 2026-11-06 工程完善与验收证据

本目录对应 `codex/robustness`，审查基线 `96210dd`；修复及图标主题提交已合并推送到 `main@fe9a1bd`，对应远程 Checks 通过。问题、实现和剩余验收见[审查报告](../../reviews/2026-11-06.md)。这里只保留经过检查的结果、公开模型／语料元数据和源码摘要，不包含配置、Token、证书、私钥、完整日志、原始录音或模型权重。

## 证据索引

| 文件 | 含义 |
|---|---|
| [invalid-port/](invalid-port/README.md) | R17：App 启动前拒绝四种越界服务端口，真实控制器红绿回归保留配置／Token，原五项就绪归属和进程生命周期回归通过 |
| [official-tts-reference/](official-tts-reference/README.md) | 官方 BF16 CPU 完整参考的源码／12 文件清单、92 包离线环境和零模型沙箱／算子预检；三种传输终态保留，完整权重未取得，七例未运行 |
| [update-local-drain/](update-local-drain/README.md) | R16：握手期间启动准备子进程的行为红测与修复，异步返回后重新检查本地排空，取消清理结束才安装一次 |
| [empty-cache/](empty-cache/README.md) | M4 空缓存验收在只读预检被空间条件拦截：13.37 GiB < 15 GiB，FFmpeg 文件存在，零下载／模型启动／目标机写入；未放宽条件或清理数据 |
| [codec-sum/](codec-sum/README.md) | 官方单次求和与 MLX BF16 左折叠的差异：367 帧重建、CPU／GPU 算子对照及七对完整生成／46 次双路 ASR；显式 FP32 累加通过结束／时长条件，但 dylan 数字回读退化且 seed117 仍有 3 个编辑，候选未采用或扩大 |
| [repeated-code/](repeated-code/README.md) | 私有连续主码排除小样本的已知失败准确回读；完整 144 候选／144 当前路由基线、590 次双路 ASR，两个路由均 2 对改善／142 持平／0 回退，141 条完整字节保留；另一种子仍 29.2 秒且回读有误，时长条件失败，不采用或重启严格负载 |
| [natural-padding/](natural-padding/README.md) | 固定七条短录音、原始／尾部／头部补零共 21 次原生请求；14 项基线匹配，总编辑数 11／11／10，但已知空转写未修复，未扩大或采用；当前源文件、App、冻结文件与生产保留 |
| [sustained-transfer/](sustained-transfer/README.md) | M4 单一 SSH 路径的四段预定持续检查：首段 8MiB 默认 TLS／SHA 通过，第二段 45 秒超时后按规则停止；未重启完整下载，14 个最终文件／176MiB 断点及生产保留，所属转发清理通过 |
| [public-download-refresh/](public-download-refresh/README.md) | 2026-11-07 新路径原生 1MiB／默认 TLS／SHA 通过；同一签名 Assets 实际续传增加 64MiB 后随 SSH 中断退出 1，14 个完整文件／176MiB 断点及生产保留，所属进程和转发确认清理；完整下载和新目录推理仍未通过 |
| [checks.json](checks.json) | R1–R7 阶段完整 `make check`：28 项原生契约，29 项现役工具测试，14 个原生渲染，行为回归和实际推理 Helper 编译；工作流／entitlement／空白检查 |
| [build.json](build.json) | R1–R7 阶段设备、源码提交、本地 App 与两个 Helper／Metal 的大小和 SHA；明确本地 ad-hoc、未公证、未交付 |
| [source-sha256.json](source-sha256.json) | 本轮本仓库 App、Native、测试、工具、打包、锁文件与工作流的内容快照；不含下载的上游源码 |
| [native-service.json](native-service.json) | 最终真实模型／离线服务／格式／音色／断流／退出重启验收 |
| [synthetic-corpus.json](synthetic-corpus.json) | 本轮系统 `say` 中英文输入来源、参考文本及音频 SHA；原始录音为合成语音 |
| [development-signing.json](development-signing.json) | 最终 App 副本的实际 Apple Development 签名、强化运行时、内置工具启动结果；未公证 |
| [cross-volume.json](cross-volume.json) | 最终资源 Helper 在独立 APFS 镜像完成排他原子发布的结果 |
| [soak.json](soak.json) | 同一实际模型实例 602.69 秒、210 次 STT、210 次完整 TTS、42 次断流恢复、21 次更新暂停／恢复与逐轮内存指标 |
| [corpus-availability.json](corpus-availability.json) | 固定真人语料的本地缺失和两条固定数据源连接超时记录 |
| [first-run-light.png](first-run-light.png)、[first-run-dark.png](first-run-dark.png) | 受控准备状态在 560 × 440 pt 下的实际原生离屏渲染 |
| [package.json](package.json) | 本地 DMG 生成后的外部验收回执：映像／SHA、只读挂载、App 嵌套签名和指定文档／二进制逐字节检查 |
| [playback-recovery/README.md](playback-recovery/README.md) | R8 设备中断续审的独立回归、源码快照与后续构建证据；上列历史回执未被替换 |
| [target-validation/README.md](target-validation/README.md) | 后续固定 100 条真人语料、M1 新旧同设备对照、M4 完整服务验收、36 项工具测试，以及尚在运行的两小时负载／公网下载快照 |
| [quality-boundaries/README.md](quality-boundaries/README.md) | 实际 ASR／TTS 长度和取消边界，30 个受控专名／数字／噪声输入的新旧对照、已排除的生成器错误，以及中英拼接整段遗漏的质量限制 |
| [extended-validation/README.md](extended-validation/README.md) | 首次 M4 两小时尝试的失败终态、失败记录修复及 37 项工具回归、M1 短句 TTS 150 次诊断、中英统一 RMS／顺序对照，以及后续重测和下载快照 |
| [reporting-transport/README.md](reporting-transport/README.md) | 补全 R9 的错误响应／PCM 中断记录路径，10 项目标回归及最新 39 项现役工具检查 |
| [model-diagnostics/README.md](model-diagnostics/README.md) | 固定种子复现短句无 EOS，私有实际 HTTP 确认 generation_limit／部分 PCM 及恢复；采样追踪、温度候选与混合语言对照，默认参数未修改 |
| [download-terminal/README.md](download-terminal/README.md) | 公网下载追加三次失败终态、2.21GB 分段与生产保留复核、成功 Range 探针和后续有界续传身份 |
| [retry-and-matrix/README.md](retry-and-matrix/README.md) | R10 分段重试及 R8 回调顺序修复，33／39／14 完整检查；M4 严格重测 generation_limit 失败，288 次配对生成／294 次回读，以及新旧下载失败终态与有界传输对照 |
| [final-candidate/README.md](final-candidate/README.md) | `939050f` 阶段隔离 App／Service／Assets 的实际完整验收 exit 0、同 M1 100／100 输出一致；保留外层采集器字段错误，并独立复核字节、报告、WAV、进程清理和生产保留 |
| [adaptive-ranges/README.md](adaptive-ranges/README.md) | `f42bafe` 大分段失败后一次 1MiB 降级、36／39／14 完整检查；新最终 App 原生／运行器实际通过和 100／100 同 M1 一致；同一 Assets 的 M4 TLS 失败终态 |
| [subtalker-followup/README.md](subtalker-followup/README.md) | 仅私有辅助生成器温度变化，六个重复对照及 144 新候选／144 旧基线、294 ASR；总指标改善但 11 对变差，默认值未修改 |
| [language-routing/README.md](language-routing/README.md) | R11 方言音色显式英文路由红绿修复及 36／40／14 完整检查；最新 `5479313` App 实际完整验收与同 M1 100／100 一致；英文数字自动回读仍未确认正确 |
| [text-layout/README.md](text-layout/README.md) | 当前 R11 私有 SDK 的两种官方文本布局：五组配对／十次生成、17 次最终 App ASR；已知失败能结束但正常控制回读回退，数字质量仍未接受，未采用；追加桌面工具不可用和字节保留证据 |
| [model-preservation/README.md](model-preservation/README.md) | R12 移除 SDK 异常加载时整目录删除；真实权重加载红绿对照、八项适配器回归和 36／44／14 完整检查；`b5607f2` 当前 App 完整原生验收及独立复核通过，同 M1 旧版 100／100 一致 |
| [stream-failure/README.md](stream-failure/README.md) | R13 流式晚期失败保留具体原因、旧 Helper 兼容、完整响应超时和部分音频拒绝；36／44／14 完整检查及 9 项状态查询／3 类晚期故障；`57c8152` 当前 App 完整验收和同 M1 100／100 一致，真实固定失败／同实例恢复及全部文件保留复核 |
| [post-delivery/README.md](post-delivery/README.md) | 36 个提交合并推送到 main、`fe9a1bd` 远程 Checks 成功及主分支必需检查回读；状态栏主题修复的八组原生渲染和实时切换；正式 App／配置保留 |
| [reference-parity/README.md](reference-parity/README.md) | 五个固定样本、146 个强制 codec 步骤的 Swift／未修改 Python 参考前向对照；750 项张量比较数值一致、误差为零，原失败前缀和生产数据保留；不包含采样、完整解码或质量通过结论 |
| [sampling-order/README.md](sampling-order/README.md) | 实际采样器的 2336 次重放及 bfloat16／top-k 顺序差异；当前语言路由 144 对、294 次 ASR，无 EOS 减少但仍有 5 对回读回退，候选未采用；正式源码与生产数据保留 |
| [reference-generation/README.md](reference-generation/README.md) | 七次未修改 Python 完整生成、观察／无观察控制与九次 ASR；相同完整 codec 的解码对照定位 72 帧窗口差异，并保留已知失败第 84 帧之后的未归因差异；未采用新策略 |
| [complete-codec/README.md](complete-codec/README.md) | 六组完整原生码／PCM 重现旧候选；5,872 个码值对照发现两次重复各 97 项不同；1,859 项完整前向比较中 42 项不同，明确短前缀零误差的范围 |
| [layer-parity/README.md](layer-parity/README.md) | 263 步原生重放及参考观察控制、3,168 项边界比较；定位 RoPE 逆频率在位置 104 的舍入传播，只替换参考进程内的该数组后完整 1,859 项比较零误差；正式策略未改 |
| [power-dispatch/README.md](power-dispatch/README.md) | 单个私有 C++ 对象的 float32 Power 分派对照：预编译标量值与 Python 全部一致；十二次原采样器生成／28 次 ASR 后，失败种子仍输出 84.72 秒且回读为空，未采用候选 |
| [transport-refresh/README.md](transport-refresh/README.md) | 最新 M4 URLSession 1MiB 探针仍在 TLS 失败；四个有界 HEAD 控制显示代理路径的两个站点均 TLS 失败、HF 直连超时；已有文件／分段和生产保留，未重启完整下载 |
| [asr-reference/README.md](asr-reference/README.md) | 十二个固定混合／单语输入完成 84 次实际 ASR；原生／未修改 Python 文本 10／12 一致。单变量注入原生 Mel 重现一个加噪样本的中文遗漏，注入 encoded 后 12／12 匹配；双方共有遗漏／翻译与数值敏感性分别保留，质量门槛未通过 |
| [asr-mel/README.md](asr-mel/README.md) | 无模型 DSP 拆解在全部十二个输入上复现原生／参考 Mel；定位窗口／滤波常量及数值流程的多处差异，266 项独立重算通过。常量替换不能建立一致，未据此改变实现或接受质量门槛 |
| [asr-segmentation/README.md](asr-segmentation/README.md) | 固定音频能量分段完成 28 次实际 ASR；八个混合文本恢复双脚本，总诊断编辑数 154→92，但两条件数字／实体拼写导致编辑数增加，未通过预定条件。评分限制和全部原值保留，未采用或扩大候选 |
| [natural-ascend/README.md](natural-ascend/README.md) | 正常 TLS 取得并验证 ASCEND 固定归档；完整 1,315 条自然测试集、373 条混说、零重试／跳过，整体／混说 MER 11.00%／11.41%，独立复算全部音频和编辑距离；保留实际翻译、专名及空转写问题，原质量门槛未关闭 |
| [natural-reference/README.md](natural-reference/README.md) | 全量 1,315 条未修改 Python 参考与原生基线对照，1,291 条规范化相同，四个自然问题原样复现；独立重算 2,630 个编辑距离；七条固定音量配对仍未解决空输出，未扩大或采用候选 |
| [acceptance-audit/README.md](acceptance-audit/README.md) | 剩余条件自动复核：M4 同一正常 TLS Range 仍失败，原生会话明确当前锁屏，无 Developer ID，新语料路径未成功；96／568／8／1357 文件摘要及既有完整测试日志核验通过。没有重复推理、修改默认值或接受尚未通过的门槛 |
| [modelscope-download/README.md](modelscope-download/README.md) | 显式固定 ModelScope 源、四段直接原生资格和一次完整公网续传；23 文件／5.55GB 摘要及 M4 新目录当前 App 验收通过；M1／M4 各自同设备 100／100 原始文本一致，37／44／15 检查及独立保护／清理复核 |

[readiness-ownership/README.md](readiness-ownership/README.md)记录 R15：三种旧响应覆盖状态的行为红测、五项归属／控制回归、完整 36／44／14 检查和当前隔离 App 的 153.18 秒真实模型／生命周期验收。修复后固定 100 条与同 M1 旧版原始输出相同。

最新 [modelscope-download/README.md](modelscope-download/README.md)记录显式固定下载源：M4 正常 TLS 直接续传 exit 0，复用原 14 个文件／176MiB 分段后，23 文件／5.55GB 全部独立摘要通过；使用这些文件的新目录当前 App 完整验收也 exit 0。M1／M4 分别 169.013／193.521 秒，各自同设备旧报告 100／100 原始文本一致；本阶段 37／44／15 检查和全部保护／清理通过。原质量、严格 M4、现场与分发门槛仍未完成；原 Hugging Face、空缓存重传及 FFmpeg 从零安装另计。

后续 [codec-sum/](codec-sum/README.md)完成官方求和精度调查：全部 367 个原生快照精确重建，显式 FP32 累加在实际 Swift／MLX CPU 与 GPU 上匹配 PyTorch CPU。七个私有候选均自然结束且最长 5.12 秒，但完整双路回读存在数字退化及 seed117 开头错误；按原条件拒绝，未改正式策略或重启大队列／严格负载。

完整原始记录保留在开发工作区 `.context/robustness/`，原始检查日志在 `/tmp/cyberaudio-robustness-*.log`，不会作为分发材料上传。

## 实际服务

硬件为 Apple M1 Max／32GiB，macOS 27.0.1。使用 R1–R7 阶段本地 App 的 Service，SHA 在 `build.json`、`development-signing.json` 与 `native-service.json` 中一致。测试 App 采用独立 bundle ID、数据目录和 18865 端口，前后确认 8765 正式服务实例身份一致。

验收包含 23 个模型资产与 tokenizer 校验、禁止主动出站网络、单所有者锁、中英文各八种输入格式、九音色 24kHz／单声道／PCM16 WAV、完整 PCM、三次断流取消与恢复、更新租约暂停／恢复、stdin EOF，以及真实 App 在活动推理中收到 TERM／KILL 后的子进程回收与重启。

复现驱动为 [verify_native.swift](../../../scripts/verify_native.swift)，需本地固定模型和带 SHA 的 `corpus.json`：

```bash
rtk proxy swiftc -parse-as-library -swift-version 5 -target arm64-apple-macos26.0 \
  scripts/verify_native.swift -o .context/verify-native
# 必须使用独立 bundle ID 的 App 副本、专用新目录与非正式端口。
rtk proxy .context/verify-native --app /absolute/path/to/isolated/CyberAudio.app \
  --root /absolute/path/to/new-data --models /absolute/path/to/models \
  --corpus /absolute/path/to/corpus --port 18865
```

测试会启动和终止指定 App，拒绝默认正式数据目录、8765、占用端口及没有验收标记的已有测试目录。进程参数禁用该次测试的全局听写和自动更新，不写正式 App 的偏好。

持续负载另用 18866 端口和全新目录，直接启动相同最终 Service 与离线沙箱。同一实例交替识别两条合成输入，轮换九个音色和 WAV／PCM，每五轮主动断流并检查 `cancelled`，每十轮验证更新暂停返回 503、取消租约后恢复。逐轮记录 TTS 的 Metal／RSS 指标和进程当前 RSS；最后检查实际 `tmp` 下没有 `request-*`，关闭 stdin 并等待进程退出，核对正式服务实例保持一致。测试无计划空闲间隔，但包含本地 HTTP、FFmpeg 和状态查询，不能视为纯 GPU 满载基准。

## 下载和签名

受控 HTTP 回归使用真实 socket 与 URLSession；SDK／推理性能不由这些下载测试证明。跨卷实测使用已验证的 1008 字节固定 ASR README 暂存文件、本机暂存卷、64MiB 独立 APFS 镜像和实际内置资源工具。两个卷的 device ID 不同。后续网络请求被本地 CONNECT 代理保持，再取消工具；代理不转发网络。摘要、最终文件、暂存消费、目标私有文件清理、Token 保留及镜像卸载均通过。

实际开发证书签名复制最终 App 并使用独立 bundle ID；签名由内到外执行，验证强化运行时和严格嵌套签名，并启动打包的 Assets 工具。证书姓名、团队、Keychain、签名凭据均未保存在本目录。

分发失败测试分别模拟公证拒绝、缺少 staple 票据与 Gatekeeper 拒绝，要求中止后续分发。它们验证失败处理，不是 Apple 真实公证的证据。

## 验收边界

以下条目描述本目录 R1–R7 阶段的历史边界。后续已恢复全部 100 条固定录音，并完成 M1／M4 真实语料回归；最新 ModelScope 公网续传、23 文件完整验证及新目录当前 App 验收通过，两设备各自同旧报告 100／100 原始文本一致。两次 M4 两小时尝试分别在 707／1229 个完成周期后失败，第二次明确 generation_limit。主／辅助采样候选均未提升为默认值；英文数字的语言路由修复不等于发音质量通过。当前状态以新增阶段证据与[总报告](../../reviews/2026-11-06.md)为准，历史下载失败、超时、合成输入和较短负载回执保留原值。

- 本轮输入是 OS 合成语音，不用于估计真人 CER／WER 或主观音质。历史 FLEURS 100 条回归保留在前一日的证据目录，本机缺少原始录音；恢复固定数据源的中英文连接均超时，尚未重跑。
- 本轮没有从公网或真实代理下载全部模型权重，没有实测完整 SOCKS5 大文件下载。
- 桌面会话锁定，桌面自动化不可用。权限标志为允许不等于物理麦克风、跨应用输入、完整键盘导航、睡眠恢复或人耳试听通过。
- Apple Development 成功不等于 Developer ID、公证、稳定版升级或系统权限保留通过。本机没有 Developer ID Application 证书。
- 修复分支已合并并推送到 main，`fe9a1bd` 远程 CI 通过；主分支必需检查已配置并保留管理员豁免，见 [post-delivery/](post-delivery/README.md)。正式安装、更新 feed 与发布资产未改动。
- Metal／RSS 指标口径不同，不能相加；预热后的短句速度不代表冷启动、内存压力、长期稳定性或能耗。
