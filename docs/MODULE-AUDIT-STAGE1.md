# 模块功能清单（阶段一 · 工作底稿）

> 用途：新前端（`apps/web`）对接后端的查漏补缺底稿。只做梳理与方案，不含重构代码。
> 依据：新前端全部源码（3663 行）、对应后端域代码、相关后端测试实测。
> 更新：2026-11-26（P0 / P1 / P2 清零；模块 9「系统页 + 设置剩余三项」、模块 10「全局接口层」、模块 11「并发运行停止隔离」、模块 12「账号与权限」、模块 13「剩余裸 dict 请求体全量迁移」落地，**P3 中不依赖方向决策的部分完成，HTTP 边界零裸 dict 入参**；后端全量测试首次跑通 1561 passed / 46 skipped / 0 failed）。
> 推进方式：指定模块后，按「功能确认 → 接口修复 → 前后端对齐 → 功能补全 → 自测交付」逐个推进。

图例：✅ 已实现且正常 ｜ ⚠️ 实现了但不完善 / 有 Bug ｜ ❌ 缺失

---

## 一、各模块功能清单

### 1. 对话运行（新建运行 + 会话详情）

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 发起运行、续聊、重新生成、重试失败、停止 | 可选子 Agent / 专家，配置快照固定到会话 |
| ✅ | 查看启动时的配置快照 | 会话页可展开 Agent / 专家快照 |
| ✅ | 待办勾选 | — |
| ✅ | 停止可打断审批等待 | 2026-11-26 修复，见第四节 |
| ✅ | 审批卡 | SSE `step.awaiting_approval` 到达即弹卡，展示工具 / 风险 / 人话步骤，支持批准、拒绝；页面刷新后由 `/api/sessions/{id}/approvals` 补回卡片 |
| ✅ | SSE 实时推送 | `/api/events?since=N` 断点续传；运行开始 / 结束、步骤、审批状态实时刷新会话与左栏；断线指数退避重连 |
| ✅ | 流式输出 + Markdown 渲染 | 2026-11-26 完成：消费 SSE `TEXT_MESSAGE_*` 逐块攒成气泡（40ms 合并渲染），`REASONING_*` 走折叠思考面板；助手消息与收尾草稿按零依赖安全 Markdown 渲染（标题 / 列表 / 代码块 / 表格 / 链接等）；落库后按内容认领草稿，不重复显示。见第四节 |
| ✅ | 模式 / 交互 / 风格 | 2026-11-26 完成：顶栏「设置」弹层切三项全局默认；新建运行可单独覆盖交互模式与回答风格（默认「跟随全局」），随 `/api/chat` 提交并固化进会话快照；顶栏与快照都显示中文名。计算模式只做全局切换（它是模型路由，单次覆盖不会换 provider）。见第四节 |
| ✅ | 步骤详情 | 2026-11-26 完成：可展开卡展示参数（JSON 还原）、结果、报错、状态中文、L0-L3 风险、耗时、失败分类 E0-E5、只读自证、产物路径；失败/拒绝自动展开，长文本截断可展开全文，分区复制，空状态引导。SSE `step.*` 事件实时刷新并补增强字段；后端把 `error_class` / `artifact_path` / `mutated` 一并落库，刷新前后一致。见第四节 |
| ✅ | 检查点回退 | 2026-11-26 完成：每条用户提问下「回退到这里」+ 完整检查点清单；三种范围（只还原文件 / 只删对话 / 两者都退）；运行中禁用；结果按「还原 / 删除 / 跳过 / 失败 / 消息 / 执行记录」如实汇总。见第四节 |
| ✅ | 多个运行共用一个中止信号 | 2026-11-26 完成（模块 11）：中止信号改成**每个运行实例一份**，一次停止只停自己那一轮；审批等待也只唤醒匹配的那个。嵌套 / 并发起跑互不波及。见第四节 |

### 2. 会话列表与详情

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 列表、按时间分组、搜索 | — |
| ✅ | 改名、删除、新建会话 | 2026-11-26 完成：左栏「+ 新会话」、行内改名（留空恢复默认标题）、删除二次确认；运行中禁删。见第四节 |
| ✅ | 分页与搜索 | 2026-11-26 完成：`offset` 翻页（30 条/页 + 加载更多，跨页按 id 去重）；`q` 走 SQL 过滤**全部**会话，`%` / `_` 是字面量不当通配。见第四节 |
| ✅ | 单滚动条 | 2026-11-26 完成：会话页外层改 `overflow-hidden`，由 SessionDetail 独占滚动与内边距 |
| ✅ | 待办 | 2026-11-26 完成：空列表也给「加一条待办」入口；勾选失败回滚并给出中文错误；标题 200 字校验；头部显示完成进度 |

### 3. 账号与权限

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 注册、登录、退出、会话恢复 | — |
| ✅ | 登录报错 | 2026-11-26（模块 12）：登录与注册拆成两套请求模型，短密码 / 账号不存在 / 账号名格式不对一律 401「账号或密码不正确」，不再用 422 泄露注册规则 |
| ✅ | 会话过期 | 2026-11-26（模块 10）：全局 401 统一回登录页，不再各面板各自报错 |
| ✅ | 改密码 | 2026-11-26（模块 12）：`POST /api/auth/password` + 设置弹层「账号」页；改后按 user_id 注销其它会话，当前设备保持登录 |
| ❌ | 角色体系 | 2026-11-26 仍**未做**：要动所有域的 owner 校验与数据库结构，属架构级，单独一轮。界面如实写明「本机没有角色体系」，不摆假下拉 |

### 4. Agent 与专家管理

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 两类定义的增删改查、共享只读态 | — |
| ✅ | 工具 / 技能 / 模型服务候选化 | 2026-11-26 完成：新增 `GET /api/agents/catalog` 聚合三份权威清单（各自降级，内核没起来不 503）；表单改成可搜索、按组折叠的多选器与下拉，枚举由后端下发；**已保存但目录里找不到的值标红保留**，不会一打开就被清空。见第四节 |
| ✅ | 写盘前字段校验 | 2026-11-26 完成：`tools` / `disallowed_tools` / `skills` / `model` 四类都在写盘前校验（原来只有 `tools`）；内核拿不到清单只跳过那一项，模型服务来自配置永远校验；顺带修掉「`tools` 为空也调 IPC，内核没起来连空定义都建不了」的 503。见第四节 |
| ✅ | 内置定义只读说明 | 2026-11-26 完成：按 `creator_id` 是否为空区分「内置定义，没有创建者记录」与「由其他创建者维护」；403 文案同步改准确，不再一律说「别人创建的」 |

### 5. 团队与自动任务

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 团队增删改、成员、信箱投递与派发 | — |
| ✅ | 自动任务增删改、启停、运行历史、手动重试 | — |
| ✅ | 团队整体保存 | 2026-11-26 完成：`PUT /api/teams/{name}` 带 `members` 走一次 `team.save` IPC，内核**先全部校验再一次落地**，校验失败内存与 SQLite 都不动；单成员换人合法，信箱按 team_id 保留。前端改成一次提交，失败整单不动。见第四节 |
| ✅ | 派发异步化 | 2026-11-26 完成：新增 `POST/GET /api/teams/{name}/dispatch/jobs`，提交即回 `job_id`，后台线程跑 IPC，前端 1.2s 轮询到 `done/failed`；在途重复提交复用同一作业。老的同步 `/dispatch` 保留给老前端。见第四节 |
| ✅ | 状态码与防重复 | 2026-11-26 完成：内核 `ok=False` 带 `reason` 精确映射 404/403/409/400（老内核按文案兜底），「至少要留一个成员」回到 400；启停 / 删除 / 重试 / 派发按动作 + id 上锁，在途禁用。见第四节 |

### 6. 知识库

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 集合增删改、多文件上传、版本、重建、检索、解析文本分页、来源下载 | — |
| ✅ | 检索范围 | 2026-11-26 完成：`POST /api/knowledge/search` 支持 `collection_id`，后端**先校验可见性再过滤**（别人的私有集合返回 404）；`%` / `_` 按字面量（`instr` 替代 `LIKE`）。界面加集合下拉并回显范围。见第四节 |
| ✅ | 解析进度 | 2026-11-26 完成：解析完成 / 失败推 SSE `knowledge.document`（按 `_owner_id` 下发），界面收到即刷新；兜底轮询改退避 1.5s→8s、页面隐藏暂停、3 分钟收口并提示手动刷新 |
| ✅ | 清空索引 | 2026-11-26 完成：本地目录索引接 `POST /api/index/clear`（二次确认、扫描中禁用、409 / 503 中文提示）；知识集合另加「清空文档」保留集合本身（`POST /api/knowledge/collections/{id}/clear`）。见第四节 |

### 7. 总览

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 编队、最近运行、下一步建议 | 三个接口通 |
| ✅ | 自动刷新 | 2026-11-26 完成：订阅 SSE，`RUN_*` / `step.*` / `team.*` / `knowledge.document` / `resync` 触发 400ms 节流重拉；另有手动刷新与「上次更新 x 前」 |
| ✅ | 建议的「执行」 | 2026-11-26 完成：**原来只有 `action` 型显示按钮且没绑事件，而绝大多数建议是 `prompt` 型** —— 现在两类都有按钮：preview 开 `/api/files/preview`，prompt 带到「新建运行」预填（不自动提交）。见第四节 |

### 8. 日志监控与设置

| 状态 | 功能点 | 说明与完善建议 |
|---|---|---|
| ✅ | 「设置」按钮与偏好切换 | 2026-11-26 已接：设置弹层可切交互模式 / 回答风格 / 计算模式，展示当前模型服务（只读）、风险徽标、加载 / 错误 / 空状态与重试 |
| ✅ | 模型服务配置 | 2026-11-26 完成：列表（云端 / 本地分组、能力徽标、密钥状态）、新增、编辑、激活、删除、本机 Ollama 模型扫描与登记。同名不覆盖、编辑留空密钥不改、激活与计算模式的关系如实提示。见第四节 |
| ✅ | 系统页：用量 / 自检 / 审计导出 | 2026-11-26 完成（模块 9）：顶栏「系统」；用量按滚动窗口如实标注、环境自检、三种导出由前端本地生成。**请求日志不进界面** —— 后端只有一行 Python logging，无持久化与查询接口。见第四节 |
| ✅ | 外观 / 特性开关 / 长期记忆 | 2026-11-26 完成（模块 9）：设置弹层补三个标签页；外观会真的应用到 DOM（原来存了也不生效）；开关自锁与占位开关如实禁用。见第四节 |
| ⚠️ | 终端 / 技能 / MCP 仍未接 | 这三项与「新前端是否取代老前端」的决策绑定（第三节第 1 条），方向未定前不迁移 |
| ⚠️ | 新旧前端差距 | 老前端调约 95 个接口，新前端约 25 个；有账号后 `/` 直接跳 `/newui`（`server.py:392`），老界面入口已不可达 |

### 9. 接口层（全局）

| 状态 | 问题 | 说明与完善建议 |
|---|---|---|
| ✅ | 统一响应的边界 | 2026-11-26（模块 10）：**不逐域包壳** —— 老前端直接读裸对象，包了会全读空；`test_error_contract.py` 也钉住这条。新前端两种形状都吃，边界在 `apiContracts.ts:unwrap()` + `BARE_ENDPOINTS` 护栏。见第四节 |
| ✅ | 错误码与请求体 | 2026-11-26（模块 10）：四套 IPC 中文子串映射收敛成 `_common.map_ipc_error()` 一套；5 条高频路径换 Pydantic，脏 body 挡在 422。**模块 13 已把剩余 9 个 HTTP 端点全量迁完，域层零裸 dict 参数注解**；静态守门见 `test_req_dict_migration.py` |
| ✅ | 前端请求层 | 2026-11-26（模块 10）：GET 15s / 写 30s / 上传 120s 超时、全局 401 回登录页（不误判 `/api/auth/me` 探测）、错误体 `message/detail/error` 归一化、`X-Request-Id` 透传；`error` 改成顶部横幅，不再顶掉整个主区 |

---

## 二、推进顺序建议

| 优先级 | 模块 | 理由 |
|---|---|---|
| P0 | 对话运行 | 全部完成（SSE、审批卡、流式与 Markdown、步骤详情、模式 / 交互 / 风格、检查点回退） |
| P0 | 设置最小集 | 全部完成（模式 / 交互 / 风格切换 + 模型服务配置） |
| P1 | 会话列表与详情 | **全部完成（2026-11-26）**：改名、删除、新建、分页与服务端搜索、待办、双滚动条 |
| P1 | Agent 管理 | **全部完成（2026-11-26）**：候选目录与多选器、四类字段写盘前校验、内置定义只读文案、403 区分 |
| P1 | 团队与自动任务 | **全部完成（2026-11-26）**：整体保存、后台派发 + 状态轮询、精确状态码、防重复点击 |
| P2 | 知识库、总览 | **全部完成（2026-11-26）**：按集合检索、解析进度 SSE + 退避兜底、清空索引、总览自动刷新、建议可执行 |
| P3 | 日志监控、其余域迁移 | **不依赖方向决策的部分全部完成（2026-11-26，模块 9 + 10 + 12 + 13）**：用量 / 自检 / 审计导出、外观 / 特性开关 / 记忆、全局接口层（错误语义 / 请求体 / 请求层）、账号与权限、剩余裸 dict 请求体全量迁移均落地；**剩余只剩方向决策项**：终端 / 技能 / MCP（待「新旧前端是否取代」拍板）、角色体系 |

---

## 三、待确认问题

1. **新前端是否完全取代老前端？** 老前端独有的工作流、技能、MCP、扩展、终端、应用库、跨端远控，是全迁还是砍一部分？
2. ~~**审批怎么呈现？**~~ **已定（2026-11-26）**：SSE 实时弹卡 + `/api/sessions/{id}/approvals` 兜底，已实现。
3. ~~**模式、交互、风格在哪选？**~~ **已定（2026-11-26）**：两处都要 —— 顶栏「设置」切三项全局默认；新建运行可单独覆盖交互模式与回答风格。计算模式（效率 / 本地）只在全局切，因为它是模型路由：单次传 `mode` 只会被记进会话快照、不会换 provider，做成可选会变成假功能。若你希望单次运行也能真正换模型，需要内核支持 per-run provider，属后续架构改动。
4. **内置定义谁都不能改，是否合理？** 要不要加「复制一份再改」或管理员角色？
   本轮**未改权限模型**，只把 403 与界面文案改准确（如实说「内置定义，没有创建者记录」，
   并指路到现有的「新建子 Agent」）；复制功能 / 管理员角色仍待定。
