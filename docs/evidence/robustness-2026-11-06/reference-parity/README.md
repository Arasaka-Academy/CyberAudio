# Swift 与 Python 参考实现的逐步数值对照

本阶段没有更改正式推理或采样参数。在 Apple M1 Max／32GiB 上，使用相同的本地 8-bit 模型和 MLX 0.32.4，对比当前受管理 Swift SDK 与未修改的 `mlx-audio 0.6.4` Python 参考实现。五个预先固定的样本共完成 146 步，750 项张量比较的形状、dtype 和数值全部一致，最大绝对、平均绝对和 RMS 误差均为零。该结果缩小了当前失败的排查范围，没有解决无 EOS、英文数字或内容质量问题。

## 对照方法

[计划](plan.json)在运行前固定文本、音色、语言、种子、采样参数和预算：每个样本最多 32 步，原生／参考各最多 600 秒，每个样本最多 120 秒，快照总量最多 64MiB。原生采集沿用真实 `generateVoiceDesign` 计算和采样表达式；[私有补丁](private-sdk.patch)仅增加可选采集点与独立入口，不替换共享 SDK 或对象。[构建回执](build.json)保留源码、命令和受保护输入摘要。

Swift 记录准备好的输入 ID、输入嵌入、文本／pad 嵌入，以及每步主模型 logits、末位隐藏状态、15 组辅助 logits、16 组 codec ID 和下一步输入。Python 通过实际 `mlx_audio.tts.utils.load(..., lazy=False, strict=True)` 加载同一模型，独立准备文本输入，再将采集的完整 codec 向量逐步强制输入；不执行随机采样。比较程序见 [compare-reference.py](compare-reference.py)，逐项结果见 [reference-comparison.json](reference-comparison.json)。

## 结果与边界

| 样本 | 采集步数 | 原生采集终态 | 准备／逐步比较 | 最大误差 |
|---|---|---|---|---|
| 已知失败短句，ono_anna／196／中文 | 32 | 诊断步数上限 | 4／160 项全部一致 | 0 |
| 相同短句控制，ono_anna／31／中文 | 32 | 诊断步数上限 | 4／160 项全部一致 | 0 |
| `1234`，eric／31／英文 | 32 | 诊断步数上限 | 4／160 项全部一致 | 0 |
| `1234`，dylan／31／英文 | 18 | 观察到 EOS | 4／90 项全部一致 | 0 |
| 日期与人名，ryan／31／英文 | 32 | 诊断步数上限 | 4／160 项全部一致 | 0 |

全部 2190 组辅助 logits 的 top-1 也一致。所有快照共 151 文件、896 个数组、12,541,858 字节；[原生记录](native-capture.json)逐文件保留形状、dtype、大小与 SHA。种子 196 的已采集 token 和 PCM 前缀与历史 1200 步失败逐字节一致，采集没有改变这一前缀轨迹。原生／参考运行器分别正常退出，约 9.82／10.85 秒，见 [native-runner.json](native-runner.json)、[reference-runner.json](reference-runner.json)。

这里的四个步数上限是预定诊断截断，不是新发现的合成失败，也不是完整合成通过。比较覆盖输入准备、talker 和 code predictor 在这些强制轨迹上的前向计算，未覆盖完整 Python 随机采样、vocoder 解码、后续长轨迹、分布一致性或人耳音质。EOS 排名来自同一原始 logits；Swift 对 EOS 的过滤保护及两套采样实现仍需另行比较。不能据此排除所有 SDK 问题，或将两次失败的 M4 两小时验收改为通过。

## 运行时、保护与复核

直接 PyPI 访问失败后，从清华 PyPI 镜像在独立目录创建 Python 3.13 环境；原 `.venv`、生产运行时和 App 未修改。[哈希锁](requirements.lock)、[安装回执](runtime-setup.json)和 [wheel 来源](source-downloads.json)保留完整依赖及获取路径。安装与保留参考快照的 Qwen 主源码 SHA 均为 `9339599982a84cbc7f2b2bd153598997ca539b3cc1e67a0ba439bada4a5f0ba8`。Python 加载后的文本／语音 tokenizer 均明确存在。两轮推理在原离线沙箱运行，参考环境另设离线标志和私有缓存。

[独立复核](verification.json)重新解析全部 safetensors 表头和 codec 数据，校验全部快照／PCM 摘要、750 项比较、原失败前缀、96 个正式源文件、568 个原模型目录文件和 8 个冻结文件；两个所属进程组均已退出，生产实例／配置／Token 保持一致。[首轮验证器记录](verification-first-attempt.json)保留对 codec `U32` dtype 的解析假设错误；修正验证器后只重做文件复核，没有重跑推理或改写原结果。

原始张量、音频和运行日志保留在 `.context/robustness/reference-parity-20261107/`。本目录采集源码供审阅，不能直接从 docs 执行。此次没有重跑完整 `make check`：正式源码与上轮 36／44／14 检查对应的 96 个文件摘要一致。严格 M4 负载、公网完整下载、自然混说、真实桌面／物理音频／试听和 Developer ID 分发验收仍未完成。
