# 第二轮：功能真实性审查与改造记录

> 目标：把「界面上有、功能上却是空壳」的能力真正做实，精简无用代码，
> 并把前端的流畅度、排版、设计、展示整体提上来，向 Marvis 式的
> 「说一句话就自己规划、动手、交付可验收成果」靠拢。
>
> 方法：先读代码取证，再按用户价值排序改动；**不凭印象判定，不改断言让测试变绿**。
> 所有结论都带文件与行号依据；未验证的明确标注。

---

## 一、结论先行

审查发现项目不缺功能入口，缺的是**入口背后的实现**。最典型的三类问题：

| 类型 | 典型例子 | 危害 |
|---|---|---|
| 开关是装饰 | `features.py` 20 个开关有 17 个在代码里零引用 | 用户以为关掉了，模型照旧能调 |
| 能力没接线 | `mcpconfig.summarize()` / `syssettings.switches()` 已实现但零调用方 | 有实现，用户完全摸不到 |
| 状态说谎 | `MUTATING_TOOLS` 漏登记 9 个工具；`workflow_run` 标 L0 却能写文件 | 安全语义错误，ask/plan 拦不住 |

本轮共落地 **6 组做实改动 + 7 组行为修复 + 7 组前端体验改造 + 30 余处精简**。
详见第三节。

---

## 二、「表面显示」清单（按用户价值排序）

### 2.1 开关层：20 个开关，17 个空转

证据：`core/oryx/features.py` 的 `feature_catalog()` 把 20 个 key 全部渲染成可切换卡片；
但除 `DisableSandbox`（`loop.py:_register_sandbox_tool`）、`EnableFeatureFlags`
（`server.py:features_toggle`）外，**其余 17 个 key 在 `core/oryx/` 下没有任何读取点**。
切换会写入 `features.yaml`（内容是 JSON）、界面会显示"已关闭"，模型可见工具集不变。

更严重的是 5 个开关承诺的能力**根本不存在**：
`DisableWebSearch` / `DisableImageGen` / `DisableVideoGen` / `DisableChannels` / `DisableA2A`
——全项目无联网搜索、图像/视频生成、IM 渠道、A2A 实现（grep `web_search|image_gen|a2a|channels`
在 `core/oryx/` 零命中）。给不存在的东西配开关是骗人。

### 2.2 能力层：实现了，但用户触达不到

| 能力 | 实现位置 | 断在哪 |
|---|---|---|
| MCP server 管理 | `mcpconfig.py:summarize()` | 零引用；且 MCP 只在启动时加载，界面启用不生效 |
| 系统设置清单 | `syssettings.py:switches()` | 零引用；`sys_*` 工具只注册给模型，界面无入口 |
| 子 Agent 扩展字段 | `agents.py` 声明 14 个 frontmatter 字段 | 实际只有 6 个生效，8 个解析了从不用 |
| 产物 | 面板只列路径 | 不可点开、不可复用，会话重开即丢失 |

### 2.3 交互层：点了没反应 / 反应是假的

| # | 问题 | 证据 | 性质 |
|---|---|---|---|
| 1 | 用户选的权限模式不生效 | `send()` 里先 `S.policy = null` 清空、再读它拼 interaction | **安全语义错误** |
| 2 | 自动任务「立刻执行」断链 | `renderAutomations` 的 `autorun` 只 POST `goal`，缺 session_id / seq / interaction | 界面永远不动 |
| 3 | `refreshUsage()` 是空函数 | 定义为空但 RUN_FINISHED 后每次调用 | 数字一直停在旧值 |
| 4 | 模型切换失败也关弹窗 | `[data-pv]` handler 不检查 `r.ok` | 假装成功 |
| 5 | 无全局 error handler | 请求失败无兜底提示 | 静默失败 |
| 6 | `S.busy` 无超时兜底 | 收尾事件丢失则永久停在「停止」态 | 界面卡死 |
| 7 | 终端「中断」假报成功 | 无运行中任务时仍 `toast('已中断')` | 假消息 |
| 8 | 技能广场搜索只在 skills 页签生效 | `#mkq` 仅一处被读取 | 三个页签输入无效 |
| 9 | 窄屏无法切换模式/风格/人设/模型 | 顶栏 `data-fit` 到 tight/ultra/mini 档时把选择器 `display:none`，无替代入口 | 窄屏功能缺失 |
| 10 | 用量标「本月」 | 后端 `store.py` 的用量统计是滚动 30 天窗口 | 与账单对不上 |