5. ~~**派发子 Agent 的预期**：等结果返回，还是后台执行加通知？~~
   **已定（2026-11-26）**：新前端走后台执行 + 作业状态轮询 + 完成通知（结论同时
   回投到持久化信箱）；老的同步 `/dispatch` 保留给 `core/web/modules/models.js`，
   不动它的语义。见第四节模块 7 记录。
6. **知识集合要不要能挂到对话上？** 模块 8 已实现按集合检索，但检索与对话仍是
   两条链路；是否要把「选哪个集合」挂到对话输入上（让 Agent 只用它检索）仍待定。
7. **自动任务**：时区固定北京时间、失败重试固定 3 次，要不要做成可配？
8. ~~**「日志监控」的范围**：审计导出、用量汇总、自检、请求日志，哪些要进新界面？~~
   **已定（2026-11-26，模块 9 默认范围）**：前三项进（`/api/usage` / `/api/doctor` /
   `/api/audit/{id}`，导出文件由前端本地生成）；**请求日志不进** —— 后端只有
   `middleware/errors.py` 一行 Python logging，没有持久化与查询接口，做它要另起
   采集与存储，属新功能而非接线。若以后要开放，再单独确认范围。
9. ~~**账号**：是否需要改密码？~~ **已定（2026-11-26，模块 12）**：做，且一并收敛登录报错与改密后的会话失效；**角色体系仍暂缓**（架构级，与第 4 条的「管理员角色」一起等拍板）。
10. ~~**并发运行的停止隔离**~~ **已定（2026-11-26，模块 11）**：改成「每个运行一份」的中止信号，一次停止只停自己那一轮。取证确认这是**真缺陷**（实测复现：停止 A 会把 B 一起停掉），已修并补回归。
11. **模型服务「激活」要不要连带切计算模式**（新增，2026-11-26）：当前保持「只记为该类默认，不切模式」，界面已如实提示。**用户决定后面再定，本项暂缓**，不改动现有行为。
12. ~~**裸对象接口要不要逐域套 v2 统一响应壳**~~ **已定（2026-11-26，模块 10）：不做**。老前端直接读裸对象、`test_error_contract.py` 也钉住「成功不包壳」；「统一」改落在错误语义、请求体校验与前端请求层。若老前端彻底退役再按 `BARE_ENDPOINTS` 逐域改造，属新决策。

---

## 四、处理记录

### 2026-11-26 修复：审批等待打不断 + 线上包落后于源码

**现象**：`tests/test_cancel.py::test_approval_wait_interrupted_by_cancel` 稳定失败，报「审批等待没被停止打断」。

**根因**：`cancel()` 被改成「只对匹配的任务与账号置位」，没有运行在跑时直接返回失败、不再碰中止标志；而审批等待只靠这个标志醒来。

**改动**（`core/oryx/loop.py`）：

- `wait_approval` 记录每个审批等待隶属于哪个任务、哪个账号（`_approval_context`），调用点传入当前任务号。
- `cancel()` 把匹配的待审批计入在途：任务号、账号都对得上才唤醒。
- 中止代号只在真有运行在跑时才递增，避免「下一条消息被秒停」。
- `_active_owner_id` 补上构造初值（此前只在 `run()` 里赋值）。

**测试**（`core/tests/test_cancel.py`）：新增 `test_cancel_only_wakes_matching_approval`，验证账号或任务对不上的停止请求不生效、不碰中止标志、不动代号。

**验证情况**：

- 定向通过：`test_cancel.py`、`test_approval.py`、`test_api_closed_loop.py`、`test_error_contract.py`、`test_interface_spec_v2.py`、`test_retry.py` 共 49 项全过，其中原先失败的 `test_approval_wait_interrupted_by_cancel` 已转绿。
- 完整测试集**未能验证**：从智能体会话里跑全量是 1186 过 / 47 跳过 / 92 失败 / 108 报错。原因是会话环境注入的「安全删除护栏」在单轮累计删除满 50 个文件后，对后续删文件动作直接 `SystemExit(1)`（日志里 68 处 `SystemExit: 1`、89 处 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`），而全量测试要反复清临时目录。同一条用例单独跑通过、全量里被杀。
- 需要在自己的终端里跑一次确认（终端里没有这个护栏）：

  ```
  cd D:\项目\Oryx\core
  D:\Python\Python311\python.exe tests\run_pytest.py
  ```

**线上包**：后端托管的新前端包（`core/web/newui/`）比源码旧 —— 线上停止按钮发的是空参数，走「最近一次请求」的旧路径；已重新构建并同步为带 `task_id` 的当前版本。目录里还有历次构建留下的 30 个旧文件（约 23 MB），未清理。

### 2026-11-26 交付：对话运行模块的审批卡 + 实时推送

**后端**（`core/oryx/loop.py`、`agentd.py`、`domains/session.py`、`domains/__init__.py`）

- 内核为每个审批等待保存一份快照（任务、账号、工具、计划、风险），供页面刷新后查询；`cancel()` 仍按任务 / 账号精确唤醒。
- 新增 IPC `chat.pending_approvals` 与 `GET /api/sessions/{task_id}/approvals`；先校验会话归属，内核未就绪时返回 `[]`。契约已写进 `docs/INTERFACE-SPEC-V2.md`。
- 审批事件带 `plan / risk / tool / task_id / _owner_id`；新增 `step.approval_resolved`，批准或拒绝后所有界面一起撤卡。
- 新增测试：快照与过滤、事件与快照字段一致、批准广播、审批接口契约。

**前端**（`apps/web/src/lib/api.ts`、`lib/types.ts`、`features/sessions/{api.ts,SessionDetail.tsx}`、`App.tsx`）

- `subscribe()` 支持 `?since=N` 续传、控制帧识别、指数退避重连与连接状态回调；`resync` 触发整页刷新。
- 会话页按 `task_id` 过滤事件：审批事件即弹卡；运行开始 / 结束、步骤、拒绝 / 通过即时刷新；运行结束同步刷新左栏会话列表。
- 审批卡展示工具、风险等级、可撤销性与人话步骤，带批准 / 拒绝、提交中、错误与「已结束」处理；页面刷新用兜底接口重建卡片。
- 类型检查、生产构建通过，构建产物已同步至 `core/web/newui/`。
- 100 项相关后端回归全过（审批、会话契约、SSE 事件、接口契约、中止、自动分类、只读工具、重试）。

### 2026-11-26 交付：流式输出 + Markdown 渲染（模块 1）

**取证结论**：后端流式链路本来就是通的 —— `providers.py` 逐 chunk 解析，
`loop.py:1787` 发 `text_delta`、`1816` 发 `reasoning_delta`，
`2460 / 2470 / 2576` 发 START / END。缺的一直是前端没消费：只靠 1.8 秒轮询
取整条消息，且渲染成纯文本。**本模块不改后端内核**。

**前端新增**

- `apps/web/src/lib/markdown.ts`：零依赖 Markdown 渲染器，沿用老前端
  `core/web/modules/core.js` 的「先转义再变换」安全约定；覆盖标题、粗斜体、
  删除线、行内代码、围栏代码块（带语言标签）、有序 / 无序列表、引用、
  表格、分隔线、链接。链接走协议白名单，拒绝 `javascript:` 与协议相对 URL。
  不引第三方依赖，不扩大 XSS 面。
- `apps/web/src/features/sessions/useStreamText.ts`：SSE 攒包器。
  START 新建草稿、CONTENT 追加、END 收尾，40ms 合并渲染；
  `REASONING_*` 单独攒成思考过程；切会话 / `resync` 清空。
- `apps/web/src/features/sessions/SessionDetail.tsx`：助手消息与收尾草稿
  按 Markdown 渲染，流式期间用预格式纯文本 + 闪烁光标（半个代码块按
  Markdown 渲染会抖，与老前端 `agui.js` 同一做法）；用户消息保持原样；
  流式期间贴底自动滚动，用户上翻超过 260px 就不打扰。
- `apps/web/src/index.css`：`.md-body` 一组排版样式，全部取 tokens.css
  的变量；`apps/web/src/lib/types.ts` 补 `StreamDraft` / `StreamThinking`。

**两个关键对齐**

- 后端 `message_id` 在 `_run_loop` 循环外生成、一轮多 turn 复用，
  所以前端**不用它当唯一键**，改成「每次 START 新建草稿」+ 前端自增 key。
- `TEXT_MESSAGE_END` 早于 `store.add_message`：END 后延迟 300ms 拉详情，
  按「内容相等」把草稿认领成正式消息，平滑接上、不出现两条。
  SSE 丢事件时 done 草稿最多挂 15 秒自动清掉，不会永远转圈。

**验证**

- 新增 `core/tests/test_stream_events.py` 4 项，连同 `test_events.py`、
  `test_thinking_and_gallery.py` 共 66 项全过：START / CONTENT / END 字段与
  可拼接性、思考通道独立、delta 进续传缓冲、provider 逐块回调拼出的正文
  == 落库 `reply.text`。
- 前端 `npm run typecheck`、`npm run build` 通过；构建产物已同步到
  `core/web/newui/`。
- `docs/INTERFACE-SPEC-V2.md` 补 SSE 流式事件契约与三个消费边界。
- 新增 `apps/web/scripts/check-markdown.cjs`（`npm run check:markdown`）：19 项就地断言，
  覆盖块级语法、链接协议白名单、HTML 转义，以及「`>` 被转义成 `&gt;` 后引用块不能消失」
  这条回归。用 esbuild 就地转译 TS，不引入新依赖，改渲染器后可一键复跑。

**已知限制**

- 未做真实浏览器流式验收（需配好模型触发一轮对话）。
- Markdown 是 lite 版：不做语法高亮、不支持嵌套列表 / 原始 HTML
  （后者是防 XSS 的硬性取舍）。
- 大量 `TEXT_MESSAGE_CONTENT` 会占用 SSE 环形缓冲（4096 条）；缓冲滚过
  前端水位时发 `resync`，代价是整页刷新。若长回复频繁触发 resync，
  需把 delta 改成「不进缓冲、只实时推」或提高缓冲上限 —— 属于架构调整，
  未在本模块改动。
- 页面在流式输出途中刷新：`?since=-1` 首连不补发历史，刷新前那半段正文要等
  `RUN_FINISHED` 落库后才由详情接口完整显示；刷新后到达的 delta 会另起一条草稿。
  彻底解决需要像审批列表那样给流式正文也做一份内核快照兜底接口，本模块未做。
- `core/scripts/check_api_coverage.py` 仍报 58 条「前端调了后端没有」（退出码 1）。
  抽查 `DELETE /api/agents/{agent_id}`：后端 `domains/persona.py:517` 确实注册了，
  是脚本的路由提取规则漏识别，属**既存误报**。本模块未新增任何接口调用，
  与本次改动无关；要消掉这批红字得单独修提取脚本。

### 2026-11-26 交付：步骤详情（模块 2）

**取证结论**：`steps` 表里 `args / result / error / risk / started_at / ended_at`
一直都有，详情接口也全带上了 —— 缺的是展示：会话页只画「工具名 + 小圆点 +
英文状态」。另一个真实缺口是 `step.done / step.failed` 事件里的失败分类、
只读自证、产物路径**只在事件里**，页面一刷新就丢，界面前后不一致。
所以这次前后端都动，但不新增接口。

**后端**

- `store.py`：steps 表补 `error_class` / `artifact_path` / `mutated` 三列
  （`CREATE TABLE` + `_ensure_columns` 幂等补老库）；`finish_step()` 增加三个
  keyword-only 可选参数，旧调用点全部不用改。
- `loop.py:3094`：工具真正跑完时把这些字段随 `finish_step()` 一并落库；
  「拒绝 / 未确认」路径不传，那些步骤本来就没有结果与产物。
- 新增 `core/tests/test_step_details.py` 5 条：详情与 `/steps` 字段齐全、
  失败分类与报错不丢、旧签名兼容、子资源与详情一致、老库幂等补列。

**前端**

- 新增 `features/sessions/stepFormat.ts`（纯函数）：安全还原 args / result 的
  JSON 字符串、状态 / 风险 / 失败分类中文文案、耗时计算、参数一行摘要，
  「落库优先、SSE 只补空」的合并规则，以及 `stepExtraFromEvent()` ——
  只保留事件真正带了的键，避免 `step.created` 把上一条事件存下的字段覆盖掉。
- 新增 `features/sessions/StepList.tsx`：可展开卡，分区展示调用参数、
  执行结果、错误信息、失败分类提示、产物路径、只读自证；失败 / 拒绝自动
  展开，长文本截到 2000 字可展开全文，每区可复制，无步骤时给引导空状态。
- `SessionDetail.tsx`：统一消费 `step.*` 事件 —— 120ms 合并刷新详情，
  并把事件里未落库的增强字段按 `step_id` 临时保存；切会话 / `resync` 清空。
  落库的 `state / args / result / error` 一律以详情为准，`?since=` 补发的
  旧事件不会把已完成的步骤改回「执行中」。
- 审批卡与步骤卡共用同一份风险文案（与后端 `policy.Risk.label` 对齐）；
  `types.ts` 补三个可选字段，`index.css` 补 `.step-*` 样式（全取 tokens.css）。
- `docs/INTERFACE-SPEC-V2.md` 补 `Step` 字段表与步骤事件契约。

**验证**

- 后端定向 65 项全过（新增步骤契约 + 会话契约 + 接口规格一致性 + 域 / 会话测试）。
- 新增 `apps/web/scripts/check-steps.cjs`（`npm run check:steps`）：37 项就地断言，
  覆盖 JSON 字符串还原、坏 JSON 容错、落库优先（`mutated=0` 不被覆盖）、
  失败分类 / 耗时 / 风险文案，以及「SSE 事件抽取不覆盖旧增强字段」这条
  交付前自测抓到的回归；与 `check:markdown` 同为可一键复跑脚本。
- `npm run typecheck`、`npm run build` 通过；构建产物已同步到 `core/web/newui/`。
- 本模块**未新增任何接口调用**，`check_api_coverage.py` 的既存误报不变。

**已知限制**

- `steps.agent` 列 `create_step()` 从未写入，恒为 null；步骤卡不显示执行者，
  子 Agent 只在会话头部展示。若要在派发场景区分「哪个 Agent 跑了这一步」，
  需要内核补写这一列（属另一个改动，未在本模块做）。
- `elapsed_ms` / `risk_label` / `reason` / `verdict` 只在 SSE 事件里，页面刷新后
  回退到落库值（耗时改由 `started_at / ended_at` 计算，风险走 L0-L3 映射），
  消失的只有自动分类判定理由等少量实时补充信息。
- 未做真实浏览器验收：需要配好模型、触发一次工具调用（含一次失败）看卡片。
- 完整后端测试集仍被本会话环境的删除护栏拦着，需按第四节给出的命令
  在自己终端跑一次确认。

### 2026-11-26 交付：模式与交互（模块 3）

**取证结论**：后端三态一直都在 —— `POST /api/chat` 的 `ChatRequest` 收
`mode / interaction / style` 并固化进 `config_snapshot.runtime`；
`POST /api/interaction`、`/api/style`、`/api/mode` 负责全局默认。
缺的是前端：`RunPanel` 只发 `goal / agent_id / expert`，顶栏把 `craft` /
`efficiency` 原样显示，「设置」按钮没绑事件。

另外钉住一个容易做错成假功能的语义：**`mode`（效率 / 本地）是全局模型
路由**，切它要同步 `active_provider`；单次运行传 `mode` 只会被记进
`create_task` 的字段，实际 provider 仍由全局 `config` 决定。所以本次只在
设置弹层里切计算模式，新建运行页不提供「本次用本地模式」这种假选项。

**后端**

- `modes.py` 新增 `catalog()`：7 个交互模式目录（含一直被文档漏掉的
  `dontAsk`），带中文名、一句话说明与 `read_only / skip_approval /
  unattended / coordination_only` 能力标记；标签与标记分别取自
  `InteractionMode.label` / `ModePolicy`，不把系统提示词发给前端。
- `preference.py` 新增 `GET /api/preferences/catalog`（v2 统一响应），
  一次给出 `interactions / styles / compute_modes` 三份目录 —— 前端不硬编码
  选项。`interaction` 的错误文案补上 `dontAsk`，与代码一致。
- `preference.py:set_mode()`：模型服务未配置时 `provider_config()` 会抛
  `KeyError`，但那时模式**已经存盘**了；原来报 500 会让前端显示「切换失败」
  这个假状态。改成降级返回空 provider / model（与 `/api/state` 首屏同一思路）。
- `chat.py:ChatRequest` 给 `mode / interaction / style` 加枚举校验：
  大小写不敏感并规范化成 canonical 名（如 `dontask → dontAsk`），空串按
  未设置处理，未知值直接 **422**。以前不校验，拼错会静默回退成 `craft`
  —— 用户以为选了问答模式，实际拿到全权限，权限相关入参不能这样降级。
- 新增 `core/tests/test_preference_catalog.py`（9 条：目录形状、与
  `MODE_POLICIES` / `STYLES` 一致、列出来的每个值都能真的 POST 回去、
  非法值 400、模式切换在无 provider 时不报 500）与
  `core/tests/test_run_preferences.py`（7 条：显式覆盖 → 内核参数 +
  快照 + `remember_run`、不传回退全局、空串不算覆盖、续聊守住固化快照、
  大小写规范化、未知值 422、目录里的值 `/api/chat` 全认）。

**前端**

- 新增 `features/settings/preferences.ts`（纯函数）：中文标签、三态摘要、
  风险徽标、**单次运行覆盖请求体**（只带显式选中的 `interaction / style`，
  绝不带 `mode`）、提交前校验、目录可用性判断。
- 新增 `features/settings/api.ts` 与 `PreferencesDialog.tsx`：设置弹层三个
  下拉 + 每项说明与风险提示 + 当前模型服务只读卡片；下拉受控于
  `/api/state`，切失败自动弹回原值不会留假状态；含加载 / 空状态 / 重试 /
  错误 / 成功提示 / 防重复点击 / ESC 与遮罩关闭。切完刷新首屏失败只提示
  「稍后更新」，不把已保存误报成失败；`onClose` 存 ref，避免父组件刷新时
  把成功提示清掉。
- `App.tsx`：设置按钮绑定弹层；顶栏三态改中文名；统一持有一份目录并
  传给新建运行页与会话详情，避免重复请求。
- `RunPanel.tsx`：新增「交互模式 / 回答风格（本次运行）」两个下拉，默认
  「跟随全局设置（执行 / 专业）」，选中具体值时显示该模式的说明与高权限
  警示；提交时一起发给 `/api/chat`。顶部显示当前全局三态与「去设置」。
- `SessionDetail.tsx`：快照区补「运行参数（本次会话固定）」，展示固化的
  交互 / 风格 / 计算模式中文名；升级前的老会话明确标注「未记录」，不拿
  当前全局默认冒充当时的设置。
- `index.css` 补 `.pref-*` / `.run-*` / `.snap-runtime` 样式（全取 tokens.css）；
  `package.json` 加 `check:preferences`。

**验证**

- 后端相关回归 **234 项全过**（含偏好目录、运行参数闭环、接口规格一致性、
  审批 / 取消 / 事件 / 会话 / 步骤 / 安全 / 令牌 / 人设共享等）；
  文档与 `set_mode` 改完后定向复测 **26 项全过**。
- 新增 `apps/web/scripts/check-preferences.cjs`（`npm run check:preferences`）：
  **33 项断言**，覆盖空串不进请求体、`mode` 绝不进单次运行请求体、
  目录缺失不硬拦启动、未知值降级显示、风险徽标等。
  连同 `check:markdown`（19 项）、`check:steps`（37 项）全部通过。
- `npm run typecheck`、`npm run build` 通过；构建产物
  `index-DBi4bLMG.js` / `index-RzxGiLi8.css` 已同步到 `core/web/newui/`，
  与源码构建产物逐字节一致。
- `docs/INTERFACE-SPEC-V2.md` 补 catalog 路由、单次覆盖语义、`/api/chat`
  的 422 契约与 `/api/mode` 的降级行为；`test_interface_spec_v2.py`
  路由双向一致性通过。

**已知限制**

- 模型服务的新增 / 编辑 / 激活仍未接（「设置最小集」剩下的那一半），
  弹层里只如实展示当前生效的 provider / model。
- 未做真实浏览器验收：需要配好模型，实际切一次模式并跑一轮带覆盖的
  运行，确认顶栏、快照与内核行为一致。
- `core/web/newui/` 被 `.gitignore` 忽略且未纳入版本控制，换机器部署要
  重新 `npm run build` 并复制产物。
- `check_api_coverage.py` 的 58 条既存误报不变（本模块新增 1 个接口调用）。
- 完整后端测试集仍被本会话环境的删除护栏拦着，需按第四节命令在自己
  终端跑一次确认。

### 2026-11-26 交付：模型服务配置（模块 4）+ 检查点回退

**取证结论**

- 模型服务：`domains/model_config.py` 的增删改查、激活、本机模型接口一直都在，
  缺的是界面入口。设置弹层只读展示了当前 provider / model。
- 检查点：`domains/checkpoint.py` 的「列表 + 回退」也一直都在（三种范围齐全），
  老前端有完整交互（每条提问下的「回退到这里」+ 范围弹层），新前端没有任何入口。
- 两处真实语义缺口，顺手补上：
  1. `POST /api/providers` 走的是 upsert —— 名字打错一个字就把同名服务的
     密钥和模型**静默覆盖**，界面还显示「添加成功」。
  2. 回退没有任何「运行中」保护：这一轮的工具调用正在写文件时回退，
     两边会撞在一起。

**后端**

- `model_config.py`
  - 新增 `_clean_name` / `_clean_base_url` / `_clean_model`：名称非空、不含斜杠、
    ≤64 字；地址必须以 `http://` / `https://` 开头；模型名非空。全部中文 400。
  - `create_provider` 同名返回 **409**，不再静默覆盖；创建与编辑都先 trim。
  - `edit_provider` 的空串仍表示「不改」，但改到地址 / 模型名时同样走校验。
  - `list_providers` 多返回 `cloud` 槽位 —— 切到本地模式后，界面才分得清
    「云端默认」是哪一条。
