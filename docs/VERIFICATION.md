# CyberAudio 0.1.0 验收报告

验收日期：2026-11-03。第 2–11 节保留初次 build 2 部署的实测记录，其中长跑测试仍为 build 1；第 12 节追加 build 3 隐藏 Dock 图标的部署与复测结果。以下不是模型官方基准或面向所有设备的性能保证。

## 1. 交付设备与安装状态

| 项目 | 结果 |
| --- | --- |
| 目标设备 | Mac mini，Apple M4，16GB 统一内存，arm64 |
| 目标系统 | macOS 27.2 |
| App | `/Applications/CyberAudio.app`，版本 0.1.0（build 3），ad-hoc 签名检查通过 |
| 源码 | `/Users/zhubby/Repository/CyberAudio`，GitHub `Arasaka-Academy/CyberAudio` 的 `main` 分支 |
| 推理环境 | 独立 Python 3.13.18；初次基准进程为 3.13.17，MLX Audio 0.6.3，MLX／Metal 0.32.4 |
| 运行目录 | `/Users/zhubby/Library/Application Support/CyberAudio` |
| 本机地址 | `http://127.0.0.1:8765` |
| HTTPS | `https://mac-mini.civet-beta.ts.net:8443`，另一台 Tailscale 设备已访问 |
| 原 ASR 系统启动项 | `system/local.qwen3-asr` 已禁用、卸载；旧 plist 已归档 |
| 网络入口 | 8443 → 8765，无 Funnel；保留 443 → 18080 及原有 Funnel |
| 基础配置再次核查 | FileVault 为 Off，Tailscale 由系统 `tailscaled` 运行；本次未修改这些配置 |

运行的是 SwiftUI 原生 App，App 直接管理 Python／MLX 子进程。模型仍由 MLX Audio 运行。App 退出时 API 与模型停止；关闭窗口时菜单栏 App 继续运行。

## 2. 固定资产与来源

| 组件 | 仓库 | revision | 校验 |
| --- | --- | --- | --- |
| ASR | `mlx-community/Qwen3-ASR-1.7B-8bit` | `a8379a2e2f9e313c9292cdf1af4055ab56d50d55` | 11 个文件，SHA256 与记录的固定资产清单一致 |
| TTS | `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` | `41d3337e8b7f2843a75841595fc14e4b9a7a4b96` | 13 个文件，固定 revision 下载，LFS 权重校验后逐文件复核 |

TTS 配置确认为 `qwen3_tts`／`custom_voice`／`1b7`／8-bit；没有用 0.6B 或 Base 版替代。ASR 从原已验证模型作独立 APFS 克隆，原资产保留供回退。独立 Python 环境从原已验证环境克隆并重新定位入口，完整依赖版本一致，迁移后的 `python -m pytest` 和 console entrypoint 可执行。

初次 build 2 部署时，目标机 App 与本地构建产物的以下 SHA256 一致；build 3 的新文件校验见第 12 节对应的部署报告：

```text
Contents/MacOS/CyberAudio
ce7543aa4a8cfdee8e742605ed23572e57559e44c7fa4ea7a0ae221c0ea016e5

Contents/Resources/backend/server.py
bd86ca6d5d94fe3c7c1b671c85456823695428fd45eb591e228ef985305fe9e8

Contents/Info.plist
35f2d43774826ba6d0f60cc78a9973bcc616e749945189aaad79fd8b9edf30f8
```

## 3. 加载、预热和内存

最终 build 2／Python 3.13.18 的正式短句测试对应实例 `ABB82F87-353A-4997-82FB-449A112E15D2`，设备为 `metal`。之后的退出与恢复测试会产生新的实例编号。

