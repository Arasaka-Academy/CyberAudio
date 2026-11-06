# CyberAudio · Neon Signal

2026-11-06 确认采用的图标方案。应用图标与 macOS 菜单栏共用切角 C 和音频脉冲标记。

## 设计

应用图标使用黑曜石底板、青色 C、洋红脉冲，搭配轻微浮雕、辉光和电路细节。C 代表 Cyber，脉冲代表录音、语音识别和音频生成。图形不包含小字号文字。

菜单栏单独绘制 18 × 18 pt 的单色模板，保留 C 的切角和四根脉冲，适配浅色、深色与菜单选中状态。Idle 与 Active 用振幅区分，外轮廓与画布尺寸一致。

| 资源 | 用途 |
| --- | --- |
| `CyberAudio.svg` | 1024 × 1024 应用图标矢量母版 |
| `CyberAudio.png` | 1024 × 1024 RGBA 预览 / 导出母图 |
| `CyberAudio.icns` | macOS 应用图标，含标准多尺寸表示 |
| `CyberAudio.iconset/` | 16、32、128、256、512 pt 的 1× / 2× PNG |
| `MenuBarIdle.svg` / `MenuBarActive.svg` | 菜单栏矢量母版 |
| `MenuBarIdle.png` / `MenuBarActive.png` | 18 × 18 px 单色透明模板 |
| `MenuBarIdle@2x.png` / `MenuBarActive@2x.png` | 36 × 36 px Retina 模板，逻辑尺寸为 18 × 18 pt |
| `preview.html` / `preview.png` | 设计展示板，含浅色、深色菜单栏示意与小尺寸预览 |

颜色：Obsidian `#0C1220`，Neon Cyan `#4FF8EB`，Signal Pink `#FA4DD2`。应用图标周围留有透明安全区；菜单栏素材只有黑色图形和透明通道。

## 应用接入

`scripts/build_app.sh` 将应用图标和菜单栏的 1× / 2× PNG 一起复制到应用包 `Contents/Resources`。

1. `packaging/Info.plist` 的 `CFBundleIconFile` 指向 `CyberAudio.icns`，供 Finder、关于窗口等系统入口使用。
2. `CyberAudioApp.swift` 的菜单栏标签使用 `MenuBarIcon`，通过 AppKit `Bundle.main.image(forResource:)` 加载 PNG 和 Retina 表示。图标视图跟随自身 `colorScheme` 更新：浅色主题将原有 alpha 蒙版填为白色并使用 `.original`，保留透明背景；深色主题使用 `.template`，由系统根据外观与选中状态着色。逻辑尺寸固定为 18 × 18 pt，待机与工作状态使用相同的主题规则。
3. `backend.busy` 为真时使用 `MenuBarActive`，其余情况使用 `MenuBarIdle`。菜单栏标签保留应用名称与状态，供辅助功能读取。

打包直接使用本目录已导出的素材，不需要额外安装图像生成或转换工具。

菜单栏图片使用 AppKit 显式加载，以兼容直接复制到应用资源目录的 PNG；脱离应用包运行的开发构建在缺少素材时回退到原系统波形。

## 导出检查

SVG XML 解析通过；PNG 尺寸与透明通道通过；菜单栏像素为黑色加 alpha；ICNS 容器长度与多尺寸条目检查通过。预览通过浏览器渲染并目视检查。

接入后，完整应用构建、嵌套代码签名校验和资源准备检查通过。应用包内五个图标文件与设计源文件的 SHA-256 一致；AppKit 可读取 ICNS 的 10 个尺寸表示，以及每个菜单栏图标的 18 / 36 像素表示。使用包内素材的 SwiftUI 原生渲染通过像素检查，确认深浅色下图形可见、待机与工作状态轮廓不同。

2026-11-07 主题修正通过 Release 编译，以及浅色／深色、待机／工作、1×／2× 的八组原生渲染检查。浅色图形为白色、背景透明，alpha 与原图一致；深色与原模板渲染一致。同一原生视图完成浅色→深色→浅色切换，确认浅色保留白色，深色接受系统前景着色。应用图标和原始 PNG 素材保持不变。

这是初版设计展示板，菜单栏画面是效果示意，不代表已安装应用的截图；浅色主题的白色透明图标以当前原生实现为准。
