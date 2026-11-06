# 完整 Python 参考生成与相同 codec 解码诊断

在 Apple M1 Max／32GiB 上，未修改的 `mlx-audio 0.6.4` 参考实现完成七次完整生成，包含失败种子重复及不加观察点的控制。六次观察生成共 367 个主 token，与前阶段私有顺序候选全部相同；七个输出完成九次最终 App ASR。已知失败的回读仍为“此句验证…”，两个英文数字仍不正确，因此参考实现的正常结束不能作为质量修复。

进一步用完全相同的 codec 分别调用两套解码器，确认长音频在第 72 帧之后有不同输出：Swift 使用模型配置的 72 帧旋转缓存／局部注意力，Python 此版本使用完整 KVCache／因果注意力。此次没有改变正式采样、解码策略或模型，不增加修复计数，也没有接受失败的严格 M4 门槛。

## 完整生成和观察控制

[计划](plan.json)在运行前固定相同六个文本／音色／种子条件，再增加一个不观察采样器的种子 196 控制；预算为单例 120 秒、总计 900 秒。实际调用 `generate_custom_voice`，参数仍为温度 0.7、top-k 50、top-p 1.0、重复惩罚 1.05、max-tokens 1200，流式间隔 0.32 秒，与当前受管理入口一致。未强制输入先前 token，也未替换采样／解码公式。

[生成程序](generate-reference.py)只保留采样器原返回的 lazy 数组，不提前取值或追加随机抽样。已知失败两次观察生成的完整 16 组 codec 与 PCM 均逐字节一致，不观察的第三次 PCM 也一致，证明这个控制上的观察没有改变音频。六次观察生成均有 EOS；第七次未采集 token，报告明确保留其 `eos_seen: null`，不据相同音频补写其 EOS 观察结果。

实际模型、解码和采样模块分别来自未修改的 Qwen／speech-tokenizer／`mlx_audio.lm.sample_utils`，SHA 与计划一致，沿用独立 MLX 0.32.4／mlx-audio 0.6.4 环境及离线沙箱。[完整生成记录](reference-generations.json)、[运行器](reference-runner.json)和[各组完整 codec](codes/)保留结果；运行器 exit 0，约 42.03 秒，程序内部计时 37.98 秒。

| 条件 | 观察主 token 数，含 EOS | 音频秒数 | 自动 ASR 原值 |
|---|---:|---:|---|
| 中文短句，ono_anna／196 | 91 | 7.2 | 此句验证语音服务的稳定性和恢复能力。 |
| 相同短句控制，ono_anna／31 | 35 | 2.72 | 持续验证语音服务的稳定性和恢复能力。 |
| `1234`，eric／31／英文 | 81 | 6.4 | You, toward side. |
| `1234`，dylan／31／英文 | 18 | 1.36 | 一枪来三十四。 |
| 人名日期，ryan／31／英文 | 51 | 4.0 | Alice will meet Bob on November sixth, twenty twenty six. |
| 失败种子观察重复 | 91 | 7.2 | 此句验证语音服务的稳定性和恢复能力。 |
| 失败种子无观察控制 | 未采集 | 7.2 | 此句验证语音服务的稳定性和恢复能力。 |

全部七个新音频由相同最终 R12 App Helper 完成九次实际 HTTP ASR，包括两个数字的强制英文对照，约 15.99 秒、exit 0。六次观察生成的自动回读与前阶段顺序候选逐字一致，强制英文也没有纠正两个数字结果。见[原始回读](reference-asr.json)、[WAV 清单](asr-corpus.json)及[回读程序](run-roundtrip.py)。这里仍混合了 TTS／ASR 误差，没有人工听辨，不把回读错误直接认定为实际发音，也不因语言标签为英文而认定通过。

## 解码对照的范围和发现

先把前阶段五组已捕获的原生 codec 送入 Python 实际解码器，每四帧一块；其最长仅 32 帧，合计 278,400 个 PCM16 样本，最大差异为一个量化单位。该结果只验证短前缀，不覆盖 72 帧之后的缓存行为。