| 指标 | 实测 |
| --- | --- |
| 两个模型加载 | 4271.6ms |
| 模型预热 | 2118.9ms |
| 预热结束 Metal 活跃内存 | 5,612,370,480 bytes，约 5.23GiB |
| 预热结束 Metal 分配峰值 | 6,307,151,204 bytes，约 5.87GiB |
| 正式短句／合成测试 Metal 峰值 | 约 6.43GB，约 5.99GiB |
| 该进程记录的峰值 RSS | 4,112,678,912 bytes，约 3.83GiB |

这是新进程启动数据，未清空 macOS 文件缓存；加载耗时也不含 App 启动前的调度、Python import 等所有阶段。RSS 与 Metal 内存统计口径不同、可能重叠，不可相加。该数据没有覆盖所有后台程序同时运行或内存紧张时的状态。

## 4. 实际 STT 内容和延迟

参考文本在识别前已经给定。公开中文真实语音与其余合成语音用途不同，少量样本不足以估计普遍 CER／WER。

| 样本 | 时长 | 结果 |
| --- | --- | --- |
| `official-zh.wav`，公开中文真实语音 | 4.2039 秒 | “甚至出现交易几乎停滞的情况。”，与参考一致 |
| `synth-en.wav`，合成英文 | 4.2241 秒 | “Please remind me to attend the meeting at three fifteen this afternoon in room two.”，与参考一致 |
| `synth-mixed.wav`，合成中英混合 | 7.25 秒 | 识别出 CyberOS、Raspberry Pi、Perseus；把参考中的 `and` 转为“和”，并有空格／标点变化 |
| `synth-names.wav`，合成人名 | 6.7441 秒 | 张三、李四、明天上午九点、苹果电脑、树莓派参考内容正确 |
| `synth-numbers.wav`，合成数字 | 6.8894 秒 | 订单号一二三四五六、三百二十八元、二十三点五度参考内容正确 |
| 精确数字静音 | 6 秒 | 空文本，`no_speech=true` |
| 合成背景噪声 | 6 秒 | 本次为空文本，`no_speech=false`；不代表实现了通用 VAD |

中英混合的具体差异如下：

```text
参考：请帮我检查 Cyber OS and Raspberry Pi 的网络连接，服务器名字是 Perseus。
输出：请帮我检查 CyberOS 和 Raspberry Pi 的网络连接。服务器名字是 Perseus。
```

最终 build 2／Python 3.13.18 预热后，对两个约 10 秒的中文／英文合成短句各请求 10 次，共 20 次，串行调用本机 HTTP API：

| 指标 | 实测 |
| --- | --- |
| P50 | 659.49ms |
| P95 | 664.81ms |
| 最慢一次 | 720.21ms |

P50 使用排序后第 11 个样本，P95 使用 nearest-rank 第 19 个样本。墙钟时间包含本机 HTTP 和 FFmpeg 转换，不包含麦克风采集、句尾等待或远程网络。该组小样本达到初始 P95 ≤3 秒目标；没有据此承诺所有 60 秒录音、口音或噪声条件均满足同一时延。

## 5. 实际 TTS 与流式输出

| 请求 | 输出音频 | 墙钟时间 | 客户端收到首段 | RTF |
| --- | --- | --- | --- | --- |
| 中文，serena，WAV | 5.68 秒 | 3377.61ms | 完整生成后，3376.81ms | 0.595 |
| 英文，ryan，WAV | 4.16 秒 | 2455.74ms | 完整生成后，2455.17ms | 0.590 |
| 混合文本，vivian，PCM 流 | 5.76 秒 | 3263.47ms | 247.24ms | 0.567 |

本表对应最终 build 2／Python 3.13.18。PCM 流分 18 次客户端读取，接收时间从 247.24ms 持续到 3219.23ms，最后确认请求记录为 `completed`。这证明本次响应渐进到达，而非等整段生成完才伪装分块。网络读取次数不等同于模型原始 chunk 数。

WAV 已解析验证为 PCM16、24000Hz、单声道，非流式 PCM 与流式 PCM 的数据完整性另有受控测试。RTF 为生成墙钟时间／音频长度；TTS 使用采样，重复请求长度与耗时会变化。

