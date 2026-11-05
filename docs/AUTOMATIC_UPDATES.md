# 自动更新与发布

## 使用

0.2.0（4）是首个支持应用内更新的版本；首次安装请选择最新的 0.2.1（5），已有 0.2.0 用户可应用内更新。设置、应用菜单和菜单栏共用一个 Sparkle 2.10.1 更新器，默认每 24 小时检查稳定版并自动下载。后台发现或下载更新只显示状态，不弹出重启窗口。手动检查使用 Sparkle 原生界面。

设置的“软件更新”显示当前版本、上次检查、检查／下载开关、下载状态、错误与重试。Sparkle 的后台下载没有公开字节进度回调，此时显示不定进度；手动下载显示字节进度。关闭自动检查后也可手动检查。

安装时先复制需要的文字并保存 WAV。运行中点“安装更新并重启…”后需再次确认；选择“稍后”继续使用。主动退出时允许安装已下载的更新，安装后保持退出。

更新先等待录音、播放、识别与生成结束，再暂停接收外部新推理，等待已开始的请求完成。0.2.1 同时跟踪响应发送，模型已空闲但 WAV／PCM 仍在发送时继续等待；0.2.0 升级前请先等待外部音频请求完全结束。等待栏和原有停止／取消任务入口同时存在，切换模式不会隐藏它们。“取消等待”恢复新任务和 API 服务。更新替换 App 与内置后端，保留数据目录中的模型、独立 Python 环境、Token、配置和日志。内存文字与尚未保存的音频不自动恢复。

## 内部任务保护接口

`POST /internal/update/prepare` 和 `/internal/update/cancel` 要求现有 Bearer Token、当前 `X-CyberAudio-Instance` 和一次更新的 `X-CyberAudio-Update` UUID。它们仅供 App 的更新控制器调用。

准备与推理共用同一把任务准入锁：准备先发生则新推理返回 `503`、`error.code=updating`、`Retry-After: 2`；推理先发生则允许完成。准备租约 30 秒有效，等待期间每秒续约。取消会记录已取消的租约，拒绝迟到的准备请求；旧取消不会解开新租约。控制器失联后租约过期恢复服务。

## 密钥与签名

- App 使用现有 ad-hoc 签名，Sparkle 内嵌组件保留上游签名，打包后验证 `codesign --verify --deep --strict`。
- App 公钥位于 `packaging/Info.plist` 的 `SUPublicEDKey`；私钥由官方 `generate_keys` 保存在本机 login Keychain，account 为 `local.cyberaudio.mac`。
- CI Secret 名为 `SPARKLE_EDDSA_PRIVATE_KEY`。脚本验证私钥对应的公钥；通过标准输入传给 Sparkle 工具，不写入仓库、App、日志或发布资产。
- 完整 ZIP 和 appcast 均使用 EdDSA；App 要求签名清单并在解压前验证更新包，严格拒绝缺失或不匹配的签名。
- Sparkle 发行工具固定 2.10.1，下载后核对固定 SHA-256；不要用移动的 latest 替换工具。

密钥丢失时不能用新密钥直接签发旧 App 可接受的更新。保留可靠的私钥备份与 Keychain 访问；轮换须遵循 Sparkle 的密钥迁移流程。

## 发布流程

1. 同时递增 `CFBundleShortVersionString` 和正整数 `CFBundleVersion`。稳定 tag 如 `v0.2.1` 必须与 App 版本一致；预发布 tag 如 `v0.2.1-rc.1` 必须在 GitHub 标为 prerelease。
2. 运行 `make check app` 和 `make update RELEASE_TAG=v0.2.1`，检查签名和 DMG。`make update` 省略 tag 时从 Info.plist 读取版本。
3. 提交、合并并推送，使用包含工作流的提交发布 GitHub Release。仅推 tag、保存草稿或编辑已发布 Release 不会触发构建。
4. `Release macOS app and updates` 使用 ARM64 macos-26 runner，比较已签名的公开 feed 和所有既有稳定 Release 的 `release.json`，防止版本回退与 CDN 缓存造成覆盖。
5. 同一次构建产出 App、DMG、完整 ZIP、两个 SHA-256 文件、签名 `appcast.xml` 和 `release.json`。上传到 Release 后通过官方 GitHub 资产 API 重新下载并逐一核对大小与 SHA-256。
6. 资产全部验证后才部署 Pages：`https://arasaka-academy.github.io/CyberAudio/appcast.xml`。全流程串行，失败不替换既有清单，预发布只产出测试通道资产。

GitHub Pages 应为 workflow 模式；`github-pages` 环境需允许 `v*` tag 部署。构建有 `contents: write`，部署独立使用 `pages: write` 与 `id-token: write`。

资产不可覆盖。网络中断时可用原始 `dist/` 再运行 `python3 scripts/release_updates.py upload --tag v0.2.1`，补齐缺失资产并检查已有资产一致；禁止 `--clobber`。已上传资产后重新构建可能因 ZIP／DMG 时间戳产生不同哈希，此时不能覆盖；保留原工作流 artifact，修复部署后仅重跑部署任务，或递增版本发布修复版。

