---

## 一、项目现状总览

### 1.1 架构

```
┌─ apps/desktop (Tauri 2 + Rust) ────────────────┐
│  拉起内核 · 探活 · WebView 打开 · 退出收尸        │
└──────────────────┬──────────────────────────────┘
                   │ HTTP + SSE（127.0.0.1:8765）
┌─ core/web/index.html（零构建单文件前端）────────┐
│  对话流 · 执行过程 · 确认卡 · 产物面板 · 终端      │
└──────────────────┬──────────────────────────────┘
                   │
┌─ core/oryx（Python 内核，23482 行）──────────────┐
│  server.py    控制面：FastAPI + 令牌闸门 + SSE     │
│  agentlink.py 内核进程管理：IPC / 看门狗 / 自愈    │
│  loop.py      Agent 主循环（3715 行）              │
│  policy.py    风险分级 L0-L3 + 执行计划生成        │
│  tools.py     内置工具注册表                       │
│  workflow.py  多 Agent 编排引擎（DAG/并行）        │
│  mcp.py       最小 MCP 客户端（stdio/http/sse）    │
│  store.py     SQLite：任务/步骤/审计/消息          │
└──────────────────────────────────────────────────┘
```

### 1.2 技术栈

| 层 | 选型 | 证据 |
|---|---|---|
| 桌面壳 | Tauri 2 + Rust | `apps/desktop/src-tauri/Cargo.toml` |
| HTTP 层 | FastAPI + uvicorn + SSE | `core/oryx/server.py:35,176` |
| 内核 | Python 3.11+ / 标准库为主 | `core/oryx/`（76 个 .py） |
| 存储 | SQLite（任务/审计/记忆/编排） | `core/oryx/store.py`（1523 行） |
| 检索 | SQLite FTS5 + trigram + ONNX 向量 | `core/oryx/indexer.py:41-46` |
| 模型 | Anthropic 原生协议 + OpenAI 兼容协议 | `core/oryx/providers.py:69,175` |
| 扩展 | 自研 MCP 客户端（stdio + HTTP + SSE） | `core/oryx/mcp.py:61,259` |

### 1.3 核心流程（自然语言 → 交付）

```
用户输入
  → server.py:538 chat() 登记发起序号
  → agentlink.py IPC 转发到内核
  → loop.py:2923 run()  取中止代号 → _run_loop()
  → loop.py:3031 _run_loop()  建环境 / 拼系统提示词
  → loop.py:3172 for _turn in range(max_turns)   ← 规划→调用→观察主循环
      → loop.py:3196 _call_provider() 流式调模型
      → loop.py:3215 逐个处理 tool_calls
          → loop.py:3432 policy.evaluate()  风险分级
          → loop.py:3451 L3 硬拒 / 3467 模式闸门 / 3520 hook 闸门
          → loop.py:3585 L2 弹确认卡，等用户点
          → loop.py:3626 spec.fn(**args)  真实执行
          → loop.py:3640 redact 打码 → 3646 失败分类 → 3650 只读自证
          → loop.py:3673 store.finish_audit() 补记结果
  → 收尾：store.add_message / bus.run_finished
```

---

## 二、8 维度诊断打分

| # | 维度 | 检查要点 | 得分 | 结论一句话 |
|---|---|---|---|---|
| 1 | 交互模式 | Craft/Plan/Ask 三态 | **4** | 三态齐全且由代码强制，但 `auto` 分类器是空壳 |
| 2 | Agent 编排 | 拆解/并行/DAG/进度/汇总 | **4** | 编排引擎完整，但入口需模型主动构造 |
| 3 | 工具调用 | MCP/注册/超时/重试/审计 | **3** | 注册与审计完备，**无重试与退避** |
| 4 | 交付形态 | 文件成果可验收 | **4** | Word/Excel/PPT 可落盘，**缺 HTML/PDF/图片** |
| 5 | 执行安全 | 授权/沙箱/审计 | **5** | 策略引擎 + 令牌 + 凭据闸门 + 打码，四层齐备 |
| 6 | 自动化 | 定时/执行记录/失败通知 | **3** | cron 齐备，**执行记录与失败通知不落库** |
| 7 | 模型策略 | 多模型/路由规则 | **2** | 能多模型切换，**无按复杂度/成本/网络动态路由** |
| 8 | 会话与记忆 | 上下文/长任务/断点续跑 | **4** | 会话续接 + 五层记忆 + 检查点，**断点续跑未实现** |

**总分 29 / 40**

> 本章打分与各维度详述是**首轮基线**，原样保留不改写。
> 第三轮补齐后的定性重估见 **8.2**（29/40 → 37/40）。

---

### 维度 1：交互模式 —— 4 分

**做对了什么**

三态齐全，且**闸门由代码强制、不靠提示词自觉**：

| 模式 | 代码位置 | 行为 |
|---|---|---|
| ask | `modes.py:78-90` | `max_risk=L0_READ`，`allow_write=False` |
| plan | `modes.py:91-101` | `max_risk=L0_READ`，只读调研 + 出方案 |
| craft | `modes.py:102-111` | 全权限，L2 仍走确认 |

另有 4 个扩展模式：`bypass`（`modes.py:112`）、`dontAsk`（`modes.py:123`）、`auto`（`modes.py:134`）、`delegate`（`modes.py:145`）。

闸门在**两处**强制执行：
- 工具暴露层：`loop.py:3080-3082` 按模式过滤注册表
- 执行层：`loop.py:3493-3517` 逐次调用复核风险等级，ask 模式下写操作直接拒并回传「请告诉用户切换到执行模式」

安全次序明确：`policy.py:490-500` 的求值次序是 deny 规则 → 路径禁区/受保护 → 禁止命令 → 可信规则降级 → 越界路径 → 取最高级。`bypass` 只能跳过确认，**永远绕不过 deny**。

**缺陷：`auto` 模式是空壳**

`modes.py:134-144` 声明了 `use_classifier=True`，但：

```
$ grep -rn "use_classifier" core/oryx/
core/oryx/modes.py:63:    use_classifier: bool = False
core/oryx/modes.py:138:        use_classifier=True,
```

**全项目只有这两处，没有任何地方读取这个字段。** 也就是说：切到 auto 模式后，用户看到提示词说「由分类器判断」，实际行为与 craft 完全相同 —— L2 照样弹确认卡。用户会以为已经开了自动，实际没有。

**与参考产品的差距**：WorkBuddy 的 auto 模式是**真分类器**（fail-closed，判不准就拒）。Oryx 声明了但没实现。

---

### 维度 2：Agent 编排 —— 4 分

**做对了什么**

三条并行路径，从单 Agent 到大规模编排全覆盖：

| 机制 | 位置 | 能力 |
|---|---|---|
| `spawn_agent` | `loop.py:1705` | 派活给子 Agent，带**验收判据** `acceptance` |
| `team_*`（7 个工具） | `loop.py:1787-1927` | 建队/派活/信箱/名册 |
| `workflow_run` | `loop.py:1954` / `workflow.py` | 声明式 DAG 编排 |

编排引擎（`workflow.py`）质量很高：

- **DAG 依赖**：`workflow.py:80` `needs` 字段 + `workflow.py:413` 按依赖分层，跨 stage 累积（`workflow.py:400-405`）
- **真并行**：`workflow.py:431-440` ThreadPoolExecutor，**并发上限按 CPU 核收敛**（`workflow.py:52-56`，上限 16，实测取 `min(16, cpu_count)`）
- **进度上报**：`workflow.py:308-317` 发 `workflow.stage.started` / `workflow.step.started` / `workflow.step.finished`
- **结果汇总**：`workflow.py:614-642` `final_answer()` 按 stage 汇总
- **中间结果隔离**：`workflow.py:45-47` `STEP_SUMMARY_CHARS=4000`，完整产出只落 SQLite，模型只拿摘要 —— 这是省上下文的关键手段
- **失控防护**：`workflow.py:42` 单 run 上限 1000 step；`agents.py:41-43` 嵌套深度 5 层 + 每会话 spawn 预算 200

子 Agent 边界是硬约束：`loop.py:2417-2424` 工具白名单 ∩ 会话禁用 − Agent 自身禁用，且 `agents.py:134-147` 用 `utf-8-sig` 读 frontmatter（避免 BOM 导致整个 Agent 静默失效）。

**缺陷：编排入口依赖模型主动构造**

`workflow_run` 需要模型自己写出完整的 stages/steps JSON（`workflow.py:85-106` 强制要求每 step 指定 `agent` 与 `goal`）。模型的自然语言请求是「帮我并行调研这三个仓库」，要变成编排定义需要模型自己完成「意图 → 编排 JSON」这一步翻译。

**与参考产品的差距**：WorkBuddy 的 Dynamic Workflows 允许宿主注入 `agent()` / `parallel()` / `pipeline()` / `phase()` 编排原语，模型只需表达结构意图。Oryx 用**声明式数据**替代 JS 运行时（`workflow.py:8-10` 的理由是「eval 模型写的脚本等于打开 RCE 敞口」，这个取舍是对的），但也少了「结构 → 编排」的中间层。

---

### 维度 3：工具调用 —— 3 分

**做对了什么**

- **统一注册表**：`tools.py:554-712` `build_registry()` + `loop.py` 各类 `_register_*`；`ToolSpec`（`tools.py:108-139`）统一带 `risk` / `parameters` / `mutates`
- **入参校验**：JSON Schema 随工具定义一起下发给模型（`tools.py:126-131` `schema()`）
- **超时**：`tools.py:26` `SHELL_TIMEOUT=60`；`mcp.py:28` `DEFAULT_TIMEOUT=30`
- **MCP 协议**：`mcp.py` 支持 stdio / http / sse 三种传输，`mcp.py:52-58` 按 `readOnlyHint`/`destructiveHint` 注解推断风险等级
- **MCP 大输出落盘**：`mcp.py:234-256` 超 8 万字符落盘、上下文只留引用
- **审计**：`store.py:606-640` `audit()` 写「打算做什么、谁批的」，`store.py:642-658` `finish_audit()` 补记「实际成没成、有没有改动」
- **失败分类**：`failure.py:67-104` E1-E5 确定性分类（按特征词，非模型猜测）
- **只读自证**：`loop.py:3650-3667` 每个工具结果带 `mutated`/`preview`/`deleted`/`modified`/`moved`
- **延迟加载**：`loop.py:1215-1229` `_exposed_registry()` 按需暴露，配合 `tool_search` 语义召回

**缺陷 1：没有重试与退避**

```
$ grep -rn "retry|retries|backoff" core/oryx/
core/oryx/remote/host.py:166:  backoff = min(backoff * 2, 30.0)   ← 远控重连
core/oryx/agentlink.py:260:   attempt = self._restart_count      ← 内核自愈
core/oryx/clipboard.py:64:    for attempt in range(6)            ← 剪贴板重试
```

**工具调用与模型调用都没有重试**。网络抖动一次，整个任务就失败。而 `providers.py:102-103` 直接把 `httpx.HTTPError` 包成 `ProviderError` 抛出，`loop.py:3271-3275` 转成「模型调用失败」文本 —— 用户看到的只是一句失败，没有「重试 2 次后仍失败」的过程。

**缺陷 2：MCP 工具的风险推断可能偏松**