中文与英文合成内容回灌 ASR 后可辨识，专名出现 `Cyber Audio` 空格和 `Mac Mini` 大小写规范化。这是辅助内容检查，不是人类试听、自然度 MOS 或声音相似度评估。没有逐一评分 9 个音色或所有表达指令组合。

验收脚本有意保存公开样本和生成音频。初次基准在 `live-verification-baseline.json` 中保留：Python 3.13.17 下 STT P50／P95 为 700.56／713.06ms，中文／英文／流式 TTS RTF 为 0.637／0.598／0.629。旧稳定性脚本曾覆盖同名中文产物，因此该次文件不能用于复原初次基准音频；当前脚本已修正，最终验收又重新生成了产物。两轮样本不能用来推断 Python 小版本导致了性能提升。

## 6. HTTPS、断开连接与互斥

另一台加入 Tailscale 的管理 Mac 通过有效 HTTPS 证书访问统一地址，完成以下实际模型请求：

- 查询两个固定模型，错误 Token 返回 401。
- 上传公开中文音频，得到预期中文文本。
- 生成并解析 4.08 秒 WAV。
- PCM 请求在 393.68ms 收到首段后主动断开，状态记录变为 `cancelled`。
- 从请求发起计时约 799.55ms 恢复空闲，没有出现永久占用或推理任务叠加。

目标机实际验证 TTS 在运行时再次请求 TTS／STT 均返回 429。推理槽直到真实工作结束才释放；受控测试额外覆盖请求 Task 被取消而 GPU 替身仍运行的情况。

HTTPS 延迟是单次客户端样本，不是网络 P50／P95。没有用手机、浏览器录音页面或 CM5 客户端完成本次交付验收；主界面已改为 Mac App。

最后检查时，从管理 Mac 访问 8443 的 `/ready` 为 200，两个模型就绪。原有 443 → 18080 转发配置及 Funnel 保留，但其 HTTPS 根路径返回 502，目标机本地 18080 无监听、连接被拒绝。未确定该后端停止的原因，本次未修改或恢复它，因此不能称为“所有原服务均正常”。

## 7. App 生命周期与离线边界

| 初次 build 1／Python 3.13.17 空闲时的实际操作 | 监听关闭 | 重新打开至就绪 |
| --- | --- | --- |
| TERM，进入 App 正常清理退出 | 110.4ms | 4233.5ms |
| SIGKILL，模拟 App 强制结束 | 110.8ms | 4378.8ms |

每次退出后观察 3 秒，没有系统服务重新拉起 API；再次打开 App 后 instance 变化，两个模型重新就绪。就绪恢复计时从 Cua Driver 启动调用返回后开始，不含该调用自身的耗时。观察窗口有限，不是长期崩溃恢复承诺。

最终 build 2／Python 3.13.18 又在真实流式 TTS 推理中完成两项退出测试。每次先收到 512 bytes PCM，确认 `/ready` 为 `busy=true`，再退出拥有它的 App：

| 推理中操作 | 监听关闭 | 模型子进程 | 重新打开至就绪 |
| --- | --- | --- | --- |
| TERM，正常清理退出 | 110.7ms | 已结束 | 4615.9ms |
| SIGKILL，强制结束 App | 110.8ms | 已结束 | 4173.8ms |

每次退出后观察 3 秒，没有自行复活；重新打开后的实例编号均变化。第二次恢复的实例为 `03ABACF2-AF4E-4594-B37E-068DF1A15983`。这项测试覆盖生成中的退出，不是只在空闲时检查监听端口；正常退出按钮的 GUI 点击和物理重启仍是不同验收项。

最后一次恢复后，真实中文 STT 返回参考文本；TTS 输出 2.40 秒的 24kHz／单声道／PCM16 WAV，状态为 `completed`。核对仅有一个生产 App 和一个直属模型子进程，8765 只由该子进程监听在 `127.0.0.1`。最终签名检查通过，54 项 Python 包与依赖锁一致，Token 权限为 0600，请求临时目录为空。旧 ASR label 已禁用且未加载；8443 不启用 Funnel，443 的原有配置保留。

