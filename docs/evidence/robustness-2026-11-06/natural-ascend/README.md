# ASCEND 自然语音完整 test split 基线

在修改任何质量策略之前，固定协议并完成当前原生实现的自然语音基线。完整 test split 共 **1,315 条、3,302.986 秒**，包括 685 条中文、257 条英文、373 条中英混说，来自两位说话人、四段会话。它扩大了原受控拼接诊断的输入范围，覆盖范围仍限于这个数据集的测试集。

本机为 Apple M1 Max／32 GiB／macOS 27.0.1。使用 `mlx-community/Qwen3-ASR-1.7B-8bit@a8379a2e2f9e313c9292cdf1af4055ab56d50d55`，默认 auto 语言和现有推理参数；单个隔离原生 Helper 串行完成所有请求，零重试、零跳过。运行于 `main@fe9a1bd`；复用已验收的 `57c8152` App 内 Helper，运行前核对当前全部 Native 源码、SDK 适配器和推理锁文件与该构建一致。稍后的菜单图标修改不涉及推理。

## 数据与预先固定的协议

作者仓库为 [HLTCHKUST/ASCEND](https://github.com/HLTCHKUST/ASCEND)，代码固定于 `8c50c2e2c65f8555eb1d7655310dd585dc1f6710`。通过 ModelScope `opendatalab/ASCEND` 的 `raw/ASCEND.tar.gz` 正常 TLS 下载 **930,038,853 字节**，SHA-256 为 `de9137065136bbf32fbbb23304abeb28e6c700d948ade2424ef202684a23cc1c`，与预先读取的固定公开校验表一致。归档内原数据卡和镜像卡均标注 CC BY-SA 4.0，并指向作者／论文。

只提取 test metadata 对应的音频，不运行归档内脚本；所有 WAV 均验证为 16 kHz 单声道 PCM16，逐个记录文件和 PCM 摘要。原始 metadata 顺序保持不变，前 300 条 ID／参考文本／标签／时长与固定公开清单交叉核对一致，未据此缩减测试集。完整清单 SHA 为 `6950c9fd603e0e7a17cb6bfe4b0d4fd33d6290c77a72b69a94dba8fba2f17eaf`。

[protocol.json](protocol.json) 在推理前固定选择、评分和边界。按作者 `utils.py`／`eval.py` 的规则对双方去指定标点、转小写，jieba 0.42.1 分词后保留 ASCII token、按字符拆分非 ASCII token。MER 为累计编辑距离除以累计参考 token 数，区别于 `jiwer.mer` 的 match-error-rate。保留数字写法、繁简、填充词、重复及 `[UNK]` 等标注，不做实体纠正或语义等价改写；评分器 19 项校验通过。英文子集也使用这一分词口径，不能直接与其他标准 WER 榜单比较。

## 实际结果与限制

| 子集 | 条数 | 编辑数／参考 token | MER |
|---|---:|---:|---:|
| 全部 | 1,315 | 1,291／11,732 | 11.0041% |
| 中文 | 685 | 349／4,807 | 7.2602% |
| 英文 | 257 | 307／1,359 | 22.5901% |
| 混说 | 373 | 635／5,566 | 11.4086% |

全部 HTTP 请求成功，整体 762 条规范化后精确一致，存在一条空转写。完整运行 510.04 秒，累计请求耗时 498.19 秒；请求 P50／P95 为 0.357／0.575 秒，实际音频时长对应的请求 RTF 为 0.151。这些时间包含解码和 HTTP，限于当前设备和这组短输入，不能代表麦克风端到端延迟或其他运行时性能。

373 条 mixed 中有 64 条输出缺少参考文本的一种脚本：38 条没有中文、26 条没有拉丁字母。这个正则信号包含填充词、音译和标注差异，不能直接解释为 64 条语义遗漏。进一步文本核对确认了具体的保真度问题：[text-triage.json](text-triage.json) 保存四条样本 ID、原参考和实际输出，包括中文短语变为英文、`Hong Kong` 变为错误汉字，以及 0.6 秒 `thank you` 返回空文本。该核对没有人耳试听，不把所有词形差异归为语义退化，也没有覆盖所有错误类型。

独立的完整矩阵实现重算全部 1,315 个混合编辑距离，与评分器一致；同时重新核对所有输入 WAV／PCM 摘要、原值／分组计数以及推理前冻结的协议和评分源码摘要。原生 Helper EOF 正常退出，端口关闭、临时请求清零，产品源码和生产实例／配置／Token 保留。模型由原生 Helper 启动时验证固定摘要，运行后核对模型与冻结文件的元数据未变；不额外声称重复完成了全模型哈希。

这是**未改变实现的完整基线**，没有据此设置更宽松的新门槛、采用旧失败候选或宣布自然混说质量通过。下一步应针对实际翻译、专名和短句遗漏定位原因，并保留完整队列及既有固定 100 条／逐例条件。原严格 M4 7,200 秒混合负载、物理麦克风、试听、完整 Hugging Face 模型下载和分发签名继续独立待验；本机取得该语料不证明 M4 的模型 TLS 路径恢复。

## 文件与许可

[acquisition.json](acquisition.json)、[preparation.json](preparation.json) 记录数据与音频核验；[run-summary.json](run-summary.json)、[score.json](score.json)、[verification.json](verification.json) 记录完整运行、全部分组统计和独立复核；[environment.json](environment.json) 记录设备；[source-manifest.json](source-manifest.json) 保存固定来源及执行脚本摘要。[score-natural-asr.py](score-natural-asr.py) 与 [scorer.lock](scorer.lock) 归档本次评分实现和依赖，保留作者评分代码的 [MIT 许可](ASCEND-CODE-LICENSE.txt)。它们使用本次隔离目录布局，原执行脚本、完整清单、录音、原始转写和私有日志保留在本地 `.context/robustness/post-delivery-20261107/`。

数据来源：Lovenia et al., *ASCEND: A Spontaneous Chinese-English Dataset for Code-switching in Multi-turn Conversation*, LREC 2022, HKUST CAiRE（[论文](https://aclanthology.org/2022.lrec-1.788/)）。ASCEND 数据、参考及派生的逐条文本（包括 `text-triage.json`）遵循 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)，保留作者署名；本次派生内容包含模型转写及诊断注释。未将原始音频或完整转写加入 Git。