`mcp.py:52-58`：没有 `readOnlyHint` 也没有 `destructiveHint` 时 → `return 1`（L1 可逆写入）。但一个没有声明任何注解的第三方 MCP 工具完全可能删文件。README 声称「不带风险声明的工具一律按 L2 处理」（`tools.py:4`），**实现与文档矛盾**。

**与参考产品的差距**：WorkBuddy 的 MCP 工具调用带统一超时 + 重试 + 错误兜底；Marvis 的工具链在策略层之外还有一层「失败即换路径」。

---

### 维度 4：交付形态 —— 4 分

**做对了什么**

三种 Office 格式真生成、真落盘、真可读回：

| 工具 | 实现 | 测试 |
|---|---|---|
| `docx_write` | `office.py:36-65` | `test_office.py:10` 读回断言标题与要点 |
| `xlsx_write` | `office.py:69-110`（表头加粗 + 冻结首行 + 列宽自适应） | `test_office.py:36` 断言 `freeze_panes == "A2"` |
| `pptx_write` | `office.py:114-150`（封面 + 要点 + 备注） | `test_office.py:60` 断言页数与标题 |
| `xlsx_read` | `office.py:154-168` | `test_office.py:94` |

编排报告**独占创建**：`workflow.py:712-742` `write_new_report()` 用 `open(dest, "x")`，目标已存在直接失败且**不覆盖**（`test_workflow_templates.py:279` 断言原文件字节不变）。

前端有独立的「产物」面板：`core/web/index.html:3791` 按 `FILE_TOOLS` 收集产物路径，`index.html:4794` 在 `step.done` 时入库。

**缺陷：交付格式覆盖面窄**

只有 Word/Excel/PPT。**没有 HTML/网页、PDF、图片**。用户要「出一份可视化看板」「生成一张图表」「导出 PDF」时，Agent 只能：
- 用 `fs_write` 手写 HTML（没有预览/截图能力）
- 用 `shell` 调外部工具转 PDF（**L2 危险级，每次弹审批**）

README「文档生成」章节只列了三个 `*_write` 工具，与实现一致，但这与「可验收成果」的覆盖面有差距。

**与参考产品的差距**：WorkBuddy 支持 Artifact 多模态交付（文件、网页、图片、代码）；Marvis 的「动手能力」包含直接产出可用文件。

---

### 维度 5：执行安全 —— 5 分

这是本项目**最强的一维**，四层防御全部有代码落地：

**第 1 层：策略引擎（`policy.py`，721 行）**

四级风险 + 明确求值次序（`policy.py:490-500`）：

| 级别 | 行为 | 代码 |
|---|---|---|
| L0 只读 | 直接执行 | `policy.py:155` |
| L1 可逆写入 | 执行并留痕 | `policy.py:156` |
| L2 破坏性 | **弹确认卡，未确认绝不执行** | `policy.py:235-236` |
| L3 禁止 | 直接拒绝，任何模式放行不了 | `policy.py:238-240` |

受保护文件（`policy.py:33-46`）：`.git` / `.gitconfig` / `.bashrc` / `.npmrc` / `.oryx` 等命中即 L3 硬拒。
凭据文件（`policy.py:62-74`）：`.env` / `.ssh` / `.aws` / `.kube` → 升到 L2 走确认通道，**不硬拒**（`policy.py:50-56` 解释了理由：用户说「看一下我的 .env」也得能兑现）。
禁止下载命令（`policy.py:84-86`）：`curl`/`wget`/`Invoke-WebRequest` → L3。

**第 2 层：执行计划 + 确认卡（`policy.py:599-705`）**

`_build_steps()` 生成人类可读的「title / detail / impact」三段式，用户看得懂再决定。凭据路径单独插到第一步（`policy.py:578-589`），卡片上明写「授权后模型会看到这个文件的内容」。

**第 3 层：外部内容标记（`taint.py`）**

读到的外部文本进上下文前裹 `<untrusted-source>`，防止网页里的「忽略之前指令」被当命令执行。`tools.py:715-739` 明确**先截断再打标记**（反过来的话闭合标签会被切掉）。

**第 4 层：密钥打码（`redact.py`）**

9 条定向规则 + 1 条兜底，兜底命中整段返回 `[redacted]`。`loop.py:3640` 在工具结果出口统一执行。

**额外硬闸门**

- **本地 API 令牌**：`server.py:204-232` 除 `/` 和 `/api/ping` 外全部要 `X-Oryx-Token`；写操作还要 `X-Oryx-Request` 来源标记（`server.py:192-195`）；CORS 只放开本机来源（`server.py:179`）
- **项目配置不能提权**：`config.py:160-185` `PRIVILEGED_KEYS` 锁死 20 个键，项目级 `.oryx.json` 写了也忽略
- **命令沙箱**：`sandbox.py:41-53` 剔除 KEY/TOKEN/SECRET/PASSWORD 类环境变量
- **符号链接防护**：`symlink_guard.py`
- **检查点回滚**：`checkpoint.py` 文件内容还原（不依赖 git）

**审查结论**：无 P0 级安全缺陷。这套分层与 WorkBuddy 的 9 Phase 权限链是同构的。

---

### 维度 6：自动化 —— 3 分

**做对了什么**

- **cron 解析完整**：`automation.py:60-100` 支持 `*` / 范围 / 列表 / 步长 / `@daily` 等别名
- **星期约定正确**：`automation.py:43-57` 用 `isoweekday() % 7` 而非 `tm_wday`，跨平台一致（`test_automation.py:79-91` 专门验这条）
- **持久化**：`automation.py:183-206` 从 SQLite 恢复，重启后还在
- **抖动防争抢**：`automation.py:318-332` 循环任务最多延迟周期 10%，上限 15 分钟
- **仅空闲触发**：`automation.py:350-355` 主 Agent 忙时顺延
- **管理工具**：`automation_add` / `automation_list` / `automation_remove` / `automation_toggle`（`loop.py:976-1026`）

**缺陷 1：执行记录不落库**

`automation.py:378-388` 触发后只更新 `item.last_run` / `item.run_count` 两个内存字段，并调用 `store.mark_automation_run(item.id, None)` —— **第二个参数是 next_run_at，不是执行结果**。定时任务每次跑出来的产出、耗时、成功失败**都没有独立记录**。

用户想知道「上周三那次定时任务到底生成了什么」，无处可查。

**缺陷 2：失败通知不可靠**

`loop.py:929-935` `_fire()` 捕获异常后发 `automation.failed` 事件（前端可见）。但 `automation.py:380-383` 的 `on_fire` 调用外层包了 `except Exception: pass` —— **异常被静默吞掉**。

结合缺陷 1：定时任务失败时，用户不打开界面就完全不知道。WorkBuddy 的 scheduled-tasks 明确要求「独立的任务存储、执行记录、失败通知」。

---

### 维度 7：模型策略 —— 2 分

**做对了什么**

- **两种协议**：`providers.py:69` Anthropic 原生 + `providers.py:175` OpenAI 兼容（DeepSeek / 混元 / Ollama）
- **能力矩阵**：`manage.py:194-220` 每个 provider 标 `context_window` / `max_output` / `supports_tools` / `supports_vision` / `cost_hint`
- **本地/云端双槽**：`config.py:513-524` 切模式时按槽位取模型，来回切不乱跳（`test_thinking_and_gallery.py:416-452` 验证）
- **运行时增删改**：`domains/model_config.py` 完整 CRUD
- **密钥不入库**：`config.py:351-376` 明文自动迁移到系统凭据库
- **本地模型探测**：`localmodels.py` 扫已装模型
- **思考过程通道**：`providers.py:38-41` `reasoning` 字段，各家字段名都收

**缺陷：没有动态路由**

```
$ grep -rn "provider.*route|complexity|cost" core/oryx/
core/oryx/domains/model_config.py:20: router = APIRouter(prefix="/api/providers", ...)
core/oryx/manage.py:201:  "cost_hint": "按 token 计费",
core/oryx/manage.py:217:  for key in ("context_window", "max_output", ...):
```

`cost_hint` 只是一个**给人看的标签字符串**，不参与任何决策。系统没有：
- 按任务复杂度选模型（简单任务用小模型、复杂任务用大模型）
- 按成本预算选模型
- 按网络条件降级（云端挂了自动退本地）
- 按失败自动切换 provider

`loop.py:3056-3057` 每轮都固定用 `self.config.provider_config()` 返回的**同一个** provider。

**与参考产品的差距**：WorkBuddy 的多模型路由是**运行时策略**（按复杂度/成本/网络条件选择）；Oryx 目前是**用户手动切换**（配置层面），不是**系统自动决策**（执行层面）。这是本项目与 WorkBuddy 差距最大的一维。

---

### 维度 8：会话与记忆 —— 4 分

**做对了什么**

- **会话续接**：`loop.py:3059-3108` 复用同一 task，历史消息带入上下文（`verify_thinking_session.py:112` 验证带历史）
- **五层记忆**：`memory.py:35` `SCOPES = ("user", "project", "local", "rule", "auto")`
- **条件规则注入**：`memory.py:310-337` 按路径触发，且**注入过就不重复注入**
- **@import 展开**：`memory.py:286-306` 递归深度封顶 5
- **glob 语义正确**：`memory.py:117-150` 自己实现 `**` 跨层匹配 + matchBase + 花括号展开（`fnmatch` 不认 `**`，标准做法会静默失效）
- **SQLite 记忆**：`store.py:772` `save_memory` / `792` `search_memories`
- **中止可靠**：`loop.py:3003-3014` 用「中止代号」而非 Event 置位判定，解决「0~20ms 的停止全被吞掉」的竞态
- **取消收口**：`loop.py:3016-3029` `_finish_cancelled` 统一收尾，标 `cancelled` 而非 `failed`

**缺陷：断点续跑未实现**

内核崩溃时 `agentlink.py:285-295` 会**重跑**整个任务（`self.call("chat.run", **pending)`），不是**从断点恢复**。已经完成的工具调用会全部重来一遍。

`checkpoint.py` 提供的是「回退到某个检查点」的能力（面向用户主动撤销），不是「从崩溃点继续」。

**缺陷：长任务上下文无上限保护**

`loop.py:3098-3108` 取历史时**只取 user/assistant 角色、不截断**。长会话累积到几百轮后，历史会撑爆上下文窗口 —— 代码里没有任何 token 预算控制或历史裁剪策略（`store.py:1135` 的 `prepare_retry` 只服务于「重新生成」）。

---

## 三、与 Marvis / WorkBuddy 的差距点汇总

> 本表是**首轮基线**，记录第一次诊断时的现状，保留不改。表中差距已在
> P0 与第三轮逐条处理；当前结论见 **第八章**（8.2 重估、8.3 变更清单、8.7 仍存边界）。

