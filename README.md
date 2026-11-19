# Oryx

类腾讯 Marvis 的操作系统级 AI 助手：你说一句话，它拆解任务、调用本机工具去完成；涉及删除、改系统、执行命令时，**先把执行计划摆出来等你确认**。

当前是 **M0-M3 可运行切片**：主循环 + 工具 + 安全闸门 + 审计日志 + 技能加载。

## 现状

| 模块 | 状态 |
|---|---|
| Agent 主循环（流式、多轮工具调用） | ✅ |
| 多 provider（Anthropic / OpenAI 兼容：DeepSeek、混元、Ollama） | ✅ |
| 工具：系统信息、文件读写、搜索、shell | ✅ |
| 风险分级 L0-L3 + 确认卡 + 审计日志 | ✅ |
| 技能（SKILL.md）加载与调用 | ✅ |
| 本地 Web UI（对话 + 执行过程 + 确认弹窗） | ✅ |
| 本地内容索引（FTS5 + 中文 trigram + 增量扫描） | ✅ |
| 知识库集合（多库管理，按目录分组，Marvis 形态卡片） | ✅ |
| 文档解析：txt / md / pdf / docx / xlsx / pptx | ✅ |
| 图片 OCR：截图、发票、白板按图中文字检索 | ✅ |
| 向量语义检索（混合排序） | ✅ |
| 浏览器 Agent（Playwright，接管网页，含等元素、下载文件） | ✅ |
| 联网搜索（可选，需自己配搜索服务 key，默认关闭） | ✅ |
| Tauri 桌面壳（原生窗口 + 自动拉起内核） | ✅ |
| 交互模式 ask / plan / craft（带工具闸门） | ✅ |
| 任务清单（多步任务可见进度） | ✅ |
| 回答风格预设（6 种） | ✅ |
| 专家角色（6 个预置 + 自定义） | ✅ |
| 插件清单兼容 + MCP 客户端 | ✅ |
| 文档生成 Word / Excel / PPT | ✅ |
| 文档生成 HTML / PDF / 图片（PNG / SVG）+ 界面内预览 | ✅ |
| 跨端：手机远控（iOS 原生 + 公网中继） | ✅ |
| 自定义模型接入（运行时增删改） | ✅ |
| 批量安装插件 / 专家 / 技能 | ✅ |
| 本机应用库（已安装程序清单） | ✅ |
| 项目文档 Agent（需求/决策/变更单文件） | ✅ |
| 子 Agent 派发（工具白名单） | ✅ |
| 长期记忆（跨会话） | ✅ |
| 用量统计（token / 调用数） | ✅ |
| 自动化（cron 定时任务） | ✅ |
| 内嵌终端（流式输出 + 中断） | ✅ |
| 通知渠道（Windows 通知 / Webhook） | ✅ |
| 提示词优化（可绑定技能） | ✅ |
| 工作目录切换 | ✅ |
| 本地 API 令牌鉴权 | ✅ |
| 人设管理（性格/说话风格/适用场景） | ✅ |
| 工作台首页（场景 + 能力入口） | ✅ |
| Windows 系统设置工具集（Marvis win_use_mcp 风格） | ✅ |
| 特性开关 features.yaml（WorkBuddy 40 开关风格） | ✅ |
| 内置 REA 预设（把 rea-agents 当 MCP server 拉起） | ✅ |
| 多 Agent 团队（建队/派活/信箱/名册可视化） | ✅ |
| 命令沙箱 shell_sandbox（env 剔密钥 + 工作目录监禁） | ✅ |
| 跨端远控策略分级（只读/可控/完整，对齐 Marvis security_policy） | ✅ |
| 多聊天渠道（IM 连接器） | ❌ |
| 可视化前端设计辅助 | ❌ |

## 快速开始

### 桌面应用（推荐）

双击桌面上的 **Oryx** 快捷方式，或：

```bash
apps\desktop\src-tauri\target\release\oryx-desktop.exe
```

安装包在 `apps\desktop\src-tauri\target\release\bundle\nsis\Oryx_0.1.0_x64-setup.exe`，
里面已经把 Python 内核一起打进去了。详见 [apps/desktop/README.md](apps/desktop/README.md)。

### 命令行

```bash
cd core
python -m oryx
```

注意：命令行方式会在浏览器打开；桌面应用是独立窗口，**不经过浏览器**。

首次运行会在 `%APPDATA%\Oryx\config.json` 生成配置模板，填一个 provider 的 `api_key` 即可。