### 2.4 正确性与安全

| # | 问题 | 根因 |
|---|---|---|
| 1 | `workflow_run` 标 L0 只读，却能派子 Agent 写文件 | 静态 risk 无法反映编排内工具白名单；ask/plan 模式拦不住 |
| 2 | `MUTATING_TOOLS` 漏 9 个会改状态的工具 | 「只读自证」字段对这些工具谎称「未改动」 |
| 3 | `/api/prompt/optimize` 选技能必崩 | `catalog.skills` 返回名字列表，`optimize()` 期望 name→对象字典 |
| 4 | 环境体检两项硬编码 `ok=True` | 沙箱/危险操作检查没真做；修复命令指向不存在的模块 |
| 5 | `yaml` 是硬依赖却只列为可选 | 缺了内核顶层 import 直接失败，体检却显示一切正常 |

---

## 三、本轮改动

### 3.1 P0 — 把空壳做实

| # | 改动 | 文件 |
|---|---|---|
| A1 | 建立「开关 → 工具集」映射，在 `_exposed_registry()` **运行时裁剪**；切换立即生效无需重启 | `features.py`、`loop.py` |
| A2 | 5 个不存在的能力如实标注「当前版本无此能力」并禁用，不假装可切换 | `features.py`、`web/index.html` |
| A3 | 修 `/api/features` 硬编码 profile、写 JSON 却按 YAML 读的不一致 | `server.py`、`features.py` |
| A4 | MCP 管理页 + 热重载：新增 `/api/mcp`、`/api/mcp/toggle`，`mcp.reload` RPC | `server.py`、`agentd.py`、`loop.py`、`web/index.html` |
| A5 | 系统设置页：新增 `/api/sys/switches`、`/api/sys/query`（**只读白名单**），写操作不提供一键按钮 | `server.py`、`syssettings.py`、`web/index.html` |
| A6 | 子 Agent 6 个字段真正生效（model / skills / permissionMode / disallowedTools / initialPrompt / memory）；删掉 3 个空承诺并如实注明 | `loop.py`、`agents.py` |

> **A1 附带的行为契约变更**：`shell_sandbox` 原本在 `_register_sandbox_tool()` 注册时
> 就读开关并跳过 —— 界面上改开关必须重启内核才生效。现在注册表始终保留该工具，
> 由 `_exposed_registry()` 每轮裁剪。因此 `tests/test_sandbox.py` 的旧断言
> 「不在 `runtime.registry`」不再成立，测试改为验证**更强的新契约**：
> 关掉时模型看不到、注册表保留、改回开启后下一轮立刻恢复、再关回去仍生效。
> 这不是放宽断言让测试变绿，而是把「动态可逆」这个旧实现做不到的行为钉死。

### 3.2 P0 — 行为正确性

| # | 改动 | 文件 |
|---|---|---|
| B1 | 权限模式取值顺序修正 | `web/index.html` |
| B2 | 自动任务「立刻执行」带全参数并切回对话页流式显示 | `web/index.html` |
| B3 | `workflow_run` / `spawn_agent` 按编排工具白名单**动态升级风险** | `loop.py` |
| B4 | `MUTATING_TOOLS` 补齐，新增一致性回归测试防止漂移 | `tools.py`、`tests/test_tool_registry_consistency.py` |
| B5 | `/api/prompt/optimize` 类型修正 | `server.py` |
| B6 | 环境体检真实检查 + `yaml` 硬依赖检测 + 修复命令 | `doctor.py` |
| B7 | 产物接口 `/api/files/open`、`/api/files/list`（越界 403），面板可打开/定位/复制/复用，会话重开从审计重建 | `server.py`、`web/index.html` |

### 3.3 P1 — 前端体验

| # | 改动 |
|---|---|
| C1 | 空会话引导：按 Craft / Plan / Ask 三组给可点任务卡，点击同时填输入框 + 切模式 |
| C2 | 设计 token：建立字号 / 间距 / 动效尺度，统一 12 处动画时长，收敛 `.sect` / `.tile` 层级 |
| C3 | 全局 error handler + `S.busy` 超时兜底；模型切换失败保留弹窗 |
| C4 | 窄屏顶栏抽屉，提供模式/风格/人设/模型的替代入口 |
| C5 | 技能广场四页签统一搜索；终端空状态与真实错误区分；`VIEW_NAME` 统一顶栏标题 |
| C6 | `step.autoclass` 事件接入界面（上一轮能力，本轮补齐可见性） |
| C7 | 顶栏抽屉复用顶栏切换逻辑，失败不再静默 |