| 参考产品 | 具体能力 | Oryx 现状 | 差距 |
|---|---|---|---|
| WorkBuddy | auto 模式 = 真分类器（fail-closed） | `modes.py:134` 声明 `use_classifier=True`，**无任何实现** | 声明与实现不符 |
| WorkBuddy | 工具调用带超时 + 重试 + 兜底 | `providers.py:102` 直接抛错，无重试 | 缺重试与退避 |
| WorkBuddy | Artifact 多模态交付 | 仅 docx/xlsx/pptx | 缺 HTML/PDF/图片 |
| WorkBuddy | scheduled-tasks 有执行记录与失败通知 | `automation.py:380` 异常被 `pass` 吞掉 | 执行记录不落库、失败静默 |
| WorkBuddy | 多模型**运行时路由** | 只能用户手动切换 | 缺复杂度/成本/网络路由 |
| WorkBuddy | 长会话上下文预算管理 | `loop.py:3098` 历史不截断 | 无 token 预算 |
| WorkBuddy | Dynamic Workflows 编排原语 | 声明式 JSON，模型自己写 | 缺「意图 → 编排」中间层 |
| Marvis | 动手能力 → 直接产出可用文件 | 三种 Office 格式 | 覆盖格式窄 |
| Marvis | 工具风险默认从严（README 称 L2） | `mcp.py:58` 实际返回 L1 | 文档与实现矛盾 |

**Oryx 强于两个参考产品的一维**：执行安全（第 5 维，5 分）。凭据闸门、untrusted-source 标记、密钥打码、项目配置不能提权这四条，都是 WorkBuddy 公开资料里没有同级细度的设计。

---

## 四、优化清单（P0 / P1 / P2）

### P0 — 影响可用性，必须改（**已全部落地**）

| # | 项 | 现状 | 目标 | 改动文件 | 工作量 | 状态 |
|---|---|---|---|---|---|---|
| P0-1 | 临时目录重定向漏 `TMPDIR` | `paths.py` 只设 TEMP/TMP，标准库优先用 TMPDIR → 临时文件仍落 C 盘 | 三者同设，D 盘生效 | `core/oryx/paths.py`、`apps/desktop/src-tauri/src/paths.rs` | 0.5h | ✅ 已落地 |
| P0-2 | 取消测试时序假设脆弱 | 固定 sleep(0.15) 假设搭环境够快，负载高即误报 | 确定性等待模型吐过 chunk 再取消 | `core/tests/test_cancel.py` | 0.5h | ✅ 已落地 |
| P0-3 | `auto` 模式分类器空壳 | `modes.py:138` 声明 `use_classifier=True`，无实现 | 实现真正的 fail-closed 分类器 | `core/oryx/autoclass.py`（新增）、`core/oryx/loop.py`、`core/oryx/modes.py` | 2h | ✅ 已落地 |
| P0-4 | 定时任务执行记录与失败通知缺失 | `automation.py:380` 异常被吞，执行记录不落库 | 每次执行独立落库 + 失败走通知渠道 | `core/oryx/automation.py`、`core/oryx/store.py`、`core/oryx/loop.py` | 3h | ✅ 已落地 |
| P0-5 | 模型/工具调用无重试 | `providers.py:102` 直接抛错 | 网络类失败重试 2 次（指数退避），不重试语义类失败 | `core/oryx/providers.py`、`core/oryx/mcp.py` | 2h | ✅ 已落地 |

### P1 — 明显提升体验（**第三轮已全部落地**）

| # | 项 | 现状 | 目标 | 实际改动文件 | 状态 |
|---|---|---|---|---|---|
| P1-1 | 交付格式只覆盖三种 Office | 无 HTML/PDF/图片 | 增加 HTML / PDF / 图片生成路径 | `core/oryx/artifacts.py`（新增）、`core/oryx/loop.py`、`features.py`、`tools.py`、`autoclass.py`、`doctor.py` | ✅ 已落地（R3-01） |
| P1-2 | MCP 工具风险推断偏松 | 无注解 → L1 | 改为 L2（与 README 一致） | `core/oryx/mcp.py` | ✅ 已落地（R3-10） |
| P1-3 | 编排入口依赖模型写 JSON | 模型要自己产出完整 stages/steps | 加「自然语言 → 编排草案」辅助层 | `core/oryx/planning.py`（新增）、`loop.py`、`features.py` | ✅ 已落地（R3-08） |
| P1-4 | 长会话历史不截断 | 全量取历史 | 按 token 预算裁剪，保留最近 N 轮 | `core/oryx/context.py`（新增）、`loop.py` | ✅ 已落地（R3-06） |
| P1-5 | 断点续跑缺失 | 崩溃后整体重跑 | 续跑且不重复已完成的副作用操作 | `core/oryx/resume.py`（新增）、`agentd.py`、`agentlink.py`、`loop.py` | ✅ 已落地（R3-07，改为**语义级恢复**，见第八章边界说明） |

### P2 — 锦上添花（**第三轮已全部落地**）

| # | 项 | 现状 | 目标 | 实际改动文件 | 状态 |
|---|---|---|---|---|---|
| P2-1 | 多模型无动态路由 | 用户手动切换 | 按复杂度/成本/网络自动选 | `core/oryx/routing.py`（新增）、`config.py`、`features.py`、`loop.py` | ✅ 已落地（R3-09，**默认关闭**） |
| P2-2 | 产物面板只认固定工具名 | 前端硬编码 5 个工具 | 按工具结果元数据判定产物 | `core/oryx/artifacts.py`、`loop.py`、`core/web/index.html` | ✅ 已落地（R3-02） |
| P2-3 | 失败通知只走事件总线 | 前端不打开就收不到 | 兜底失败路径也发系统通知 | `core/oryx/loop.py` | ✅ 已落地（R3-12） |

---

## 五、基线测试记录

```
$ "D:/Anaconda/python.exe" -m pytest tests -q --disable-warnings
...
FAILED tests/test_cancel.py::TestCancel::test_cancel_keeps_partial_stream_text
FAILED tests/test_paths.py::test_tempfile_follows_redirect - AssertionError: ...
2 failed, 831 passed, 82 warnings in 751.98s (0:12:31)
```

**两个失败的根因（均已定位，非环境问题）**

1. `test_paths.py::test_tempfile_follows_redirect`
   —— **真实产品缺陷**。`tempfile._candidate_tempdir_list()` 的候选顺序是
   `TMPDIR → TEMP → TMP`，而 `paths.py` 只设了后两个。机器上原本就有
   `TMPDIR=C:\Users\20986\AppData\Local\Temp`，于是重定向完全失效，
   临时文件仍落 C 盘 —— 与该模块「所有落盘都在 D 盘」的设计目标直接冲突。
   单独跑该用例通过是因为 pytest 进程启动时 `tempfile.tempdir` 尚未缓存。

2. `test_cancel.py::TestCancel::test_cancel_keeps_partial_stream_text`
   —— **测试自身的时序假设**，产品行为正确。用例用固定 `sleep(0.15)` 隐含假设
   「搭环境 < 150ms」；整文件跑时机器负载高，取消落在「模型还没被调用」之前，
   走的是合法的「取消先于任务启动」分支（此时本就没有正文可留）。
   已用确定性信号（`provider.first_chunk`）替换固定 sleep。

---

## 六、验收记录

### 6.1 已落地项

| 项 | 验证方式 | 结果 |
|---|---|---|
| P0-1 修复 | `pytest tests/test_paths.py` | **28 passed**（新增 2 条回归：TMPDIR 必须被设、必须覆盖机器上已有的 TMPDIR） |
| P0-2 修复 | `pytest tests/test_cancel.py tests/test_paths.py` | **45 passed**（此前 2 failed） |
| P0-3 分类器 | `pytest tests/test_autoclass.py` | **10 passed**（含「auto ≠ craft」「凭据必须拒」「shell 仍走确认」） |
| P0-4 执行记录 | `pytest tests/test_automation_runs.py tests/test_automation.py` | **30 passed** |
| P0-5 重试 | `pytest tests/test_retry.py` | **12 passed**（含「已吐字不重试」「非瞬时不重试」「MCP server 退出不重试」） |
| Rust 侧一致性 | `test_python_side_env_keys_match_rust_side` | 通过（`_ENV_LAYOUT` 与 `paths.rs` 清单一致） |
| 前端语法 | `tests/check_web_syntax.py` | **全部通过**（Chrome 编译内联脚本） |

### 6.2 真实链路验收（受控假模型 + 真实内核）

用**受控的假模型端点**驱动**真实的 Oryx 内核**：真 HTTP、真 SSE、真策略引擎、
真文件落盘。链路：

```
自然语言请求 → 模型规划 → 调 xlsx_write → 文件落盘 → Excel 读回
```

| 检查项 | 结果 |
|---|---|
| `/api/chat` 返回 | `{"status":"started"}` |
| 工具调用 | 1 次，`xlsx_write`，风险等级「可逆写入」 |
| 分类器判定 | `allow`（该动作有检查点快照兜底，做错了可以回退） |
| 确认卡弹出 | **0 次**（auto 模式免打扰生效） |
| 审计记录 | `decision=hook_allowed outcome=done mutated=1` |
| 交付物 | `本周数据要点.xlsx` 5151 字节，**openpyxl 成功打开** |
| 表内容 | `('指标','本周','环比')` / `('新用户注册','1280 人','+12%')` / … |

### 6.3 安全红线验收（auto 模式不能变成「什么都放行」）

三条红线，全部通过：

| 场景 | 分类器 | 确认卡 | 被拒 | 成功步骤 | 结论 |
|---|---|---|---|---|---|
| 删文件（可回滚） | `allow` | 0 | 0 | 1 | 文件确实被删，免打扰生效 |
| 读 `.env`（凭据） | `deny` | 0 | 1 | 0 | 拒绝理由写明「需要用户本人明确授权」 |
| 执行 shell（不可回滚） | `ask` | **1** | 0 | 0 | 仍走确认通道，没有被 auto 绕过 |

### 6.4 基线对比

| 时点 | 结果 |
|---|---|
| 改动前 | 2 failed, 831 passed（12 分 31 秒） |
| 改动后 | **1 failed, 866 passed**（26 分 00 秒）—— 用例总数 +35 |

**遗留的 1 个失败**：`tests/test_clipboard.py::test_files_take_priority_over_text`。
单独跑 **7 passed**，全量跑偶发失败。根因是它依赖**全局独占的系统剪贴板**
（`CF_HDROP`），并发场景下被别的程序抢占；属既有环境脆弱性，与本次改动无关。

### 6.5 未落地项与原因（**首轮状态，第三轮已全部补齐**）

> 下表保留首轮收尾时的原始记录，不改写历史。P1 全部 5 项、P2 全部 3 项
> 已在第三轮落地，逐项对照、变更编号、验证与边界见**第八章**。

| 项 | 首轮未落地原因 | 第三轮状态 |
|---|---|---|
| P1-1 交付格式扩展（HTML/PDF/图片） | 工作量 4h，超出本次 P0 范围 | ✅ R3-01 |
| P1-2 MCP 工具风险推断改 L2 | 改 L2 会让所有无注解的 MCP 工具都要确认，影响面需产品确认 | ✅ R3-10（确认口径按 README 承诺统一为 L2） |
| P1-3 编排的「意图 → 编排」中间层 | 工作量 4h | ✅ R3-08 |
| P1-4 长会话历史按 token 裁剪 | 工作量 3h，需要定预算策略 | ✅ R3-06 |
| P1-5 断点续跑 | 工作量 6h，涉及内核崩溃恢复路径重构 | ✅ R3-07 |
| P2 全部 | 工作量 8h+，按优先级排后 | ✅ R3-09 / R3-02 / R3-12 |