也可以直接编辑仓库里的 `config.example.json` 作为参考。

### 用本地模型（纯离线）

```bash
ollama serve
ollama pull qwen3:8b
```

然后界面右上角切到「本地模式」。

## 架构

```
┌─ 桌面壳 apps/desktop (Tauri 2 + Rust) ─────────────┐
│  拉起内核 · 等就绪 · WebView 打开界面 · 退出时收尸    │
└──────────────────┬─────────────────────────────────┘
                   │ HTTP + SSE（127.0.0.1:8765）
┌─ Web UI (core/web/index.html) ─────────────────────┐
│  对话流 · 执行过程面板 · 确认卡 · 索引状态            │
└──────────────────┬─────────────────────────────────┘
                   │
┌─ Python 内核 (core/oryx) ───────────────────────────┐
│  server.py      本地服务 + 事件推送 + CORS           │
│  loop.py        Agent 主循环（规划→调用→观察）        │
│  policy.py      风险分级 + 执行计划生成              │
│  tools.py       内置工具（每个工具声明自身风险）       │
│  browser.py     浏览器自动化（独立线程）              │
│  indexer.py     索引：FTS5 + 向量 + OCR              │
│  providers.py   模型适配（流式、工具调用）            │
│  skills.py      SKILL.md 技能加载                    │
│  store.py       SQLite：任务/步骤/审计/消息          │
└──────────────────┬─────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
   本机文件系统/浏览器      系统命令 / 硬件信息
```

## 目录

```
apps/desktop/        Tauri 2 桌面壳（Rust，3MB）
  dist/index.html    启动页：轮询内核 → 跳转
  src-tauri/         main.rs / tauri.conf.json / icons
apps/ios/            iOS 原生 App（SwiftUI，需在 Mac 上编译）
relay/               公网中继（单文件 WebSocket + Docker + Caddy）
core/oryx/           Python 内核
  prompts/system/     主控系统提示词（14 个 md，按语义分段）
  registry.py         扩展注册总线（六类扩展的统一发现入口）
  rules/              规则集（危险命令 + 前端质量）
  ui_audit.py         前端质量门禁
core/web/            界面（零构建 ES Module）
  index.html          169 行，只留壳
  modules/*.js        22 个功能模块
  styles/main.css     样式
core/scripts/        下载模型、生成图标、拆分前端模块、切分提示词
core/tests/          80+ 个用例
skills/ experts/ agents/   扩展（每个可带 oryx.json 声明元数据）
skills/              技能包（SKILL.md）
experts/             专家 persona（EXPERT.md）
plugins/             插件目录（兼容 .codebuddy-plugin / .claude-plugin）
```

## 安全模型

| 级别 | 触发条件 | 行为 |
|---|---|---|
| L0 只读 | 读类工具，路径在允许范围内 | 直接执行 |
| L1 写入 | 可逆写入，路径在允许范围内 | 执行并留痕 |
| L2 危险 | 删除、覆盖、执行命令、路径越界 | **弹确认卡，未确认绝不执行** |
| L3 禁止 | 支付/转账/凭据，或路径命中禁区 | 直接拒绝 |

规则文件：`core/oryx/rules/dangerous.yaml`（RCE / NET / DEL / SYS / PAY 五类）。

每次工具调用都会写入 `audit` 表，可通过 `GET /api/audit/{task_id}` 导出。

## 交互模式

同一个助手，三种"权限姿态"。**闸门由代码强制，不靠提示词自觉**：

| 模式 | 能做什么 | 典型场景 |
|---|---|---|
| 问答 ask | 只能只读工具；写操作一律拦下并提示切换 | "这个报错是什么意思" |
| 规划 plan | 只读调研 + 输出方案，不动手 | "先告诉我你打算怎么改" |
| 执行 craft | 全权限，破坏性动作仍走确认 | "帮我改了" |

切到问答模式后让 Agent 删文件，它会拿到明确的拒绝理由，而不是靠模型自觉。

另有三个扩展模式：开发者 `bypass`（跳过确认，但禁止规则仍拦得住）、
无人值守 `dontAsk`（需要确认的一律拒绝）、委派 `delegate`（只调度不执行）。

### 自动判定（auto）

`auto` 模式由**分类器**替用户决定这次要不要确认。三档判定：

