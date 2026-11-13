# CyberAudio · Cutline

沿用 2026-11-12 已选定的 Cyber 产品家族 B「断讯 / Cutline」，于 2026-11-13 接入 CyberAudio 0.4.4（11）。

CyberAudio 使用独立的前倾 A 形，与 CyberOS 的 O 形共享横向断切、切角和重型字骨。应用图标为冰青 `#93E4EA` 标志与暖黑 `#1E1517` 底板，直接采用已确认套件的 SVG、PNG、ICNS 和完整 iconset，不添加辉光或装饰电路。

菜单栏使用套件的小尺寸 A 形，去掉底部细条并保留较宽断口。待机图标直接采用原套件的 18 / 36 px 黑色透明模板；工作图标在右下角增加独立圆点，保持主体不变。两个状态均使用 18 × 18 pt 逻辑尺寸。

| 文件 | 用途 |
| --- | --- |
| `CyberAudio.svg` / `CyberAudio.png` | 应用图标矢量母版 / 1024 px 导出 |
| `CyberAudio.icns` / `CyberAudio.iconset/` | macOS 多尺寸应用图标与十张源 PNG |
| `CyberAudio-symbol.svg` | 冰青标准符号 |
| `CyberAudio-symbol-small.svg` | 单色小尺寸符号 |
| `CyberAudio-wordmark.svg` / `CyberAudio-wordmark-black.svg` | 轮廓化冰青 / 黑色字标，README 随深浅主题选择 |
| `MenuBarIdle.svg` / `MenuBarActive.svg` | 菜单栏两种状态的矢量母版 |
| `MenuBarIdle{,@2x}.png` / `MenuBarActive{,@2x}.png` | 18 / 36 px 菜单栏模板 |
| `preview.html` / `preview.png` | 当前资产预览与尺寸对照，菜单栏为效果示意 |

`scripts/build_app.sh` 将 ICNS 和四张菜单栏 PNG 复制到 App 的 `Contents/Resources`；`packaging/Info.plist` 通过 `CFBundleIconFile` 引用 `CyberAudio.icns`。

`MenuBarIcon.swift` 通过 AppKit 加载 PNG 的 1× / 2× 表示。浅色主题沿用现有白色透明蒙版规则，深色主题使用系统模板着色；`backend.busy` 选择工作状态。缺少打包资源的源码开发运行仍使用系统波形回退。

源套件为所链接设计聊天交付的 `cyber-cutline-logo-kit/cyberaudio`。应用图标与待机模板保持原文件字节。运行时不依赖设计工具、第三方字体或 SVG 渲染器。
