# M4 Core ML 实测证据

本目录保存小型实测 JSON、100 条参考文本及来源，结论见 [验证记录](../../VALIDATION-2026-11-05.md)。模型、原始录音、生成音频、编译及下载缓存留在忽略的目录，不随代码分发。

- `m4-baseline.json`／`m4-candidate.json`：同一组 100 条真人录音，生产 Qwen3-ASR 与原生 SenseVoice 的逐条结果。
- `m4-comparison.json`：由原生 `compare` 计算的分语言 CER／WER 与 nearest-rank P95。
- `m4-asr-cold.json`：目标机首次 SenseVoice 加载／编译记录；该早期 smoke 使用 SDK 默认 without-itn，不参与正式准确率比较。
- `m4-fused-eos.json`：throughput／fused 模式、Serena 连续三次生成，记录每次 EOS 与回调。
- `m4-voices-eos.json`：同一实例、九音色各两次、有表达指令，throughput／fused 模式。
- `m4-cancel-eos.json`：同一实例首次请求在两块音频后取消，随后再次生成。
- `m4-english-eos.json`：Ryan 英语及表达指令，两次生成。
- `m1max-eos-check.json`：M1 Max 的比例上限复现，不用于 M4 性能验收。
- `corpus.json`／`provenance.json`：固定语料、音频摘要、参考文本和不可变来源。
- `experiment.json`：版本、二进制与资产锁摘要、结果脱敏规则和 TTS 门槛的取值口径。
- `evidence.json`／`gate.json`：依据实测填写的切换条件和原生门槛检查结果。缺失的实体、人工试听、混说、能耗、ANE 跟踪与原生 API 回归均不算通过。

可以在不加载模型的情况下复核数值：

```sh
"$PROBE" compare --baseline Experiments/CoreML/Results/2026-11-05/m4-baseline.json \
  --candidate Experiments/CoreML/Results/2026-11-05/m4-candidate.json \
  --report /tmp/coreml-comparison.json
"$PROBE" gate --evidence Experiments/CoreML/Results/2026-11-05/evidence.json \
  --report /tmp/coreml-gate.json
```

`gate` 返回退出码 2 表示不满足替换条件。TTS 的 `completed` 表示 SDK 已报告 EOS；听感、发音、表达指令效果和设备连续播放需要独立验收。

FLEURS 录音及本目录引用的参考文本来自 Google／FLEURS（Conneau 等，2022），许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。数据卡：[google/fleurs](https://huggingface.co/datasets/google/fleurs)，固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`。本目录仅对实测结果进行汇总及路径脱敏，语料参考文本未修改；模型输出与参考文本分别保存。