- `loop.py` 新增 `is_task_running(task_id)`：只认**这个会话**自己的运行
  （含已发起、还没进入主循环的排队窗口），别的会话在跑不影响。
- `agentd.checkpoint_rewind`：目标会话运行中直接返回 `ok:false`，
  域层映射成 400 + 人话，不碰文件也不删消息。
- 新增 `core/tests/test_provider_domain.py`（18 条）与
  `test_checkpoint_rewind.py` 的运行中守卫 3 条。

**前端**

- 检查点（`features/sessions/`）
  - `checkpoints.ts`：`msg_count` → 消息下标的映射（取 seq 最大的那条）、
    三种范围的文案、影响面（文件数 / 待删消息数）、结果摘要、时间格式。
  - `RewindDialog.tsx`：先选范围再执行；不可撤销警示；运行中禁用；
    结果按还原 / 删除 / 跳过 / 失败 / 消息 / 执行记录逐项列出，失败与跳过可展开明细。
  - `SessionDetail.tsx`：每条用户提问下挂「回退到这里」；步骤区下方补一份
    完整检查点清单（重试产生的点不落在某条提问上，也能从这里回退）；
    监听 SSE `checkpoint.rewound`，回退后清草稿、重拿详情与检查点、刷新左栏；
    `RUN_FINISHED / CANCELLED / ERROR` 时补拉检查点（本轮的点跑完才可退）。
  - `sessions/api.ts` + `lib/types.ts`：列表 / 回退接口与类型。
- 模型服务（`features/settings/`）
  - `providers.ts`：表单校验（同名、斜杠、协议、模型名）、只带改动字段的编辑载荷、
    密钥状态、能力徽标、云端 / 本地分组、槽位徽标、激活提示、体积格式。
  - `ProviderPanel.tsx`：分组列表 + 新增 / 编辑 / 激活 / 删除（二次确认）+
    本机 Ollama 扫描与「登记并使用」；加载 / 空 / 错误 / 成功提示齐全；
    正在生效的服务禁止删除；编辑时名称锁定、密钥留空保持原样。
  - `PreferencesDialog.tsx`：加「对话偏好 / 模型服务」两个标签页，宽版弹层；
    原来那句「模型服务将在后续模块接入」改为指向模型服务页。
  - `settings/api.ts`：provider CRUD、激活、删除、本机模型探测与登记。
- 样式：`index.css` 补 `.msg-row` / `.rewind-*` / `.cp-*` / `.rw-*` / `.pv-*` / `.pref-tab*`，
  全部取 tokens.css 变量。

**验证**

- 前端：`check:checkpoints` **33 项**、`check:providers` **42 项**全过；
  原有 `check:markdown` 19、`check:steps` 37、`check:preferences` 33 全过；
  `typecheck`、`build` 通过。
- 后端：新增两个测试文件 **38 项全过**（模型服务契约 + 检查点守卫）。
- 构建产物 `index-BBag0fzF.js` / `index-BY5auRDY.css` 已同步到 `core/web/newui/`，
  与 `dist` 逐字节一致（`cmp` 校验）。
- `docs/INTERFACE-SPEC-V2.md` 补：provider 的 409 / 校验错误与 `cloud` 字段；
  检查点回退的「运行中 400」。

**已知限制**

- 完整后端测试集已在**用户本机**跑通（2026-11-26）：**1441 passed / 46 skipped /
  1 failed，248s**。唯一失败项是 Windows 中文控制台的 GBK 编码问题，不是业务代码；
  已同日修复并补回归测试，待复跑确认全绿。本会话环境仍被删除护栏（累计 213 > 50）
  拦着，无法代跑全量。复跑命令：
  ```
  cd D:/项目/Oryx/core
  D:/Python/Python311/python.exe tests\run_pytest.py
  ```
- 本机 `core/.pytest_cache` 的 ACL 损坏（WinError 5 拒绝访问，连 `Get-Acl` / `rd` 都被拒），
  pytest 写不进 `cache/nodeids`、`cache/lastfailed`、`cache/stepwise`。只是警告，
  不影响测试结果，但 `--lf` / `--stepwise` 这类增量功能失效。修法见第四节末。
- 未做真实浏览器验收：需要配好模型，实际回退一次（含「只退对话」）并新增 / 激活
  一条模型服务，核对界面、会话快照与顶层 `state.provider` 一致。
- 检查点认领规则依赖「打点时机在用户消息之前」。正常轮次成立；**重试**产生的点
  落在提问之后，不会挂到按钮上，只能从检查点清单里回退（已在清单中覆盖）。
- 模型服务「激活」不会替你切计算模式：效率模式下激活本地服务，只是记为本地默认。
  界面已如实提示。2026-11-26 用户决定**后面再定**，当前保持不切模式。
- `check_api_coverage.py` 的既存误报未处理；本模块没有新增「前端调了后端没有」的接口。

### 2026-11-26 修复：完整测试集唯一失败项（GBK 控制台打印崩溃）

**现象**：用户本机跑完整测试集，**1441 passed / 46 skipped / 1 failed（248s）**。
红的是 `tests/test_prompts.py::test_split_script_is_idempotent`，报错
`UnicodeEncodeError: 'gbk' codec can't encode character '\u2713' in position 0`。

**根因**：`scripts/extract_system_prompt.py` 在**校验已经通过**之后才
`print(f"✓ 分段拼装与原文逐字节相同…")`。Windows 中文终端默认 cp936/GBK，
`✓`(U+2713) / `✗`(U+2717) 编不进去，一句祝贺性质的打印直接抛异常，
`--check` 以非零码退出，父测试断言 `returncode == 0` 就红了。
**业务数据（提示词逐字节等价）其实是对的**，纯粹被输出编码掀翻。
本会话 Bash 环境 stdout 是 UTF-8，所以之前从未复现。

**改动**

- `scripts/extract_system_prompt.py`
  - 成功 / 失败前缀改成纯 ASCII `[OK]` / `[FAIL]`。
  - 新增 `_safe_stdout()`：把 stdout 的 `errors` 降级为 `backslashreplace`，
    以后有人再往 print 里加 emoji 也不会把脚本崩掉（中文照常显示，
    `test_prompts.py` 断言的「逐字节相同」不受影响）。
- `tests/test_prompts.py` 新增 `test_split_script_survives_gbk_console`：
  用 `PYTHONIOENCODING=gbk` 强制子进程走 GBK，断言退出码 0、无
  `UnicodeEncodeError`、有 `[OK]` —— 把「换 locale 也不能崩」锁成回归。