运行期使用本地模型路径、离线环境变量及禁止外联 sandbox。对同一 profile 下的 Python 执行连接 `1.1.1.1:443` 探针，返回 `PermissionError`；App 随后能在该约束下重新启动并识别、合成。没有关闭整个 Mac 的网络，Tailscale／SSH 继续可用；证据覆盖的是推理进程外联被阻断时的本地运行。

## 8. 连续运行

2026-11-03 15:00:17–15:30:17（UTC+8）连续运行 1800 秒，80 个周期，每周期完成一次 TTS 与一次 STT，共 160 个成功请求。TTS 中 40 次 WAV、40 次流式 PCM；周期之间等待约 20 秒，属于有间隔的混合工作负载，不是持续满载压力测试。错误记录为空。

| 连续测试指标 | 结果 |
| --- | --- |
| GPU 活跃内存范围 | 5,612,360,248–5,612,425,784 bytes，变化 64KiB |
| 首／末次 GPU 活跃内存 | 均为 5,612,360,248 bytes |
| Metal 分配峰值 | 6,459,218,584 bytes，约 6.02GiB |
| 进程峰值 RSS | 3,751,133,184 bytes，约 3.49GiB |
| 周期错误 | 0 |

所采样的 GPU 活跃内存未持续上升，没有永久 busy 或请求临时目录残留。这是 30 分钟有间隔负载的观察，不能替代数天运行、高并发或内存压力验收。该轮使用 build 1／Python 3.13.17；build 2 的变化是登录项状态控制，打包后端的 SHA256 一致。最终 build 2／3.13.18 的实际模型与退出复测已完成，但没有再重复 30 分钟测试。

## 9. 构建与后端测试

管理 Mac `make check` 成功：24 项后端测试、Python 编译检查、Shell 语法、plist 检查及 Swift release 编译。目标机独立环境也完成 24 项后端测试。

后端测试使用真实 FastAPI、FFmpeg 和受控推理替身，覆盖认证、固定模型身份、语言／格式参数、损坏音频、20MiB 文件上限、60 秒音频上限、精确静音、TTS 文本与指令上限、全局互斥、早期失败、流式部分失败、流式字节完整性、客户端取消、启动失败、chunked 请求体限制和临时文件清理。测试替身会检查模型操作始终在初始化的同一线程。

本地最小开发环境有一条 Starlette TestClient／httpx 的弃用提醒；Swift 链接器还报告了两个本机 Command Line Tools 搜索路径缺失的 warning，release 编译退出码仍为 0。没有为消除提醒而修改经过验证的生产依赖锁。

指南中 22 个 shell 代码块已用 `zsh -n` 检查，3 个 JSON 块可解析、1 个 Python 块可编译。静态语法检查不等于在全新 Mac 上逐条执行了一遍安装步骤。

## 10. 原生 GUI 与待验收部分

管理 Mac 上以隔离数据目录和 `tests/ui_stub.py` 检查原生 App：

- STT／TTS 布局与侧栏导航。
- 通过真实 NSOpenPanel 选择音频，提交后显示测试结果。
- 点击复制，实际剪贴板与可见测试结果一致。
- 非流式 WAV 走 AVAudioPlayer，观察到播放状态及结束后重播／保存按钮可用。
- PCM 走 AVAudioEngine，完成一秒测试音频播放与完成状态。
- NSSavePanel 能打开；保存文件流程未完成，不把对话框出现当成文件落盘成功。
- 测试 App 退出后测试 API 监听关闭，临时测试输入已清理。

测试接口没有进入生产 App。目标机尚未授予辅助功能／屏幕录制权限，所以真实模型验收和 GUI 替身验收需分别解读。未启用额外系统权限来替代这些边界。