| 判定 | 什么时候 | 行为 |
|---|---|---|
| 放行 allow | 有检查点快照兜底的动作（写/删/移文件、生成文档） | 直接执行，不弹确认卡 |
| 确认 ask | 不可回滚的动作（执行命令、系统设置、远程控制） | 照常弹确认卡 |
| 拒绝 deny | 涉及凭据文件（`.env` / `.ssh` / `.aws` / `.kube`） | 直接拒，并说明需要本人授权 |

**fail-closed 是硬要求**：判不准一律落到"确认"，绝不默认放行。
凭据文件之所以直接拒而不是弹确认，是因为自动判定的前提恰恰是**没人能点确认**。

界面上的步骤卡会显示判定徽章（自动放行 / 仍需确认 / 自动拒绝），悬停看理由。

## 任务清单

多步任务时 Agent 会调用 `task_create` / `task_update` / `task_list` 维护清单，界面侧栏实时显示进度。工具只在**执行模式**下注册——问答模式里连清单都建不了，避免产生"看起来做了很多"的错觉。

## 回答风格

6 种风格，只影响表达方式，不影响工具权限：

| 风格 | 特点 |
|---|---|
| 专业 | 先结论后依据，术语准确，不夸大 |
| 高效 | 极简直给，去掉寒暄和铺垫 |
| 亲切 | 有温度，术语顺带解释 |
| 直白 | 有问题直接指出，不和稀泥 |
| 创意 | 额外给 1-2 个不同角度，但落地部分必须具体 |
| 引导 | 先点出关键前提，必要时反问帮你想清楚 |

切换后会写回 `config.json`，重启仍生效。

## 专家角色

给助手挂一个角色，改变它的工作方式和输出标准。目录结构同技能：

```
experts/senior-developer/EXPERT.md
```

frontmatter 支持人设要素：

```yaml
name: 资深开发
description: 十多年全栈经验…
scenarios: 架构设计, 代码审查
tags: 冷静的技术搭档          # 人设标签
personality: 严谨,克制,直接    # 性格关键词
speaking_style: 不做多余寒暄，直奔问题核心
use_cases: 适合技术方案、代码审查、疑难排查
self_intro: 我是你的资深技术搭档…
```

这些要素会注入系统提示，界面上「人设管理」页能看到完整的人设卡片。

预置 6 个：资深开发、数据分析师、文档写作、演示设计、资料研究、产品经理。

专家只影响"怎么做"，不影响"能不能做"——工具权限仍由交互模式和策略引擎控制。

## 插件与 MCP

把插件放进 `plugins/`，启动时自动识别，兼容两种清单格式：`.codebuddy-plugin/plugin.json`（WorkBuddy）和 `.claude-plugin/plugin.json`（Claude Code）。

- 插件里的 `skills/*/SKILL.md` 自动进入技能目录
- 插件声明的 `mcpServers` **只发现不启动**，必须在配置里显式 `"enabled": true`
- 启用的 MCP 工具以 `mcp__<server>__<tool>` 出现，风险等级按 `readOnlyHint` / `destructiveHint` 注解推断

自带一个最小 MCP 客户端（stdio + JSON-RPC，约 200 行），只实现 `tools/list` 和 `tools/call`。

**设置 → 扩展工具** 能看到所有已登记的 MCP server：传输方式、地址、实际挂载的工具数、
运行状态，以及启动时的配置错误。点「启用」会热重载工具注册表，**立即生效、不用重启内核**。
外部 server 是可执行程序，默认不启动，必须在这里显式启用。

详见 [plugins/README.md](plugins/README.md)。

## 功能开关

**设置 → 功能开关** 里的每个开关都在**工具暴露层**生效：关掉某项能力，
该能力的工具根本不进入模型看到的清单，切换后立即生效、不用重启。

| 能力组 | 关掉之后 |
|---|---|
| Office（含 `html_write` / `pdf_write` / `image_write`）/ OCR / 浏览器 / 本地索引 / 自动任务 | 对应工具从模型可见清单里消失 |
| 长期记忆 / 技能 / 子 Agent（含 `workflow_draft`）/ 内嵌终端 / 项目文档 / 系统设置 | 同上 |
| 命令沙箱 (`DisableSandbox`) | `shell_sandbox` 不再暴露 |

`EnableModelRouting` 是唯一的**执行期**开关：它不裁剪工具，而是控制是否在运行时
按复杂度 / 成本 / 网络条件换用配置里**真实存在**的 provider，**默认关闭**；
开启后每次切换都会发 `model.routed` 事件，界面可见，不会凭空编造模型名。