**验证**：`PYTHONIOENCODING=gbk python scripts/extract_system_prompt.py --check`
退出码 0，输出 `[OK] 分段拼装与原文逐字节相同（5950 字符，14 段）`；
`pytest tests/test_prompts.py -q -p no:cacheprovider` **18 项全过**（原 17 + 新增 1）。
待用户本机复跑完整测试集确认 1442 全绿。

**顺带记录的两个本机环境噪音（不影响结果）**

- `PytestCacheWarning: could not create cache path …\.pytest_cache\v\cache\nodeids
  (WinError 5 拒绝访问)`：目录 ACL 损坏，连 `Get-Acl` 和 `rd /s /q` 都被拒。
  需要管理员权限执行一次：
  ```
  takeown /F "D:\项目\Oryx\core\.pytest_cache" /R /D Y
  icacls  "D:\项目\Oryx\core\.pytest_cache" /reset /T
  rd /s /q "D:\项目\Oryx\core\.pytest_cache"
  ```
  或者绕开它：新建 `core/pytest.ini` 写 `cache_dir = %TEMP%\oryx-pytest-cache`。
- `PytestDeprecationWarning: asyncio_default_fixture_loop_scope is unset`：
  pytest-asyncio 的配置项没设，加 `asyncio_default_fixture_loop_scope = function`
  即可消掉。`tests/run_pytest.py` 用了 `-p no:warnings`，直接跑 `pytest` 才会看到。

### 2026-11-26 交付：会话列表与详情（模块 5）

**取证结论**

后端的增删改查其实**一直都在**（`domains/session.py` 的改名、删除、待办新增与状态、
详情、步骤、用量），前端 `sessions/api.ts` 里连 `renameSession` / `deleteSession`
都写好了 —— 缺的只是页面入口。所以本模块以补全交互为主，顺手修掉四个真缺口：

1. **删会话不清检查点**：`store.delete_session` 只清了 `messages / steps / audit /
   todos / tasks / usage`，漏了 `checkpoints` 与 `checkpoint_files`。模块 4 刚接的
   回退会留下孤儿：`POST /api/checkpoints/{id}/rewind` 还能拿这个点去还原文件、
   裁剪一个已经不存在的会话。
2. **列表没有分页，也没有服务端搜索**：`GET /api/sessions` 只有 `limit`，前端
   写死 `fetchSessions(100)`，搜索只在这 100 条里过滤 —— 老会话永远搜不到。
3. **没有「新建会话」接口**：只有「新建运行」能产生会话，用户想先开个空会话
   再慢慢聊做不到。
4. **时间分组把秒当毫秒**：后端 `updated_at` 是 `time.time()`（秒），
   `SessionList.groupLabel` 直接用 `new Date(ts)`，结果所有会话都被归到「更早」。
   检查点那边用的是 `ts * 1000`，两处不一致。

**后端**

- `store.py`
  - `list_sessions(limit, owner_id, offset=0, query=None)`：`LIMIT ? OFFSET ?`；
    `query` 用 `instr(lower(COALESCE(title|goal)), lower(?))` 在 SQL 里过滤全部会话
    —— **用 instr 而非 LIKE**，用户搜 `%` / `_` 是字面量，不该被当通配符。
    空会话（没改名、没 goal）标题兜底成「新会话」，列表里不再是一条空白。
  - `delete_session`：按 `checkpoint_files → checkpoints → messages/steps/audit/
    todos/tasks` 顺序清（子表先删，它有外键指向 checkpoints），再删 metrics 库的
    `usage`。删完再回退会拿到 404「没有这个检查点」。
  - 新增 `create_empty_session()`：直接插一条 `status='idle'` 的空任务。
    不能复用 `create_task()`（它强制 `running` 并塞入 goal/mode/provider）——
    内核续聊本来就会把状态置成 running（`loop.py:2268`），预先置位只会让界面
    误以为「正在运行」而禁止发第一句话。`config_snapshot` 留空，等首次
    `/api/chat` 才固化，不提前锁死一份还没选过的配置。
- `domains/session.py`
  - 新增 `CreateSessionRequest`（`title` 可选，body 可整个省掉）与
    `POST /api/sessions` → `{task_id, title, status:'idle'}`；名字 >60 字返回
    400 中文。`data` 是对象，v2 形状不变。
  - `GET /api/sessions` 加 `offset` / `q` 两个查询参数；`list_sessions` 域函数
    透传并 clamp（limit 1–200、offset ≥0）。
- 新增 `core/tests/test_session_management.py`（**14 条**）：空 body 建会话、
  idle 与无快照、带名字建会话、超长名 400、分页不重叠、搜索在 SQL 层
  （目标压到第一页之外仍能搜到）、`%_` 字面量、按改名后的名字搜、边界 clamp、
  删会话连检查点父子表一起清 + 删后回退 404、手动加待办与三类中文 400。

**前端**

- 新增 `features/sessions/sessionOps.ts`（纯函数）：`groupLabel` 修成 `ts * 1000`
  并支持注入 `now` 便于断言；`SESSION_STATUS_CN` 补 `idle: 未开始`；
  `groupSessions` / `buildSessionQuery` / `hasMorePage` / `validateSessionTitle` /
  `validateTodoTitle` / `sessionMeta` / `todoSummary` / `canDeleteSession`。
  文件名特意叫 `sessionOps.ts` —— Windows 不区分大小写，`sessionList.ts` 会撞上
  `SessionList.tsx`（TS1149，且 import 会解析到没默认导出的那个文件）。
- `SessionList.tsx` 重写：顶部「+ 新会话」；搜索 250ms 防抖后走服务端 `q`；
  30 条一页 + 「加载更早的会话」（跨页按 id 去重）；每行「改名 / 删除」，改名
  行内编辑（留空恢复默认标题、ESC 取消）、删除二次确认；运行中会话禁删并给出
  提示；新建 / 改名 / 删除都通过回调通知外层。
- `SessionDetail.tsx`：待办区改为**始终显示**，空列表也给「加一条待办」入口；
  勾选失败不再静默回滚，会显示中文错误并恢复原状态；头部显示完成进度；
  状态徽标补 `idle`；新增 `refreshKey` prop，左栏改名后详情标题跟着刷新。
- `App.tsx`：会话页外层容器改成 `overflow-hidden`（其余页保持 `overflow-y-auto
  p-6`），消掉双滚动条与双 padding；新建后打开会话、删除当前会话退回总览、
  改名后刷新详情。
- `sessions/api.ts` + `lib/types.ts`：`fetchSessions` 改成对象参数（limit/offset/q）、
  新增 `createSession` / `createTodo`、`renameSession` 返回完整 `RenameResult`；
  补 `CreateSessionResult` / `RenameResult` 类型。
- `index.css` 追加 `.sl-*` / `.todo-*` 样式，全部取 tokens.css 变量。
- 新增 `apps/web/scripts/check-sessions.cjs`（`npm run check:sessions`）：**54 项**
  就地断言，重点是「秒级时间戳必须乘 1000，否则永远落到『更早』」这条回归，
  以及查询串编码、分页判断、名字 / 待办校验、状态文案与待办汇总。

**验证**

- 前端：`check:sessions` **54 项**全过；原有 `check:markdown` 19、`check:steps` 37、
  `check:preferences` 33、`check:checkpoints` 33、`check:providers` 42 全过；
  `typecheck`、`build` 通过。
- 后端：会话 / 检查点定向回归 **60 项全过**（新增 14 + 会话契约 20 +
  接口规格一致性 2 + 检查点守卫 24）。
- `scripts/check_api_coverage.py` 退出码 **0**：前端调用的 156 个接口后端全都有，
  本模块新增的 `POST /api/sessions` 与 `POST /api/sessions/{id}/todos` 都在。
- 构建产物 `index-B_S7HJ-p.js` / `index-BwiOGENL.css` 与 `index.html`
  已同步到 `core/web/newui/`，`cmp` 逐字节一致。
- `docs/INTERFACE-SPEC-V2.md` 补 `POST /api/sessions`、`GET` 的 `offset` / `q`、
  删除连检查点一起清、`idle` 状态与空会话标题兜底。

**已知限制**

- **后端删除仍不拦运行中的会话**：只有前端禁删并提示「先停止」。
  要像检查点回退那样做内核级 `is_task_running` 守卫，得把运行态暴露到 server 侧，
  属单独一轮；直接调 API 仍能删。
- 分页用 `offset` 而非游标：翻页期间若有会话被续聊（updated_at 变大、重排），
  可能出现重复或跳过一条。前端按 id 去重能兜住重复，但漏掉那条需要刷新。
  会话量不大时够用；真要严格一致得改成 `(updated_at, id)` 游标。
- `idle` 是新增状态，其它按状态枚举的地方（老前端、统计口径）若硬编码了四态
  需要单独核对；后端 `session_stats` 只数 `running`，不受影响。
- 未做真实浏览器验收：需要实际新建 / 改名 / 删除一次会话、翻页与搜索、
  加待办并触发一次勾选失败，确认界面与数据库一致。
- 其余 P1（Agent 管理、团队与自动任务）、P2、P3 以及第三节 11 条待确认问题不变；
  「激活是否切计算模式」按用户决定**暂缓**。

### 2026-11-26 交付：Agent 管理（模块 6）

**取证结论**：后端 CRUD 一直都在（`persona.py` 的专家 / 子 Agent 增删改查），
`AgentPayload` 已有 `kind` / `permission_mode` 的 Pydantic 校验（422），
`tools` 在 create / update 里查工具注册表 —— 但 `skills` / `disallowed_tools` /
`model` **三类完全不校验**，填错要等派活时才炸。候选数据源本来就有且分散：
`catalog.tools`（按组）、`catalog.skills_detail`、`config.data.providers`，
前端一个都没接，所以只能手敲文本框。另有两个真缺口：

1. **内置定义没有创建者记录**，保存返回 403「此共享内容没有创建者记录，只能查看」，
   前端详情却写死「此定义由其他创建者维护，当前为只读」—— 事实不符。
2. **`agent_create` 即使 `tools` 为空也会 `await known_tools(agent)`**：内核没起来时
   连一条空工具定义的 Agent 都建不了（503）。`agent_update` 又只在 `tools` 有值时查，
   两条路径行为不一致。

**后端**（`core/oryx/domains/persona.py`）

- 新增 `GET /api/agents/catalog`：一次返回 `tools[]`（name/group/description/risk_label）、
  `skills[]`（name/description）、`models[]`（name/model/type）、
  `permission_modes[]` / `kinds[]` 枚举与 `available{tools,skills,models}`。
  三份清单**各自降级** —— 内核没起来时 tools/skills 给空数组并标 `available=false`，
  界面照常能开，不会整个 503 白屏；models 直接读 config，永远可用。
  `model` 的取值是**模型服务名**（`inherit` = 跟随主 Agent），不是模型 ID。
- `catalog` 是保留字，路由必须注册在 `/api/agents/{agent_id}` 之前，
  否则会被当成 Agent id 去查详情返回 404（已有契约测试钉住）。
- 新增 `validate_agent_assets(agent, config, req)`：写盘前统一校验
  `tools` / `disallowed_tools`（对工具注册表）、`skills`（对技能目录）、
  `model`（对已配置的模型服务名）。内核拿不到清单时**只跳过那一项**，
  不阻断合法编辑；模型服务来自配置，这项永远校验。错误文案：
  `这些工具不存在：X` / `额外禁用工具里有不存在的：X` /
  `这些技能不存在：X` / `模型服务「X」不存在，请先到设置里添加`。
  `agent_create` / `agent_update` 都改走它，顺带修掉上面的 503。
- 枚举常量 `PERMISSION_MODES` / `AGENT_KINDS` 提到模块顶层，
  Pydantic validator 与 catalog 接口共用同一份，前后端不再各改各的。
- `_require_editor` 的两种 403 文案改准确：内置定义说「没有创建者记录」，
  他人创建说「由其他创建者维护」。

**前端**

- 新增 `features/agents/agentCatalog.ts`（纯函数）：候选取名、工具分组、
  多选切换、未知值识别与保留、下拉选项（含「当前值，目录里找不到」）、
  降级提示、只读原因三档、以及**与后端同一套规则**的 `validateAgentDraft`。
- `features/agents/api.ts` 加 `fetchAgentCatalog()`。
- `AgentManagement.tsx`：工具 / 技能 / 额外禁用工具换成可搜索、按组折叠的
  `MultiSelectField`（已选 chips 可点掉）；模型服务 / 权限基线 / 定义类型换成
  由后端枚举驱动的下拉，去掉 `allowCustom` 手敲框；保存前本地先拦一道；
  只读原因按 `creator_id` 是否为空分三档；左栏徽标补「内置 · 只读」；
  候选目录降级时给一句人话提示。专家人设分支保持不变（场景 / 标签本就是自由文本）。
- `index.css` 追加 `.ag-*` 样式，全部取 tokens.css 变量。
- 新增 `apps/web/scripts/check-agents.cjs`（`npm run check:agents`）：**71 项**断言，
  重点是「已保存但不在目录里的值不能丢」「内核降级时跳过那一项校验但模型仍校验」
  「内置定义不能说成别人创建的」。

**验证**

- 前端：`check:agents` **71 项**全过；原有 `check:sessions` 54、`check:markdown` 19、
  `check:steps` 37、`check:preferences` 33、`check:checkpoints` 33、
  `check:providers` 42 全过；`typecheck`、`build` 通过。
- 后端：新增 `core/tests/test_agent_catalog.py`（**15 条**）+ 既有人设契约 4 条 +
  接口规格一致性 2 条 = **21 项全过**。覆盖路由顺序、目录降级、四类字段 400、
  空 tools 在内核未就绪时可创建、403 两种文案、422 枚举校验。
- `scripts/check_api_coverage.py` 退出码 **0**：前端调用 157 个接口后端全有
  （新增 `GET /api/agents/catalog`）。
- 构建产物 `index-DHVrvAAB.js` / `index-6a06PIAC.css` / `index.html` 已同步
  `core/web/newui/`，`cmp` 逐字节一致。
- `docs/INTERFACE-SPEC-V2.md` 补新路由、四类 400 文案、候选降级与保留字说明、
  403 两种情形。

**已知限制**

- **未改权限模型**：内置定义仍然谁都不能改，只是文案如实说明；「复制一份再改」
  与管理员角色仍属第三节第 4 条待确认问题。