尚待实际使用验收：目标机物理麦克风录音、扬声器真人听感、目标机完整导入／保存操作、手机采音、9 音色偏好、登录项注册后的真实重新登录／重启、冷启动／断电恢复。登录启动功能已经实现；本次未承诺注销后或用户未登录时可提供语音服务。

本次连续测试期间，基础 Homebrew Python 3.13 路径曾被移除，已启动进程继续运行，但新进程无法启动。移除原因未确定。恢复安装后基础解释器为 3.13.18，固定 Python 包清单保持一致，Metal 运算与 24 项后端测试重新通过。build 2 的重新启动、真实 STT／TTS、20 次延迟样本及推理中退出均完成复测；本报告第 3–5 节采用这次最终环境的结果，第 8 节保留原 3.13.17 的连续测试边界。

当前为自用 ad-hoc App，Python／模型放在独立数据目录，venv 仍依赖基础解释器。未做签名公证、完整资源安装器或通用安装包验收。

## 11. 原始证据与复测

目标机 `~/Library/Application Support/CyberAudio/reports/` 保留以下记录：

| 文件 | 内容 |
| --- | --- |
| `assets-verified.json` | 两个模型逐文件校验计数与 revision |
| `asr-provenance.json` | 原 ASR 资产来源和校验 |
| `live-verification.json` | 正式模型内容、短句延迟、WAV／PCM、互斥结果 |
| `live-verification-baseline.json` | 初次 build 1／Python 3.13.17 基准 |
| `lifecycle.json` | 空闲退出、强制结束、恢复、外联限制探针 |
| `active-exit.json` | 推理中退出的补充验收记录 |
| `stability.json` | 连续运行周期及内存元数据 |
| `final-deployment.json` | build 2 版本、签名和文件 SHA256 |
| `python-recovery.json` | 基础解释器恢复与版本记录 |
| `final-audit.json` | 最后恢复后的真实模型冒烟、监听归属、依赖锁及入口检查 |
| `existing-443-status.json` | 原有 443／18080 后端未响应的检查记录 |
| `qa-cleanup.json` | 临时界面启动测试工具清理及 App 就绪确认 |
| `acceptance.log`／`final-acceptance.log` | 公开测试输出，非日常请求正文日志 |

管理 Mac 仓库的忽略目录 `.context/` 保存 HTTPS、GUI、文档和证据副本。生产 Token 未写入这些报告、源码或 Notion。

可运行 `scripts/verify_live.py`、`scripts/verify_client.py` 复测 API。`scripts/verify_lifecycle.py` 与 `scripts/verify_active_exit.py` 会主动退出并重新启动目标机 App，只应在允许短时中断时手动执行；需要目标机已可用的 Cua Driver 启动能力。它们不由常规 `make check` 自动运行。

## 12. build 3：隐藏 Dock 图标

2026-11-03 将 `LSUIElement` 设置为 `true`，构建号更新为 3，服务设置中的说明同步为菜单栏入口。关闭窗口后仍可从菜单栏选择“显示主窗口”；选择“退出 CyberAudio”仍由 App 负责停止语音引擎。

本轮实际验证：

- `make check app` 通过：24 项后端测试、Python／Shell／plist 检查、Swift release 构建与 App 签名验证。
- 管理 Mac 的隔离测试 App、Mac mini 的实际安装 App，均由 macOS 报告 `activationPolicy = 1`（`accessory`），确认运行于隐藏 Dock 的应用模式。
- Mac mini 原 App 退出后，其进程和 API 监听均关闭；替换为 build 3 后，两个真实模型重新加载，`/ready` 返回 `ready`，STT／TTS 都为 `ready`。
- 新版真实 TTS 返回 HTTP 200、24kHz 单声道 WAV、2.96 秒音频；交给真实 STT 后返回 HTTP 200 和“你好，欢迎使用 Cyber Audio。”。这是一条合成语音回环检查，未据此重新估计准确率或延迟分位数。
- 另一台 Tailscale 设备访问原 HTTPS 8443 入口，确认同一新实例就绪。
- 目标机安装包的可执行文件、plist 与后端源文件 SHA256 均与本地构建一致；后端源文件 SHA256 仍为 `bd86ca6d5d94fe3c7c1b671c85456823695428fd45eb591e228ef985305fe9e8`。
- 隔离界面测试 App 的正常退出仍关闭其测试 API；真实接口测试结束后临时录音文件为零。