标注「**当前版本无此能力**」的开关（联网搜索、图像/视频生成、IM 渠道、A2A）
在项目里没有对应实现，因此是禁用状态 —— 不给不存在的东西假装提供开关。

## 系统设置

**设置 → 系统设置** 按电源 / 显示 / 输入三组列出 Oryx 能读、能改的系统设置：

- **只读项**（电源与电池状态、分辨率与缩放、鼠标设置、已装输入法）点了直接出结果；
- **写操作**（锁屏、改息屏时间、改缩放、换壁纸）没有一键按钮 —— 在对话里下指令，
  由 Agent 出执行计划等你确认。这是刻意的：写操作必须走 L2 审批与审计记录，
  设置页不能成为绕过闸门的捷径。

## 产物

Agent 生成的文件会进右侧「产物」面板，可以**预览、用默认程序打开、在资源管理器定位、
复制路径，或挂回输入框交给 Oryx 继续处理**。会话重开后产物列表会从审计记录重建。

产物不再靠前端硬编码的工具名认，而是由后端按结果元数据判定：这次调用 `ok`、
确实写出了带字节数的文件、并且结果里给了路径，才算产物，只读工具不会混进来。

「预览」走 `GET /api/files/preview`，支持和「打开」共用的路径闸门：
只允许 `allowed_roots` 内的真实文件（默认用户主目录），越界 403、路径不存在 404、
空路径 400、不支持的格式 415、不带令牌 401，超过 8MB 建议改用系统程序打开。
模型生成的 HTML / SVG 属于不可信内容，返回时一律加 **CSP sandbox**（禁脚本、
唯一 origin）、`nosniff` 与 `no-store` —— 能看，但不能借同源身份调本地接口。

## 文档生成

| 工具 | 产出 |
|---|---|
| `docx_write` | Word：标题 + 小节（段落 / 要点列表） |
| `xlsx_write` | Excel：多工作表，首行自动加粗冻结、列宽自适应 |
| `pptx_write` | PPT：自动封面 + 逐页标题与要点，支持备注 |
| `xlsx_read` | 读回表格前 N 行，供自检 |
| `html_write` | 网页看板 / 报告：自包含单文件，离线可打开；用户输入全部 HTML 转义 |
| `pdf_write` | PDF 报告：内嵌中文字体，标题 + 小节 + 表格 + 图表（依赖 `matplotlib`） |
| `image_write` | PNG 位图或 SVG 矢量图：柱状 / 折线 / 占比图（依赖 `Pillow`） |

只做生成，不做"编辑既有复杂排版"——后者要处理样式继承，和目标不同。
HTML / PDF / 图片都走纯 Python 库落盘，不调 shell，因此不会额外触发命令审批；
`pdf_write` 在缺少 `matplotlib` 时会明确报错，环境体检 (`/api/doctor`) 也会列出这项依赖。
生成的文件自动进产物面板，可直接预览。

## 运行时体验

- **主动建议**：每完成一步 / 产出一个文件，会给出可点的下一步建议，并同步结构化
  进度（`run.suggestion` / `run.progress`）。建议按真实产物与失败原因用确定性规则生成，
  不额外调模型；**只展示，不自动执行**，失败建议不会让你用同样参数原样重试。
- **一屏总览**：右侧「总览」页签聚合阶段、子 Agent 成员、步骤、产物数与进度条。
  这是前端旁路聚合，不是新的内核状态机。
- **长会话与恢复**：历史按 token 预算裁剪，保留最近 N 轮，更早内容压成「历史摘要」
  注入系统提示（裁剪发 `context.trimmed`）。内核异常退出后优先 `chat.resume`：
  复用同一个 task、注入已完成步骤清单，**不重做已存在的文件**；找不到断点才普通重跑。
- **编排草案**：`workflow_draft` 把一句自然语言拆成可被 `WorkflowPlan.parse()` 自检的
  `stages/steps` 草案，只引用真实存在的子 Agent，只给草案、不自动执行。
- **降级可见 / 失败可知**：向量检索退回关键词、OCR 引擎缺失会在顶部显示降级提示条；
  定时任务连调度器兜底失败路径也发系统通知，30 秒内不重复刷屏。

## 前端结构

界面是**零构建**的：没有 npm、没有打包步骤，改完刷新就生效。
以前是一个 9705 行的单文件（HTML + CSS + 6791 行 JS 全塞一起），
现在拆成 ES Module：

