# Swift Qwen：M4 实测证据

结论及口径见 [完整报告](../../VALIDATION-2026-11-05.md)。本目录保留同权重 Swift／Python 对比结果；生产部署未替换。

- `m4-qwen-python-baseline.json`：本轮重新采集的生产 Qwen 100 条结果。
- `m4-qwen-swift-run1.json`／`run2.json`：两个离线原生进程的各 100 条结果。
- `m4-qwen-swift-comparison1.json`／`comparison2.json`：原生配对评分；两轮中文 CER 不变、英文 WER 少一个词错误。
- `m4-qwen-swift-cold.json`：首次独立模型加载及同一录音三次识别，不混入 100 条评分。
- `m4-qwen-swift-voices.json`：九音色各两次的所有真实 token／音频块、EOS、首块及 RTF。
- `m4-qwen-swift-cancel.json`：两个块后取消、等待 2 秒、同实例恢复；不证明 producer 已立即排空。
- `m4-qwen-swift-english.json`：Ryan 英文两次合成。
- `m4-qwen-swift-pcm-verified.json`：22 份 WAV／PCM 格式、字节数、摘要及分块完整性校验。
- `m4-qwen-swift-tts-roundtrip.json`：21 份完整 TTS 输出经原生 ASR 检查，文本与输入相同；不代替试听。
- `m4-production-models-verified.json`：生产模型文件集合和全部摘要未改变，未生成生产 tokenizer 文件。
- `environment-before.json`／`after.json`／`final.json`：环境、swap 及脱敏生产就绪状态；final 位于补充音频内容检查之后。
- `m4-gpu-smoke.json`／`local-gpu-smoke.json`：目标机及本机的原生 Metal 算术验证。
- `measurements.json`：从保留记录派生的延迟、内存、重复性和 TTS 汇总。
- `assets.lock.json`／`metallib.json`／`python-versions.json`／`linked-libraries.txt`：模型、着色器、基线环境与原生链接信息。
- `experiment.json`：固定版本、执行顺序、二进制及原始／脱敏证据摘要、已知边界。

去除了生产 readiness 载荷、实例和请求 ID，将 TTS 文件路径缩短为实验根目录相对路径；文本、参考文本、计时、录音／PCM 摘要、token、音频块及模型身份均未改变。生成音频保存在本机忽略的 `artifacts/qwen-swift/audio/{voices,cancel,english}/`，不随 JSON 分发。

语料 manifest／来源复用 [corpus.json](../../../CoreML/Results/2026-11-05/corpus.json)／[provenance.json](../../../CoreML/Results/2026-11-05/provenance.json)。全部 100 条结果核对同一 ID、音频摘要、语言与参考文本。

使用 Core ML 实验的原生工具重算两轮指标（不加载模型，`PROBE` 为该工具的绝对路径）：

```sh
RESULTS=Experiments/QwenSwift/Results/2026-11-05
for run in 1 2; do
  "$PROBE" compare --baseline "$RESULTS/m4-qwen-python-baseline.json" \
    --candidate "$RESULTS/m4-qwen-swift-run${run}.json" \
    --report "/tmp/qwen-swift-comparison${run}.json"
done
```

重新构建、准备隔离资产及推理命令见 [实验 README](../../README.md)。需使用原来的固定模型修订、同一语料以及匹配 MLX 0.32.4 的着色器；本次没有修改上游 SDK 推理代码。

FLEURS 及参考文本归属 Google／FLEURS（Conneau 等，2022），许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。[数据卡](https://huggingface.co/datasets/google/fleurs)，固定修订 `168de341b3db6859a9bac1c50a2ef5e3b47647e0`，参考文本未修改。