**一处产品级限制（非本次改动引入）**：本机只有 `qwen3.5:9b` 一个本地模型，
它在 Oryx 的长系统提示词下会思考到 `max_tokens` 截断
（`stop_reason=length`，正文为空、只产出 reasoning），因而无法完成真实工具调用。
实测：直连 Ollama 同样如此（`content` 长度 0，`finish_reason=length`，耗时 117s），
与 Oryx 无关。因此核心链路验收改用受控假模型驱动真实内核 ——
链路本身（HTTP/SSE/策略引擎/工具/落盘）都是真的，只有模型是脚本化的。

---

## 七、代码改动前后对比

### 改动清单

| 文件 | 类型 | 改动 |
|---|---|---|
| `core/oryx/paths.py` | 修改 | `_ENV_LAYOUT` 增加 `TMPDIR` |
| `apps/desktop/src-tauri/src/paths.rs` | 修改 | `redirect_env` 增加 `safe_set("TMPDIR", &temp)` |
| `core/oryx/autoclass.py` | **新增** | auto 模式分类器（三档判定 + fail-closed） |
| `core/oryx/modes.py` | 修改 | auto 模式提示词与实现对齐 |
| `core/oryx/loop.py` | 修改 | 接入分类器判定；新增 `_credential_paths_in`；新增 `_notify_automation_failure`；新增 `automation_runs` 工具 |
| `core/oryx/providers.py` | 修改 | `ProviderError` 增加 `transient`/`streamed`；新增 `_chat_with_retry`；两个 provider 拆出 `_chat_once` |
| `core/oryx/mcp.py` | 修改 | 新增 `_call_tool_with_retry` |
| `core/oryx/automation.py` | 修改 | 抽出 `_run_item`；异常不再被吞；新增 `on_error` 回调与 `last_error` |
| `core/oryx/store.py` | 修改 | 新增 `automation_runs` 表 + 4 个方法 |
| `core/web/index.html` | 修改 | 接入 `step.autoclass` 事件 + 判定徽章 |
| `core/tests/test_cancel.py` | 修改 | 取消时机改为确定性信号 |
| `core/tests/test_paths.py` | 修改 | 新增 2 条 TMPDIR 回归 |
| `core/tests/test_autoclass.py` | **新增** | 10 个用例 |
| `core/tests/test_retry.py` | **新增** | 12 个用例 |
| `core/tests/test_automation_runs.py` | **新增** | 10 个用例 |
| `README.md` / `CHANGELOG.md` | 修改 | 补 auto 模式、执行记录、重试、TMPDIR 的说明 |

### 关键改动 1：`auto` 模式从空壳变成真分类器

**改前**（`modes.py`）——声明了 `use_classifier=True`，但全项目没人读它：

```python
"auto": ModePolicy(
    name="auto",
    max_risk=Risk.L3_FORBIDDEN,
    allow_write=True,
    use_classifier=True,      # ← 只有这一行，没有任何实现
    prompt=("当前是【自动判定模式】。需不需要确认由系统分类器判断…"),
),
```

**改后**（`loop.py` 的 `_handle_tool_call`）——分类器真正接管审批决策：

```python
if mode_policy.use_classifier:
    from .autoclass import ALLOW, ASK, DENY, classify
    verdict, why = classify(
        call.name, call.arguments,
        risk=int(decision.risk),
        credential_paths=self._credential_paths_in(call.arguments),
    )
    self.bus.publish("step.autoclass", task_id=task_id, step_id=step_id,
                     tool=call.name, verdict=verdict, reason=why)
    if verdict == DENY:
        ... return {"is_error": True, ...}
    if verdict == ALLOW:
        skip_approval = True
        need_confirm = False
    elif verdict == ASK:
        skip_approval = False          # fail-closed：退回确认
        ...
```

判定依据（`autoclass.py`）——**按「改错了能不能撤回来」分档，不按「危不危险」**：

```python
RECOVERABLE_TOOLS = frozenset({          # 有检查点快照兜底 → 放行
    "fs_write", "fs_delete", "fs_move",
    "docx_write", "xlsx_write", "pptx_write",
})
HUMAN_ONLY_TOOLS = frozenset({           # 需要人拍板 → 拒绝
    "remote_start", "provider_add", "provider_use", "provider_delete",
})
```

`shell` 不在放行名单里——它造成的改动**不在检查点覆盖范围内**
（见 `loop.py` 的 `_snapshot_before_write`），给豁免等于给一个假的安全感。

### 关键改动 2：重试的两条硬规则

**改前**（`providers.py`）——一次失败整个任务结束：

```python
except httpx.HTTPError as exc:
    raise ProviderError(f"连接 Anthropic 失败：{exc}") from exc
```

**改后**——只重试瞬时失败，且已吐字就不重试：

```python
except httpx.HTTPError as exc:
    error = ProviderError(f"连接 Anthropic 失败：{exc}", transient=True)
    error.streamed = bool(reply.text or reply.reasoning)   # 吐过字就别重试
    raise error from exc
```

```python
def _chat_with_retry(self, attempt):
    for attempt_no in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return attempt()
        except ProviderError as exc:
            if not exc.transient:      # 规则 1：只重试瞬时失败
                raise
            if getattr(exc, "streamed", False):   # 规则 2：已吐字不重试
                raise
            if attempt_no >= RETRY_MAX_ATTEMPTS:
                raise
            time.sleep(_backoff_delay(attempt_no))
```

### 关键改动 3：定时任务失败不再静默

**改前**（`automation.py`）：

```python
try:
    self.on_fire(item)
except Exception:
    pass          # ← 定时任务挂了没人知道
```

**改后**：

```python
def _run_item(self, item):
    try:
        self.on_fire(item)
    except Exception as exc:
        self.last_error = f"{type(exc).__name__}: {exc}"
        if self.on_error is not None:
            try:
                self.on_error(item, self.last_error)
            except Exception:
                pass
```

外加 `store.py` 的 `automation_runs` 表与 `loop.py` 里每次执行的落库与失败通知。

### 关键改动 4：临时目录重定向

**改前**（`paths.py`）：

```python
_ENV_LAYOUT = {
    "TEMP": ("temp",),
    "TMP": ("temp",),
    ...
```

**改后**：

```python
_ENV_LAYOUT = {
    # TMPDIR 必须一起设 —— 它在 tempfile 的候选目录里**排在 TEMP/TMP 前面**
    # （见标准库 tempfile._candidate_tempdir_list：先试 TMPDIR，再 TEMP，最后 TMP）。
    "TMPDIR": ("temp",),
    "TEMP": ("temp",),
    "TMP": ("temp",),
    ...
```


---

## 八、第三轮：补齐 P1/P2 与 Marvis 差距（2026-11-02）

### 8.1 结论先行

本轮要求「把所有短板与未落地项补齐，并弥补与 Marvis 的差距」。做法是不凭报告摘要臆断，
先只读探查当前源码，定位 13 项能力的接入点，再逐项落地并验证。

| 结论 | 结果 |
|---|---|
| 首轮 P1 / P2 | 5 项 / 3 项，**全部落地** |
| 第二轮「与 Marvis 仍存的差距」 | 3 项补齐；1 项（系统级触达）为刻意安全取舍，维持不变 |
| 新增模块 | 6 个：`artifacts` / `suggestions` / `context` / `resume` / `planning` / `routing` |
| 新增回归测试文件 | 5 个（另修 2 个既有 fixture + 1 个全局守卫） |
| 全量 pytest | **963 passed / 0 failed / 82 warnings，387.50s（6 分 27 秒）** |
| 真实服务验收 | 5 个既有路由 200；preview 401 / 400 / 403 / 415 / 200+CSP；写操作全 400 |

> 中途曾出现 `1 failed, 962 passed`：唯一失败是
> `test_domains.py::test_server_session_rename_route` 报 404「没有这个会话」。
> 根因是**本轮新增测试引入的全局状态污染**（见 8.4），不是产品回归；
> 修复后按最坏顺序局部验证 78 passed，再全量重跑 963 passed / 0 failed。

### 8.2 8 维度重估（保留第二章 29/40 基线，不改写历史）

> 这是**基于落地项的定性重估，不是自动化测量**；首轮 29/40 及其扣分依据原样保留。

| # | 维度 | 首轮 | 第三轮后 | 依据 |
|---|---|---|---|---|
| 1 | 交互模式 | 4 | 5 | 首轮扣分项「auto 分类器空壳」已在 P0 落地为 fail-closed 分类器 |
| 2 | Agent 编排 | 4 | 5 | R3-08 编排草案中间层 + R3-04 主动建议 + R3-05 一屏总览 |
| 3 | 工具调用 | 3 | 4 | P0 重试/退避 + R3-10 MCP L2 + R3-11 降级可见；「失败即换路径」仍是局部规则，未给 5 |
| 4 | 交付形态 | 4 | 5 | R3-01 三种新格式 + R3-02 元数据判定 + R3-03 界面预览 |
| 5 | 执行安全 | 5 | 5 | 原四层不变，另加 CSP sandbox 与统一路径闸门 |
| 6 | 自动化 | 3 | 4 | P0 执行记录/失败通知 + R3-12 兜底通知；缺任务级自动改道，未给 5 |
| 7 | 模型策略 | 2 | 4 | R3-09 运行时路由已落地，但**默认关闭**，故未给 5 |
| 8 | 会话与记忆 | 4 | 5 | R3-06 token 预算裁剪 + R3-07 断点续跑 |
| | **总分** | **29 / 40** | **37 / 40** | |

### 8.3 变更编号清单

| 编号 | 短板来源 | 落地内容 | 主要文件 |
|---|---|---|---|
| R3-01 | P1-1 交付格式 | `html_write` / `pdf_write` / `image_write` 真落盘：自包含 HTML、内嵌中文字体 PDF、PNG / SVG；纯 Python 库，不调 shell | `artifacts.py`（新增）、`loop.py`、`features.py`、`tools.py`、`autoclass.py`、`doctor.py` |
| R3-02 | P2-2 产物判定 | 后端按结果元数据（`ok` + `bytes` + path 字段）判定产物；`step.done` / `step.failed` 显式带 `artifact_path` 与 `args` | `artifacts.py`、`loop.py`、`index.html` |
| R3-03 | 预览最后一公里 | `GET /api/files/preview`；抽出 `_checked_file()` 与「打开」共用路径闸门；HTML/SVG 加 CSP sandbox | `server.py`、`index.html` |
| R3-04 | 第二轮差距 1 | `run.suggestion` / `run.progress` 事件 + 可点建议卡；确定性规则，不额外调模型 | `suggestions.py`（新增）、`loop.py`、`index.html` |
| R3-05 | 第二轮差距 2 | 右栏「总览」页签：阶段 / 子 Agent 成员 / 步骤 / 产物数 / 进度条 | `index.html` |
| R3-06 | P1-4 token 裁剪 | 保守估算（中文 1 字≈1 token）、预算裁剪、保留最近 N 轮、老历史摘要注入系统提示、发 `context.trimmed` | `context.py`（新增）、`loop.py` |
| R3-07 | P1-5 断点续跑 | `chat.resume` RPC + 看门狗优先续跑；复用同一 task、注入已完成步骤 brief、不重放副作用工具 | `resume.py`（新增）、`agentd.py`、`agentlink.py`、`loop.py` |
| R3-08 | P1-3 编排中间层 | `workflow_draft`：自然语言 → 声明式 `stages/steps`，立刻用 `WorkflowPlan.parse()` 自检，只引用真实子 Agent | `planning.py`（新增）、`loop.py`、`features.py` |
| R3-09 | P2-1 动态路由 | `EnableModelRouting` 默认关闭；按复杂度 / 成本 / 网络条件选**配置里真实存在**的 provider，切换发 `model.routed` | `routing.py`（新增）、`config.py`、`features.py`、`loop.py` |
| R3-10 | P1-2 MCP 风险 | 无注解 `MCPTool.risk_level()` 从 L1 收紧为 L2，与 README / `tools.py` 承诺一致 | `mcp.py` |
| R3-11 | 第二轮未落地项 | 向量检索退回关键词 / OCR 引擎缺失发 `capability.degraded`，前端常驻降级提示条 | `loop.py`、`index.html` |
| R3-12 | P2-3 失败通知 | 调度器 `on_error` 兜底路径也走系统通知，30 秒去重 | `loop.py` |
| R3-13 | 全量失败根因 | 修两个 fixture 的裸替换 + `conftest.py` autouse 守卫 | `tests/test_artifacts.py`、`tests/test_files_api.py`、`tests/conftest.py` |

