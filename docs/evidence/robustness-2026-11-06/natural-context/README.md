# 自然混说的固定上下文指令对照

单条通用逐字转写指令没有改变本轮十个样本的输出。十次新空上下文基线全部复现先前完整 Python 参考结果；随后十次固定指令调用的文本也逐字相同，作者分词口径的总编辑数保持 **32 → 32**。两个中文被译成英文的样本、`Hong Kong` 误识和短句空转写均未改善。该 pilot 已结束，没有扩大队列、搜索更多提示或改动生产默认值。

本实验检验的是这一条具体指令及预先固定的十个样本，不能据此断言其他上下文或模型都无效，也没有将小样本结果当作整体准确率。它接续[全量自然参考归因](../natural-reference/README.md)，保留原自然语音、固定 100 条逐例、严格 M4 两小时、现场与分发验收要求。

## 固定协议

[协议](protocol.json)在调用模型前固定，基于 `main@7ed9b28`，模型及原始音频保持原值。四个问题样本沿用已经公开的自然失败示例；六个控制分别取完整原始 ASCEND test split 中 zh／en／mixed 的首、末元数据行，不按新输出选择。原顺序、WAV／PCM16／16kHz／mono、参考文本、auto 语言、greedy／temperature 0、最多 1024 token 和评分归一化均保持不变。

唯一的候选指令为：

> Transcribe all speech verbatim in its original language. Preserve code-switching. Do not translate, summarize, or add words.

提示没有包含专名、目标转写、参考答案或说话主题。使用未修改的 MLX 0.32.4／mlx-audio 0.6.4／Transformers 5.22.2／NumPy 2.3.4 参考环境，通过原 `system_prompt` 参数传递；没有替换模型方法。先在同一模型实例上完成十个空上下文基线并逐项核对原始 `text`、`language`，通过后再按相同顺序完成十个候选。零重试、零跳过，逐次清理 MLX 缓存。

预定的初步资格要求是：两个翻译样本都减少编辑数，按顺序保留原中文字符和英文词；六个元数据控制及另外两个问题控制的编辑数均不得增加。仅有中英文字符并存不足以通过。即使满足这些条件，也仍需另行固定原生完整队列和原有质量／性能协议后才能继续评估，不据此采用候选。

## 实际结果与输入核验

| 已知问题 | 空上下文编辑数 | 固定指令编辑数 | 文本是否变化 |
|---|---:|---:|---|
| `所以我们现在开始talk about our research topics` 被译成英文 | 8 | 8 | 否 |
| `找个地方which is speak in english` 被译成英文 | 6 | 6 | 否 |
| `Hong Kong` 被识为错误汉字 | 2 | 2 | 否 |
| 0.6 秒 `thank you` 为空 | 2 | 2 | 否 |

全部十对文本逐字相同，没有改善或退化的样本；总编辑数 32／32 包含六个控制，不是表中四项之和。逐例结果和原始语言值见[摘要](summary.json)，完整控制转写保持私有。这里沿用原评分对数字写法、繁简及语料标注的限制，没有据编辑距离声称人耳质量已通过。

[生成回执](inference.json)确认 20 次调用、10 个基线匹配，程序内部用时约 11.15 秒；[外层运行器](runner.json)exit 0、约 12.72 秒。该计时范围不同于原生 HTTP／麦克风端到端，不作为性能比较。

为排除参数被忽略，核对实际结果中每段的 prompt token 数，十个候选均比对应基线多 **26**。随后执行[无权重的提示复核](prompt-qualification.json)：原 `_build_prompt` 的 AST 保持不变，使用同一固定 tokenizer 重建全部二十个提示长度，确认新增指令、不同 token 序列和不变的音频 token 数；仅把最终 MLX 数组构造替换为 NumPy 返回，不实例化模型或追加生成。该检查是源码／tokenizer 重建，不能表述为额外的内部张量 trace。

[分析程序](analyze.py)沿用固定作者分词评分器，并用独立完整矩阵算法重新计算全部二十个编辑距离；重新解码十个原 WAV，复算 PCM 和 float32 输入 SHA，与两组调用和旧完整参考逐项匹配。101 个当前跟踪源文件、1026 个原模型／冻结文件 metadata 保持不变。生产实例仍 ready／idle，配置和 Token 保留，所属模型进程组已退出；没有安装、替换或重启正式 App。

## 审阅源码和保留边界

本目录中的 [运行器](run.py)、[生成程序](transcribe-context.py)、[分析程序](analyze.py)和[提示复核](verify-prompt.py)是原工作目录 `.context/robustness/natural-context-20261107/` 的审阅副本。相对路径指向私有工作目录，不能从 docs 直接执行。正文、JSON 与源码由 [SHA256SUMS.txt](SHA256SUMS.txt)对应；[私有文件摘要](private-artifacts.json)保留原始完整结果、控制转写、日志及保留快照的来源，原音频、模型、配置和凭据不入 Git。

数据与派生文本为 **CC BY-SA 4.0**，署名：ASCEND，Lovenia et al.，LREC 2022，HKUST CAiRE。完整语料出处及固定归档见[自然语料证据](../natural-ascend/README.md)。本目录仅再次引用已公开的四个自然问题示例。生产模型与参数未变；短句 TTS 终止、自然混说保真、目标机严格负载、物理录音／试听、公网完整模型下载、Developer ID 公证和实际 GUI 升级仍待验收。
