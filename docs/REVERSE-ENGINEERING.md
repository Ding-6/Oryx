# 逆向工程报告：腾讯 Marvis 与 WorkBuddy

> 工具：rea（morluto/rea v6.7.0，MIT）、asar 自解析器、SQLite 只读快照
> 日期：2026-10-21
> 目标：为 Oryx 的功能规划提供依据。**只做能力与架构分析，不复制代码、提示词、模型或品牌资产。**

---

## 一、结论速览

| | Marvis（马维斯） | WorkBuddy |
|---|---|---|
| 归属 | 腾讯应用宝团队 | 腾讯 CodeBuddy 系列（`@genie/workbuddy-desktop`） |
| 版本 | 5.4.7 客户端 / Windows 1.60.3000 | 5.4.7 |
| 技术栈 | **Qt5 + CEF** 原生壳 + **Python 3.11** Agent 内核 | **Electron** + TypeScript CLI |
| 核心资产 | 89 个加密技能 + 4 个 MCP server + 交互模式 | 32 个工具 + 插件市场 + CLI 沙箱 |
| 通信 | A2A（云端 Agent）+ MCP（本地） | ACP + MCP（双协议） |
| 跨端 | 原生串流（BorderlessSpace） | `centrifuge` 实时通道 |

**两者其实是同一套产品哲学的两个实现**：出厂预置好一切，零配置上手，同时留出 MCP / Skill / 插件的扩展口子。

---

## 二、Marvis

### 2.1 进程架构

```
MarvisLauncher.exe (渠道更新, 8.6MB)
└─ MarvisHost.exe (32MB, Qt5 + QCefView/CEF)      ← UI 宿主
   ├─ MarvisAgent.exe   (Python 3.11 内嵌)          ← Agent 运行时
   │   ├─ prompts/      60+ 提示词（Fernet 加密）
   │   ├─ skills/       89 个技能（SKILL.md，Fernet 加密）
   │   ├─ mcp_server/   win_use_mcp · commerce_mcp · coupon_activity · yyb_alg_mcp
   │   └─ resource/     ripgrep.exe · tiktoken · security_rules/
   ├─ Knowledgebase.exe (Python 3.11, PyPDF2/PIL/Shapely) + KbMCP.exe
   ├─ LocalModelService.exe                        ← 端侧模型独立进程
   ├─ BorderlessSpace.exe (libstreaming + drivers)  ← 远程串流
   ├─ AndrowsMCP.exe / AndrowsInstaller.exe        ← 应用宝，跑手机 APK
   └─ MarvisSvr / DlSvr / Update / Diagnosis / CefRendererProcess
```

各组件独立版本号（Host 1.60.3000.145 / Agent 1.60.3000.716 / KB 1.60.3000.446 / Space 1.60.2600.72），
可单独灰度更新。

### 2.2 技能体系（89 个）

技能明文可见的是**目录结构**，内容用 Fernet 加密（`gAAAAAB...` 是 Fernet token）。
从目录名可读出完整的能力域：

| 域 | 技能举例 |
|---|---|
| 硬件诊断 | `hardware-battery-diagnosis-repair`、`hdd-diagnosis-repair`、`hardware-fan-diagnosis`、`hardware-monitor-color` |
| 系统修复 | `system-dll-repair`（53KB）、`system-quickfix`、`os-slowness-diagnosis`、`driver-diagnosis` |
| 磁盘 | `disk-space-analysis`（55KB）、`cloud-disk-repair`（51KB） |
| 文件 | `file-search`（23KB）、`file-organizer`（58KB）、`image-search` |
| 文档 | `document-writer`（46KB）、`general-ordering`（78KB）、`excel-processing-and-analysis`、`docx`/`pptx`/`pdf` |
| 票据 | `invoice-retrieval`（32KB）、`web-qqmail-invoice` |
| 跨端 | **`smart-desktop-ops`**、**`smart-phone-ops`**、`phone-forward-toggle`、`mac-wireless-screen-cast` |
| 企业 IT | `8000-*` 共 10 个（腾讯企业微信 IT 场景：网络/激活/权限/重装/远程桌面） |
| 其他 | `knowledgebase_creation`、`planning-with-files`、**`persona-update-flow`**、**`app-restricted-action-guard`**、`agent-browser` |

注意每个诊断类技能都带 `references/` 和 `tests/` 子目录——**技能自带验证用例**。

### 2.3 `win_use_mcp`：Windows 自动化工具箱

从二进制里的模块路径可见其分类学：