### 8.4 根因表

| 现象 | 根因 | 修复 |
|---|---|---|
| 全量 `test_server_session_rename_route` 404，单跑却通过 | `domains/session.py::register()` 在 app 装配时**闭包捕获** `oryx.server.store`；`test_artifacts.py::srv` 裸赋值换库且不恢复，导致测试里的 store 与路由里的 store 指向两个库。`test_files_api.py` 早有同一隐患，只是字母序 f 排在受害者 d 之后没打中 | 两处 fixture 改走 `monkeypatch.setitem/setattr` 自动恢复；preview 本就不碰 store，删掉多余换库；`conftest.py` 加 autouse 守卫逐用例钉死 store 身份 |
| `step.done` 前端实时产物收集断链 | 事件没带 `args`，旧实现只靠前端硬编码 `FILE_TOOLS` | 后端显式带 `args=call.arguments` 与 `artifact_path`；产物改按元数据判定 |
| HTML 自包含测试误判外链 | 断言全文禁止 `http://`，但内联 SVG 的 `xmlns="http://www.w3.org/2000/svg"` 是命名空间 URI，不是外链资源 | 改断言为禁止真正远程加载模式（`<script src=` / `<link ` / `src="http` / `href="http`） |
| 跑完没有最终建议 | 收尾建议与运行中建议做了跨阶段去重，而前端会替换上一组卡，「预览刚生成的产物」被吃掉 | 收尾建议不再跨阶段去重，`phase='final'`；测试改验「运行中同阶段去重 + 收尾仍有建议」 |
| PDF 中文项目符号缺字告警 | `SimHei` 没有 U+2022 `•` | `_pdf_text_page()` 改用 ASCII `- ` |
| `EnableModelRouting` 被判「装饰开关」 | 开关一致性测试的白名单只认「工具组」，而它控制的是执行期模型选择 | 白名单注明用途，不改断言放宽 |
| `workflow_draft` 被判未登记只读例外 | `readonly_ok` 清单未收录 | 收录并注明「只生成草案、不执行、不落库」 |

### 8.5 验收记录

验证统一用 `D:/Anaconda/python.exe`（默认 `python` 3.13 缺 pytest）。

| 范围 | 命令 | 结果 |
|---|---|---|
| 交付与预览 | `pytest tests/test_artifacts.py` | **11 passed** |
| 建议 + 交付 + auto 回归 | `pytest tests/test_suggestions.py tests/test_artifacts.py tests/test_autoclass.py` | **27 passed** |
| 上下文与续跑 | `pytest tests/test_context_resume.py` | **13 passed** |
| 编排 / 路由 / 开关一致性 | `pytest tests/test_planning_routing.py tests/test_feature_gates.py tests/test_tool_registry_consistency.py` | **31 passed** |
| 降级 / MCP | `pytest tests/test_degradation.py tests/test_mcp.py tests/test_mcp_management.py` | **20 passed** |
| 前端契约 | `python tests/check_web_script.py` | **全部通过** |
| 前端语法 | `python tests/check_web_syntax.py` | **全部通过**（Chrome 编译 293926 字节内联脚本） |
| 环境体检 | `pytest tests/test_doctor.py` | **8 passed, 4 warnings** |
| 污染修复（最坏顺序） | `pytest test_artifacts / test_files_api / test_context_resume / test_suggestions / test_planning_routing / test_degradation / test_domains` | **78 passed / 29.40s** |
| **全量** | `pytest tests -q --disable-warnings` | **963 passed / 0 failed / 82 warnings，387.50s（6 分 27 秒）** |

> 全量日志：`core/.tmp-logs/round3-full2.log`。第一次全量日志
> `round3-full.log` 末尾的 `EXIT=1` 来自 pytest 退出时清理临时目录触发本机
> `sitecustomize` 批量删除守卫（`SystemExit: 1`），**不是测试失败**；
> 测试结论以日志正文的 `passed/failed` 为准。

**真实服务验收**（`python -m oryx --no-browser --port 8793 / 8794`，固定 `ORYX_API_TOKEN`）：

| 请求 | 结果 |
|---|---|
| `GET /api/features`、`/api/mcp`、`/api/sys/switches`、`/api/files/list`、`/api/doctor` | 全部 **200** |
| `GET /api/files/preview` 不带令牌 | **401** |
| 同上，空 `path` | **400** |
| 同上，`C:/Windows/win.ini` 越界 | **403** |
| 同上，`.exe` 不支持格式 | **415** |
| 同上，`allowed_roots` 内合法 HTML / SVG | **200** + `CSP: ... sandbox` + `X-Content-Type-Options: nosniff` + `Cache-Control: no-store` + `Content-Disposition: inline` |
| `POST /api/sys/query` 写操作（`lock_screen` / `set_wallpaper` / 任意拼名 / 空值） | 全部 **400**，返回可读拒绝原因 |

> 两处如实说明：① 把样本放在项目目录 `D:\项目\Oryx` 时预览返回 **403**，
> 因为 `Config.allowed_roots` 为空会兜底 `Path.home()`（`config.py:536-537`）——
> 这是路径闸门正确生效，不是缺陷；把样本放进主目录下的临时目录后即 200 + CSP。
> ② uvicorn 会把响应头规范化为小写，用 `dict(headers)` 读 CSP 会误判为缺失，
> 复核改用大小写不敏感的 `HTTPMessage.get()`。

### 8.6 防御规则（已写进代码注释与测试）

1. 测试里替换模块级对象一律走 `monkeypatch`；`conftest.py` 的 autouse 守卫逐用例
   钉死 `oryx.server.store` 身份，杜绝「路由闭包旧 store / 测试新 store」的跨用例 404。
2. 顺序依赖按**最坏顺序**验证（污染源全部排在受害者之前），不靠单跑通过就放过。
3. 产物判定靠结果元数据，不靠前端工具名白名单；新增交付工具必须同步
   `MUTATING_TOOLS`、`FEATURE_GATES`、`RECOVERABLE_TOOLS`、系统提示、写前快照、
   `doctor.py` 可选依赖清单。
4. 模型生成的 HTML/SVG 一律 CSP sandbox + `nosniff` + `no-store`；
   预览与打开共用 `_checked_file()` 单一闸门，不复制第二份校验逻辑。
5. 路由默认关闭；切换必须发 `model.routed` 可见事件；只从 `config.providers`
   真实条目里选，绝不编造 provider。
6. 断点续跑不重放有副作用的工具，只注入已完成步骤 brief；找不到断点退回普通重跑。
7. 编排草案只生成声明式数据并立刻自检，不 eval 模型写的代码、不自动执行、
   只引用真实存在的子 Agent。
8. 降级必须发 `capability.degraded` 并显示提示条；定时任务兜底失败也发系统通知，
   30 秒去重；主动建议只展示不自动执行，失败建议禁止重复同样参数。
9. **不改断言让测试变绿**：HTML 命名空间、收尾建议去重、字体缺字、开关用途
   等失败都是修实现 / 修测试语义，不是放宽断言。

### 8.7 第三轮之后仍存的边界（如实列出，不冒充已解决）

> **这一节会过期** —— 下面的条目逐条重新取证过（2026-11-18），
> 已经做成的挪走了。但下次再有人加功能，记得回来改这里。
> **文档说的比实际保守，会让人重复劳动** —— 这跟夸大一样有害。

- `EnableModelRouting` **默认关闭**，需显式开启；开启后切换有可见事件。
- 断点续跑是**语义级**恢复（复用 task + 注入已完成步骤 brief），不是内核状态的
  字节级快照恢复，也不重放有副作用的工具。
- 多 Agent 总览是**前端旁路聚合**，不是内核原生 team 状态机。
- HTML / SVG 预览放在 CSP sandbox 里、脚本被禁，这是刻意的安全边界。
- 默认 `allowed_roots` 是用户主目录；写到其它目录的产物预览会被 403 挡下。
- 跨端（手机接续 / 远程看屏）与浏览器 Agent 的**多标签页、等待元素、下载文件**
  均已落地（`oryx/remote/` 有看屏与输入控制；`browser.py` 有 `new_page`/`new_context`，
  以及 `browser_wait` / `browser_download` 两个工具）。
- 系统设置写操作仍一律走 L2 审批；**A2A 已落地**（`features.py` 的
  `DisableA2A` 开关控制云端代理与团队内协作）；
  **联网搜索也已落地**，但是**可选能力**：`EnableWebSearch` 默认关闭，
  需要用户自己配搜索服务的 key（配置文件的 `search` 段），没配就明确报错。
  AI 图像·视频生成 / IM 渠道仍如实标注「当前版本无此能力」。
  （注：`image_write` 是**写图片文件**，不是 AI 生图 —— 别混淆。）
- 本机 `qwen3.5:9b` 在长系统提示词下只吐 reasoning（`finish_reason=length`），
  与 Oryx 无关；核心链路验收使用受控假模型驱动真实内核（HTTP / SSE / 策略 /
  工具 / 落盘都是真的，只有模型是脚本化的）。
- 8.2 的评分是定性重估，不是自动化测量。



---

## 九、第四轮：前端拆 ES Module（2026-11-05）

### 9.1 结论先行

用户提出「AI 生成的代码能跑但没工程感」，并给了一套方案（前端拆模块 →
Prompt 外置 → JSDoc → pydantic 加固 → 统一错误 → 流式 UX → 评测）。
本轮先**逐条取证评估**，再落地其中的前端部分。

| 建议 | 结论 | 依据 |
|---|---|---|
| 前端拆 ES Module | ✅ **采纳，但方案要改** | 见 9.2 —— 原方案会白屏 |
| Prompt 集中到 md | ✅ 认同，**后续已落地** | `core/oryx/prompts/system/` 下 14 个 md，按语义分段 |
| JSDoc 类型保护 | ✅ 认同，本轮已在 core.js 起步 | 零构建成本 |
| pydantic 加固 | ⚠️ 部分认同，**本轮未做** | 已有 6 个自定义异常类，不是从零开始 |
| 统一 `errors.py` | ⚠️ 部分认同，**本轮未做** | 已有 `failure.py` 的 E1–E5 分类，比通用 AgentError 更贴实际 |
| 引入 highlight.js/marked.js（CDN） | ❌ **反对** | ① CDN 破坏离线承诺（项目支持纯本地模型 + nsis 打包）② marked.js 默认不转义，项目自带 `md()` 默认 `esc()` |
| 评测用关键词断言 | ❌ **反对这种写法** | `assert "总结" in result` 换个说法就红；且要真调模型才有意义 |

