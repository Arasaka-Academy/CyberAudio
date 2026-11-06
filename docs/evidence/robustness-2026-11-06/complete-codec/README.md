# 完整音频码与长轨迹前向对照

这次补齐全部 16 组原生音频码。六个既定条件的 367 个主 token 和完整 PCM 均重现旧顺序候选，新增观察点没有改变输出。与完整 Python 生成相比，四个控制组全部音频码一致；已知失败及重复各有 97 个不同值，首次都在零起始第 84 步、第 3 组，原生为 409、Python 为 405。

进一步强制使用原生完整音频码运行未修改的 Python 前向计算，1,859 项比较中 1,817 项一致、42 项不同。此前至多 32 步的零误差结论不能推广到完整轨迹。正式 SDK、默认参数和解码策略均未改变，后续定位见 [layer-parity](../layer-parity/README.md)。

## 完整捕获控制

[计划](plan.json)固定相同六个条件，沿用此前私有候选的温度先于 top-k 顺序，其他参数仍为温度 0.7、top-k 50、top-p 1、重复惩罚 1.05、最多 1,200 步及每四帧流式解码。观察点保存输入、logits、hidden、15 组辅助 logits、完整码和下一步嵌入。

[探针](CompleteCodecProbe.swift)逐例要求原始主 token 和完整 PCM 一致，再允许解释张量。[构建](build-native.py)使用独立源码副本、模块和对象，不替换共享产物。[原生回执](native-runner.json)exit 0、18.68 秒；[完整记录](native-complete-capture.json)保留 373 个快照的形状、dtype、大小与 SHA，总计 30,490,779 字节。

| 条件 | 步数，含 EOS | 不同码值，含 EOS 步 | 实际音频帧中的差异 | EOS 步辅助码差异 |
|---|---:|---:|---:|---:|
| 已知失败，ono_anna／196 | 91 | 97 | 82 | 15 |
| 正常短句，ono_anna／31 | 35 | 0 | 0 | 0 |
| 英文数字，eric／31 | 81 | 0 | 0 | 0 |
| 英文数字，dylan／31 | 18 | 0 | 0 | 0 |
| 人名日期，ryan／31 | 51 | 0 | 0 | 0 |
| 已知失败重复 | 91 | 97 | 82 | 15 |

全部 5,872 个码值参与比较，主 token 全部相同。97 不是不同音频帧数，最后 15 项位于不送入解码器的 EOS 步；两次失败是同条件重复，不是独立样本。详见[比较程序](compare-codecs.py)、[完整差异](code-comparison.json)和[原生向量](native-codes/known-failure.json)。

## 前向比较的适用范围

[参考程序](compare-reference.py)使用实际未修改的 `mlx-audio 0.6.4`，不随机采样，强制全部原生码。准备张量及每步输入／下一步嵌入均一致；只有失败及重复在第 84–90 步的 logits、hidden、辅助 logits 不同，每个条件 21 项。首步最大绝对误差分别为 0.1875、0.25、0.4375，整个对照最大为 0.501953125。

[参考回执](reference-runner.json)exit 0、14.41 秒，[逐步结果](reference-comparison.json)和[摘要](forward-summary.json)保留非零误差。主 token 相同不足以断言全部计算／音频码相同，差异也不能全部归因于随机数或解码窗口。两端主生成器均使用完整 KVCache，解码器的 72 帧策略是另一个问题。

## 复核与保留

[文件复核](verify.py)重新解析全部快照、码向量和 PCM 摘要，确认计数和位置，以及 96 个正式源码、568 个模型目录文件、8 个冻结文件保持不变；所属进程全部退出，生产实例／配置／Token 保留。首次复核把保留头 `__metadata__: null` 当作张量而失败，[原记录](verification-first-attempt.json)与[旧脚本](verify-first-attempt.py)保留，修正读取规则后通过，[结果](verification.json)没有依赖重新推理。

原始张量和音频位于工作区 `.context/robustness/complete-codec-20261107/`，仅保存 [379 项私有文件摘要](private-artifacts.json)。PCM 与已回读候选完全相同，本轮未重复 ASR。源码是审阅副本，路径指向私有目录，不从 docs 直接执行。没有接受物理／试听、严格 M4 两小时、自然混说、公网完整下载或分发门槛。
