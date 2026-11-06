# 长轨迹分叉的逐层与单变量定位

完整前向计算在零起始第 84 步的差异，已定位到主生成器 RoPE 逆频率。Swift／Python 的 64 个 float32 值中有 42 个不同，最大差值仅 `2.9802322387695312e-08`；绝对位置 104 的一个 cosine 值跨过 bfloat16 舍入边界，差异从第一层旋转 Q/K 开始向后传播。

只把 Python 进程内主生成器逆频率换成原生捕获值，其余参数、权重、代码和强制音频码不变，完整六条件、367 步、1,859 项张量比较便全部一致、误差为零。这解释了本次分叉，没有证明短句失控、开头错词或英文数字质量已修复，也没有采用新的正式策略。

## 观察控制

[计划](plan.json)重放已保存的输入嵌入，选择失败、重复和较长 eric 控制，共 263 步。[原生 caller](LayerParityProbe.swift)通过[私有观察点](instrument-private.py)记录失败第 82／83／84 步及数字控制第 79／80 步。全部 263 步 logits／hidden 与原始捕获一致，不重新抽样或解码音频。

[Python 程序](compare-reference.py)先调用原实现重放，再增加只保存中间数组的观察点重放；两遍 logits／hidden 摘要全部一致，未观察结果也逐项重现此前参考比较。原生／参考运行分别为 8.99／10.65 秒，见[原生回执](native-runner.json)、[逐步记录](native-layer-capture.json)及[参考回执](reference-runner.json)。

每个选定步骤保存 396 个边界，覆盖位置编码和 28 层的输入、Q/K/V、Q/K 归一化、旋转、完整缓存、注意力与层输出。原生／参考各 8 个快照，共 205,434,434 字节。3,168 项比较中 2,390 项一致、778 项不同，见[完整结果](reference-comparison.json)。观察控制通过不等于两种实现数值一致。

| 条件／步骤 | 绝对位置 | 不同边界 | 范围 |
|---|---:|---:|---|
| 失败，第 82 步 | 102 | 1 | 仅逆频率，cos／sin 及所有层输出一致 |
| 失败，第 83 步 | 103 | 1 | 同上 |
| 失败，第 84 步 | 104 | 386 | 逆频率、cos 和后续层；sin 一致 |
| eric，第 79／80 步 | 93／94 | 每步 1 | 仅逆频率，所有层输出一致 |
| 失败重复，第 82／83／84 步 | 102／103／104 | 1／1／386 | 完整重现首次结果 |

分叉步骤的模型输入、第一层输入、Q/K/V 投影和 Q/K 归一化仍一致，cosine 是首个传播差异的中间结果。两端主生成器均使用完整 KVCache；语音解码器的 72 帧窗口与此无关。

## 单变量对照与标量拆解

[追加计划](native-frequency-injection/plan.json)只替换独立 Python 进程内的 `model.talker.model.rotary_emb._inv_freq`，不修改辅助生成器、模型文件或已安装模块。[程序](native-frequency-injection/compare-reference.py)强制全部原生码，[结果](native-frequency-injection/reference-comparison.json)中完整 1,859 项准备／输入／logits／hidden／辅助 logits／下一步嵌入比较均为零误差，[运行器](native-frequency-injection/reference-runner.json)exit 0、14.31 秒。这是归因对照，不是 Swift 候选验收。

不加载模型的 [Swift 探针](RopeScalarProbe.swift)复制原始公式，完整重现原生 64 个逆频率。[Python 拆解](rope-components.py)使用相同 base、dtype、索引和指数：索引／指数全部一致，幂运算输出 46 个值不同，倒数后剩 42 个，分别与模型捕获一致，见[拆解结果](rope-components.json)。位置 102／103 的 cos／sin 均一致，104 的半维 cosine 仅一个值不同，见[公式控制](rope-formula.json)。

两边 `mlx.metallib` 的字节摘要相同，运行时幂运算输出仍不同。更底层的编译器、动态内核或后端机制尚未判定，也没有证据判断哪端数学精度或音质更好。任何常数／后端候选仍须通过原有内容对照和目标机门槛。

## 构建、复核与保留

首次私有 wrapper 把非可选 talker 当作 Optional，编译失败，[回执](first-native-build.json)和[日志](first-native-compile.txt)保留，该轮未启动模型。删除多余绑定后[构建成功](native-build.json)，共享 SDK／对象和 Helper 未覆盖。标量 caller 首次链接仍引用旧 main alias，[日志](first-scalar-link.txt)和[回执](native-rope-runner.json)保留；修正别名并复用已编译对象后，[后续回执](native-rope-repair.json)exit 0，无模型重跑。

[独立复核](verify.py)解析全部 16 个层快照和两份标量快照，重算 3,168 项误差，并检查观察控制、单变量对照全部码与零误差统计。96 个正式源码、568 个模型目录文件、8 个冻结文件和共享构建输入未变，所属模型进程全部退出，生产实例／配置／Token 不变，见[结果](verification.json)。归档扫描曾误匹配自身源码的私钥标记测试字符串，改为只匹配实际 PEM 标记行后通过；未出现私钥内容或重新推理，原失败保留在私有目录。

原始张量位于工作区 `.context/robustness/layer-parity-20261107/`，仅保存 [18 项私有文件摘要](private-artifacts.json)。源码与日志是审阅副本，仍指向私有目录。本轮没有新增 ASR，也没有接受物理／试听、严格 M4 两小时、自然混说、公网下载或 Developer ID／升级验收；正式默认参数及修复计数均未改变。