```
core/web/
├── index.html       169 行，只留壳
├── styles/main.css  2746 行
└── modules/*.js     22 个功能模块（core.js 是依赖根，boot.js 是入口）
```

模块之间**不互相 import** —— 依赖分析发现分区之间有 21 个循环依赖环
（UI 事件处理天然双向），ES Module 成环会触发 TDZ 让页面白屏。
跨模块调用统一从 `core.js` 取；运行期会被改值的变量放进 `core.js` 的 `mut` 容器
（`import` 绑定是只读的，不能重新赋值）。

完整规则、依赖图与验证脚本见 **[前端模块结构](docs/FRONTEND-MODULES.md)**。

## 扩展系统

技能、专家、Agent、插件、工作流、策略 —— 六类扩展**共用一条注册总线**
（`core/oryx/registry.py`）。加一种扩展类型不用改别处的代码。

每个扩展可以带一个**可选**的 `oryx.json` 声明元数据：

```jsonc
{
  "id": "web-research",
  "version": "1.1.0",
  "triggers": ["联网", "查一下", "上网"],   // 显式触发词，路由用
  "caps": ["net.egress", "net.fetch"],     // 能力声明（受信任级别裁剪）
  "contracts": {
    "gates": ["至少两个独立来源交叉验证"],   // 什么条件下算完成
    "fail_closed_when": ["来源全部不可访问"],
    "handoffs": ["doc-writer"]
  }
}
```

没有 `oryx.json` 也能加载 —— 老目录结构**一行不用改**。

三个关键机制：

- **信任分级** —— 仓库里带的扩展（`git clone` 来的）默认不可信，
  声明不了 `proc.spawn` 这类高危能力。防线在加载时，不在运行期。
- **上下文预算** —— 技能目录按本轮提问 + token 预算裁剪，
  不再全量注入。技能装到几十个也不会撑爆提示词。
- **三级路由** —— 显式触发词 → 描述 bigram 重合 → 名称匹配。
  （中文按空格分词是行不通的 —— 这是修掉的一个老 bug。）

界面代码也有门禁：

```bash
cd core
python -m oryx.ui_audit --strict    # 有 block 级问题就退出码非 0
```

完整说明（清单格式、信任矩阵、预算配置、钩子开关、门禁规则）见
**[扩展系统](docs/EXTENSION-SYSTEM.md)**。

**要自己写一个扩展** → **[写扩展指南](docs/WRITE-EXTENSION.md)**
（三分钟起步、caps 与 requires 怎么写、常见问题排查表）。
指南里的例子**由测试守着**（`tests/test_write_extension_guide.py`）——
文档写错了测试会红。

## 工具清单
想知道这个项目到底有哪些工具、每个干什么、风险多大 →
**[工具清单](docs/TOOLS.md)**。

工具是**分散注册**的（`build_registry()` 给 10 个，`loop.py` 里 21 个
`_register_*` 再加 60 多个，MCP 和用户扩展最后到），
所以「有哪些工具」读代码答不出来。清单从**真实运行时**导出，
并由 `tests/test_tool_docs.py` 守着与实现一致 —— 不会腐烂。

## 系统提示词
主控 Agent 的提示词在 `core/oryx/prompts/system/` 下，**14 个 md 文件，按语义分段**：

```
00-opening.md  01-closed-loop.md  02-routing.md  03-discipline.md
04-safety.md   05-output.md       06-memory.md   07-failure.md
08-self-check.md  09-tools.md      10-multi-agent.md  11-workflow.md
12-security-rules.md  13-skills.md
```

- 改提示词**不用碰 Python**，编辑器直接改 md；
- 段落边界清楚，diff 能看出「改了哪句提示」；
- 两处占位符 `{agents}` / `{skills}` 在运行时填入（Agent 清单与技能目录
  是运行时才知道的）；
- 少了变量或少了文件会**直接报错**，不会静默降级 ——
  提示词悄悄变短是最难查的那种 bug。

改完提示词后要更新基准快照（`tests/fixtures/system_prompt_baseline.txt`）：

```bash
cd core
python scripts/extract_system_prompt.py --check   # 校验当前 md 与基准是否一致
```

> 这是刻意的摩擦：提示词变更影响模型行为，应该被显式记录在版本控制里，
> 而不是悄悄改掉。

## 本地 API 安全

服务只监听 `127.0.0.1`，但**同机浏览器里的任意网页都能向它发请求**。CORS 只挡得住读响应，挡不住发请求——所以凡是能改变系统的接口都要求一个令牌：