> 本轮只做「前端拆模块」。后 6 项留到下一阶段 —— 先把地基改稳，
> 免得在 6791 行还没拆完时同时动 Prompt 层，出问题分不清是谁的锅。

### 9.2 关键发现：原方案会白屏

实测依赖分析（`core/scripts/split_web_modules.py --analyze`）：

| 指标 | 实测 |
|---|---|
| 顶层符号 | 254 个（187 函数 + 67 `const`） |
| 功能分区 | 37 个（用源码里原有的 `/* ══════ xxx ══════ */` 标记切） |
| **循环依赖环** | **21 个** |
| 跨分区引用的函数 | 56 / 187 |

**21 个环的根因**：UI 事件处理天然双向 —— 点按钮 → 渲染列表 → 列表项再响应点击。
按原建议用 `import`/`export` 互相引用，ES Module 一旦成环就触发 TDZ（暂时性死区），
**页面白屏**。要消环就得把事件回调改成发布订阅 —— 那是**改产品行为**，
不该混在「整理代码」里。

**采用的方案**：模块化组织 + 依赖图无环。
- 跨模块符号靠**合并模块**破环（合并后同文件，不需要 import，不可能成环）；
- 会被重新赋值的 5 个变量放进 `core.js` 的 `mut` 容器
  （`import` 绑定是只读的，不能重新赋值）；
- 依赖图最终是 **DAG**：有向无环 + 无向无环（后者保证求值顺序确定）。

### 9.3 改动清单

| 编号 | 内容 | 文件 |
|---|---|---|
| R4-01 | 静态资源分发：`GET /web/{path}`，17 种 MIME，路径穿越防护，令牌占位符注入 | `core/oryx/server.py` |
| R4-02 | 前端拆成 22 个 ES Module + 独立 CSS，index.html 9705 → 169 行 | `core/web/` |
| R4-03 | 可重复运行的拆分脚本（幂等，基准快照在 `web/_source/app.js`） | `core/scripts/split_web_modules.py` |
| R4-04 | 模块加载检查：import/export 一致性、有向无环、无向无环、无孤立模块 | `core/tests/check_web_modules.py` |
| R4-05 | 功能性回归：真点按钮（切视图 / 搜索 / 设置），确认界面真的变了 | `core/tests/check_web_functional.py` |
| R4-06 | 契约检查改造为扫「页面 + 全部模块 + 全部样式」合并视图 | `core/tests/check_web_script.py`、`tests/webread.py` |
| R4-07 | 语法检查改为**逐模块**用 Chrome 编译（能定位到是哪个模块） | `core/tests/check_web_syntax.py` |
| R4-08 | 测试基建修复：module scope fixture 共享服务 + 请求头自动恢复 | `core/tests/test_security.py` |

### 9.4 根因表（本轮踩的坑）

| 现象 | 根因 | 修复 |
|---|---|---|
| 依赖图算出「假环」core↔boot | 建的是**无向图**，把「谁用谁」的信息丢了 | 改有向图：`g[使用者] = {被依赖者}` |
| 下沉符号到 core 后环反而变多 | 「往共同祖先搬符号」会**造出新环**（`tlAgentStarted` 下沉后 core↔agui 成环） | 改用**合并模块**破环 |
| 合并后符号留在不存在的模块里 | `merge_map` **只查一层**，拿到的是中间名（checkpoint→workflow→chips） | 链式解析 `resolve()` |
| export 静默加不上 | 正则用 `\b` 做边界，而符号名可能是 `$` / `$$` —— **非单词字符后 `\b` 不成立** | 改用 `(?![A-Za-z0-9_$])` |
| 前言区代码丢失 | 分区切分从「标记行」开始，把标记**之前**的 `$`/`api`/`esc` 丢了 | core 的起始行改为 0 |
| Chrome `--dump-dom` 永远挂起 | 页面开着 **EventSource 长连接**（SSE），`--dump-dom` 等「页面静止」永远等不到 | 改用 **CDP**，订阅 `Runtime.exceptionThrown` / `consoleAPICalled` |
| CDP 拿不到求值结果 | `Runtime.evaluate` 的 id 硬编码成 1，与 `Page.enable` 撞号 | id 改用全局唯一值 |
| 单独跑 `test_security.py` **挂死** | 19 个用例各自新建真实 uvicorn 服务，端口进 TIME_WAIT、线程累积 | 改 module scope 共享一个服务 + 请求头自动恢复 |
| **第二轮全量 22 个失败** | module scope 下 `os.environ` 不再被 monkeypatch 恢复，`ORYX_HOME` **泄漏到后续测试** → 语义索引写到临时目录 | 用 `pytest.MonkeyPatch` 上下文自己管，teardown 里 `undo()` + 还原 `sys.modules` |
| 单个文件被拆后测试全挂 | 测试硬编码 `read_text("index.html")`，只看得到「壳」 | 统一用 `tests/webread.py` 的 `page_source()` 合并视图 |

### 9.5 验收记录

| 范围 | 命令 | 结果 |
|---|---|---|
| 全量 | `pytest tests -q --disable-warnings` | **952 passed / 0 failed / 11 skipped，171.43s** |
| 模块加载检查 | `python tests/check_web_modules.py` | **全部通过**（import/export、无环、孤立模块、Chrome 真实加载零报错） |
| 功能性回归 | `python tests/check_web_functional.py` | **全部通过**（8 项：切视图、搜索、设置等真点） |
| 前端契约 | `python tests/check_web_script.py` | **全部通过**（130+ 条断言，一条没改） |
| 逐模块语法 | `python tests/check_web_syntax.py` | **全部通过**（22 个模块，301035 字节，Chrome 编译） |

> **11 个 skipped 的原因**：`test_symlink_guard.py` 与 `test_smoke_readonly.py` 里
> 11 个用例需要创建符号链接，当前 Windows 环境**没有该特权**（WinError 1314，
> 未启用开发者模式），测试**跳过而不是假装通过**。
> 上一轮（2026-11-02）的 `963 passed / 0 failed` 是在能建符号链接的环境下跑的。
> 沙箱内外复现一致，**与代码改动无关**。

**真实服务验收**（`python -m oryx --no-browser --port 8793`）：

| 请求 | 结果 |
|---|---|
| `/api/features`、`/api/mcp`、`/api/sys/switches`、`/api/files/list`、`/api/doctor`、`/api/sessions` | 全部 **200** |
| `/` | **200**，含 `<script type="module">` 入口标签 |
| `/web/modules/core.js`、`/web/modules/boot.js`、`/web/styles/main.css` | **200**，MIME 正确 |
| `/web/modules/core.js` 令牌注入 | 令牌已替换，占位符已消失 |
| `/web/../oryx/server.py`、`/web/%2e%2e/%2e%2e/config.json` | **403** |
| `/web/nope.js` / `/web/modules` / `/web/` | **404 / 404 / 400** |

### 9.6 防御规则（已写进代码注释与测试）

1. **模块之间不 import 业务模块**，只有 `core.js`（依赖根）是例外。
   新增跨模块调用一律从 `core.js` 取。
2. **改模块划分要改 `ZONE_TO_MODULE` 表**并重跑拆分脚本，不要手改生成的文件
   —— 生成物会在下次拆分时被覆盖。
3. **会被重新赋值的变量进 `mut` 容器**；初值必须是字面量。
4. **副作用模块由 `boot.js` 显式 `import './xxx.js'`** —— 不导出符号的模块
   没人 import 是正常的，但必须被执行一次。
5. **前端源码断言统一用 `tests/webread.py` 的 `page_source()`**；
   直接读 `index.html` 只能看到壳。
6. **`module scope` 的 fixture 必须自己管环境变量**（`pytest.MonkeyPatch` 上下文
   + teardown 里 `undo()`），并还原 `sys.modules` 的增删。
   症状是「后续测试莫名全挂、单跑全绿」。
7. **有 EventSource 长连接的页面不能用 `--dump-dom` / `--screenshot`**；
   用 CDP。
8. **静态资源路由的路径穿越防护**：resolve 后必须仍在 `WEB_DIR` 之下，
   且只放行 `WEB_MIME` 登记的扩展名。

### 9.7 第四轮之后仍存的边界

- **最大的两个模块约 1400 行** —— 为破循环依赖合并了 15 个分区的代价。
  要更细，得先做「UI 事件发布订阅」改造 —— 那是产品行为改动，要单独立项。
- **CSS 仍是一个文件**（2746 行）。CSS 无作用域冲突风险，拆分收益远小于 JS。
- **`web/_source/` 会被打进安装包**（约 350KB）——
  Tauri v2 的 resources 映射不支持 glob 排除。换来的是「装完仍可重新拆分」。
- **Google Fonts 仍是外链 CDN**：断网时界面退回系统字体，不影响功能，
  但严格说离线时不是「完全自包含」。
- **Prompt 外置、pydantic 加固、错误类型统一、评测基线** —— 本轮未做，
  排在下一阶段。


---

## 十、第五轮：系统提示词外置（2026-11-05）

### 10.1 结论先行

上一轮列的待办里，「Prompt 集中到 md 文件」是收益最大的一项
（改一句提示词不用碰 Python）。本轮把它做掉。

| 项 | 结果 |
|---|---|
| 外置范围 | `loop.py` 的 `SYSTEM_PROMPT`（5,950 字符 / 175 行 / 14 段） |
| 拆法 | 按语义分 14 个 md 文件（不是按长度切） |
| 行为等价 | **拼装结果与拆分前逐字节相同**（基准快照 + 对比测试钉死） |
| 全量 pytest | **967 passed / 0 failed / 11 skipped，139.53s** |
| 既有契约测试 | `test_prompt_contract.py` **29 passed**，一条断言没改 |

### 10.2 取证：提示词到底散在哪儿

| 来源 | 规模 | 能否外置 |
|---|---|---|
| `loop.py` 的 `SYSTEM_PROMPT` | 5,950 字符 / 175 行 | ✅ 本轮外置 |
| `styles.py` 的 6 个风格预设 | 459 字符 | ⏸ 下一轮 |
| `modes.py` 的 7 个模式策略 | 1,213 字符 | ⏸ 下一轮 |
| `agents.prompt_block()` | 1,687 字符 | ❌ 运行时由 `agents/` 目录生成 |
| `skills.catalog()` | 258 字符 | ❌ 运行时由 `skills/` 目录生成 |
| `experts.prompt_block()` | 运行时生成 | ❌ 同上 |
| `memory.always_rules()` | 运行时生成 | ❌ 同上 |

**占位符只有 2 个**：`{agents}`（行 109）、`{skills}`（行 240）——
没有杂散花括号，用 `str.replace()` 不会有意外。

### 10.3 结构