- 内核未就绪时后端会跳过工具 / 技能名字校验（放行），等内核起来后这份定义里
  可能留着无效名字；界面已把这类值标红，但不会自动清理。
- `catalog` 作为 Agent id 被占为保留字，理论上一个真叫 `catalog` 的旧定义会
  查不到详情；仓库现有 8 个内置定义里没有这个名字。
- 候选目录只在页面挂载时拉一次：内核中途启动、或期间新增了技能 / 模型服务，
  需要刷新页面才会出现在候选里（已保存的值不受影响）。
- 版本控制：`apps/web/src/features/agents/`、`apps/web/scripts/` 目前仍是未跟踪
  目录（`??`，不是被 .gitignore 忽略），换机器前需要 `git add`。
- 未做真实浏览器验收：需要实际勾选候选、保存、触发一次 400，并在内核未就绪
  时确认页面不白屏、旧值不被清空。
- 下一轮建议做 **P1 剩下的团队与自动任务**（整体保存、派发异步化、状态码与
  防重复点击）。按老规矩，等用户说「开始模块 7」再动。→ **模块 7 已交付，见下节。**

### 2026-11-26 交付：团队与自动任务（模块 7）

**取证结论**

后端 CRUD 一直都在，四个缺口坐实，另有两个比底稿写的更麻烦：

1. **团队保存是 5+ 次串行请求**：`OperationsPanel.tsx` 先 `updateTeam` 改名，
   再逐个 `removeTeamMember` / `addTeamMember` / `updateTeam` 改分工。中途任何
   一次失败就留下半保存的团队；**单成员团队换人必败** —— 旧的先删后加会撞上
   「团队至少要留一个成员」。
2. **派发同步阻塞**：`POST /api/teams/{name}/dispatch` 是 `def`（其余都是
   `async def`），`agent.call` 要等 `run_subagent` 整轮跑完（几分钟），期间前端
   只转圈。**且这条路由被老前端 `core/web/modules/models.js:1619` 使用，还读裸
   `content`** —— 不能直接改语义，否则老前端坏。
3. **状态码靠子串猜**：`remove_member` / `update_team` 用「error 里有没有
   『创建者』」二分，于是「至少要留一个成员」被压成 404、「已经有同名团队」被
   压成 400。
4. **启停 / 删除 / 重试无防重复点击**：只有 `saving` / `retryingRun` 两个分散的
   busy 位，toggle 与 remove 完全没锁。

**后端**

- `core/oryx/loop.py` 新增 `team_save()`：名字 / 目标 / 成员名单一次性替换，
  **先全部校验再一次落地**（校验没过内存与 SQLite 都不动），成员整体替换而非
  逐个增删，所以单成员换人合法；信箱按 `team_id` 关联不丢消息。只校验**新加入**
  的成员在子 Agent 目录里，已保存但目录查不到的成员保留（沿用模块 6 原则）。
  返回带 `reason`（`not_found/forbidden/conflict/invalid`）。
- `core/oryx/agentd.py` 注册 `team.save` IPC 并转发到 runtime。
- `core/oryx/domains/team.py`：
  - `TeamUpdateRequest` 加 `members: TeamMemberInput[]`（1–8 项）。带它 → 走
    `team.save` **一次 IPC**；不带 → 保持旧的 `team.update`（改名 / 改目标 /
    改单个成员分工），老调用方不受影响。
  - `team_error()` 按 `reason` 精确映射 404/403/409/400，老内核没带 `reason`
    时按文案兜底。
  - 新增 `DispatchJobRegistry`：提交即回 `job_id`，**后台 daemon 线程**跑
    `agent.call("team.dispatch")`，`GET .../jobs/{job_id}` 按 owner 隔离查状态。
    没有用 `asyncio.create_task` —— 挂在请求 loop 上的任务生命周期不可靠（实测
    TestClient 里请求一结束就被丢弃）。同一 (团队, 成员, 账号) 有在途作业时
    重复提交返回同一个 `job_id`，不会真的派两遍活。
  - 新增 `POST /api/teams/{name}/dispatch/jobs` 与
    `GET /api/teams/{name}/dispatch/jobs/{job_id}`；**老的同步 `/dispatch` 原样保留**。
- `domains/schedule.py` 逻辑未改：`automation.toggle` 本身幂等，本轮补的是
  前端防重复点击，避免为「防抖」引入多余的 IPC。

**前端**

- 新增 `features/operations/ops.ts`（纯函数）：`teamSaveBody()` 只带
  `name/goal/members`（**绝不带旧的 member/role**，否则会走回老路径）、
  `validateTeamDraft()`（下限 1 / 上限 8 / 斜杠 / 重复 / 超长）、
  `teamDraftEquals()`、派发轮询规则（1.2s、250 次上限、running 才继续）、
  `dispatchResultNotice()`、启停 `busyKey/canToggle/toggleIntent/toggleNotice`。
- `features/operations/api.ts` 新增 `DispatchJob` 类型与
  `saveTeamConfig` / `startDispatchJob` / `getDispatchJob`。
- `OperationsPanel.tsx`：
  - 团队保存改**单请求**（创建走 POST、编辑走 `PUT` 整体保存），失败时草稿
    原样保留让用户重试，不再出现「改了一半」。
  - 派发改提交即回执 + 轮询：成员卡片显示「派发中 / 已完成派发 / 派发失败」，
    同成员在途禁用按钮；完成后刷新团队详情让信箱跟上；结论成功走绿色通知、
    失败走红色错误；轮询超时给人话提示并指向持久化信箱。
  - 启停 / 删除 / 重试按「动作 + id」上锁，在途禁用并转圈；切 tab 卸载时
    清掉所有轮询定时器。
- `index.css` 追加 `.op-save-hint` / `.op-dispatch-state*`，全部取 tokens.css。
- 新增 `apps/web/scripts/check-ops.cjs`（`npm run check:ops`）：**67 项**断言，
  重点是「整体保存请求体形状」「单成员换人合法」「轮询上限」「启停防重复」。

**验证**

- 前端：`check:ops` **67 项**全过；原有 `check:agents` 71、`check:sessions` 54、
  `check:markdown` 19、`check:steps` 37、`check:preferences` 33、
  `check:checkpoints` 33、`check:providers` 42 全过；`typecheck`、`build` 通过。
- 后端：`core/tests/test_team_ops.py` 新增 **20 条**（内核整体保存 6 + HTTP 契约 14）；
  与规格一致性、团队、自动任务、人设、Agent、会话、检查点一起跑 **156 项全过**。
  覆盖：校验失败零落地、单成员换人、改名 + 换成员 + 改目标一次成功且消息保留、
  `team.save` 只发一次 IPC、四类状态码、老内核兜底、POST 立即返回、后台完成、
  重复提交幂等、作业按 owner 隔离、老同步路由仍可用。
- `scripts/check_api_coverage.py` 退出码 **0**：前端调用的接口后端全有
  （新增两条 dispatch jobs 路由）。
- 构建产物 `index-BoahUhjE.js` / `index-DYvhVx51.css` / `index.html` 已同步
  `core/web/newui/`，`cmp` 逐字节一致。
- `docs/INTERFACE-SPEC-V2.md` 补两条新路由、整体保存语义、`reason` 状态码表、
  派发异步化说明，并把第三节缺陷 #10 标为已处理（同步 `def` 是有意保留）。

**已知限制**

- 作业表在**控制面内存**里，服务重启会丢在途状态；但内核派发结束会把结论
  `post` 回团队信箱（持久化），刷新页面仍看得到，只是「派发中」的进度条没了。
- 前端轮询上限 5 分钟。超时只提示「稍后刷新信箱」，不自动重连作业（作业可能
  仍在跑）。真正需要长任务进度的场景应接 SSE `team.member.done`，本轮未接。
- 后台派发没有取消接口：提交了就只能等它跑完或超时。
- 整体保存的「先校验后落地」在内核里成立，但 `_persist_team()` 沿用既有的
  「落库异常吞掉」行为 —— 极端情况下内存已改、SQLite 写失败，与旧的
  `team_update` 一致，未在本轮扩大改动范围。
- 老前端的同步 `/dispatch` 仍会长时间占一条 HTTP 线程（FastAPI 线程池），
  这是保留兼容的代价；新前端不受影响。
- 未做真实浏览器验收：需要实际整体保存一次（含单成员换人）、触发一次 400、
  提交派发并看到状态从「派发中」变「已完成」、连续点两次启停只生效一次。
- 版本控制：`apps/web/src/features/operations/`、`apps/web/scripts/` 等仍是
  未跟踪目录（`??`，非 .gitignore），换机器前需要 `git add`。

**至此 P1 三项全部完成。** P2 知识库 / 总览已由模块 8 完成（见下一节）。

---

### 2026-11-26 交付：知识库与总览（模块 8）

**取证结论**

后端能力比底稿写的更全：`POST /api/index/clear` 早就存在（`domains/index.py:253`），
规格文档 §2.14 也列了，缺的只是界面入口。四个缺口坐实，另有两个比底稿更麻烦：

1. `KnowledgeBase.search()` 三条 SQL 都不带集合条件，`SearchPayload` 只有
   `query` / `limit`；界面搜索框是全库检索。顺带发现兜底模糊匹配用
   `LIKE '%q%'`，用户搜 `%` / `_` 会被当通配符（与模块 5 会话搜索同类问题）。
2. 知识库解析**完全不推 SSE 事件**（`KnowledgeBase` 不持有 bus），进度只能靠
   1.5s 定时轮询；而且 `useEffect` 依赖 `documents`，每次刷新都是新数组 →
   timer 反复重建，页面隐藏不暂停、卡住的文档无限轮询、没有退出条件。
3. 总览 `useEffect(..., [])` 只在挂载时拉一次，跑完不刷新。
4. **建议「执行」比底稿更严重**：`OverviewPanel.tsx:201` 是
   `{s.action && <Button>执行</Button>}` —— 只有 `action` 型（目前仅 `preview`）
   才显示按钮，绝大多数建议是 `prompt` 型（要发起新一轮对话），界面根本没得点；
   唯一那个按钮也没绑事件。老前端 `core/web/modules/models.js:2345` 有正确实现可参考。

**后端**（`core/oryx/knowledge.py` / `domains/knowledge.py` / `domains/__init__.py`）

- `search(owner_id, query, limit, collection_id=None)`：传了 id 先走 `_collection()`
  校验可见性（别人的私有集合 / 不存在的集合 → 404「没有这个知识库集合」），
  再给 FTS 分支与兜底分支都加 `AND d.collection_id = ?`。返回体新增
  `collection_id` 回显范围。兜底改用 `instr(lower(...), lower(?))`，`%` / `_` 是字面量。
- 新增 `clear_collection()` 与 `POST /api/knowledge/collections/{id}/clear`：
  保留集合本身（名字 / 说明 / 可见范围），只删文档、chunks（级联）、FTS 行与原文件；
  解析中的文档一律 409，非创建者 403，集合不存在 404。
- `KnowledgeBase` 可选注入 `bus`（`register_all` 传入），`_rebuild_document`
  成功 / 失败各推一条 SSE `knowledge.document`，带 `_owner_id` 由服务端按账号下发；
  发布异常被吞掉，不影响解析结果落库。不传 bus 时完全静默，老调用方与既有单测无感。
- `POST /api/index/clear` 未改（已存在，返回裸对象 `{status}`，扫描中 409）。

**前端**

- `knowledge/kbOps.ts` + `check-knowledge.cjs`（68 项）：范围选项 / 请求体形状 /
  失效范围识别、退避与超时规则、SSE 事件判定、清空确认文案。
- `KnowledgeBase.tsx`：检索框加集合下拉并回显范围；订阅 SSE 立即刷新 + 退避轮询
  兜底（隐藏暂停、3 分钟收口）；集合卡加「清空文档」；本地索引页加「清空索引」
  二次确认（扫描中禁用、409 / 503 中文提示）。
- `overview/overviewOps.ts` + `check-overview.cjs`（59 项）：**prompt 型建议也必须
  有可点动作**、SSE 刷新判定与节流、统计卡整形。
- `OverviewPanel.tsx`：SSE 400ms 节流自动刷新 + 手动刷新 + 「上次更新 x 前」；
  两类建议都有按钮：preview → `/api/files/preview`（手动清 opener），
  prompt → `onUsePrompt` 带到新建运行预填，**不自动提交**。
- `App.tsx` / `RunPanel.tsx`：`runPreset` + `initialGoal` / `presetKey`，预填后由用户确认；
  提交后清空 preset，避免下次进「新建运行」还带着旧句子。
- `index.css` 追加 `.kb-*` / `.ov-*`，全取 tokens.css 变量。

**验证**

- 前端：`check:knowledge` **68**、`check:overview` **59** 全过；原有
  `check:ops` 67 / `check:agents` 71 / `check:sessions` 54 / `check:markdown` 19 /
  `check:steps` 37 / `check:preferences` 33 / `check:checkpoints` 33 /
  `check:providers` 42 全过；`typecheck`、`build` 通过。
- 后端：`core/tests/test_knowledge_scope.py` 新增 **16 条**（集合筛选、越权 404、
  共享集合、通配符字面量、FTS 分支范围、清空保留集合 / 清索引 / 权限 / 解析中 409、
  SSE ready / failed 事件、无 bus 与总线异常不拖垮解析）；与规格一致性、既有知识库、
  总览、建议及前几轮模块一起跑 **143 项全过**。
- `scripts/check_api_coverage.py` 退出码 **0**。
- 构建产物 `index-TAwzesiI.js` / `index-BSHiXWqh.css` / `index.html` 已同步
  `core/web/newui/`，`cmp` 逐字节一致。
- `docs/INTERFACE-SPEC-V2.md` 补新路由、`collection_id` 入参与可见性语义、
  `instr` 字面量说明、SSE 事件字段、本地索引清空入口。

**已知限制**

- SSE 事件带 `_owner_id` 只发给集合创建者；**共享集合的其他账号看不到别人的解析
  完成事件**，仍靠退避轮询（最慢 8s）。这是服务端按账号隔离的必然结果。
- 轮询 3 分钟收口后不再自动恢复，只提示手动刷新；解析特别慢的大文件需要用户点一下。
- 「清空索引」是**本机账号共用**的内核级操作（不影响磁盘原文件），二次确认里已写明；
  没有做按账号隔离，也没做清空后的自动重扫。
