# 手动建 Xcode 工程（不用 XcodeGen）

如果你不想装 XcodeGen，或者 `xcodegen generate` 报错，按这里的步骤来。
全程 5 分钟。

## 1. 新建工程

Xcode → **File ▸ New ▸ Project…**

| 选项 | 选什么 |
|---|---|
| 模板 | iOS ▸ App |
| Product Name | `OryxRemote` |
| Team | 你的 Apple ID（没有付费账号也能选 Personal Team） |
| Organization Identifier | 随意，比如 `com.你的名字` |
| Interface | **SwiftUI** |
| Language | **Swift** |
| Storage | None |
| Include Tests | 不勾 |

保存到 `apps/ios/` 下（和 `OryxRemote/` 同级）。

## 2. 加入源码

Xcode 会自动生成 `OryxRemoteApp.swift` 和 `ContentView.swift`。

1. **删掉** `ContentView.swift`（右键 ▸ Delete ▸ Move to Trash）。
2. 把 `apps/ios/OryxRemote/` 里的 5 个文件拖进工程导航栏：
   - `OryxRemoteApp.swift`（会提示同名文件，先删掉自动生成的再拖）
   - `Crypto.swift`
   - `RemoteSession.swift`
   - `PairingView.swift`
   - `ViewerView.swift`
3. 拖入时勾选 **Copy items if needed**、**Create groups**、Target 勾 `OryxRemote`。

## 3. 配置 Info.plist

选中工程 ▸ Target ▸ Info，加这几项：

| Key | 值 | 原因 |
|---|---|---|
| `NSAppTransportSecurity` ▸ `NSAllowsLocalNetworking` | `YES` | 本地调试要走 `ws://`；生产用 wss 可去掉 |
| `UISupportedInterfaceOrientations` | Portrait + Landscape | 横屏看画面更舒服 |

## 4. 签名

Target ▸ Signing & Capabilities：

- **Automatically manage signing** 勾上
- Team 选你的 Apple ID
- Bundle Identifier 如果冲突，改成唯一的，比如 `com.你的名字.oryxremote`

免费账号的限制：证书 7 天过期，到期重新 Run 一次；不能上 TestFlight。

## 5. 跑起来

```bash
# PC 上先起中继
python relay/server.py --host 0.0.0.0 --port 8899

# 再起内核（配置里 remote_relay 指向这台机器）
cd core && python -m oryx
```

界面点「远程」，记下配对码。

手机上填：

- 中转地址：`ws://<你电脑的局域网 IP>:8899`（真机调试时用局域网最省事）
- 配对码：刚才那串

真机和电脑要在同一个 Wi-Fi 下。用 4G/5G 就得先部署公网中继（见 `relay/README.md`）。

## 第一次编译大概率遇到的坑

| 报错 | 原因 | 解决 |
|---|---|---|
| `Value of type 'X' has no member 'topBarLeading'` | 用了 iOS 17 API，但部署目标 16 | 改成 `.navigationBarLeading`（本仓库已经是） |
| `onChange(of:perform:)` 废弃警告 | iOS 17 改了签名 | 只是警告；本仓库用单参数版本，两代都能编 |
| `Call to main actor-isolated initializer in a synchronous nonisolated context` | `@MainActor` 类在 App 里初始化 | 给 App 结构体加 `@MainActor`（本仓库已加） |
| `Failed to register bundle identifier` | Bundle ID 被别人占用 | 改一个唯一的 |
| 连不上、一直转圈 | ATS 拦了 `ws://` | 加 `NSAllowsLocalNetworking`，或改用 `wss://` |
| 连上了但没画面 | 电脑端没点「远程」，或配对码不一致 | 核对界面上的码；看电脑端 `/api/remote/status` |
| 画面卡住 | 中继读超时 | Caddy 里放宽 `read_timeout`（仓库自带的已配） |

## Swift 6 严格并发

如果你用的是 Xcode 16 且默认开了 Swift 6 语言模式，可能会报一堆 actor 隔离错误。
在 Target ▸ Build Settings 里把 **Swift Language Version** 设成 **Swift 5**，
或把 `SWIFT_STRICT_CONCURRENCY` 设为 `minimal`。

## 验证连接是否真的通了

电脑端状态接口：

```bash
curl http://127.0.0.1:8765/api/remote/status
```

看到 `"peer_ready": true` 就说明手机连上了。