```
win_use_mcp.toolkit.input_device      鼠标/键盘/触摸板/打印机/扫描仪
   .mouse_scroll  .mouse_double_click  .mouse_properties
   .mouse_cursor_style  .mouse_speed  .mouse_precision
   .keyboard_repeat_speed
win_use_mcp.toolkit.taskbar_window    .window_control  .taskbar_handlers
   .start_multitasking  .setting_control_widgets
win_use_mcp.toolkit.power             .lock_screen  .screen_timeout
win_use_mcp.toolkit.display           .screen_scale
win_use_mcp.toolkit.personalization   .wallpaper_lockscreen
win_use_mcp.toolkit.notification      .setting_control_lockscreen
win_use_mcp.toolkit.language_region  .input_method

sys_use_mcp_core.specs.*              JSON Schema 定义
sys_use_mcp_core.cards.*              面向用户的工具卡片
```

**两个关键设计**：

1. **规格与卡片分离**——`specs` 给模型看（参数 schema），`cards` 给人看（可点击的设置项）。
2. **系统设置项也做成工具**。不只是「点击、输入」，连「锁屏、屏幕超时、壁纸、输入法」都是 MCP tool。
   这解释了文档里说的 Computer Agent 能「改系统设置、硬件检测、底层配置」。

### 2.4 安全规则（92 条）

三份规则文件，按解释器分开：

| 文件 | 条数 | 编号段 |
|---|---|---|
| `bash_dangerous.yaml` | 46 | RCE-2xx / NET-2xx / DEL-2xx |
| `pwsh_dangerous.yaml` | 23 | RCE-0xx / NET-0xx / DEL-0xx |
| `python_dangerous.yaml` | 23 | RCE-1xx / NET-1xx / DEL-1xx |

规则字段：`id` `category` `category_name` `description` `pattern` `severity` `case_sensitive`

类目分布：RCE 19 条、NET 20 条、DEL 42 条。

**与文档不符的一点**：官方文档只说 L2 一级（删除/改文件/改系统核心配置 → 先出计划再确认）。
但代码里 `severity` 只有两个取值——`critical`（11 条）和未设置（81 条）。
说明真实模型是**两层**：

- 第一层：工具自身声明的风险等级（对应我们 L0-L3）
- 第二层：92 条正则黑名单，命中 `critical` 的直接禁止，其余作为升级信号

我们的实现把 L0-L3 做成完整刻度、黑名单作为升级输入，**比 Marvis 更细**，但少了一个"直接禁止"档位。

### 2.5 交互模式（4 种，工具策略分离）

`resources/plugins/workbuddy-builtin/interactionmode/{ask,craft,plan,expert}/fragments/*.md`（明文）：

| 模式 | 工具策略 |
|---|---|
| `ask` | **只读**。要动手就提示用户切到 Agent 模式 |
| `craft` | 全权限 + 任务管理 + 插件推荐 |
| `plan` | 产出方案后让用户确认再切模式 |
| `expert` | 专家角色 |

`ask` 模式的 `tool-use.md` 原文要点：
- 不要向用户提具体工具名
- 引用文件用 `file_path:line_number`
- 独立调用并行，有依赖则串行
- 优先专用只读工具而非 shell

`craft` 模式的 `task-management.md`：
- **只有 3 步以上、或用户明确要求拆解时才建任务**
- 开始前标 `in_progress`，做完立刻标 `completed`，不批量补标
- 未完成不要标完成

### 2.6 数据层（SQLite 实测）

| 库 | 表 | 行数 | 含义 |
|---|---|---|---|
| `local_app_info.db` | `app_info` | 101 | **应用库**：local_pkg_name, display_name, cate_name_new, tag_name, game_type |
| | `app_behavior_event` | 52,556 | 用户行为埋点（event_type + extra） |
| `RemoteControl.db` | `remote_control` | 24 | **跨端配对** |
| `InstalledApp.db` | `apps` | 0 | 已安装应用：package_name, version_code, icon, md5, file_path, state |
| | `third_party_shortcut` | 0 | 第三方快捷方式 |
| `FileStorage.db` | `hide_files_Info` | 0 | **隐藏文件**（path, other_info, is_topic）→ 对应「不可读取文件夹」禁区 |
| `Configs.db` | `configs` | 1 | key/value/encrypt/json_data |
| `login.db` | `configs` | 1 | key=`userinfo`, encrypt=1 |
| `bc_*.db` | `beacon_events` | 1 | 腾讯灯塔埋点 |

**跨端配对记录结构**（关键）：

```json
{
  "common_info": {
    "mobile_device_id": "str",
    "mobile_device_name": "str",
    "msg_id": "str",
    "security_policy": 1,      ← 安全策略等级，按设备配
    "verify_code": "str"       ← 配对验证码
  },
  "start_timestamp": 1234567890
}
```

