# 实际模型边界与受控音频对照

运行时为 `e2f34df` 的实际 Helper，SHA-256 `cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89`；采集时仓库源码为 `96ea078`。设备为 Apple M1 Max／32GiB／macOS 27.0.1。全部使用独立目录和非生产端口，没有改动正式 App、模型、配置或 Token。

## 边界与取消

[m1-boundaries.json](m1-boundaries.json) 对应真实 HTTP／实际模型运行，驱动退出 0，总计 88.06 秒。

| 输入或操作 | 实际结果 |
|---|---|
| 固定真人录音循环并裁为 60 秒 | HTTP 200，识别成功；属于受控时长输入 |
| 相同构造、60.1 秒 | HTTP 413、`audio_too_long` |
| 活动 ASR 请求断开连接 | 先观察 busy，再断连；请求 `cancelled`，约 299ms 后空闲，同实例下一次识别成功 |
| 417 个 Unicode scalar 英文段落 | HTTP 200、完整请求终态，生成 31.68 秒 WAV；合成音频的 ASR 回读包含末句 |
| 500 个 Unicode scalar 中文输入 | HTTP 422、`generation_limit`，没有把截断音频当成成功 WAV；合法输入长度不保证一定在生成上限内结束 |
| 数字和语气指令短句 | 20.4 秒 WAV，ASR 回读包含时间、订单号、温度；没有据此判定语气或自然度合格 |
| 活动非流式 TTS 请求断开连接 | 请求 `cancelled`，约 364ms 后空闲，同实例下一次 WAV 成功 |
| 清理与退出 | 无 `request-*` 暂存目录；关闭 stdin 后约 146ms、退出 0；正式服务实例及隔离配置／Token 保留 |

取消时间是该次设备、输入和负载下的断连至空闲观测值，不是所有 GPU 工作的中断上限。生成音频独立解析为 24kHz／单声道／PCM16，原始 WAV 保留在私有工作区供后续人耳试听。

## 受控质量对照

[quality-corpus.json](quality-corpus.json) 固定 30 个输入，SHA-256 `c48623b26410a3129695cb06cbdc32ac8a21a198cf611be61151c2800c5375f2`。从先前固定 FLEURS 语料中按专名、日期、比例、数量等内容预先选择八条，分别构造 clean、20dB、10dB 高斯噪声，共 24 个单语输入；另有四个中英拼接输入、六秒静音和六秒噪声控制。详细来源和变换见 [quality-provenance.json](quality-provenance.json)。

原始来源是 **IEEE float32 WAV**。FFprobe 确认 `pcm_f32le`，逐样本 float 读取与 FFmpeg 解码完全相同。转换先将峰值归一至 0.35，再添加确定性噪声并量化为 PCM16；所有输出由 FFmpeg 独立解码复核，clean 误差不超过半个 PCM16 量化单位，没有削波，实测 SNR 与目标差小于 0.15dB。

最初的私有 v1 生成器错误地将四字节 float 当作整数，已明确排除其质量结果，原始数据保留；[rejected-cohort.json](rejected-cohort.json) 保存错误原因和旧结果摘要。本目录的质量数字全部来自修正后的 v2，不涉及生产解码代码变更。

在同一 M1 顺序运行安装中的旧 0.4.3 Helper 和当前 Helper，两次分别约 28.21／27.68 秒，均退出 0。结果分别见 [m1-quality-installed.json](m1-quality-installed.json)、[m1-quality-candidate.json](m1-quality-candidate.json)。[严格比较](m1-quality-comparison.json) 核对 30 条 ID、音频 SHA、参考、语言和重复号；**30／30 原始输出完全一致**，24 条单语主指标不回退。

| 条件 | 中文 CER，旧＝新 | 英文 WER，旧＝新 |
|---|---:|---:|
| clean，每种语言四条 | 29.1667% | 6.8493% |
| 20dB，每种语言四条 | 29.1667% | 20.5479% |
| 10dB，每种语言四条 | 29.1667% | 12.3288% |

沿用仓库 `CoreMLEvidence.TextScore`，只规范化大小写、标点和空白，**没有**将“十五”与 `15`、英文数字读法或参考中的括号译名视为语义等价。该小样本表是固定条件的原始评分，不构成专名实体准确率或噪声强度与错误率的总体关系；不同背景负载的顺序运行也不构成速度或能耗对照。

四个拼接输入暴露了实际质量限制：桥梁中文＋比例英文的组合只输出英文；总统中文＋Apple 英文的组合只输出中文，新旧版本一致。拼接处为 250ms 静音，来自不同真人录音，**不能计作自然中英混说验收**，但整段遗漏需要在后续混合语音工作中处理。当前证据尚未确定遗漏来自模型、提示／分段策略还是其他推理集成因素，不能将它归为本轮修复回退。静音和纯噪声两个控制均返回空文本，也不能由这两个样本推断通用 VAD 能力。

## 文件与复现

[validation.json](validation.json) 保存运行及核验范围；[SHA256SUMS.txt](SHA256SUMS.txt) 覆盖本目录其他文件。没有提交音频、模型、配置或原始 Token。

[boundary-driver.py](boundary-driver.py)、[quality-driver.py](quality-driver.py) 和 [quality-comparator.swift](quality-comparator.swift) 是实际采集源码的原样归档。驱动采用工作区私有目录布局，拒绝覆写已有运行目录；它们不是可直接在此文档目录运行的通用 CLI。复现前应在独立验收目录恢复相同布局并指定已验证的 App、模型和语料；Swift 比较器同时编译仓库的 `Experiments/CoreML/Sources/CoreMLEvidence/Evidence.swift`。

FLEURS 与参考归属 Google／FLEURS（Conneau 等，2022），固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`，许可 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，[数据卡](https://huggingface.co/datasets/google/fleurs)。本目录只保留来源、参考和模型输出。

这些结果没有覆盖物理麦克风、人耳听感、自然混说、真实房间噪声、正式公证升级或 M4 两小时终态。它们与此前的 100 条固定真人回归、M4 完整服务和仍在运行的下载／持续负载分别计量。