- 令牌启动时用 `secrets.token_urlsafe(32)` 生成，存进 `config.json`
- 前端脚本加载时把令牌注入模块（`core.js`），前端所有请求都带 `X-Oryx-Token`
- 除 `/`（首页）、`/api/ping`（无副作用探活）与 `/web/*`（静态资源）外，
  **所有接口都必须带令牌**
- 静态资源不要求令牌 —— `<script type="module">` 带不了自定义头，
  要求令牌前端会直接加载失败。静态资源里没有用户数据，与首页同一套信任模型；
  路径穿越一律 403
- CORS 只放开 `tauri://localhost` 与 `http://127.0.0.1:*`，不用通配符

没有这道闸，任意网页都能 POST `/api/terminal/run` 在你机器上执行命令。

## 新增能力

### 项目文档 Agent

所有需求、决策、代码改动都记在**同一个文件**：`.oryx/PROJECT.md`。

```
## 概况
## 需求      REQ-001 …
## 决策      DEC-001 …
## 变更记录  CHG-001 …
## 待办      TODO-001 …
```

主 Agent 改完代码要调 `doc_change_log` 留痕；也可以直接派给子 Agent：

```
spawn_agent("doc-manager", "把这次改动记录下来：新增了用量统计，动了 store.py 和 loop.py")
```

子 Agent 有**工具白名单**（`agents/<id>/AGENT.md` 的 frontmatter），拿不到名单外的工具——实测它调不到 `fs_delete`。

### 长期记忆

`memory_save` / `memory_search` / `memory_forget`。记住的事实会在下一轮注入系统提示，跨会话有效。

### 用量统计

`GET /api/usage` 返回今日/本月的调用数与 token 分模型统计。界面上顶栏显示「今日 N 次 · Xk tokens」。

### 自动化

`automation_add`（cron）+ `automation_list` / `automation_toggle` / `automation_remove`。到点自动跑一次 Agent 循环。

cron 用 5 段格式，支持 `*`、范围、列表、步长和 `@daily` 别名。**星期按 cron 约定（0=周日）实现**——Windows 的 `time.localtime` 是「周一=0」，直接用会导致定时任务整体错位。

**每次执行都留一条独立记录**（`automation_runs` 表）：状态、产出摘要、耗时、失败原因。
用 `automation_runs` 工具查（给 id 查某个任务的历史，不给就查全部）：

```
automation_runs(id="", limit=20)
```

任务卡住或进程被杀时，记录停在 `running` —— 那就是现场，不是凭空消失。

**失败会通知你**：定时任务是没人盯着跑的，失败只发事件等于白跑。
失败时按配置走通知渠道（`notify_webhook` 配了就 POST 过去，没配就发 Windows 系统通知）。

### 内嵌终端

侧栏可展开终端面板，命令边跑边输出，能中断。Agent 也能用 `term_run` 启动。

### 通知渠道

`notify` 工具：Windows 系统通知，或 POST 到任意 webhook（钉钉/飞书/Slack 自建机器人都行）。

### 提示词优化

输入框旁的「优化」按钮。检查原文缺了动作、范围还是验收标准，然后补齐；可以同时绑定技能、专家和风格。

## 跨端远控

```
[Oryx 桌面] --wss--> [中继 relay/] <--wss-- [iOS App apps/ios]
```

电脑抓屏 → 中继转发密文 → 手机显示；手机点击/滑动 → 反方向回传 → PC 注入键鼠。

| 部分 | 说明 |
|---|---|
| PC 端 | `core/oryx/remote/`：抓屏（Pillow）、JPEG 编码、SendInput 注入 |
| 中继 | `relay/server.py`：单文件 WebSocket 服务，只管配对转发 |
| iOS | `apps/ios/`：SwiftUI 原生，Swift CryptoKit 解密 |

**端到端加密**：配对码 → HKDF-SHA256 派生密钥，每条消息 AES-GCM 独立 nonce。中继只看到密文。

**配对码**：启动远程时生成，手机输入即可。房间号就是它。

界面上点「远程」开启，侧栏显示配对码；`remote_start` 工具是 L2，Agent 要用也得你确认。

详见 [relay/README.md](relay/README.md) 与 [apps/ios/README.md](apps/ios/README.md)。

中继部署最省事的方式：

```bash
cd relay
ORYX_DOMAIN=relay.你的域名 docker compose up -d
```

仓库自带 `Dockerfile` + `Caddyfile`，Caddy 自动申请证书并把 wss 转发进来。