本轮原生自动化未能解析测试窗口或截取窗口图像，因此没有完成菜单栏点击恢复、关闭窗口和点击退出的人工交互复测；隐藏 Dock 的判断来自实际运行策略，而非截图。第 10 节列出的真实麦克风、重新登录／重启等待验收项目仍然适用；本轮没有重复 30 分钟长跑。

目标机原 App 保留在 `~/Library/Application Support/CyberAudio/updates/dock-build3-20261103/CyberAudio.build2.app`。部署与接口原始报告分别为 `reports/dock-build3.json` 和 `reports/dock-build3-api.json`。已发布的 `v0.1.0` Actions DMG 是原 build 2；本轮 build 3 更新源码与 Mac mini 安装版，后续 Release 会按工作流生成新的下载包。

报告结束标记：CYBERAUDIO-VERIFICATION-END-20261103。

## 13. 2026-11-05：Liquid Glass 界面本地验收

本节只记录本次界面改造的本地结果，不替代前述 Mac mini 部署、模型性能或真实设备验收。源码位于 `codex/liquid-glass-workspace`；本地环境为 Apple Silicon、macOS 27.0.1（26A434），交付 App 为 `dist/CyberAudio.app`，尚未安装到目标设备或发布新版。

### 构建与实现

- 最终源码的 `make check app` 通过：24 项后端测试、Python 编译、Shell 语法、plist 检查、Swift release 构建及 App 打包。`codesign --verify --deep --strict` 再次通过。
- 原有 Starlette/httpx 弃用提示和 Command Line Tools 的两条 linker 搜索路径提示仍存在，未影响本次构建与启动。
- 主界面改为统一工作台，使用 macOS 26 原生 `glassEffect`、`GlassEffectContainer`、`.glass`、`.glassProminent`。文本面板使用稳定底色；原 HTTP API、模型与音色 ID 保持原实现。
- `AudioWorkspace` 新增只读实时电平和 48 点波形历史，按 50ms 间隔采样 `AVAudioRecorder`。代码路径覆盖停止录音、约 59 秒保护上限及退出时结束采样；真实麦克风验证见下方边界。
- 导入／保存使用原生异步窗口附属面板，保存动作在打开面板前保留待写入的数据快照。识别和生成均可取消。

### 隔离原生界面测试

沿用 `tests/ui_stub.py`，使用 `.context/glass-qa/data`、独立 Bundle ID 和本机 63041 端口；未改动生产运行目录、登录项或目标设备。替身只生成固定转写和合成音频，不运行 MLX 模型。请求记录只含路径、音色、流式标记及完成／断开状态，不记录输入正文。