- 建议的 prompt 型只做「预填 + 用户确认」，**不会自动发起一轮**（避免误触产生昂贵运行）；
  与老前端「直接 send」的行为不同，这是有意选择。
- 总览刷新是三个接口整份重拉（400ms 节流），不是 SSE 增量覆盖；运行中步骤密集时
  请求数会比纯 SSE 方案多一些。
- 未做真实浏览器验收。版本控制：`apps/web/src/features/knowledge/`、
  `overview/overviewOps.ts`、`scripts/` 等仍是未跟踪文件（`??`，非 ignore），
  换机器前需要 `git add`。

**至此 P2 完成**，P0 / P1 / P2 全部清零。P3 中不依赖方向决策的部分已由模块 9
完成（见下一节）。

---

### 2026-11-26 交付：系统页 + 设置剩余三项（模块 9）

**取证结论**

后端能力比底稿写的更全：`/api/usage`（用量）、`/api/doctor`（自检）、
`/api/audit/{id}`（审计）一直都在；老前端的「导出审计 / 对话 / 执行计划」
是**前端本地生成文件**，不需要新增后端路由。四个真实缺口：

1. `POST /api/features/toggle` 是 `req: dict` + `bool(req.get("value"))` ——
   传 `{"value": "false"}` 得到 **True**（想关结果开了）；`key` 完全不校验，
   任意名字都能写进 `features.yaml`，成为目录里看不见的幽灵键。
2. `GET /api/usage` 的 `hours` 没有边界：负数把 `since` 推到未来（恒为 0），
   超大值等于全表扫描。
3. **外观最容易做成假功能**：`tokens.css` 早就有 `[data-theme="dark"]` 与
   `#bgLayer` 规则（新前端 `@import` 已经吃到），但整个新前端**没有任何 JS
   调 `/api/appearance`** —— 服务端存的主题 / 背景在新界面根本不生效。
4. 规格 §3 缺陷 3 / 4 的**代码早已修好、文档还标「待修」**：
   `background.png` 改用 `resolve_token_candidate` 认同源 cookie；
   `POST /api/memory` 已有 `MemoryRequest` 把 null 归一成空串 → 400。

「请求日志」核实：后端只有 `middleware/errors.py` 一行 Python logging
（方法 / 路径 / 状态码 / 耗时 / 请求 id 写服务端日志），**没有持久化、没有
查询接口**，故本轮不进界面（第三节第 8 条已按此定范围）。

**范围**：新增「系统」页（用量 / 自检 / 审计导出）+ 设置弹层三个标签页
（外观 / 特性开关 / 长期记忆）+ 两处后端校验修复。**不新增路由**；
终端 / 技能 / MCP 与「新前端是否取代老前端」绑定，方向未定不碰。

**后端**（`domains/feature.py` / `domains/system.py`）

- `feature.py`：新增 `FeatureToggleRequest`（`extra="forbid"`，`key: str`、
  `value: bool`）。`value` 走 Pydantic 标准布尔解析（`"false"` → false）；
  `key` 白名单从 `feature_catalog()` 动态取，不另抄一份；目录外 400
  「没有这个特性开关：{key}」，非法请求不写盘。403 自锁与 500 文案保持。
- `system.py`：`/api/usage` 把 `hours` clamp 到 1–8760，clamp 后的值由
  `window_hours` 回显，界面以回显值为准。
- 这批老接口**全部保持裸对象**（老前端还在用），刻意不改成 v2；
  全局统一响应那笔债单独留着。
- `docs/INTERFACE-SPEC-V2.md`：补 toggle 契约与四类错误、usage clamp、
  日志监控边界、审计 404 语义；顺手把 §3 缺陷 3 / 4 标为已修。

**前端**

- `features/system/`：`systemOps.ts`（窗口 clamp 与如实标签、Token 缩写、
  四张概览卡、自检状态与依赖整形、三种导出的文件描述符、文件名防路径注入）
  + `api.ts` + `SystemPanel.tsx`。三种导出**由浏览器生成 Blob 下载**，
  不新增后端接口；`RUN_*` / `step.*` 等 SSE 事件 400ms 节流刷新用量。
- `features/settings/settingsOps.ts`：`appearancePlan()` 把服务端状态换算成
  可执行的 DOM 计划（主题 / 背景图 / 填充 CSS）；`canToggleFeature()` 处理
  自锁与占位开关；`confirmToggleText()` 在关闭 `EnableFeatureFlags` 前二次确认；
  记忆的秒级时间戳、null 守卫与去重提示。
- `applyAppearance.ts`：`loadAndApplyAppearance()` 在 App 挂载时应用，
  `applyAppearanceState()` 写 `data-theme` 与 `--bg-image / --bg-size / ...`
  并创建 `#bgLayer`。**`index.css` 里把 `#root` 抬到 `z-index:1`** ——
  `fixed + z-index:0` 的背景层默认会盖住普通块级 `#root`，不抬就全屏被图挡住。
- `AppearancePanel` / `FeaturePanel` / `MemoryPanel` 三个标签页；改动成功后
  立即把新状态交给 App 应用到 DOM，不等下一次接口往返。
- `PreferencesDialog.tsx`：5 个标签页，标题 / 说明 / 底部提示按页切换；
  `App.tsx` 顶栏加「系统」入口，有背景图时根容器去掉 `bg-bg` 让背景透出。
- `index.css` 追加 `.sys-*` / `.ap-*` / `.ff-*` / `.mm-*` 与背景层规则，
  全部取 tokens.css 变量。

**验证**

- 前端：`check:system` **85**、`check:settings` **74** 全过；原有 10 个断言脚本
  （68 / 59 / 67 / 71 / 54 / 19 / 37 / 33 / 33 / 42）全过；`typecheck`、`build` 通过。
- 后端：新增 `core/tests/test_settings_ops.py` **16 条**（toggle 字符串 false、
  目录外 key、缺 value / 多字段、自锁 403、usage clamp、审计 404 与可序列化、
  自检 / 外观 / 记忆契约）；与规格一致性及前几轮模块一起跑 **208 项全过**。
- `scripts/check_api_coverage.py` 退出码 **0**。
- 构建产物 `index-Dvi8aG3l.js` / `index-CLZ9Yx-Q.css` / `index.html` 已同步
  `core/web/newui/`，`cmp` 逐字节一致。

**已知限制**

- 「请求日志」未做（无后端查询接口）；导出是整份会话快照，不支持按时间范围筛。
- `#bgLayer` 是全局固定层，主区卡片仍不透明，背景图只在空白处透出；
  与老前端观感一致，但不是逐组件贴图。
- 特性开关 `Disable*` 前缀的 `enabled` 与文件值 `value` 语义相反，界面同时
  显示「文件值 / 实际生效」避免误读；`implemented=false` 后端仍允许切换
  （与老前端一致），界面禁用。
- `EnableFeatureFlags` 关掉后**自锁**：连它自己都不能从界面打开，只能手改
  `features.yaml`，界面已如实提示。
- 外观读取失败时静默退回浅色，不把「外观读不到」顶成整页错误。
- 未做真实浏览器验收。版本控制：`features/system/`、`apps/web/scripts/`、
  `core/tests/test_settings_ops.py`、`docs/MODULE-AUDIT-STAGE1.md` 等仍是
  未跟踪文件（`??`，非 ignore），换机器前需要 `git add`。

---

### 2026-11-26 交付：全局接口层（模块 10）

**功能确认**：底稿只把 P3 剩余列成「一串」、没指定模块 10，用户拍板选
「全局接口层」。选项里原本写的是「逐域套统一响应」，**取证后改了方案** ——
这一步如实记在下面，不假装做到了。

**取证结论**：后端地基 2026-11-19 就做完了（`api/response.py:ok()`、
`middleware/errors.py:error_body()`、请求日志），前端 `unwrap()` 也早就同时
吃得下 v2 壳与裸对象。真正剩下的缺口是三处：

1. **错误码多套规则**：`_common` / workflow / checkpoint / schedule 各写一份
   `_map`，同一句「只有创建者」在 `_common` 是 403、在 schedule / checkpoint
   是 400；「内核未就绪」在 workflow 是 503、别处可能是 400。
2. **`req: dict` 零校验**：`/api/paste`、`/api/terminal/run|interrupt`、
   `/api/sys/query`、`POST /api/checkpoints/{id}/rewind` 直接 `req.get`，
   body 传数组 / 字符串会 `AttributeError` → 500。
3. **前端请求层**：fetch 无超时（卡住就永远转圈）、无全局 401（会话过期时
   各面板各自报「加载失败」）、`error` 一旦置位顶掉整个主区（`App.tsx`）。

**为什么不逐域包 v2 壳**（关键决策）：老前端 `core/web/modules/*.js` 对
`/api/state` 等 6+ 个接口是 `await (await api(...)).json()` 后**直接读业务字段**
（`s.mode` / `d.kind` / `d.optimized`）；`core/tests/test_error_contract.py`
开篇也写明「成功 → 保持原样（裸 dict），**不包壳** —— 包了会砸掉 195 个
现有调用点」。所以规格 §5 步骤 4 由「⬜ 待做」改成「⚠️ 不做」，
`apps/web/src/lib/apiContracts.ts:BARE_ENDPOINTS` 作为改动护栏（21 条）。
新前端两种形状的唯一边界是 `unwrap()`：要求 `code` 是数字、`message` 是
字符串、`data` 键存在**三特征同时成立**才剥，避免误剥「恰好带 data 键」
的裸对象。

**后端**（`domains/_common.py` 与 6 个域文件）

- `_common.py` 新增 `map_ipc_error(exc, *, not_found/conflict/forbidden)`：
  全项目唯一一套规则，优先级 403 > 503 > 409 > 404 > 400；`rpc_failure()`
  委托它。workflow / checkpoint / schedule 三份 `_map` 全部改成一行委托，
  workflow 只追加「没有这次运行 / 没有正在跑」两条域特有文案。
- 5 条 `req: dict` 换 Pydantic：`PasteRequest`、`TerminalRunRequest`、
  `TerminalInterruptRequest`、`SysQueryRequest`、`RewindRequest`。
  脏 body 由 **422** 挡在业务层之外；空值仍由业务层给中文 400
  （`command 不能为空` 等），不改成 422 —— 老前端的错误提示路径不变。
- `system.py` 新增 `optimize_payload()`：`promptopt.optimize()` 空文本路径的
  `content` 是字符串，以前直接 `json()` 会让响应退化成 JSON 字符串，
  老前端 `d.optimized` 拿到 undefined；现在两条路径都恒为对象，成功路径
  仍保留 `optimized/notes/original/critique`。
- `file.py`：`list_files` 空 `allowed_roots` 也返回 `count: 0`。
- 去掉 `loop.py` / `server.py` / `workflow.py` 的 UTF-8 BOM（二进制级只删
  前 3 字节）。`extract_system_prompt.py` 读的是基准快照，逐字节校验不受影响。
- 核实规格 §3 #7 / #8 **不是缺陷**：`schedule._call()` 的 `except IpcError`
  是唯一映射入口；`ExtensionActionRequest.id` / `TrashRequest.name` 是
  多端点复用模型里的兼容位，保留不动。

**前端**（`lib/apiContracts.ts` 新文件、`lib/api.ts`、`App.tsx`）

- 新增纯函数层 `apiContracts.ts`：超时档位、`unwrap`、`pickMessage`
  （补 `error` 字段与 `detail` 对象）、401 判定、横幅文案、裸接口清单，
  全部可被 `check:api` 就地断言，不依赖浏览器。
- `api.ts`：GET 15s / 写 30s / 上传 120s 超时（自管 `AbortController`，
  区分「用户主动取消」与「真超时」）、全局 401 注册入口、
  `X-Request-Id` 透传到 `ApiError.requestId`、`ApiError.kind` 三类
  （http / timeout / network）。SSE 不走超时 controller，仍由指数退避重连。
- `App.tsx`：注册全局 401，会话过期的 401 统一清态回登录页；
  `shouldHandleUnauthorized()` 挡掉「未登录时探测 `/api/auth/me`」的误判。
  `error` 改成顶栏下方的**可关闭横幅**（带「重试」），主区内容照常渲染。
- `index.css` 追加 `.app-error*`；`package.json` 加 `check:api`。

**验证**

- 后端：新增 `core/tests/test_api_layer.py` **34 项**；连同 sys switches、
  错误契约、接口规格一致性、paste、检查点、安全、domains、能力一致性、
  会话管理、闭环、设置、提示词等定向回归 **193 项全过**。
  其中 `test_domains.py` 两条既有单测同步改成传 `TerminalRunRequest`
  （与 `workflow.run_workflow(..., WorkflowRunRequest)` 的既有惯例一致），
  **没有在生产代码里塞 dict 兼容分支**。
- 前端：`check:api` **81 项**全过；原有 12 个断言脚本共 642 项全过；
  `typecheck`、`build` 通过。
- `scripts/check_api_coverage.py` 退出码 **0**：后端 184 条路由、
  前端 160 条调用，前端调用的后端全有（另有 13 条后端有、前端零调用）。
- 另补跑静态守门（`test_no_undefined_names` / `test_version_consistency` /
  `test_registry` / `test_tool_registry_consistency` / `test_prompt_contract`）
  **60 项全过**，确认改 import 与去 BOM 没破坏静态工具。
- 构建产物 `index-DxRfhfl7.js` / `index-r1cfaEab.css` / `index.html`
  （含 `.map`）已同步 `core/web/newui/`，4 个文件 `cmp` 逐字节一致。

**已知限制**

- **仍有约 20 处裸 dict 请求体未迁移**（`app` / `file` / `mcp` / `skill` /
  `toolpack` / `workflow` / `persona` 等域的内部函数与路由）。它们多与老前端
  共用，逐条换模型要把原来的中文 400 重新对齐成 422，属单独一轮；
  本轮只修了 5 条高频 / 高风险路径，规格 §0.4 已注明。
  **→ 已在模块 13 全量清零**（9 个 HTTP 端点 + 6 个实现函数换模型，
  5 个 `persona` 内部函数改名 `data`，中文 400 保持不变而非改成 422）。
- 未做真实浏览器验收：需要实际触发一次请求超时、一次会话过期 401，
  并确认错误横幅出现时主区仍可操作、重试能恢复。
- 超时档位是前端硬编码的默认值；单个超长 HTTP 请求要用
  `api.get/post(..., { timeoutMs })` 覆盖。SSE 与轮询不受影响。