完整生成与旧顺序候选的主 token 虽相同，较长音频仍有更大差异。因此[追加计划](decoder-followup-plan.json)预先固定六个完整 codec 输入，通过私有入口调用原生解码器，以相同四帧分块、reset 和 PCM16 量化方式排除输入不一致。

| 相同完整 codec 输入 | 帧数，不含 EOS | Swift／Python 最大 PCM16 差值 | 首个差值超过 1 的零起始帧 | Swift 解码是否与旧顺序候选 PCM 相同 |
|---|---:|---:|---:|---|
| 已知失败，ono_anna／196 | 90 | 84 | 72 | 否 |
| 正常短句，ono_anna／31 | 34 | 1 | 无 | 是 |
| 英文数字，eric／31 | 80 | 386 | 72 | 是 |
| 英文数字，dylan／31 | 17 | 1 | 无 | 是 |
| 人名日期，ryan／31 | 50 | 1 | 无 | 是 |
| 已知失败重复 | 90 | 84 | 72 | 否 |

第 72 帧约为 5.76 秒。源代码确认 Swift 的 `DecoderTransformer` 使用 `RotatingKVCache(maxSize: config.slidingWindow)` 和窗口 mask，实际配置为 72；Python 的 `make_cache` 返回完整 `KVCache`，这里只生成因果 mask。相同 codec 的对照隔离出了两种解码行为的差异，不能把 Python 这个版本当作所有策略均相同的参考，也没有据此取消原生窗口。

两次已知失败的相同 codec 原生解码，在第 84 帧起又与旧顺序候选出现额外差异，最大 1151 个 PCM16 单位；其余四个输出则完整重现旧候选 PCM。旧候选没有保存全部辅助 codec，主 token 相同不足以断言完整 16 组相同。这部分差异仍需完整辅助码对照，不能全归因于解码窗口或声明所有计算已一致。逐块数值和位置见[独立复核](verification.json)，原生六次解码及运行器约 7.01 秒，均正常结束，见[原值](native-same-code-decoding.json)与[回执](decoder-native-runner.json)。

## 独立构建和保护

首轮尝试用私有 caller 直接访问已优化模块，编译成功，但内部 `Qwen3TTSSpeechTokenizer.decoder` getter 未导出，链接失败。[构建回执](first-decoder-build.json)、[链接日志](first-decoder-link.log)和[首轮 caller](ReferenceDecodeProbe.swift)均保留；该轮没有启动模型。

随后按[访问入口追加计划](decoder-adapter-plan.json)，在私有源码副本末尾增加[诊断入口](decoder-adapter.swift)，原有生成／解码函数体逐字不变；全部对象和模块产物写入独立目录。[私有构建](decoder-adapter-build.json)约 173.96 秒成功，并通过[新 caller](ReferenceDecodeAdapterProbe.swift)执行相同 codec 条件。共享 SDK／对象、正式 Helper 与 App 未被替换。构建／运行源码均随本目录保留；首次优化符号限制不计为应用运行失败。

[文件复核程序](verify.py)确认完整 codec／PCM／浮点前缀、观察控制、367 个主 token 对照、九次 WAV 载荷、原生相同 codec 输出及全部源码摘要。96 个正式源文件、568 个原模型目录文件、8 个冻结文件及生产实例／配置／Token 保持不变。全部所属模型进程已退出，18890 端口关闭，临时请求目录清空。当前正式源码仍与前轮 36 个原生合约／44 个工具测试／14 个渲染检查对应，因此此次未重复完整构建。

[原始音频摘要清单](private-artifacts.json)指向工作区 `.context/robustness/reference-generation-20261107/` 的 23 个私有音频／浮点文件；原始音频和模型不入 Git。这里的源码是审阅副本，命名和路径指向原私有工作目录，不能从 docs 直接执行。自动诊断继续推进，现场麦克风、人耳试听及物理／桌面流程按用户当前安排留待后续；严格 M4 两小时、自然混说、公网完整下载和 Developer ID 分发各自仍未接受。