Marvis 的跨端安全是**按配对设备可配策略等级**的，存 `verify_code` 做握手校验。

### 2.7 云端能力：A2A

缓存里的标准 A2A Agent Card：

```json
{
  "name": "ppt-generation-agent",
  "description": "面向办公场景的演示文稿生成 Agent…",
  "supportedInterfaces": [{
    "url": "https://yybadaccess.3g.qq.com/marvis-vedas-ppt/rpc",
    "protocolBinding": "JSONRPC", "protocolVersion": "1.0"
  }],
  "capabilities": { "streaming": true, "pushNotifications": false },
  "defaultInputModes": ["text/plain","text/markdown","application/pdf","image/png","image/jpeg"],
  "defaultOutputModes": ["application/vnd.openxmlformats-officedocument.presentationml.presentation"],
  "skills": [{ "id": "ppt-generate-from-topic", "tags": ["ppt","generation","from-topic"] }]
}
```

**分工清晰**：本地重能力自己扛（索引、OCR、系统操作），重计算/重内容生成放云端，通过 A2A 接。

---

## 三、WorkBuddy

### 3.1 结构

```
WorkBuddy.exe (204MB, Electron)
└─ resources/app.asar (287MB, 2.3 万文件)
   ├─ main/       119 个模块（打包压缩，函数名已混淆）
   │              acp · fs-protection · credential-protection · daemon-bootstrap
   │              cli-prewarm-pool · desktop-monitor-service · eval-inject-server
   ├─ preload/    313KB
   ├─ renderer/   React
   ├─ cli/        @genie/agent-cli（TypeScript 编译，产物 dist/codebuddy.js）
   ├─ native/     turing-sdk、wechat-copydata-decoder
   └─ resources/  插件（.codebuddy-plugin）、扩展、模板
```

### 3.2 CLI 依赖全景（100 个依赖，关键分组）

| 层 | 依赖 | 说明 |
|---|---|---|
| 协议 | `@agentclientprotocol/sdk` 0.25 + `@genie/agent-client-protocol` | **ACP**：Agent 间通信 |
| | `@modelcontextprotocol/sdk` 1.29 | MCP：工具接入 |
| 沙箱 | **`@anthropic-ai/sandbox-runtime` 0.0.17** | Anthropic 官方沙箱运行时 |
| | `@genie/sandbox-cli`、`@lydell/node-pty`、`@xterm/headless` | 终端与 PTY |
| | `e2b` 2.3.6 | 云端代码执行沙箱 |
| 权限 | **`@genie/enterprise-policy`** | 企业策略（权限） |
| 加密 | `@genie/at-rest-crypto` | 静态加密（对应 Marvis 的 Fernet） |
| 提示词 | `@genie/prompts` | 集中管理 |
| 模型 | `@genie/agent-provider` | 多 provider |
| 遥测 | `@genie/telemetry` + OTel | 用量与链路 |
| 实时 | **`centrifuge`** | Centrifugo 实时通道（多端推送） |
| 编辑 | `diff` 8.0.2、`diff-match-patch` | 文件差异应用 |
| 查找 | `fast-glob`、`fuzzy-search` | 文件检索 |
| 解析 | `acorn` + `acorn-walk` | JS AST |

### 3.3 完整工具面（32 个，来自 `product.cloudhosted.json`）

```
Agent            Read    Write   Edit     Bash      Glob      Grep
PowerShell       EnterPlanMode      ExitPlanMode
TaskCreate       TaskGet TaskUpdate TaskList TaskStop TaskOutput
WebFetch         WebSearch
Skill            AskUserQuestion    StructuredOutput
ToolSearch       DeferExecuteTool   SendMessage
TeamCreate       TeamDelete         ImageGen
DelegateTool     WeChatReply        WeComReply
ListMcpResources ReadMcpResource
```

**三个关键机制**：

- **`ToolSearch` + `DeferExecuteTool` + `DeferToolLoading`** —— 工具数量多时**按需检索与延迟加载**，
  避免把 32 个工具的 schema 全塞进上下文。
- **`SendMessage` / `TeamCreate` / `TeamDelete`** —— **多 Agent 团队**：Agent 之间发消息、组队、拆团队。
- **`EnterPlanMode` / `ExitPlanMode`** —— 计划模式作为**工具**而非配置项，模型可自行进出。

### 3.4 特性开关（40 个）

命名规律：`Disable*` 为真表示**关闭**。