- 全局 401 只在「已登录 + 非 `/api/auth/*`」时触发；登录 / 注册接口的
  错误仍由 `AuthScreen` 自己显示。
- `BARE_ENDPOINTS` 是人工维护的护栏，新增裸接口要同步补；将来老前端退役
  后逐域改造时也要同步删掉它。
- 去掉 BOM 会让这三个文件在 git diff 里显示首行变化（其余字节不变）。
- 完整后端测试集仍被本会话环境的删除护栏拦着（累计临时文件超阈值），
  需按第四节给出的命令在自己终端跑一次确认。
- 版本控制：`apps/web/src/lib/apiContracts.ts`、
  `apps/web/scripts/check-api.cjs`、`core/tests/test_api_layer.py`、
  两份文档等仍是未跟踪文件（`??`，非 ignore），换机器前需要 `git add`。
- 历次构建留在 `core/web/newui/assets/` 的旧产物（约 23 MB）未清理。

---

### 2026-11-26 交付：并发运行停止隔离（模块 11）

**功能确认**：底稿待确认 #10「并发运行的停止隔离」。用户拍板做这一块。
选项里原本还有账号与权限、终端/技能/MCP 接线、`req: dict` 全量迁移，
本轮都没做。

**取证结论：这是真缺陷，而且实测复现了。**

阶段一的描述是「要不要把中止信号改成『每个运行一份』」—— 听起来像
可选的优化。实际读代码后发现是**必现的 Bug**：

    A、B 两轮并发，各记下自己的中止代号 0
    用户停止 A → 全局代号 +1 → 变成 1
    B 查自己的代号（0）发现「变了」→ B 也被当成被停止

根因有**四处**，全部是全局共享：

| # | 全局共享的东西 | 位置 | 串扰后果 |
|---|---|---|---|
| 1 | `self._cancel_event` | `loop.py` | 停止 A 置位全局 Event → B 的审批等待被一起打断 |
| 2 | `self._cancel_generation` | `loop.py` | 停止 A 让**全局**代号 +1 → B 判定「代号变了」→ B 也停 |
| 3 | `self._cancel_consumed` / `_cancel_at` | `loop.py` | 「未消费的中止请求」是全局水位 → A 的中止会被 B 消费掉 |
| 4 | `self._active_task_id`（**单槽**） | `loop.py` | 两个会话并发时后者顶掉前者 → `cancel(task_id=A)` **找不到 A**（停不掉）；`is_task_running(A)` 误报 false → **检查点回退的运行中保护失效**（能在工具正写文件时回退，文件会写花） |

**反向验证（重要）**：`git stash` 式回退在这个仓库里**不可靠** ——
模块 4–10 的改动从未提交，HEAD 里的 `loop.py` 缺 `mark_run_pending(task_id=…)`
等接口，回退后测试报的是「AttributeError / TypeError」，那是「接口不存在」，
不是「行为不对」，证明不了任何事。改用 `core/tests/probe_cancel_isolation.py`：
把旧机制的真实语义（全局 Event + 全局代号）照抄成对照组，与新机制并列跑：

    旧机制：停止 A 后，B 是否被判为取消 = True
    新机制：停止 A 后，B 是否被判为取消 = False

**后端改动**（`core/oryx/loop.py`）

- 新增 `_RunAbort`：一轮运行自己的中止信号（`generation` 代号 +
  `event` 置位标志 + `consumed` 消费水位 + `at` 时刻）。
- 新增 `_RunSlot`：一个运行实例的登记（`rid` / `task_id` / `owner_id` /
  `abort` / `phase`）。
- 运行时新增 `_runs: dict[int, _RunSlot]` 实例表 —— **取代**原来的
  `_cancel_event` / `_cancel_generation` / `_cancel_consumed` / `_cancel_at`
  四个全局字段（已全部删除，无残留引用）。
- `mark_run_pending()` 现在登记的是**一个运行实例**（含自己的中止信号），
  并把 `rid` 记进 `_pending_runs`。
- `run()` 领走实例时按 `rid` **精确交接**（不是按 task_id 猜）——
  交接与「登记 → 开跑」之间那笔中止因此不会丢。
  `run()` 收尾时 `self._runs.pop(slot.rid)` 注销实例，
  中止信号随实例一起消失，不会漏到别的运行上。
- `cancel()` 改按 `(task_id, owner_id)` 精确匹配实例表，只动目标那一份；
  唤醒审批等待也只唤醒匹配的（新增 `_approval_matches` 静态方法，
  口径与实例匹配一致）。
- `_active_task_id` / `_active_owner_id` 从**普通实例属性**改成
  **线程局部**（`threading.local` + `_UNSET` 哨兵）：两个会话并发时
  后者不再顶掉前者，审批归属与停止匹配都不再串。
  哨兵的作用是区分「本线程显式设为 None」与「本线程从没设过」。
- `is_task_running()` 改查实例表 —— 以前靠单槽，并发下判错。
- `wait_approval()` 轮询**本轮专属**的 Event（不是全局的）。
- `_call_provider()` / `_is_cancelled()` 显式接收本轮的 abort 对象；
  子 Agent 继承**父运行**的信号（`_recent_abort()`）。

**踩到并修掉的一个坑**：`cancel()` 原本在持有 `self._lock` 时调用
`_wake_matching_approvals()`，后者又 `with self._lock` ——
而 `self._lock` 是普通 `threading.Lock`（**不可重入**）→ 死锁。
症状是审批用例整个卡死（单跑通过、连跑就挂）。修法：唤醒动作移到锁外。

**前端改动**

- 协议**不需要改**：会话页的停止请求本来就带 `task_id`
  （`SessionDetail.tsx:405`），后端语义修好即可。
- `check-sessions.cjs` 补 2 项断言，钉住「停止必须带当前会话的 task_id」
  —— 将来谁改成空 body，这条会红。
- `domains/chat.py` 补 `cancel_run_payload()`：内核的中止结果回传
  「停的是哪一个会话」，界面据此判断停住没有。

**验证**

- 新增 `core/tests/test_cancel_isolation.py` **13 项**（10 项并发隔离 +
  3 项 HTTP 契约），全过；`core/tests/test_cancel.py` 21 项全过
  （其中 3 条断言按新语义更新：`_cancel_event` → 本轮实例的 abort）。
- 相关回归 **197 项全过**（取消 / 审批 / 派发跟踪 / 检查点回退 /
  Agent / 团队 / 自动任务 / 产物 / 事件 / hook / 会话）。
- **后端全量测试集跑通了**：**1561 passed / 46 skipped / 0 failed**
  （1598 项收集，用时 6 分钟）。这是本项目第一次跑通完整测试集 ——
  此前几轮都被本会话环境的删除护栏拦着（累计临时文件超阈值），
  本轮没有再拦。
- 前端：13 个断言脚本共 **725 项**全过（`check:sessions` 56 项含新增 2 项、
  `check:api` 81 项），`typecheck`、`build` 全过。
  构建产物哈希未变（前端源码无实质改动，只加了断言脚本）。
- `scripts/check_api_coverage.py` 退出码 **0**：后端 184 条路由、
  前端 160 条调用，前端调用的后端全有（另有 13 条后端有、前端零调用）。
- 构建产物 `index-DxRfhfl7.js` / `index-r1cfaEab.css` / `index.html`
  （含 `.map`）已同步 `core/web/newui/`，4 个文件 `cmp` 逐字节一致。

**已知限制**

- **老前端那条停止路径在并发下仍可能停错**：老前端按 `seq` 停、不带
  `task_id`，域层会退回「按 dispatch 跟踪器猜当前那一个」——
  跟踪器本身是**全局单值**（`_task_id`）。新前端不受影响（永远带 task_id）。
  要彻底修需要改老前端的停止协议，属「新旧前端是否取代」的决策范围。
- `_active_task_id` / `_active_owner_id` 改线程局部后，
  **跨线程读**这两个字段的地方（如 `toolsets/` 里几处）拿到的仍是兜底值。
  这些是工具调用路径，正常都在运行线程内执行；跨线程读它们的场景
  本轮没有逐一核查。
- 子 Agent 继承父运行的 abort 是**按线程判定**的
  （`_recent_abort()`）：被工具链直接调用时退到「最近登记的实例」。
  工作流编排的嵌套场景符合预期，但极端并发下「最近」未必是「父」——
  本轮未构造该场景的回归测试。
- 未做真实浏览器验收：需要实际开两个会话、在其中一个点停止，确认另一个
  会话继续跑、审批卡不消失。
- 版本控制：`core/tests/test_cancel_isolation.py`、
  `core/tests/probe_cancel_isolation.py`、`core/oryx/loop.py`、
  `core/oryx/domains/chat.py`、两份文档等仍是未提交状态
  （模块 4–10 的改动同样未提交），换机器前需要 `git add`。

**至此 P0 / P1 / P2 与 P3 中不依赖方向决策的部分全部完成。** 剩余为
终端 / 技能 / MCP（待「新旧前端是否取代」方向拍板）、角色体系，
以及剩余 `req: dict` 全量迁移等技术债。模块 13 已完成最后一项技术债；
**现在剩下的全部是需要产品方向拍板的问题**，不再有可直接开工的功能缺口。

---

### 2026-11-26 交付：账号与权限（模块 12）

**功能确认**：底稿只写了「下一轮等用户说『开始模块 12』再动」，**没有预设范围**。
取证后把剩余三类摆出来让用户拍板，选定「账号与权限」：
改密码 + 登录报错收敛 + 改密后的会话失效；**不含角色体系**（架构级，单独一轮），
也不碰「激活是否切计算模式」（用户已定暂缓）与终端 / 技能 / MCP（待方向拍板）。

**取证结论**

1. `account.py` 只有 register / login / me / logout / status，`store.py` 没有
   改密码方法，也没有按账号批量注销会话的能力；前端 `AuthScreen.tsx`
   只有登录 / 注册，设置弹层里没有账号页 —— 改密码是**真的没有入口**。
2. 登录报错：注册与登录共用 `Credentials`（密码 ≥10 位），
   登录时输短密码直接 422「密码至少需要 10 位」，既答非所问，
   又把注册规则泄露给正在试密码的人。
3. 底稿标的「会话过期各面板各自报错」**已经不是缺口** —— 模块 10 的
   全局 401 回登录页已修好，本轮只把表格改准确，不重复造轮子。
4. 角色体系要动所有域的 owner 校验 + 数据库结构，与「内置定义复制 /
   管理员角色」是同一个待拍板问题，本轮明确不做，界面如实写明。

**后端**（`domains/account.py` / `store.py`）

- 拆成两套请求模型：`Credentials` 保留给注册（强校验），
  新增 `LoginCredentials` 只 trim 账号名、不校验格式与密码长度；
  `_login` 里账号不存在 / 密码不匹配 / 名字格式不对一律 **401**
  「账号或密码不正确」，只有 body 形状脏才由 Pydantic 挡成 422。
- 新增 `PasswordChangeRequest` 与 `POST /api/auth/password`：
  校验原密码、新密码长度（10–256）、新旧不能相同；
  成功后按 `user_id` 删除**其它**全部 `login_sessions`，
  返回 `{changed, revoked_sessions}`，发起请求的当前会话保留。
  - 原密码错误 / 新旧相同刻意用 **400**，不用 401：
    401 在前端是「登录态失效」的信号，语义上这是表单填错。
  - 未登录 401；字段形状脏或新密码长度不合规 422（中文文案）。
- `store.py` 新增 `user_credentials()`（单独开一个带 `password_hash`
  的入口，避免把 hash 泄进 `/me` 的返回体）、
  `update_password_hash()`、`delete_login_sessions_except()`。
  **已有方法一个没改**。
- 规格 §2.0 补新路由与两套模型的语义；`test_interface_spec_v2.py`
  的双向一致性由红转绿。

**前端**

- `features/settings/accountOps.ts`（纯函数）：固定顺序的表单校验、
  **只带两个字段**的请求体、防重复提交、按真实注销数的成功提示、
  秒级 `created_at` ×1000（模块 5 踩过的坑）、退出二次确认文案。
- `AccountPanel.tsx`：账号信息卡（名字 / 创建时间 / ID）、
  改密码表单（原密码 / 新密码 / 确认，本地先拦一道）、
  成功 / 错误 / 提交中状态、退出登录（二次确认后交给 App 统一清态）。
  如实写「本机没有角色体系」，不摆点了没用的下拉。
- `PreferencesDialog.tsx` 加第 6 个标签页「账号」；
  `App.tsx` 把 `user` 与已有的 `logout` 传进去；
  `settings/api.ts` 加 `changePassword()`；`index.css` 补 `.ac-*`。
- 新增 `scripts/check-account.cjs`（`npm run check:account`）：**42 项**
  就地断言，重点是校验顺序、请求体不含 confirm / username、
  成功提示不把「当前会话保留」说成「全部下线」、时间戳单位。

**验证**

- 后端：新增 `core/tests/test_account_password.py` **16 项**全过；
  账号 / 会话 / 设置 / 取消 / 审批 / 错误契约 / 接口层 / 规格一致性
  定向回归 **185 项全过**；把全量里几个「看起来像真失败」的用例
  （web_search / workflow_templates / projectdoc / sessions / suggestions /
  team_ops）单独复跑 **49 项全过** —— 证实它们是护栏导致的共享临时目录污染。
  静态守门 **60 项全过**。