| 检查 | 实际观察 |
| --- | --- |
| 导入与复制 | 原生文件面板打开测试 WAV；识别区显示“这是 CyberAudio 的界面测试结果。”；复制后系统剪贴板与该文本一致 |
| WAV 与 PCM | 分别进入 `AVAudioPlayer` 和流式 `AVAudioEngine` 播放路径，界面显示播放／边生成边播放；重播可启动，停止可结束播放 |
| 实际保存 | 经原生保存面板生成 `artifacts/liquid-glass/CyberAudio.wav`；文件为 48,044 bytes、24,000Hz、单声道、PCM16、24,000 帧 |
| 生成取消与模式切换 | 延迟生成时切换到识别页，底部仍有“取消生成”；按钮与 `⌘.` 观察到取消效果，替身记录连接断开 |
| 识别取消与模式切换 | 延迟识别时切换到朗读页，底部仍有“取消识别”；取消后状态条退出，替身记录连接断开 |
| 内容保留 | 两种模式来回切换后，转写结果、朗读文本与表达指令保留；任务持续到完成或显式取消 |
| 长文本 | 朗读文本超过 500 字、表达指令超过 200 字时显示本地提示并禁用生成；正常朗读文本搭配 208 字指令也独立验证了该限制 |
| 加载和错误 | 替身注入加载、引擎失败及 HTTP 500，显示对应状态／局部错误，并正确禁用依赖引擎的操作 |
| 设置共享状态 | 齿轮和 `⌘,` 打开独立设置窗口；打开设置前后后端实例不变 |
| 窗口尺寸 | 默认外框 1040 × 760；最小外框 820 × 640 经独立窗口几何读回确认，内容可滚动，跨模式取消按钮保持可见 |
| 关闭与恢复 | 点击原生关闭按钮后主窗口不可见，API 保持可用；通过 App 菜单“显示 CyberAudio”恢复主窗口，后端实例不变 |
| 退出 | 测试 App 正常退出后 63041 端口关闭；本轮测试结束已再次确认关闭 |

界面验收所用的 release 可执行文件另复制到隔离 Bundle，重新签名后验证了导入、复制和流式生成。该 Bundle 因测试身份不同而具有不同代码签名；对比 36 个已加载 Mach-O section 的内容均一致，未在其中加入界面测试逻辑。生产 App 捆绑的仍是正式后端。

最后为装饰边框关闭命中测试后，完整构建检查再次通过；最终 App 的隔离启动截图正常、`/ready` 返回 `ready`。此次重新签名启动的辅助功能树无法解析，因此未据此宣称重复了全部交互用例。该测试实例通过 App 已实现的 SIGTERM 正常退出路径关闭，测试端口再次确认释放。

### 外观证据与验证边界

`artifacts/liquid-glass/` 保留 `stt-light.png`、`tts-light.png`、`stt-dark.png`、`tts-dark.png`、设置窗口、紧凑窗口、跨模式任务、加载、失败和辅助功能预览截图。浅色最终截图来自上述 release 可执行文件的隔离 Bundle。深色通过仅用于本地预览的 App 包设置原生 `NSAppearance.darkAqua`，使用同一份生产视图；降低透明度、减少动态效果和增强对比度则通过该预览包的 SwiftUI 环境注入。未修改用户的系统外观或辅助功能设置。

已观察到降低透明度预览中控件采用实色、背景渐变消失，增强对比度时内容边框更清晰；减少动态效果的符号动画条件也经过代码检查。截图由后台窗口捕获，系统会淡化非活动窗口中的原生玻璃按钮。

以下项目保留为后续人工／设备验收，未据本次替身测试宣称通过：

- 真实麦克风波形、物理输入电平、持续录音到上限及权限对话框的真人操作。
- 新界面配合真实 MLX 模型的完整识别／合成，以及真人听感；本轮只确认合成测试音频的原生播放路径和保存文件格式。
- 完整 Tab 焦点顺序、`⌘A` 文本选择、文件面板 Escape、录音快捷键。自动化按键投递／目标窗口聚焦未能稳定完成，不能以工具的按键发送成功代替交互验证。
- 菜单栏图标中的“设置…”与“显示主窗口”的实际点击。入口已实现且存在于原生辅助功能树，但本机自动化未能解析该菜单图标的完整调用路径；本轮恢复主窗口验证使用 App 菜单入口。
- macOS 26 实机、重新登录／重启、目标设备安装和版本发布。

本地生产可执行文件 SHA256：`5298e68e07ff0eb7112efaf354a8c3fd12029bc29a6d732cac8a1dc3eac376ed`。后续源码变化或重新签名会使其变化。