## 技能

`skills/<name>/SKILL.md`，带 YAML frontmatter：

```markdown
---
name: file-search
description: 按文件名或文件内容查找文件
---
操作步骤……
```

模型判断任务相关时会调用 `use_skill` 读取完整步骤再执行。格式与 Claude Code / claude-plugins-official 一致，第三方技能可直接放进 `skills/`。

## 测试

```bash
cd core
python -m pytest tests -q
```

安全回归重点：未确认的删除不执行、确认后执行、禁区操作被拒绝、每次调用都写审计。

## 本地索引

索引文件在 `%APPDATA%\Oryx\index.db`，全程本地，切换模型模式不影响。

- 检索方式：SQLite FTS5，`trigram` 分词，中文按子串命中；2 字以内自动退化为 LIKE。
- 增量：文件 `size + mtime` 未变则跳过，不重复计算。
- 自动跳过：禁区目录、系统目录、`node_modules`/`.git` 等、超过 10MB 的文件、非文本类扩展名。
- 支持格式：`txt md csv json yaml py js ts html …`、`pdf docx xlsx pptx`、`png jpg bmp webp tif`（图片走 OCR）。
- 数字检索：金额、单号会自动去千分位建立摘要，搜 `12800` 能命中 `￥12,800.00`。

### 图片 OCR

截图、发票、白板照片会被本机 OCR 识别后入库，可以直接按**图中文字**检索。

- 引擎：RapidOCR（ONNX，端侧运行，不联网）
- 安装：`pip install rapidocr-onnxruntime`
- 关闭：配置里设 `"ocr": false`
- 单张耗时：约 1.1 秒（首次含模型加载约 2.4 秒）

也可以让 Agent 直接调 `ocr_image` 工具读某张图。

### 向量检索

关键词检索能精确命中，但搜不出"意思相近"的内容。向量检索补上这一层，两者用 RRF 融合排序。

```bash
# 一次性下载模型（ONNX int8，约 24MB）
python core/scripts/fetch_embedding_model.py

# 需要 PyTorch 兜底权重时（再 +96MB）
python core/scripts/fetch_embedding_model.py --with-torch
```

- 模型：`BAAI/bge-small-zh-v1.5`（512 维，CLS pooling，端侧 CPU 推理）
- 推理后端自动选择：**ONNX int8** → ONNX fp32 → PyTorch
- 位置：`%APPDATA%\Oryx\models\bge-small-zh-v1.5`
- 关闭：配置里设 `"embeddings": false`，自动退回纯关键词
- 相关性阈值 0.45（实测相关文档 0.57–0.72，无关文档 0.23–0.32）

效果对比（同一份项目语料）：

| 查询 | 纯关键词 | 混合检索 |
|---|---|---|
| 怎么防止 AI 乱删文件 | ✗ | ✅ `rules/dangerous.yaml`、`README` |
| 图片里的文字怎么识别 | ✗ | ✅ `receipt-ocr/SKILL.md` |
| 发票怎么整理成表格 | ✗ | ✅ `receipt-ocr/SKILL.md` |
| 12800 | ✅ | ✅ |

### 索引性能

| 场景 | 耗时 |
|---|---|
| 全量 200 文件 / 200 片段 | 8.3 秒（41ms/片段） |
| 无变化重扫 | 0.27 秒 |
| 改 1 个文件后重扫 | 0.32 秒 |
| 检索 | 4–28ms |

三条优化：

1. **片段哈希复用**：文件改动后，内容未变的片段直接沿用旧向量，只有变化的部分重算。
2. **跨文件批量 embedding**：攒够一批再送模型，避免小文件各算各的。
3. **ONNX int8 后端**：比 PyTorch 快约 2.1 倍，输出余弦相似度 0.9935（实测）。

长片段（接近 512 token）下批处理的收益有限——CPU 算力才是瓶颈，所以量化模型带来的提升比批量更大。

界面上点「建立索引」即可；也可以让 Agent 自己调 `index_scan`。索引建好后，`index_search` 能按**正文内容**找文件，而不是靠文件名。

```bash
# 直接调接口
curl -X POST http://127.0.0.1:8765/api/index/scan -H "Content-Type: application/json" -d "{\"roots\":[\"D:/docs\"]}"
curl -X POST http://127.0.0.1:8765/api/index/search -H "Content-Type: application/json" -d "{\"query\":\"发票\"}"
```

## 浏览器 Agent

```bash
pip install playwright
```