- **完整测试集这次没能跑绿**：`tests/run_pytest.py` 报
  **98 failed / 1326 passed / 47 skipped / 152 errors**。根因是本会话环境的
  安全删除护栏在单轮累计删除满 50 个文件后对后续删文件直接
  `SystemExit(1)`（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`），
  日志里成片的 `CancelledError` / `FileExistsError` 都是它级联出来的；
  TestClient 的 startup 被杀掉后，同一进程里后续用例集体 ERROR。
  **与本次改动无关** —— 模块 4–10 也记录过同一现象，模块 11 那次
  护栏没触发才跑出 1561 全绿。需在自己终端复跑确认：
  ```
  cd D:/项目/Oryx/core
  D:/Python/Python311/python.exe tests\run_pytest.py
  ```
- 前端：14 个断言脚本共 **767 项**全过（新增 `check:account` 42 项），
  `typecheck`、`build` 通过；构建产物
  `index-BYxSLDUE.js` / `index-DKQKLqmP.css` / `index.html`
  （含 `.map`）已同步 `core/web/newui/`，4 个文件 `cmp` 逐字节一致。
- `scripts/check_api_coverage.py` 退出码 **0**：后端 185 条路由、
  前端 161 条调用（各 +1），前端调用的后端全有，零调用仍是 13 条。

**已知限制**

- **角色体系未做**：账号之间仍是「创建者可改、他人只读」两档；
  界面如实说明，没有假的管理员下拉。
- 改密码只注销**数据库里的其它会话**：已经拿到的 cookie 在下次请求时才
  会 401，服务端不会主动推送「你被踢了」；当前设备不重新登录。
- 改密码不轮换当前会话 token（它是随机值，与密码无关，无需轮换）；
  也没有做限流等额外风控。
- 登录现在对账号名只 trim、不做格式校验 —— 极端长的用户名会走一次
  数据库查询，但 `user_by_username` 走索引，不构成实际问题。
- 未做真实浏览器验收：需要实际改一次密码，确认当前设备不掉线、
  另一个浏览器会话立刻回登录页，并检查退出二次确认。
- 版本控制：模块 4–13 的改动**全部未提交**（多为 `??` 未跟踪文件），
  换机器前需要 `git add`。历次构建留在 `core/web/newui/assets/` 的
  旧产物（约 48 MB）未清理。
- 剩余方向：终端 / 技能 / MCP（待「新旧前端是否取代」拍板）、
  角色体系。裸 dict 请求体已在模块 13 清零。

---

### 2026-11-26 交付：剩余裸 dict 请求体全量迁移（模块 13）

**功能确认**：用户选「A：把 27 处 `req: dict` 迁移做了」。
逐处取证后发现**上一轮报的 27 是错的** —— grep 把 7 行「已迁移完留下的
历史注释」也数进去了。真实签名 **20 处**，拆开是：

| 类别 | 数量 | 处置 |
|---|---|---|
| 真正零校验的 **HTTP 端点 body** | **9** | 全换 Pydantic，脏 body 挡 422 |
| 与端点 1:1 对应的实现函数 | 6 | 跟着换模型 |
| `persona` 内部纯函数（路由层早有 `extra="forbid"` 模型保护） | 5 | 参数改名 `data: dict[str, Any]`，不再冒充请求体 |
| 非 `req` 名的裸 `dict` 内部参数 | 8 | 精确化成 `dict[str, Any]` |

9 个端点：`/api/apps/launch`、`POST|PUT /api/apps/custom`、
`POST|PUT /api/skills`、`/api/files/open`、`/api/mcp/toggle`、
`/api/workflows/templates/{id}/run`、`/api/tool-packs/import`。
**新前端零调用**（全在老前端 `core/web/modules/models.js`），
所以本轮无前端源码改动、产物哈希不变。

**后端**

- 新增 8 个模型：`LaunchAppRequest` / `CustomAppRequest`（app）、
  `SkillCreateRequest` / `SkillUpdateRequest`（skill，`exclude_unset`
  保留「未传字段沿用旧值」）、`OpenFileRequest`（file）、
  `McpToggleRequest`（mcp）、`TemplateRunRequest`（workflow，
  body 仍可整个省略）、`ToolPackImportRequest`（toolpack，
  `RootModel[dict[str, Any]]` —— body 就是清单本体，不是 `{manifest:...}` 包装）。
- 6 个实现函数签名与路由同步换模型；`PUT /api/apps/custom/{id}`
  仍以**路径 id 覆盖 body id**（补了专门测试）。
- `persona` 的 5 个内部函数参数 `req` → `data`，
  连带 `req.get` / `req.items()` / `**req` 全部机械改名；
  路由层调用都是位置参数，行为不变。
- 边界**刻意与模块 10 一致**：body 形状脏（数组 / 字符串 / 数字）→ **422**；
  业务字段空 → **中文 400**（`id 不能为空` / `name 不能为空` /
  `应用名不能为空` / `path 不能为空` / `清单里没有名字`），
  老前端靠 `e.detail` 提示的路径不变。
- 清掉 8 行源码里「以前是裸 dict 请求体」的历史注释，
  让「归零」可被静态断言验证。

**契约测试**（新增 `core/tests/test_req_dict_migration.py`，**40 项**）

- 9 端点 × 3 种脏 body = 27 项断言一律 422；
- 7 条业务 400 文案逐字不变；1 项验证 `SkillUpdateRequest` 的
  「只传 description 时 body 沿用旧值」；
- 模型单元：默认值、额外字段忽略、显式 null 被拒、路径 id 权威性；
- **静态守门 2 项**：AST 扫 `domains/*.py`，禁止任何裸 `dict` / `Dict` /
  `dict | None` 参数注解；扫 `core/oryx/**/*.py`，零 `req: dict` 字面；
- **OpenAPI 守门 1 项**：9 个端点的 requestBody 必须 `$ref` 到本轮模型
  （`TemplateRunRequest | None` 的 `$ref` 藏在 `anyOf` 里，递归找）——
  这条能真正区分「换了模型」与「只改了类型注解」。
- 规格 `INTERFACE-SPEC-V2.md` §0.4 从「46 处裸 dict / 未做全量迁移」
  改成「HTTP 边界 100% Pydantic / 0 处」，并同步 13 张路由表的入参列；
  顺手修掉表格里 2 行完全重复的 `/api/tool-packs/import` 记录。
  `test_interface_spec_v2.py` 双向一致性 **2 项全过**。

**验证**

- 后端定向回归（本轮 + 模块 10/11/12 相关 + 静态守门）**350 项全过**：
  `test_req_dict_migration` / `test_domains` / `test_apps` /
  `test_api_layer` / `test_error_contract` / `test_interface_spec_v2` /
  账号 / 取消隔离 / 会话 / 设置 / 工作流模板 / 自动任务 / 审批 /
  `test_no_undefined_names`（抓 `persona` 改名后是否漏改）等。
- 前端 14 个断言脚本 **767 项全过**，`typecheck`、`build` 通过；
  产物仍是 `index-BYxSLDUE.js` / `index-DKQKLqmP.css` / `index.html`（含 map），
  与 `core/web/newui/` **4 文件 `cmp` 一致**（本轮无前端改动，无需重复同步）。
- `scripts/check_api_coverage.py` 退出码 **0**：后端 185 路由 / 前端 161 调用，
  零调用仍是 13 条（本轮没加新路由）。
- 完整测试集结果：见下方「全量测试补记」。

**已知限制**

- 老前端 9 个调用点全部按原样发 JSON 对象，实测契约兼容；
  **未做真实浏览器验收**（需要逐个在老界面点一遍新增 / 编辑 / 启动）。
  新前端不接这 9 个端点，仍等「新旧前端是否取代」拍板。
- `/api/workflows/templates/{id}/run` 的 body 可整个省略，
  这是老前端传 `{}` 的历史语义，保留。
- 桌面端打包副本已于 2026-11-26 同步并重新打包（见文末「桌面端打包收口」）：
  `target/release/core/` 与源码逐文件一致（`sync_core.py --check` 归零），
  NSIS 安装包重打为 3.2 MB。不再有「旧 core 落后」的问题。
- 版本控制：模块 4–13 及本轮打包收口的改动**全部未提交**（100 项，多为 `??`），
  换机器前需要 `git add`。
- **全量测试补记**：`tests/run_pytest.py` 本轮报
  **98 failed / 1330 passed / 47 skipped / 188 errors**（5m24s）。
  根因仍是本会话环境的安全删除护栏：日志里
  `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50}`
  与累计 `count:219` 触发 `SystemExit(1)`，TestClient startup 被杀后
  同进程用例集体 `CancelledError` / `FileExistsError`。
  与模块 12 那次（98 failed / 1326 passed / 152 errors）几乎逐项一致，
  **与本次改动无关**。把全量里标红且与本轮改动相关的用例单独复跑
  （`test_req_dict_migration` / `test_domains` / `test_toolpack` /
  `test_persona_sharing` / `test_agent_catalog` / `test_workflow_templates` /
  `test_security::test_apps_launch_*` / `test_projectdoc` / `test_sessions` /
  `test_suggestions` / `test_team_ops` / `test_web_search` / `test_knowledge*` /
  `test_registry` / `test_symlink_guard`）**219 项全过**。
  请在本机终端复跑确认：
  ```
  cd D:/项目/Oryx/core
  D:/Python/Python311/python.exe tests\run_pytest.py
  ```

---

## 桌面端打包收口（2026-11-26）

用户要求「桌面端旧 core 也同步更新，重新打包一次」。走完整打包流程，
并顺带修掉两个打包链路的真实缺陷。

**同步与产物**

- `sync_core.py` 增量同步 `core/oryx`（29 个文件）、`core/web`（40 个）、
  `core/scripts`（1 个）到 `target/release/`，零删除；首轮 `--check` 归零。
- `build.py` 完整四步：构建新前端 → 同步 → `cargo build --release`（2m45s）
  → `tauri build --bundles nsis`（2m34s）。产物：`oryx-desktop.exe` 3.2 MB、
  `Oryx_0.1.0_x64-setup.exe` 3.2 MB（3.17 MiB），时间 2026-11-26 20:56。
  新前端哈希仍是 `index-BYxSLDUE.js` / `index-DKQKLqmP.css`，
  `core/web/newui/` 与 `apps/web/dist/` 4 文件 `cmp` 一致。
- 重建新前端时 `core/web/newui/assets` 由 70 个（50 MB）历史残留清成 3 个；
  副本里对应的 67 个旧文件随后也清掉，两边都只剩 3 个，`--check` 归零。
  安装包因此仍是 3.2 MB，没把 50 MB 垃圾打进去。

**修掉的两个缺陷**

- `apps/desktop/scripts/build.py`：`run()` 里的 `_d_env.apply(env)` 改为
  `d_env.apply(env)`。原来一跑完整打包就 `NameError`，根本走不到编译。
- `apps/desktop/scripts/sync_core.py`：`diff()` 增加「目标有源无」检测。
  原来只查「缺 / 内容旧」，源码删掉的旧产物会永远留在副本里，
  `--check` 还误报「全部与源码一致」（实测残留 67 个）。修后同步才走进
  既有删除分支（`set(dst_map) - set(src_map)` 本来写好了）。

**防回归**：新增 `core/tests/test_desktop_scripts.py`（5 项全过）——
3 个桌面脚本无未定义名（把守门范围补到 `apps/desktop/scripts/`）、
`diff()` 报多余 / 完全一致为空、`sync()` 删除多余且 `--check` 能报错。

**环境**：完整打包要 `rmtree` 清空重建 `core/web/newui/`（77 个历史产物），
超过本会话安全删除护栏阈值 50，需以无沙箱方式授权跑一次；删除对象全是
可由 `apps/web/dist` 重建的构建产物，不含源码或用户数据。打包副本只用于
「exe 单独拷走」的兜底，安装包资源由 `bundle.resources` 直接从仓库根
`core/` 取，所以修 `sync_core.py` 不需要重打安装包。

**仍需本机确认**：实际双击安装包装一次，验证 `/newui` 正常、模块 4–13
的功能在桌面端可用（本轮未做真实安装验收）。

---

## 探活身份校验修复（2026-11-26，按 C 方案）

**问题**：8765 被「地导目标分析 AI 问答后端」
（`D:\项目\地导系统\周一交付\backend\serve.py`）占用。桌面端 `main.rs` 的
`port_ready()` 只做一次 TCP connect，端口能连上就判定「内核已就绪」→
不拉起 Oryx 内核；`setup` 随后主动
`window.location.replace('http://127.0.0.1:8765')` → 窗口直接显示对方的
`{"service":"地导目标分析 AI 问答后端", ...}` JSON。浏览器直接访问 8765
也是同一行 JSON，所以两个入口看到的一样。壳 `poll()` 同样只看 `res.ok`。

**修复**

- `core/oryx/server.py`：`GET /api/ping` 从 `{"ok":true}` 改为
  `{"ok":true,"service":"oryx"}`。保留 `ok` 兼容既有调用方；`service` 是身份标识，
  规格 §683 已同步。
- `apps/desktop/src-tauri/src/main.rs`：删掉只探 TCP 的 `port_ready()`，改为
  `probe_port()` 三态（`Ready` / `Foreign` / `Free`）——标准库手写最小 HTTP GET
  `/api/ping`，状态行 200 且响应体含 `"service":"oryx"` 才算 `Ready`。
  Cargo.toml 没加任何依赖。`spawn_backend` / `wait_ready_or_report` / 抢先拉起
  三处统一用它；`Foreign` 时立即返回「端口被其它服务占用（不是 Oryx 内核）……
  关闭占用进程或设置 ORYX_PORT」，不再干等 25 秒超时。
- `apps/desktop/dist/index.html`：`res.ok` 之后还要 `data.service === 'oryx'`
  才 `window.location.replace`；身份不符直接显示占用提示。
- 防回归：`core/tests/test_ping_identity.py`（4 项）钉住字段、紧凑 JSON、
  无副作用、无令牌可访问；`core/tests/test_desktop_scripts.py` 新增
  `test_shell_and_rust_probe_verify_identity`，钉住两处都验身份、老的
  `fn port_ready(` 已消失。

**验证**

- 后端定向回归 **70 项全过**（新增契约、静态守门、安全、规格一致性）。
- 用等价手写 HTTP 探针实测当前被占用的 8765：状态行非 200、不含
  `"service":"oryx"` → 判定 `Foreign`，即**不会再误连到地导服务**。
- 完整打包 Exit 0：`cargo build --release` 1m09s + `tauri build` 1m06s，
  Rust 改动编译通过。产物 `Oryx_0.1.0_x64-setup.exe` 3.2 MB /
  `oryx-desktop.exe` 3.2 MB，时间 2026-11-26 21:16；`sync_core.py --check`
  归零，`core/web/newui/` 与 `apps/web/dist/` 4 文件 `cmp` 一致。

**已知限制（如实）**

- 这个修复解决的是「**静默连错服务**」。端口真被占用时 Oryx 内核仍绑不上，
  桌面端会明确报「端口被占用」而不是显示对方页面；要真正使用仍需
  关闭占用进程，或设置 `ORYX_PORT` 换端口。
- 桌面壳 `dist/index.html` 的 `PORT` 常量仍硬编码 8765；设 `ORYX_PORT` 时
  由 Rust 侧导航到实际端口覆盖，壳的轮询可能短暂误报，不影响最终载入。
- 未做真实安装 / 启动验收（没有在 8765 被占用的机器上双击新安装包验证
  错误提示文案）。

