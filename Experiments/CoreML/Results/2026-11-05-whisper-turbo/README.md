# 普通 Turbo：M4 实测证据

结论见 [完整报告](../../WHISPER-TURBO-2026-11-05.md)。本目录保存本次普通 `openai_whisper-large-v3-v20240930_turbo` 的独立测试；前一轮 SenseVoice／TTS 记录保留在相邻目录。

- `m4-whisper-turbo-baseline.json`：本轮重新采集的生产 Qwen 100 条结果，保留真实模型修订。
- `m4-whisper-turbo-sensevoice.json`：相同 100 条录音的 SenseVoice 结果。
- `m4-whisper-turbo-run1.json`／`run2.json`：两个独立原生进程的各 100 条 Turbo 结果，包含语言检测、回退次数及阶段耗时。
- `m4-whisper-turbo-comparison1.json`／`comparison2.json`／`sensevoice-comparison.json`：原生配对评分结果；三次均不满足准确率不退步。
- `m4-whisper-turbo-cold.json`：首次模型加载／预热和第一条中文录音的三次识别，不用于 100 条准确率计算。
- `m4-whisper-turbo-verified.json`／`local-whisper-turbo-verified.json`：两台机器全部固定资产通过原生校验。
- `whisper-turbo-environment-before.json`／`after.json`：测试前后环境和脱敏生产检查，含 swap；结束后原实例保持正常。
- `measurements.json`：P50／P95／最大延迟、加载、RSS、累计耗时和重复一致性汇总。
- `experiment.json`：固定版本、执行顺序、sandbox 策略、原始／脱敏文件摘要和语料引用。

语料 manifest 和来源复用 [corpus.json](../2026-11-05/corpus.json)／[provenance.json](../2026-11-05/provenance.json)。100 条 ID、音频摘要、参考文本及语言与本次四组完整测试全部一致。去除了生产 readiness 载荷、实例和请求 UUID；识别文本、参考文本、耗时与模型身份未改动。模型、录音及编译缓存位于忽略的 `artifacts/`，不随结果文件分发。

不加载模型即可重算两轮主指标：

```sh
RESULTS=Experiments/CoreML/Results/2026-11-05-whisper-turbo
for run in 1 2; do
  "$PROBE" compare --baseline "$RESULTS/m4-whisper-turbo-baseline.json" \
    --candidate "$RESULTS/m4-whisper-turbo-run${run}.json" \
    --report "/tmp/whisper-turbo-comparison${run}.json"
done
"$PROBE" compare --baseline "$RESULTS/m4-whisper-turbo-baseline.json" \
  --candidate "$RESULTS/m4-whisper-turbo-sensevoice.json" \
  --report /tmp/whisper-turbo-sensevoice-comparison.json
```

重新准备同一资产并运行推理（在仓库根目录，先按 [构建说明](../../README.md) 设置 `PROBE`）：

```sh
python3 scripts/prepare_coreml_models.py --lock Experiments/CoreML/whisper-turbo.lock.json \
  --root artifacts/coreml/models-whisper-turbo
sandbox-exec -p '(version 1)(allow default)(deny network*)' "$PROBE" asr \
  --engine whisper-turbo --models artifacts/coreml/models-whisper-turbo \
  --lock Experiments/CoreML/whisper-turbo.lock.json \
  --corpus artifacts/coreml/corpus/corpus.json \
  --report artifacts/coreml/reports/whisper-turbo-reproduction.json
```

两轮 Turbo 均为 100／100 条完成、原始文本一致、语言检测匹配、温度回退次数 0。准确率、延迟口径与限制详见报告；这些结果不构成能耗、混说、实体正确率或生产 API／生命周期的通过证据。

FLEURS 及所引用参考文本来自 Google／FLEURS（Conneau 等，2022），许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。[数据卡](https://huggingface.co/datasets/google/fleurs)，固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`。参考文本未修改。