不需要 `playwright install`——直接复用系统已装的 **Chrome / Edge**，失败才回退到内置 Chromium。

工具：`browser_open` / `browser_snapshot` / `browser_click` / `browser_type` /
`browser_extract` / `browser_links` / `browser_screenshot` / **`browser_wait`** /
**`browser_download`** / `browser_close`

- `browser_wait` —— 等元素出现或消失（visible/hidden/attached/detached）。
  **写自动化用它代替 `sleep` 猜时间**：页面慢一点也能等到，不会抓错元素。
- `browser_download` —— 下载文件到本地。给 `url` 直接访问下载地址，
  或给 `selector` 点页面上的导出按钮；不给 `path` 就用服务器建议的文件名。

**关键设计：不把页面塞进上下文。** 整页被压成「带编号的可交互元素清单 + 文本摘要」，模型凭编号点击：

```
URL: https://example.com
标题: 某某官网
可交互元素（21 个）：
  [1] a 「返回首页」 -> /
  [2] a 「下载」 -> /download
  [3] input type=search placeholder=搜索
页面文本摘要：……
```

实测压缩比：

| 页面 | 原始 HTML | 快照 | 比例 |
|---|---|---|---|
| marvis.qq.com | 80,094 字符 | 2,319 字符 | 2.9% |
| cn.bing.com | 15,766 字符 | 872 字符 | 5.5% |

安全性：点击与输入都会过策略引擎。`确认支付`、`注销账号` 这类文案会被拦下——浏览器 Agent 不能替你付钱，也不能替你销户。

浏览器跑在独立线程（Playwright 同步 API 不跨线程），会话在多次工具调用之间保持存活。

## 与 Marvis 的关系

功能对齐，实现独立。技能目录、风险分级、确认机制的设计参考了其公开文档与实机结构，但代码、提示词、规则、UI 均为自研，未使用其任何资产。

## 下一步

五轮系统诊断与改造的完整记录：

- [首轮诊断：8 维度打分、P0/P1/P2 清单、改动与验收](docs/DIAGNOSIS-MARVIS-WORKBUDDY.md)
  —— **第八章**是第三轮（P1/P2 全量补齐）、**第九章**是第四轮（前端拆模块）、
  **第十章**是第五轮（提示词外置）
- [第二轮：功能真实性审查与改造记录](docs/ROUND2-REALITY-AUDIT.md)
- [前端模块结构](docs/FRONTEND-MODULES.md)：第四轮把 9705 行单文件拆成 22 个 ES Module
- [写扩展指南](docs/WRITE-EXTENSION.md)：怎么写一个扩展、装在哪、为什么没加载
- [更新日志](CHANGELOG.md)：每一轮用户能做什么、什么时候会被挡住

第三轮已补齐：运行中主动建议与多 Agent 一屏总览、HTML / PDF / 图片交付与预览、
向量 / OCR 降级可见化、长会话 token 裁剪与断点续跑、意图 → 编排草案、
多模型运行时路由、MCP 无注解工具风险收紧到 L2、定时任务兜底失败通知。

第四轮已补齐：前端从单文件拆成 ES Module（依赖显式化、依赖图无环、
真实浏览器加载与功能回归双重把关）、服务端静态资源分发（含路径穿越防护）。

第五轮已补齐：主控系统提示词外置成 14 个 md 文件（改提示词不用碰 Python），
拼装结果与拆分前逐字节相同。

仍未落地 / 刻意不做（按用户价值排序）：

1. 跨端：手机接续 / 远程看屏（对应 Marvis 的 BorderlessSpace）
2. 浏览器 Agent 增强：多标签页、等待元素、下载文件
3. 联网搜索 / 图像·视频生成 / IM 渠道 / A2A：项目无对应实现，开关如实标注禁用
4. 系统设置写操作仍一律走 L2 审批 —— 安全取舍，不提供绕过入口
5. `EnableModelRouting` 默认关闭；断点续跑是语义级恢复，不是字节级状态快照
6. 前端模块仍有约 1400 行的大文件（为破循环依赖合并的代价）；
   要更细得先把 UI 事件改成发布订阅 —— 那是产品行为改动，需单独立项
7. 风格预设与模式策略还没外置成 md（它们是 dataclass 字段，改造要动类定义）
8. 本机 `qwen3.5:9b` 在长系统提示词下只吐 reasoning（`finish_reason=length`），
   与 Oryx 无关；核心链路验收用受控假模型驱动真实内核