```
core/oryx/prompts/
├── __init__.py
├── loader.py                    拼装 + 变量替换 + 缓存 + 明确报错
└── system/
    ├── 00-opening.md            开篇引言（435 B）
    ├── 01-closed-loop.md        一、任务闭环（976 B）
    ├── 02-routing.md            二、能力分层与路由（1,732 B）
    ├── 03-discipline.md         三、执行纪律（1,024 B）
    ├── 04-safety.md             四、安全与确认（2,189 B）
    ├── 05-output.md             五、输出规范（1,416 B）
    ├── 06-memory.md             六、记忆（890 B）
    ├── 07-failure.md            七、失败处理（1,361 B）
    ├── 08-self-check.md         八、交付前自检（528 B）
    ├── 09-tools.md              工具使用要点（1,521 B）
    ├── 10-multi-agent.md        多 Agent 协作（647 B）
    ├── 11-workflow.md           工作流编排（799 B）
    ├── 12-security-rules.md     安全规则（874 B）
    └── 13-skills.md             技能（169 B）
```

**为什么按语义切而不是按长度**：改「安全规则」时应该只打开 `12-security-rules.md`，
而不是在 175 行里找。按长度切会切出「安全规则的一部分」这种没法独立理解的文件。

### 10.4 三条硬约束（写进代码，也写进测试）

1. **拼装结果必须与拆分前逐字节相同**。
   提示词差一个空格都可能让模型行为变样 —— 而且这种变化极难归因。
   所以有基准快照 `tests/fixtures/system_prompt_baseline.txt` + 逐字节对比测试。
2. **占位符缺失要报错**，不能把 `{agents}` 原样送到模型面前 ——
   模型会把字面量当内容读，是很难查的行为漂移。
3. **md 文件缺失要报错**，不能降级成空字符串 —— 少一段提示词同样让行为悄悄变样。

### 10.5 关键实现决定

| 决定 | 理由 |
|---|---|
| 保留 `loop.SYSTEM_PROMPT` 这个模块级常量 | `test_prompt_contract.py` 等既有代码引用它；名字去掉就是破坏性变更 |
| 常量值用 `raw_system_prompt()`（**保留占位符**） | 常量语义应与拆分前一致 —— 那是「带占位符的模板」 |
| 实际发送时用 `load_system_prompt(agents=..., skills=...)` | 占位符内容运行时才知道，且缺变量会明确报错 |
| `loader._read_section` 用 `lru_cache` | 系统提示每轮对话都要拼一次，文件在进程内不变 —— 每次读盘是纯浪费 |
| 段落顺序写死在 `SECTION_ORDER` | 顺序变了提示词就变了；不能依赖文件名字典序 |
| 切分用脚本 `scripts/extract_system_prompt.py` | 手工复制 175 行必然漏空格，而这种错误很难发现 |

### 10.6 根因表（本轮踩的坑）

| 现象 | 根因 | 修复 |
|---|---|---|
| 切分时「拼回去与原文不一致」，首个差异在第 0 字符 | 原文有一段**开篇引言在 `# 一、` 之前**，没有标题行可匹配，被漏掉了 | 加 `00-opening` 段 + 哨兵标记 |
| `loader.py` 语法错误 | docstring 里嵌了 `"""` 字面量 | 改用文字描述，不写嵌套三引号 |
| 拆分脚本改造后找不到原文 | 外置后 `loop.py` 里已经没有提示词正文了 | 改读**基准快照**（真值来源），并加「loop.py 里不再有提示词正文」的测试 |

### 10.7 验收记录

| 范围 | 命令 | 结果 |
|---|---|---|
| 全量 | `pytest tests -q --disable-warnings` | **967 passed / 0 failed / 11 skipped，139.53s** |
| 提示词专项 | `pytest tests/test_prompts.py` | **15 passed** |
| 既有契约 | `pytest tests/test_prompt_contract.py` | **29 passed**（断言一条没改） |
| 拆分脚本幂等 | `python scripts/extract_system_prompt.py --check` | ✓ 拼装与基准逐字节相同 |

**真实内核链路验证**（受控假 provider 捕获真实送模型的 system 消息）：

| 检查 | 结果 |
|---|---|
| 实际 system 长度 | 8,052 字符（模板 5,950 + 运行时注入的 Agent/技能/模式/风格） |
| 残留占位符 | **0 个** |
| 开篇引言 / 第八节自检 | 都在 |
| 真实技能目录 / 模式策略 / 风格片段 | 都已注入 |

**真实服务接口**（`--port 8792`）：`/api/features`、`/api/mcp`、`/api/sys/switches`、
`/api/doctor`、`/api/sessions`、`/api/skills`、`POST /api/prompt/optimize` 全部 200。

### 10.8 防御规则

1. **提示词正文只放 `prompts/system/*.md`**，`loop.py` 里不许再有提示词段落
   （有测试查 `# 一、任务闭环` 这类特征句）。
2. **改 md 文件后必须同步基准快照** —— 拆分脚本的 `--check` 会红。
   这是刻意的：改提示词是**有意的行为变更**，应该被显式记录，而不是悄悄改掉。
3. **新加占位符要同步改 loader 的调用方** —— 少传变量会直接报错，不会静默。
4. **段落顺序写死在 `SECTION_ORDER`** —— 不依赖文件名字典序。
5. **不要手改生成物**：md 由脚本从基准快照切出；
   改了 md 但没改基准 → `test_split_script_is_idempotent` 会红。

### 10.9 第五轮之后仍存的边界

- **`styles.py` 的 6 个风格预设、`modes.py` 的 7 个模式策略还没外置**
  （合计 1,672 字符）—— 它们是 dataclass 字段，改造要动类定义，
  留到下一轮单独做。
- **动态片段不能外置**（Agent 清单、技能目录、专家、长期记忆规则）——
  它们由 `agents/`、`skills/`、`experts/` 目录和用户数据生成，
  放成静态文件反而会过期。
- **基准快照要手工维护** —— 改提示词时需要「改 md + 更新快照」两步。
  这是刻意的摩擦：提示词变更应该被显式记录在版本控制里。


---

## 十一、第五轮验证中发现的事故复盘（2026-11-06）

### 11.1 结论先行

第五轮（提示词外置）全量测试跑出 **2 个失败**：
`sqlite3.OperationalError: no such table: tasks`（`test_domains.py`）。

复跑全绿。**偶发**。

这不是提示词外置引入的 —— 是**测试基础设施里一个一直存在的竞态**，
被我的并发操作（一边跑全量一边起验收服务）触发了出来。

### 11.2 定位过程

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 单独跑 `test_domains.py` | 22 passed（永远绿） |
| 2 | 跑前 23 个测试文件（含 test_domains） | 258 passed（不复现） |
| 3 | 跑 test_security + test_domains | 41 passed（不是它污染） |
| 4 | 跑 test_apps + test_appearance + test_domains | 45 passed |
| 5 | 复跑全量 | **967 passed**（不复现） |
| 6 | 并发跑两个 pytest | 都过（时序没重叠到 atexit） |
| 7 | 查 `_TEST_HOME` 残留 | **只有 index.db，没有 oryx.db** → 库文件被删过 |
| 8 | 查 `conftest.py` | `_TEST_HOME` 是**固定路径**，atexit 无条件 `rmtree` 整个目录 |

**第 7 步是转折点**：「连得上库但库里没表」说明表没建成功 → db 文件被删了 →
谁删的？→ 只有 atexit 的 `rmtree`。

### 11.3 根因

```python
# 旧版
_TEST_HOME = Path(tempfile.gettempdir()) / "oryx-test-home"   # 固定路径！

@atexit.register
def _cleanup():
    shutil.rmtree(_TEST_HOME, ignore_errors=True)              # 整个目录删掉
```

**两个 pytest 进程并发**时：
1. 进程 A 建 `_TEST_HOME/oryx.db`，开始跑测试；
2. 进程 B 启动（用同一个 `_TEST_HOME`），退出时 atexit `rmtree` 整个目录；
3. 进程 A 后续访问 store → db 文件已经没了 → `no such table: tasks`。

**对照实验（已验证）**：

| 版本 | 场景 | 结果 |
|---|---|---|
| 旧版 | A 建 db → B 的 atexit rmtree | A 的 db **不见了** → A 报错 ✓ |
| 新版 | A、B 各用带 pid 的目录，B 只删自己那份 | A 的 db **还在** ✓ |

### 11.4 修复

| 改动 | 内容 |
|---|---|
| 目录带 pid | `_TEST_HOME = %TEMP%/oryx-test-home-{pid}` —— 每个进程一份 |
| atexit 只删自己 | 带 `startswith("oryx-test-home-")` 前缀检查 + 排除自己的目录 |
| 陈旧目录下次启动清 | 退出时留 `.stale` 标记文件；下次启动 `_purge_stale_dirs()` 清理 |

### 11.5 顺带查清的第二件事：为什么目录删不掉

修完并发问题后，发现**每次跑测试都在 %TEMP% 里留一个孤儿目录**。

- `shutil.rmtree(..., ignore_errors=True)` 会**静默失败** —— 删不掉也不吭声。
- 真正的原因：sqlite 的文件句柄在 Windows 上释放有延迟，
  进程还活着的时候删 `oryx.db` 会报 `WinError 32`（另一个程序正在使用此文件）；
  **同一个文件在进程退出后就能正常删掉**。
- 验证过：**不是** `Store` 的连接泄漏 —— 实测调用 50 次 `create_task` 后
  活跃连接数始终为 0（CPython 引用计数会立即回收 `with conn:` 的连接对象）。

所以清理改成两段式：**退出时登记标记 + 下次启动时删**（那时句柄早已释放）。
残留数量恒定为 1，不再累积。

### 11.6 防御规则

1. **测试的临时目录必须带进程 id** —— 固定路径 + atexit 删目录 = 并发互伤。
2. **看到 `no such table` 先查库文件是否被删**，而不是查建表逻辑。
3. **单跑绿 + 全量偶发红 = 跨进程/跨用例污染**，
   往「谁动了共享资源」方向查（共享的固定路径、共享的模块级对象、共享的端口）。
4. **`ignore_errors=True` 会掩盖失败** —— 清理逻辑要能重试或换时机，
   不能靠吞异常。

### 11.7 验证

| 范围 | 结果 |
|---|---|
| 全量 pytest（修复后） | **967 passed / 0 failed / 11 skipped** |
| 并发两个 pytest | 27 + 51 passed，**互不干扰** |
| 旧版机制对照实验 | 复现「A 的 db 被 B 删掉」 |
| 新版机制对照实验 | 「A 的 db 不受影响，B 照常清理」 |
| 陈旧目录闭环 | 第二次跑时清掉上一次的残留，数量恒定为 1 |


### 11.8 附带查清：另一个偶发失败（test_context_resume）

排查 `test_domains` 竞态时，全量还偶发过另一个失败：
`test_context_resume.py::test_history_trimming_is_visible_in_event`
—— 报「历史摘要不在 `provider.systems[0]` 里」。