### 3.4 精简

- 净删除 **13 处零引用后端符号**、**15 个零引用 CSS 类**、1 个死 JS 函数（`clip()`）、
  1 个死事件监听器（`data-tplname`）、2 个只写不读的状态字段（`S.providerIsLocal` / `S.modelLocal`）。
  数字是全项目复查后的净结果 —— 4 个误删后恢复的公共函数不计入。
- `VIEW_NAME`、`S.agentSubs` 本可删，改为接上真实用途（统一顶栏标题、团队面板显示成员职责）。

> **一次误删与纠正**：首轮零引用检查只扫了 `oryx/`，且正则 `(?<![\w.])` 把
> `模块.函数()` 形式的调用全部排除，导致误删 4 个测试在用的公共函数
> （`clear_disk_cache` / `clipboard_available` / `invalidate_cache` / `switches`）。
> 发现后全部恢复并补注释说明用途，复查脚本改为**全项目扫描且包含限定调用形式**。
> 防御规则：删公共符号前必须同时扫 `core/`、`apps/`、`tests/`；匹配模式不能排除 `.` 前缀。

---

## 四、验收记录

验证命令统一使用 `D:/Anaconda/python.exe`（默认 `python` 3.13 缺 pytest）。

| 项 | 命令 | 结果 |
|---|---|---|
| 前端脚本语法 | `python tests/check_web_syntax.py` | **全部通过**（28 万字节内联脚本，Chrome 编译通过） |
| 前端契约 | `python tests/check_web_script.py` | **全部通过** |
| 特性开关生效 | `pytest tests/test_feature_gates.py` | **15 passed** |
| 开关 + 清单一致性 | `pytest tests/test_features.py tests/test_tool_registry_consistency.py tests/test_feature_gates.py` | **25 passed** |
| 产物接口安全 | `pytest tests/test_files_api.py` | **8 passed** |
| MCP 管理 | `pytest tests/test_mcp_management.py` | **7 passed** |
| 系统设置接线 | `pytest tests/test_sys_switches_api.py` | **5 passed** |
| 子 Agent 扩展字段 | `pytest tests/test_agent_fields.py` | **8 passed** |
| 子 Agent 回归 | `pytest tests/test_agents.py tests/test_agent_fields.py` | **17 passed** |
| 环境体检 | `pytest tests/test_doctor.py` | 通过 |
| 误删恢复回归 | `pytest test_appicon / test_clipboard / test_syssettings / test_thinking_and_gallery` | **84 passed** |
| 开关运行时裁剪契约 | `pytest tests/test_sandbox.py` | **11 passed**（含关→开→关动态可逆） |
| 本轮改动合并验证 | 18 个相关测试文件一次跑完 | **208 passed / 0 failed**（44.93s） |

> 上一轮已落地项（auto 分类器 10/10、重试 12/12、定时任务 30/30 等）保持不变。

**真实服务验收**（`python -m oryx --no-browser --port 8793`，带 API 令牌）：

| 路由 | 结果 |
|---|---|
| `/api/features` | 200，返回带 `implemented` 标注的开关清单（不存在的能力如实标为未实现） |
| `/api/mcp` | 200，`{servers, warnings, errors}` 结构完整 |
| `/api/sys/switches` | 200，返回 power / display / input 三组能力清单 |
| `/api/files/list` | 200，空目录返回 `{files:[], count:0}` |
| `/api/doctor` | 200，自检报告正常 |
| `POST /api/sys/query` 写操作 | **400 拒绝**：`lock_screen` / `set_wallpaper` / 任意拼名 / 空值全部被白名单挡下 |

**全量测试**（`D:/Anaconda/python.exe -m pytest tests -v --disable-warnings`）：

> **915 passed / 0 failed / 82 warnings，438.94s（7 分 18 秒）**
>
> 最慢的三项：`test_indexer.py::test_scan_indexes_text_files` 27.07s、
> `test_appicon.py::test_extract_batch_is_faster_than_single` 20.12s、
> `test_appicon.py::test_extract_batch_returns_png` 5.82s ——
> 都是真实扫盘 / PowerShell 图标提取，不是卡死。
> 此前有一次后台全量跑了 19 分钟无输出，是同时并跑多个 pytest 争抢资源所致；
> 单独干净跑一次即 7 分 18 秒。