首次 feed 不存在时仅允许 0.2.0（4）初始化。其他网络错误、签名失败或版本不递增均中止发布。

## 验证记录（2026-11-05）

实际升级使用区别于正式 App 的独立测试 bundle ID、签名本地 HTTP 清单、0.2.0（4）→ 0.2.1（5）两个测试版本和隔离数据目录，后端为 `tests/ui_stub.py` 合成接口。测试不覆盖正式 `/Applications` 安装。

- 完成实际 ZIP 下载、验证、替换、重启；重启后的同一隔离数据目录、配置端口和单个 App 进程有效。
- “稍后”保持 build 4，未发送准备请求；明确确认后进入等待。外部推理保持运行，新推理返回 503；取消等待后原请求完成 200，新请求亦成功。
- 跨模式等待栏保留。设置和 `⌘,` 使用同一后端实例。
- 正常退出在已有请求完成后安装 build 5，无自动重启。模型、环境、配置的保留哨兵文件均未变。
- 修改 ZIP 中单个字节且保持长度不变后，真实 Sparkle 在解压前拒绝签名，App 保持旧版。篡改签名清单内容后，真实 Sparkle 拒绝清单；官方验签工具也拒绝错误的归档签名，CI 公钥检查拒绝不匹配密钥。
- 关闭本地清单服务器，真实 Sparkle 报告连接失败并保持旧版。相同版本和旧版本清单只被读取，未下载 ZIP，也未替换 App。
- 从只读挂载的测试 DMG 启动，真实 Sparkle 拒绝更新并明确报告只读／临时安装位置，原 App 保留。

自动化后端测试覆盖认证、同锁准入竞争、租约过期、迟到取消／准备；0.2.1 增加阻塞真实 ASGI 响应发送的 WAV／PCM 场景，确认模型空闲后仍等待发送完成，并验证断连释放等待。Swift 行为测试覆盖音频等待、取消恢复、准备失败与重复动作；发布脚本测试覆盖版本回退、非法版本、空清单和预发布分类。

初次接入的 `make check app` 完成 34 项 Python 测试；0.2.1 补充后共 37 项，另有 4 项 Swift 行为场景，编译、打包与严格签名验证均通过。`make update` 完成 DMG 创建／校验和 ZIP／清单签名验证。

正式版本 [v0.2.0](https://github.com/Arasaka-Academy/CyberAudio/releases/tag/v0.2.0) 对应源码 `6c99d827c316911f4b41e4301c27b987ef0809bb`。[发布工作流 41990598311](https://github.com/Arasaka-Academy/CyberAudio/actions/runs/41990598311) 的构建、资产上传与下载校验、Pages 部署均成功。另行下载云端 ZIP，确认嵌套签名有效、内置后端与该提交一致。

本机此前访问 GitHub 标准下载域名曾超时；2026-11-05 15:58（UTC+8）使用 `Foundation.URLSession` 从正式公开下载 URL 实际取得 ZIP，HTTP 200，1,412,083 字节，SHA-256 与发布 manifest 一致，EdDSA 验签通过。固定 Pages appcast 返回 HTTP 200，2,292 字节，与 Release 清单字节相同且签名有效。本地实际替换／重启测试与这次公开网络下载分别记录，未把资产 API 下载当作公开 URL 的连通性证明。

随后发布的 [v0.2.1](https://github.com/Arasaka-Academy/CyberAudio/releases/tag/v0.2.1) 对应 `85e2b3544fd79dc097f53ee0572eb8c165a4ad05`，补齐慢速音频响应保护。[工作流 41992829625](https://github.com/Arasaka-Academy/CyberAudio/actions/runs/41992829625) 构建、上传资产并重新下载校验、Pages 部署全部成功。本地 App、DMG、ZIP 与该提交匹配。线上清单返回 HTTP 200，1,968 字节，版本 0.2.1／build 5，EdDSA 验签通过；对这份真实清单的版本检查拒绝旧版与同版覆盖。

0.2.1 公开 ZIP 在本机 16:17、16:20（UTC+8）的两次 `URLSession` 下载均为 `NSURLErrorDomain -1001` 超时；本机通过资产 API 重新下载也发生网络超时。因此本轮最终版本的在线包在 CI 校验通过，本机在线完整下载仍受间歇性网络故障影响；0.2.0 的一次成功下载不能替代 0.2.1 的本机在线升级验收。本地可运行 App、DMG 和完整更新 ZIP 仍可直接交付。

真实麦克风授权保留、真人听感、真实模型推理、登录项注册与重新登录，以及需要管理员权限的安装仍需在实际交付设备验收；本地替身不能证明这些行为。桌面锁定后未继续手动下载进度、完整焦点巡检和深色更新设置截图；已取得浅色设置与等待状态截图。ad-hoc 签名也不等同于 Developer ID 公证。