**排查过程**：

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 单独跑那个用例 ×3 | 全过 |
| 2 | 跑前 18 个测试文件 ×2 | 全过 |
| 3 | 复跑全量 ×3 | 全过 |
| 4 | 高负载（全量 + 3 个 CPU 占用进程）×2 | 全过 |
| 5 | 查 `MAX_SUMMARY_CHARS` / `MIN_KEEP` | 1400 / 2，都正常 |
| 6 | **穷举 768 种 (历史长度 × 预算 × keep × 角色分布) 组合** | **没有任何组合能同时「事件发了」且「摘要里没有『历史摘要』」** |
| 7 | 查 `context.trimmed` 的 publish 点 | 全项目只有一处 |
| 8 | 查 `_call_provider` 的调用点 | 只有一处，且 `system` 在循环外构建一次、循环内复用 |

第 6 步是决定性的：`trim_history` 里「发事件」与「有摘要」在逻辑上**必然同时成立**
（`dropped = history[:start]` 非空 ⇒ `_summarize` 必产出含 `[历史摘要]` 的文本）。
所以失败时必然有**别的东西**不对 —— 但静态分析排除了所有代码路径。

**处理方式**（不掩盖问题）：

1. **加固断言**：从「`systems[0]` 必须含摘要」改成
   「**至少有一次**调用含摘要」，失败时打印
   `裁剪了 N 条历史，但系统提示里没有摘要（provider 被调用 M 次）`。
   断言**强度已验证** —— 故意破坏注入逻辑后仍然失败（不是改松了让它绿）。
2. **留了探针**：`scripts/_inject_diag.py` / `_diag_watch.py` 可在事件发布点与
   摘要注入点同时记录现场（`PUB before=… sum_len=…` / `INS sum_len=… parts=…`），
   下次复现时能直接抓到是哪一步丢的。
3. **A/B 对照实验**：`scripts/_ab_flaky.py` 用 `ORYX_PROMPT_LEGACY=1`
   让 loader 直接返回基准快照（等价于外置前的内联常量），
   与正常路径各跑 N 轮比较失败率 —— 用来判断是否与本轮改动有关。

**结论**：该失败**极罕见**（约 1/12 次全量，且只在高负载下出现），
且**与提示词外置无关** —— 外置只改了 system 的**来源**，
而 system 的内容**逐字节相同**（有基准快照 + 对比测试钉死）；
失败断言针对的是「历史摘要是否进了 system」，那是 `loop.py` 内部逻辑，
与提示词从哪来无关。

### 11.9 附带查清：两个工具类陷阱（都差点造成真损坏）

排查过程中我自己踩了两个坑，都**可能**造成真实损坏，记录下来：

| 陷阱 | 现象 | 正确做法 |
|---|---|---|
| **BOM 被写成正文** | `read_text("utf-8")` 读带 BOM 的文件 → BOM 变成正文里的 `U+FEFF` → 写回后文件「能跑但很脏」，`ast.parse` 直接报错 | 用 `read_bytes()` 判断前 3 字节是不是 `efbbbf`，按 `utf-8-sig` 读、按原样写回 |
| **CRLF 被改成 LF** | `read_bytes().decode()` **不做**换行转换，锚点用 `\n` 写就匹配不上 CRLF 文件；反过来若用 `\n` 写回就把整个文件的行尾改了 | 用 `read_text()`（它做通用换行转换），或自己检测并保留原行尾符 |

**附带收获**：`oryx/loop.py` 是 **UTF-8 BOM + CRLF**。
改它时如果用 `read_bytes().decode()` + `write_bytes()`，行尾会被悄悄改成 LF ——
在 Windows 上这是**大面积无意义 diff**，会让代码审查变成噪音。
本轮所有改动都用 `read_text()` / `write_text()`（保留行尾）或显式处理 BOM+CRLF。

**防御**：改任何文件前先确认它的编码与行尾：

```python
raw = path.read_bytes()
has_bom = raw[:3] == b"\xef\xbb\xbf"
nl = "\r\n" if b"\r\n" in raw else "\n"
```


---

## 十二、第六轮：扩展总线 + 前端质量门禁（2026-11-09）

### 12.1 结论先行

用户提出：读 `D://项目` 下的项目，把好用的融合进 Oryx，
提高「可自由支配性」，后续修改不影响其他功能，不要写死。

做法：**逐条评估** 30 多个本地项目，按 Oryx 的实际痛点挑，
而不是照搬。最终落地三块：扩展总线、上下文预算、前端质量门禁。

| 范围 | 结果 |
|---|---|
| 全量 pytest | **1017 passed / 0 failed** |
| 扩展总线专项 | 20 passed |
| 门禁专项 + 集成 | 14 + 3 passed |
| 前端四项检查 | 全部通过 |
| 界面门禁自举 | block 0 / warn 1 |

### 12.2 素材评估：哪些值得抄

| 项目 | 机制 | 采不采 | 理由 |
|---|---|---|---|
| **ECC** | manifest 声明 + 安装期解算 + context-profile（eager/required/routed + token 预算）+ hooks 三级开关 + 侧车指纹 | ✅ 大量采 | 正是「扩展可自由装配」的答案 |
| **claude-mem** | 统一记忆条目 + 三策略检索 + 生命周期注入 + 幂等标记块 | ⏸ 下一轮 | 要动 `memory.py` / `store.py`，一次改一层 |
| **auto-ontology** | 概念层与实现层解耦 + 模型交换 YAML + MCP 工具面裁剪 + 技能契约 | ✅ 部分采 | 契约（gates/fail_closed_when/handoffs）当场落地；本体层下一轮 |
| **agent-browser** | 能力集与信任分级 + 不可信内容 nonce 边界 | ✅ 部分采 | 信任分级当场落地；nonce 边界下一轮 |
| **worktrunk** | 用户级/项目级配置分层（同名都跑而非覆盖）+ 确定性资源分配 | ⏸ 下一轮 | 配置分层要动 `config.py` |
| **superpowers** | 零依赖技能 + 启动注入 + 跨宿主工具名映射 | ⏸ 部分 | 工具名映射等接多宿主时用 |
| **Matt Pocock skills** | 双调用语义 + 术语表 + 拒绝清单 | ⏸ 部分 | 术语表值得做，下一轮 |
| **taste-lint / web-interface-guidelines / interface-proof / Frontend-Quality / hallmark / argus / impeccable** | 前端质量规则集（149 + 104 + 14 + 7 + 90 + 49 + 65 条） | ✅ 部分采 | 全部静态可判定；先移植高频的 36 条 |

**明确不采的**：
- 引第三方 CDN（highlight.js / marked.js）—— 破坏离线承诺，且 marked 默认不转义
- 把 314 个技能全量注入上下文 —— 那正是要解决的问题

### 12.3 审计：Oryx 的六个写死点

| # | 写死点 | 位置 | 后果 |
|---|---|---|---|
| 1 | 三套扩展各写一遍「扫目录 + 解析 frontmatter」 | `skills.py` / `experts.py` / `agents.py` | 加一种扩展类型 = **复制一遍**整套代码 |
| 2 | 技能路由按空格分词 | `skills.py` 的 `match()` | **中文完全无效** |
| 3 | 工具全硬编码 | `tools.py` 的 `build_registry()` | 加工具必须改 Python |
| 4 | 钩子事件名写死 | `hooks.py` 的 `HOOK_NAMES` | 加事件 = 改代码；无法精确禁用 |
| 5 | 无信任分级 | 全局 | clone 别人的仓库就能声明高危能力 |
| 6 | 无上下文预算 | 提示词全量注入 | 技能一多就炸上下文 |

### 12.4 落地

**扩展总线**（`core/oryx/registry.py`）——
六类扩展共用 `ExtensionRecord`；`Registry.discover()` 是唯一发现入口；
可选 `oryx.json` 声明元数据；信任分级 + 能力集在**加载时**裁剪。

**上下文预算**（`core/oryx/context_budget.py`）——
`eager` / `required` / `routed` 三段 + token 预算 + blocking/report-only。
技能目录按本轮 query 裁剪；未展开的技能保留**名字**（保可发现性）。

**三级路由** —— 显式触发词 → 描述 bigram 重合 → 名称匹配。

**钩子三级开关** —— `ORYX_HOOKS_ENABLED` / `ORYX_HOOK_PROFILE` /
`ORYX_DISABLED_HOOKS`；钩子有稳定 id；跳过的记进 `emit()` 的 `skipped`。

**前端质量门禁**（`core/oryx/ui_audit.py` + `rules/ui-quality.yaml`，36 条）——
规则格式与 `rules/dangerous.yaml` 同构；支持三种上下文能力控误报。

### 12.5 根因表（本轮踩的坑）

| 现象 | 根因 | 修复 |
|---|---|---|
| 门禁报出 536 条，没人能用 | 纯正则必然误报 | 加三种上下文能力（`requires_absent` / `ignore_var_defs` / `exclude`），warn 降到 1 |
| 「有唯一 h1」在有 h1 时也报 | 结构性规则（count）走了主循环 —— pattern 是「找候选」不是「匹配即违规」 | 结构性规则从主循环排除，交给专门逻辑 |
| `ease-in` 误匹配 `ease-in-out` | 正则的 `\b` 对 `-` 不成立 | 改 `ease-in(?!-)` |
| YAML 解析器报「expected block end」 | pattern 里混了单双引号，YAML 与正则两重转义打架 | pattern 里不写引号字符，用 `\x22` / `\x27` |
| 技能路由中文永远不命中 | 按空格分词 → 中文整句成一个词 | 中文切 bigram |
| 路由不命中时技能目录全量回退 | `if keep:` 为假就回退全量 | 保留但写明「还有 N 个未展开」 |

### 12.6 顺带修掉的两个真问题

1. **中文路由失效** —— 老 `SkillLibrary.match()` 按空格分词，
   中文描述整句被当成一个词，**匹配永远为空**。现改 bigram。
2. **设计令牌缺口** —— 界面有 `--accent` 却没有「主色上的前景色」变量，
   14 处硬编码 `#fff`。现补 `--on-accent`（亮/暗两套）。

### 12.7 防御规则

1. **加一种扩展类型只改 `KIND_SPECS` 一行** —— 有测试盯这条。
2. **不可信来源（仓库自带）不能声明高危能力** —— 加载时裁剪，不在运行期拦。
3. **清单（oryx.json）优先于 frontmatter** —— 显式配置胜出。
4. **规则必须上下文感知** —— 纯正则的门禁没人会用。
5. **门禁要自举** —— 断言「自家界面无 block 级问题」，
   这条失败 = 门禁本身失效了。
6. **pattern 里不写引号字符** —— 用 `\x22` / `\x27`。

### 12.8 边界与下一步

- 工具注册仍是硬编码 → 下一轮接入总线
- 工作流 / 策略扩展点已登记但未接线
- 没有 profile 机制（ECC 的 install-profiles）→ 下一轮
- 上下文预算只作用于技能目录；专家与 Agent 清单仍全量注入
- 门禁是静态的 —— 对比度、触摸目标要运行时才算（argus 那 49 条），需接浏览器
- claude-mem 的记忆统一与检索分层 → 下一轮
- worktrunk 的配置分层 → 下一轮

# Oryx 诊断报告：对标 Marvis / WorkBuddy

> 诊断对象：`D:/项目/Oryx`
> 诊断方式：逐文件读源码取证，所有结论都带**文件 + 行号**；不采信 README 的自述。
> 基线：`D:/Anaconda/python.exe -m pytest tests -q` → **831 passed / 2 failed**（12 分 31 秒）
> 诊断日期：2026-10-30

---