---

## 五、未落地项与原因（**第三轮已补齐其中两项**）

> 同样保留第二轮收尾时的原始判断，只补记第三轮结论，不改写历史。
> 第三轮完整记录见 `docs/DIAGNOSIS-MARVIS-WORKBUDDY.md` 第八章。

| 项 | 第二轮原因 | 第三轮结论 |
|---|---|---|
| 联网搜索 / 图像·视频生成 / IM 渠道 / A2A | 项目无对应实现，属于**新增能力**而非「把空壳做实」；本轮改为如实标注禁用，避免继续开空头支票 | **仍不提供**，且继续如实标注「当前版本无此能力」—— 不打算为了对齐清单而造假 |
| `effort` / `background` / `mcpServers` 子 Agent 字段 | 各厂商参数不一（Anthropic `thinking.budget_tokens`、OpenAI `reasoning_effort`），实现成本高；从解析器移除并注明，比留着假装支持诚实 | **仍不提供**，维持删除并注明 |
| 向量检索 / OCR 静默降级的界面提示 | 已识别，需改 indexer 状态回传链路，工作量较大，排到下一轮 | ✅ 已落地（R3-11）：`capability.degraded` 事件 + 前端常驻降级提示条，30 秒去重 |
| 长会话按 token 裁剪 / 断点续跑 | 涉及内核恢复路径重构，超出本轮范围 | ✅ 已落地（R3-06 / R3-07）：`context.py` 预算裁剪 + `resume.py` 语义级续跑 |

**一处产品级限制（非本轮引入）**：本机只有 `qwen3.5:9b` 一个本地模型，
它在 Oryx 的长系统提示词下会思考到 `max_tokens` 截断
（`stop_reason=length`，正文为空、只产出 reasoning）。直连 Ollama 同样如此，
与 Oryx 无关。因此核心链路验收需用受控假模型驱动真实内核 ——
HTTP / SSE / 策略引擎 / 工具 / 落盘都是真的，只有模型是脚本化的。

---

## 六、与 Marvis 的差距（第三轮复核）

1. **主动性** —— ✅ 已补齐（R3-04）：`run.suggestion` / `run.progress` 事件 +
   前端可点建议卡。建议由确定性规则按真实产物后缀 / 失败 / 调研工具生成，
   不额外调模型，且只展示、不自动执行。
2. **过程可视化** —— ✅ 已补齐（R3-05）：右栏新增「总览」页签，前端旁路聚合
   工作流阶段、子 Agent 成员、步骤与结构化进度条。
3. **系统级触达** —— **维持刻意取舍**：Marvis 能操作更多 OS 层对象；本项目系统设置
   仍以 PowerShell 读写为主，且写操作一律留在 L2 审批内。这是安全边界，不是缺陷。
4. **交付格式** —— ✅ 已补齐（R3-01 / R3-02 / R3-03）：新增 `html_write` /
   `pdf_write` / `image_write`，产物按结果元数据判定，并能在界面里直接预览
   （HTML/SVG 带 CSP sandbox）。
5. **第三轮之后仍存的边界**（如实列出，不冒充已解决）：
   - `EnableModelRouting` 默认关闭，需显式开启；切换会发 `model.routed` 事件。
   - 断点续跑是**语义级**恢复（注入已完成步骤 brief、不重放有副作用的工具），
     不是内核状态的字节级快照恢复。
   - 多 Agent 总览是前端旁路聚合，不是内核原生 team 状态机。
   - 跨端（手机接续 / 远程看屏）、浏览器 Agent 多标签页仍未落地。

---

## 七、防御规则（写进本轮代码注释与测试）

1. 特性开关必须在**工具暴露层**裁剪，不能只写进提示词或注册时一次性判断；新增能力必须同步登记映射。注册表保留工具是刻意的（为了运行时可逆），「不暴露」≠「未注册」。
2. 公共符号删除前，全项目（`core/` + `apps/` + `tests/`）扫引用，且不得排除 `模块.符号` 形式。
3. `MUTATING_TOOLS` 与注册表的一致性由测试钉死，新增会改状态的工具必须同步登记。
4. 写操作不提供绕过 L2 审批的一键入口；只读白名单之外的名字一律拒。
5. 任何异步请求都要有失败提示；忙碌态必须有超时兜底。
6. 界面标签必须与后端统计口径一致（滚动 30 天 ≠ 自然月）。
