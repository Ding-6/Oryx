# Oryx Remote（iOS 原生）

手机端：输入配对码，实时看电脑屏幕，手指点击/滑动直接操作电脑。

> **必须先说明**：这套源码是在 Windows 上写的，**没有在 Xcode 里编译验证过**。
> 首次编译大概率需要修一两处（SwiftUI API 版本差异、签名配置）。逻辑部分
> （加密、协议、坐标换算）与 PC 端已有端到端测试对齐。

## 构建步骤

需要一台 **Mac**（Xcode 15 以上）。

### 方式一：XcodeGen（推荐）

```bash
brew install xcodegen
cd apps/ios
xcodegen generate
open OryxRemote.xcodeproj
```

在 Xcode 里选你的开发者账号 → 选真机 → Run。

### 方式二：手动建工程

不想装 XcodeGen，或 `xcodegen generate` 报错时，按 [MANUAL_SETUP.md](MANUAL_SETUP.md) 走，
里面有逐步操作、Info.plist 配置和**首次编译常见报错对照表**。

## 没有付费开发者账号怎么办

用免费 Apple ID 也能装到自己手机上，证书 7 天过期，到期重新 Run 一次即可。
不能上 TestFlight、不能长期分发。

## 协议要点

与 `core/oryx/remote/protocol.py` 严格对应：

| 项 | 值 |
|---|---|
| 密钥派生 | `HKDF-SHA256(ikm = SHA256(配对码), salt="oryx-remote", info="oryx-remote-v1", 32B)` |
| 信封 | `AES-GCM`，`combined = nonce(12) + 密文 + tag(16)` |
| 明文首字节 | `0x01` = JSON 控制消息，`0x02` = JPEG 帧 |
| WebSocket | `wss://<中继>/ws?role=client&room=<配对码>` |

输入事件（JSON，加密后发出）：

```json
{"t":"input","v":1,"kind":"tap","x":0.5,"y":0.5}
{"t":"input","v":1,"kind":"swipe","x1":0.2,"y1":0.8,"x2":0.2,"y2":0.2,"duration_ms":260}
```

坐标是**归一化值**（0–1，相对屏幕左上角），所以 PC 分辨率变化不影响定位。

## 已知限制

- 只做了画面 + 触摸；没有实体键盘输入（可后续接 `UIKeyInput`）。
- 不做音频、不做剪贴板同步。
- 中继可见配对码（房间号）。**配对码短，中继若被攻破可离线爆破**——自建中继并只用 wss。
- 后台运行会断开（iOS 限制），回到前台自动重连由用户手动点。