| 开关 | 含义 |
|---|---|
| `DeferToolLoading` | 工具延迟加载 |
| `CloudAgentsSmartMerge` | 云端 Agent 智能合并 |
| `CustomModelsJSON` / `CustomModelIdPrefix` | 自定义模型接入 |
| `Artifact` / `ShareLink` / `HtmlPreviewShare` | 产物与分享 |
| `ScheduledTasks` | 定时任务 |
| `ProjectLocalTask` | 本地项目任务 |
| `DisableMemoryPersonalization` | 记忆个性化开关 |
| `ChannelWechatKf` / `ChannelSlack` / `ChannelTelegram` / `ChannelDiscord` | **多聊天渠道** |
| `Connector` / `WebSearch` | 连接器与联网搜索 |
| `ImageGen` / `VideoGen` | 图像/视频生成 |
| `EnableArdot` | 设计能力 |
| `TencentDocsAiEdit` | 腾讯文档 AI 编辑 |
| `PublishedApps` / `ArchivedTasks` | 应用市场与任务归档 |

**这套开关体系本身就是一个设计范式**：能力按部署形态（cloudhosted / internal / ioa / selfhosted）
差异化开启，不做代码分支。

### 3.5 模型目录（24 个）

每个模型带 `id` `vendor` `maxInputTokens` `maxOutputTokens` `supportsToolCall`
`supportsImages` `credits`（计费倍率）。

```
deepseek-v4-pro       in=1000000 out=50000 img=True  cred=x0.16
deepseek-v4-flash     in=1000000 out=50000 img=True  cred=x0.06
hunyuan-chat          in=200000  out=8192           cred=x0.10
hy3-preview-agent     in=192000  out=64000 img=True  cred=x0.04
glm-5.1               in=200000  out=48000 img=True  cred=x1.06
kimi-k2.6             in=256000  out=32000 img=True  cred=x0.59
```

模型选择同时受**上下文窗口、是否支持工具调用、是否支持图像、计费倍率**四个维度约束——
不是简单的模型下拉框，而是能力矩阵。

### 3.6 部署形态

`product.{cloudhosted,internal,ioa,selfhosted}.json` 四套配置，同一份代码按配置裁剪能力。
`links` 里暴露了官方文档路径，其中三个直指我们要做的功能：

```
mcpHelpDocument      = .../docs/cli/mcp
hooksHelpDocument    = .../docs/cli/hooks
sandboxHelpDocument  = .../docs/cli/bash-sandboxing
```

### 3.7 IPC 频道（137 个，命名 `namespace:action`）

```
app:getVersion          app:openUrl              app:consumePendingOpenUrls
client-menu:get-preload-url
wechat:shareFile        wechat:shareLink         wechat:launchMiniProgram
wechat:getShareTicket
workbuddy:tencentDocs:setTheme   workbuddy:tencentDocs:getWebviewPreloadUrl
workbuddy:registerOnlineDocPreview  workbuddy:updateDocsFeatureList
workbuddy:devtools-terminal:resize  workbuddy:zoom-shortcut
crash:renderer-js-error   crash:renderer-js-error
renderer:ready  renderer:skeleton-ready  renderer:signalStartupFirstPaint
monitor:updateQimei36    telemetry:updateQimei36
menu:openSelfCheck  menu:openNetworkCheck
wb:invoke  wb:event  wb:edit-command
```

可以看出产品面：微信集成、腾讯文档集成、终端内嵌、崩溃上报、渲染首帧性能埋点、菜单里的自检/网络诊断。

---

## 四、两个产品的共同设计范式（我们要抄的）

| 范式 | Marvis | WorkBuddy | Oryx 现状 |
|---|---|---|---|
| **出厂预置，零配置** | 6 Agent / 89 技能 | 32 工具 / 插件市场 | ✅ 20+ 工具 / 5 技能 |
| **能力按需暴露** | 技能目录 + `use_skill` | `ToolSearch` + 延迟加载 | 🟡 技能目录有，但没做工具延迟加载 |
| **协议分层** | A2A(云) + MCP(本地) | ACP(Agent间) + MCP(工具) | 🟡 只有 MCP |
| **模式即权限** | ask/plan/craft/expert 的工具策略 | EnterPlanMode/ExitPlanMode 工具 | ✅ ask/plan/craft |
| **插件清单格式** | `.codebuddy-plugin` | `.codebuddy-plugin` | ✅ 两种都兼容 |
| **提示词静态加密** | Fernet | `@genie/at-rest-crypto` | ❌ 明文 |
| **执行沙箱** | 危险命令黑名单 92 条 | `@anthropic-ai/sandbox-runtime` | ✅ 黑名单 + `sandbox.py`（env 剔密钥 + cwd 监禁） |
| **多 Agent 团队** | 主 Agent 调度 | `SendMessage`/`TeamCreate` | ✅ team.py 七工具，投递/执行分离 |
| **多渠道** | 微信/应用宝 | Slack/TG/Discord/企微 | ❌ |
| **特性开关** | 无明显 | 40 个 `Disable*` | ✅ 21 个开关 + 四 profile |
| **模型能力矩阵** | 混元/DeepSeek/Qwen 路由 | 24 模型 × 4 维度 | ✅ 设置页能力矩阵（上下文/工具/视觉/成本） |
| **跨端配对策略** | `security_policy` + `verify_code` | centrifuge 通道 | ✅ 三级策略（只读/可控/完整）+ 配对码 |
| **用量/遥测** | 灯塔埋点 | OTel | ✅ 已做 |

