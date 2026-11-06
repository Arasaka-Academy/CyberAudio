# macOS 签名与公证

构建提供三个显式模式。默认 `adhoc` 用于本地开发。`development` 使用 Apple Development 证书检验强化运行时与嵌套代码。`distribution` 要求 Developer ID Application 证书、匹配的 Team ID 和可用公证凭据；任一签名、公证、票据或 Gatekeeper 检查失败即停止产物流程。

## 本地开发

```bash
bash scripts/build_app.sh
```

默认输出仍为 ad-hoc App，不宣称已公证。当前稳定版的实际签名状态以对应验收报告为准。

使用已有 Apple Development 证书时：

```bash
export CYBERAUDIO_SIGN_MODE=development
export CYBERAUDIO_SIGN_IDENTITY='证书 SHA-1 或完整名称'
export CYBERAUDIO_TEAM_ID='实际 Team ID'
bash scripts/build_app.sh
```

工具从精确匹配的证书读取 OU 验证团队；Apple Development 名称末尾的标识不一定是 Team ID。开发模式不请求时间戳或公证，不适合作为 Developer ID 分发包。

## Developer ID 分发

先通过 Apple 工具把公证凭据保存在本机 Keychain：

```bash
xcrun notarytool store-credentials CyberAudio-Notary
export CYBERAUDIO_SIGN_MODE=distribution
export CYBERAUDIO_SIGN_IDENTITY='Developer ID Application 证书 SHA-1 或完整名称'
export CYBERAUDIO_TEAM_ID='实际 Team ID'
export CYBERAUDIO_NOTARY_PROFILE=CyberAudio-Notary
bash scripts/build_app.sh
```

自定义 Keychain 可设置 `CYBERAUDIO_SIGN_KEYCHAIN`，用于读取该 Keychain 中的公证凭据。签名证书还需在用户 Keychain 搜索列表中可见。

所有嵌套 Mach-O、Sparkle 组件、资源 Bundle 和 App 按由内到外的顺序签名，使用强化运行时。Sparkle 保留原有标识与 entitlement；App 使用麦克风所需的 `com.apple.security.device.audio-input`，不增加 JIT 或关闭库验证的权限。分发模式附带安全时间戳。

工具提交临时 ZIP，确认 Apple 返回 `Accepted`，随后给 App staple 票据，运行 `stapler validate`、Gatekeeper 执行评估和严格嵌套验签。完成后才允许后续 DMG／Sparkle ZIP 构建。Sparkle EdDSA 更新签名与 Apple 代码签名分别验证。

分发模式的 `verify` 和 `build_dmg.sh --reuse-app` 同样要求票据与 Gatekeeper 验证，复用已有 App 不能跳过公证。签名本身及提交公证前的内部验证只检查代码身份；提交成功后才允许完整分发验证通过。

## GitHub Actions

发布流程默认仍为 `adhoc`。启用分发时配置仓库变量 `CYBERAUDIO_SIGN_MODE=distribution`、`APPLE_TEAM_ID`，以及以下 Secrets：

- `DEVELOPER_ID_P12_BASE64`：含私钥的 P12，Base64 编码。
- `DEVELOPER_ID_P12_PASSWORD`：P12 密码。
- `DEVELOPER_ID_SIGN_IDENTITY`：证书 SHA-1 或完整名称。
- `APPLE_NOTARY_ID`、`APPLE_NOTARY_PASSWORD`：公证 Apple ID 与专用密码。
- 原有 `SPARKLE_EDDSA_PRIVATE_KEY`：与 App 公钥匹配的 Sparkle 密钥。

密钥只在受信任的 Release 作业中导入临时 Keychain；流程结束恢复原搜索列表并删除临时 Keychain 与 P12。PR 检查不接触发布凭据。`release.json` 记录 `app_signing.mode` 与 `notarized`，避免把 Sparkle 验签误写成 Apple 公证。

## 仍需实机验收

Apple Development 的成功构建只能验证开发签名路径。实际 Developer ID、公证、首次下载安装、旧版到新版的更新，以及麦克风／辅助功能／输入监控授权保留，需要对应证书和实际交付设备。新签名无法追溯保证旧 ad-hoc 版本的授权自动保留；首次迁移时应按系统提示重新确认权限。