## 五、明确不该抄的

1. **代码、提示词、加密后的技能内容**——全部 Fernet/自有加密，我们自己写。
2. **腾讯云服务、A2A 服务端、灯塔埋点**——闭源依赖，自建替代。
3. **品牌、图标、UI 视觉**——自研。
4. **微信/企微深度集成**——涉及第三方 SDK 与合规，优先级低。
5. **`e2b` 云沙箱**——是商业服务，自建沙箱或本地进程隔离更可控。

---

## 六、工具限制说明

| 限制 | 影响 | 变通 |
|---|---|---|
| WorkBuddy 主进程已压缩混淆 | 语义图只有行号无函数名 | 转而分析 preload/CLI 的可读部分与 IPC 频道名 |
| WorkBuddy 全量分析超时（2.3 万文件） | 无法全量语义图 | 用 `--source-path` 定点分析 7 个关键模块 |
| Marvis 技能内容 Fernet 加密 | 读不到技能正文 | 用目录名 + 交互模式明文片段推断能力边界 |
| rea 的 SQLite 检查需 Node ≥24.15（本机 24.14.1） | 无法用 rea 读库 | 用只读快照 + 只取 schema/聚合（不含标识符与遥测明细） |
| rea 需 Hopper/Ghidra 才能做原生反编译 | 无法反编译 MarvisHost.exe | 用 `inspect-artifact` 做静态制品图 + 字符串/模块路径分析 |
| `inspect-electron-fuses` 输出被截断 | 融合子完整性细节未读全 | 已知不影响能力规划 |

---

## 七、融合落点（实测 + 已上线）

> 把上述范式转成 Oryx 内部模块，2026-10-21 落地。

| 范式 | 落地位置 | 说明 |
|---|---|---|
| 系统设置工具集 | [syssettings.py](file:///D:/项目/Oryx/core/oryx/syssettings.py)（8 个工具：`sys_power_status`、`sys_lock_screen`、`sys_set_screen_timeout`、`sys_display_settings`、`sys_set_dpi_scaling`、`sys_set_wallpaper`、`sys_mouse_settings`、`sys_input_methods`） | 对齐 Marvis `toolkit.{power,display,input_device}`；写操作标 L2，必须经用户确认 |
| 特性开关 | [features.py](file:///D:/项目/Oryx/core/oryx/features.py) + [features.example.yaml](file:///D:/项目/Oryx/config/features.example.yaml) | 21 个 `Enable* / Disable*` 开关（对齐 WorkBuddy 40 开关）。`personal / team / selfhosted / cloud` 四种 profile |
| 内置 REA 预设 | [loop.py](file:///D:/项目/Oryx/core/oryx/loop.py) `_init_plugins`：`npx -y rea-agents mcp` | 受 `EnableReaMcp` 控制。失败时只记一行 `plugin_errors` 不阻塞启动 |
| 工具按需检索 | `POST /api/tools/search` | 已存在；关键字 → 评分排序，返回精简 schema |
| 协议分层 | Oryx 已用 MCP；`aea` 与 A2A 列在 `DisableA2A`，留口子 | A2A 由特性开关控制 |
| 模型能力矩阵 | `core/oryx/providers.py` 已有 `provider_config()`，下一轮加维度字段 | — |
| 命令沙箱 | [sandbox.py](file:///D:/项目/Oryx/core/oryx/sandbox.py) + `shell_sandbox` 工具 | env 剔密钥 + cwd 监禁；黑名单仍升级审批；`DisableSandbox` 控制 |
| 跨端策略分级 | [host.py](file:///D:/项目/Oryx/core/oryx/remote/host.py) `POLICY_READONLY/CONTROLLED/FULL` | 对齐 Marvis `security_policy`；`remote_start(policy=…)`，系统键在「可控」级拦截 |

API 新增：`GET /api/features` `POST /api/features/toggle`。
