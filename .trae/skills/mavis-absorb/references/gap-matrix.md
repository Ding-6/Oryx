# Mavis → Oryx 差距清单

盘点日期：2026-10-27。参考项目：`D:\项目\Mavis-main\Mavis-main`（`marvis` 2.1.3）。
Oryx：`D:\项目\Oryx`。

状态取值：`缺口`（Oryx 真没有）/ `部分`（有雏形但有洞）/ `已有`（不必动）/ `不抄`（形态或定位不符）。

> **2026-10-27 源码核实修订**（两侧逐条对照 `文件:行号` 后修正）：
> - 第 2 条 符号链接：Oryx `localfs.py` **不是纯缺口**。`:104` 有 `Path(raw).resolve()` 前置解链、`:129/:164/:166` 有 `follow_symlinks=False`。真正缺的是**条目级 symlink 显式分类**（Mavis 的 `os.readlink` + `commonpath` 比对）与 `indexer.py` 的对应防护。状态：`缺口` → `部分`。
> - 第 10 条 workflow：Oryx 的模板机制**比清单描述完整得多**（`stage`/`step`/`needs`/`parallel` 四层语义、`to_dict`↔`parse` 双向对称、`workflow_run_plans` 单独建表规避 ALTER）。缺的只有 `read_only` 标记、别名表、报告落盘。状态：`缺口` → `部分`。
> - 第 4 条：现有测试是 **575** 条（不是 562）。
> - 新增缺口：`core/oryx/tools.py:203-226` 的裸 `shell()` **完全绕过沙箱**（无 env 剔除、无 cwd 监禁、无打码），且风险等级比 `shell_sandbox` 更高。打码接入必须一并覆盖。
> - Mavis 侧澄清：测试在**根级 `tests/`**（不是 `marvis/tests/`）；`doctor` 函数真名是 `doctor()`（`run_doctor` 只是 MCP 层别名导入）；Mavis 实际**没有**统一打码层（`log_summary.redact_log_text` 8 条正则带兜底整行抹除，`system_manager._redact` 只有 2 条且无兜底）—— Oryx 做统一层是**改进**而非照抄。

> **2026-10-27 第二轮核实**（`pytest -q` 全绿 **632 passed**；逐条对源码定位）：
>
> | # | 原状态 | 现状态 | 落点 |
> |---|---|---|---|
> | 1 统一打码层 | 缺口 | **已有** | `core/oryx/redact.py`（9 条正则 + 兜底整段抹除）、`redact_obj`/`redact_result`；接入 `sandbox.py:96-107`、`tools.py:257/314-320`（含裸 `shell`）、`loop.py:2179`（子代理输出）、`loop.py:3078`（工具返回值统一过一遍） |
> | 2 符号链接防护 | 缺口→部分 | **已有** | `core/oryx/symlink_guard.py`（`same_tree`/`classify_symlink`/`is_junction`/`describe_link`）；`localfs.py:195` 逐条目标注 `escapes_root`；`indexer.py:72-85,494,517,522` 不跟随链接 |
> | 3 输入侧注入隔离 | 部分 | **部分（仍缺）** | `core/oryx/taint.py` + `tools.py:256`（`fs_read`）、`browser.py:318`（快照）。**未覆盖**：`index_search`（`loop.py:224`）、`doc_read`（`loop.py:1434`）、`xlsx_read`（`loop.py:463`）、`ocr_image`（`loop.py:296`）、MCP 工具返回（`loop.py:2306`）、`search_files` 内容命中（`tools.py:322`）、`term_status` 输出（`loop.py:1111`） |
> | 4 断言式安全测试 | 缺口 | **已有** | `core/tests/test_redaction.py`（18 例）、`test_symlink_guard.py`（17 例）、`test_readonly_tools.py`（9 例）、`test_doctor.py`（8 例）、`core/tests/samples/secrets/` 样本目录 |
> | 5 只读自证字段 | 缺口 | **已有** | `tools.py:34-89` `MUTATING_TOOLS` 清单 + `ToolSpec.mutates`（`tools.py:104-108`）、`loop.py:3082-3087` 注入 `mutated`/`preview`/`deleted`/`modified`/`moved` |
> | 6 扫描三重上限 | 部分 | **已有** | `indexer.py:447-477`（`max_depth`/条目/超时 → `partial`/`limit_hits`）、`imagegallery.py:392-398` 同款 |
> | 7 预览清单模式 | 部分 | **已有** | `policy.py:116-153` `ExecutionPlan.steps`、`policy.py:556+` `_build_steps`（title/detail/impact）、前端 `index.html:4531-4541` 渲染 |
> | 8 参数注入白名单 | 部分 | **不抄** | Oryx 工作流是**声明式数据**（`workflow.py:62-101`），`goal`/`args` 不拼进 shell，没有注入面。与 Mavis 的 `{{param}}` 字符串替换不是同一形态 |
> | 9 `oryx doctor` | 缺口 | **已有** | `core/oryx/doctor.py`（`run_doctor`，不联网/不扫用户文件）、`server.py:838` `/api/doctor`、前端 `index.html:7577-7582,7861`「环境体检」 |
> | 10 workflow 只读分级+别名+报告不覆盖 | 缺口→部分 | **部分（仍缺）** | 已有：`workflow.py` 四层语义、`store.py:1229-1301` 模板 CRUD、`agentd.py:470-502` 模板接口。**仍缺**：`read_only` 标记、别名表、报告独占创建 |
> | 11 单一输出出口 | 部分 | **已有** | `tools.py:126-131` `_ok`/`_err` 统一构造器、`loop.py:3068-3072` 统一异常包装、`loop.py:3117` `tool_result_text` 统一出口 |
> | 12 CHANGELOG | 缺口 | **缺口** | 项目根仍无 `CHANGELOG.md` |
> | 13 CI 冒烟跑只读模板 | 缺口 | **缺口** | 无 `.github/`、无 `test_smoke_readonly.py` |
> | 14 samples 样本目录 | 缺口 | **部分** | 已有 `core/tests/samples/secrets/`（密钥样本）。**仍缺**：`links/`（符号链接样本）、`logs/`（日志样本） |

> **2026-10-27 第三轮实现**（`pytest -q` 全绿 **707 passed** = 632 基线 + 32 taint + 24 工作流模板 + 19 冒烟）：
>
> | # | 现状态 | 落点 |
> |---|---|---|
> | 3 输入侧注入隔离 | **已有** | 改成**出口级打标**：`core/oryx/taint.py` 新增 `UNTRUSTED_TOOLS`（15 个工具 + `mcp__` 前缀通配）、`is_untrusted_tool()`、`source_label()`、`taint_text()`；`core/oryx/tools.py` `tool_result_text(result, tool_name)` 在**截断之后**打标（顺序反了闭合标签会被切掉）。测试 `core/tests/test_taint.py`（32 例），含「内核运行时接线」一条 —— 用 `doc_read`（实现里无 wrap）而不是 `fs_read`（函数级已 wrap），避免假绿灯 |
> | 10 workflow 只读分级+别名+报告不覆盖 | **已有** | `core/oryx/workflow.py` 新增 `plan_read_only()`（**从子 Agent 工具白名单反推**，查不到的 agent 按「不可知即不安全」判非只读）与 `write_new_report()`（`open(dest,"x")` 独占创建）；`core/oryx/store.py` 补 `aliases`/`read_only` 两列（`_ensure_columns` 幂等补列，不走 ALTER 迁移）+ 别名查找；`core/oryx/loop.py` 新增 `_agent_tool_map()`/`list_workflow_templates()`/`get_workflow_template()`（只读标记**重算**不读存的那份）/`_write_workflow_report()`；`core/oryx/agentd.py` 模板接口改走 runtime + `_workflow_result` 补 `report_path`；`core/oryx/domains/workflow.py` `WorkflowTemplateRequest` 加 `aliases`；前端 `core/web/index.html` 模板卡加「只读」徽章 + 别名行、编辑器加别名输入框、运行后提示报告路径。测试 `core/tests/test_workflow_templates.py`（24 例）+ `core/tests/verify_ui_mavis.py`（headless Chrome 真点） |
> | 12 CHANGELOG | **已有** | 项目根新建 `CHANGELOG.md` —— 按 Marvis 写法写「用户能做什么 + 行为边界」+ 一张边界速查表 |
> | 13 只读冒烟 | **已有** | `core/tests/test_smoke_readonly.py`（19 例）：只读不改东西 / 自证字段 / 外部来源标记 / 打码 / 越界拦截 / 符号链接 / 扫描上限 / 样本目录。**CI 配置本身仍缺**（无 `.github/`）—— Oryx 不是 git 仓库，接 CI 需先有仓库 |
> | 14 samples 样本目录 | **已有** | `core/tests/samples/secrets/`（密钥）+ 新增 `samples/logs/`（固定内容假日志）+ `samples/links/`（**现造**链接的 `make_links.py` —— 链接不是普通文件，放进仓库在别人机器上会一直跳过，看着像测过其实没测） |
>
> **第三轮顺带修掉的两个真 bug**（都是「字段算了但出口丢掉」）：
> 1. **只读自证字段形同虚设**：`loop.py:3155` 把 `mutated`/`preview`/`deleted`/`modified`/`moved` 写进工具返回体，但下面三处只传 `content`，字段在出口就被丢了 —— 界面与审计都看不到。修法：把 `proof` 挂到 `step.done` 事件上（`loop.py:3166-3172`）。
> 2. **报告路径出不了内核**：`agentd.py` 的 `_workflow_result` 把引擎返回收成 `{ok, content, run_id, status}` 四个字段，多余字段**静默丢弃** —— `report_path` 就这么没了，报告写了一份谁也找不到。修法：收口层显式带出 `report_path`，前端运行后提示路径。
>
> **第四轮（2026-10-27 收尾）又挖出第三个「算了但没人看」** —— 前两个修完只算了「字段到了事件里」，没问「谁在读它」：
> 3. **自证字段没人消费**：字段挂到 `step.done` 上之后，**前端一个 `mutated` 都没读**（`index.html` 全文搜不到），审计表的 `outcome` 也**永远停在 `'pending'`** —— 审计行是动手前写的（记的是「打算做什么、谁批的」），工具跑完没人回去补记结果。于是「这个操作改了什么」在界面与账本里都查不到，CHANGELOG 写的「界面与审计都能据此回答」是**假的**。
>    修法（三处一起，缺一处还是看不见）：
>    - `store.py`：`audit()` 返回审计 id；新增 `finish_audit(audit_id, outcome, mutated)`（按 id 更新，不按 step_id —— 同一 step 可能有多条审计）；`audit` 表补 `mutated` 列（`_ensure_columns` 幂等补列）。
>    - `loop.py`：执行完调 `finish_audit` 把 outcome 补成 `done`/`failed`、mutated 补上。
>    - `index.html`：新增 `markProof()` 渲染「未改动」/「已改动」徽章 + 步骤卡加 `.proof` 槽位 + `data-proof-step` 属性；右栏审计列表加 outcome 与改动标记。
>    验证：`core/tests/verify_sse_proof.py` —— **直连后端读 SSE**（走代理会堵死长连接，一个事件都收不到），用会发 tool_call 的假 provider 跑完整链路 模型→工具→内核→agentd→server→SSE→审计，9 条断言全过；`core/tests/verify_ui_mavis.py` 加两条界面断言（headless Chrome 真喂 `handle()` 走 SSE 同款分发路径）。
>    **教训：这个仓里「字段算了但出口丢掉」出现过三次，三次形态都不一样** —— 出口丢字段 / 收口层丢字段 / 字段到了但没人读。查一次不够，要顺着「谁在读它」一路查到界面。
>
> **第四轮顺带修的一个既有 flaky**：`test_empty_clipboard` 整轮跑时偶发失败（单跑不犯）。根因是它直接调 `OpenClipboard` 且**不检查返回值** —— 剪贴板是全局独占资源，别的程序正占着时它是**静默失败**的，接着 `EmptyClipboard` 也没真清空，剪贴板里还留着上一个测试写的文件列表，读出来就成了 `files` 而不是 `empty`。修法：改用模块里带退避重试的 `_open_clipboard()` 并断言返回值。
>
> **第四轮基线**：`pytest -q` 全绿 **710 passed**（707 + 3 条新断言式测试）。

> **第五轮（2026-10-28，对照两份主控提示词）** —— 这一轮不在上面那份 Mavis 清单的范围内，
> 是按「Agent 主控系统提示词」与「Marvis Agent 优化提示词全集」逐条落地，
> 做的都是**把文档里的规矩变成代码里拦得住的规矩**。
>
> `pytest -q` 全绿 **812 passed**（710 + 29 提示词契约 + 21 失败分类 + 52 相关调整）。
>
> | 落点 | 内容 |
> |---|---|
> | `core/oryx/policy.py` | 新增 `CREDENTIAL_NAMES`（`.env`/`.ssh`/`.aws`/`.kube`/`.gnupg`/`.docker`/`.pgpass`/`.htpasswd`/`.pypirc`）+ `CREDENTIAL_PREFIXES`（`.env.` 家族）+ `_credential_hit()` + `is_credential_path()`；`evaluate()` 的路径检查改为「禁区 → 受保护 → 凭据 → 越界」，凭据命中升到 **L2**（不是 L3 —— 提示词原文是「除非用户明确授权」，一刀切拒会让合法诉求永远做不了，规则比需求更硬）；L2 时给 `plan.steps` **插入置顶**的凭据说明 |
> | `core/oryx/tools.py` | `fs_glob` / `fs_grep` / `search_files` 遍历时**跳过**凭据文件 |
> | `core/oryx/indexer.py` | `TEXT_EXT` **删掉 `.env`**（原先显式收录）；`_walk()` 按**路径整体**判（只看扩展名不够：`.env.local` 的扩展名是 `.local`，`.ssh/` 里文件扩展名五花八门） |
> | `core/oryx/failure.py`（新） | 工具失败分类 E1–E5：确定性规则（**不能让模型自己判**，否则同一错误两次调用给出两个分类）+ 每类对应的建议动作 + `annotate()` 只动失败结果、返回新字典 |
> | `core/oryx/loop.py` | `SYSTEM_PROMPT` 重写为八条原则的完整结构（原先只有「工具使用要点」「安全规则」两段）；语言规则从写死的「中文回答」改为「严格跟随用户输入」；`{agents}` 占位符接入；`spawn_agent` 加 `acceptance` 参数；`run_subagent` 把它落进子 Agent 提示词；工具结果出口接 `annotate_failure()`；`step.failed` 事件带 `error_class` |
> | `core/oryx/agents.py` | 新增 `AgentLibrary.prompt_block()` —— 主提示词要求「L1 能闭环就不许拆碎步」，但模型**不知道**有哪些 L1 可派（`spawn_agent` 描述里只写死了「目前可用：doc-manager」），管家一多就漏 |
> | `core/web/index.html` | 新增 `.step .ecls` 样式 + `ERR_CLASS_HINT`（E1–E5 中文说明）+ `step.failed` 分支追加分类徽章 |
> | `agents/*/AGENT.md` | 六张子 Agent 角色卡各补 `## 边界` 段（不处理 / 不决策 / 不确认 / 不碰凭据）；`file-agent` 白名单补 `fs_move`/`fs_glob`/`fs_grep` |
> | `core/tests/test_prompt_contract.py`（新，29 例） | 测**接线**而不是函数本身 —— 提示词段落写错了模型照样干活，闸门没接上则 `.env` 会经由 `index_search`/`fs_grep` 直接进上下文 |
> | `core/tests/test_failure_class.py`（新，21 例） | 12 个参数的分类表 + 「E2 必须排在 E5 前」+ 确定性 + `annotate` 不改原对象 + 分类真的进了模型上下文（走真实 `_handle_tool_call`） |
> | `core/tests/verify_ui_prompt_contract.py` | headless Chrome **真点一次**：发消息 → 读不存在的文件（E5 徽章）→ 弹凭据审批卡（点确认）→ 再弹一张（点取消）→ 密钥值没泄且已打码。**30 项全过** |
>
> **第五轮挖出的四条绕过路径**（本轮最要紧的发现）：
> 策略引擎只看得到工具的**入参**（`root`/`path`），看不到遍历过程中撞上的每一个文件。
> 于是 `fs_grep(root="项目目录", pattern="SECRET")` 会把目录里 `.env` 的内容读出来，
> 而 `root` 根本不长得像凭据文件；`indexer.py` 原本**显式把 `.env` 收进 `TEXT_EXT`**。
> 四条路径（`fs_glob` / `fs_grep` / `search_files` / 索引器）全部堵上并写了断言式测试。
>
> **第五轮踩的两个验收坑**（都是「症状离根因十万八千里」型）：
> 1. **验收代理堵死 SSE**：代理用 `resp.read()` 一次性读完再返回，
>    对 SSE 来说会把长连接**堵死**（后端要等流结束才吐数据）——
>    页面照常显示、接口全通，但**一个事件都收不到**。修法：SSE 走独立的流式转发路径，逐块转。
> 2. **写死端口撞上残留进程**：上一轮调试跑剩的假 provider 还占着那个端口，
>    新进程绑不上、读不到 READY，脚本却**照样返回**，
>    只是 base_url 指向那个旧进程 —— 旧进程的调用计数早就走完了，答回来的是一段纯文本，
>    验收表现为「界面什么都没有」。修法：端口动态挑 + 核对 READY 确实来自**这个**进程。
>
> **教训补一条**：「静态检查与 HTTP 契约测试测不出界面层语义错配」这条要再加一层 ——
> **验收基础设施本身也会骗你**。代理堵 SSE、端口撞残留进程，
> 两个都是「脚本跑完了、结果全 FAIL」但根因在验收脚本而不在产品代码。
> 所以端口要动态挑、READY 要核对来源、SSE 要流式转发、探针 JS 要先 `node --check`。

> **第六轮（2026-10-30，中止竞态）** —— 前五轮是「把提示词落地」，
> 这一轮是**修前五轮做完之后暴露出来的真 bug**：按「停止」按不住。
> 触发点是回归测试跑完之后拿真模型（不是假 provider）做冒烟 ——
> 假 provider 的 `chat()` 会正常结束，走不到漏网的那条路径。
>
> `pytest -q` 全绿 **833 passed**（812 基线 + 19 中止用例 + 2 控制面用例）。
>
> | 落点 | 内容 |
> |---|---|
> | `core/oryx/loop.py` | **中止代号**（`_cancel_generation`/`_cancel_consumed`/`_cancel_at`）取代单纯的 Event 两态；**忙碌标志拆成两个计数**（`_run_pending` 已发起未开跑 / `_run_in_flight` 正在跑）取代「最近一个任务」的句柄；`run()` 拆成薄包装 + `_run_loop()`（保证任何异常路径都归还计数）；`_is_cancelled()` / `_finish_cancelled()` 抽出来给轮首/轮末共用；**轮末两条 break 路径都查停止标志**；`_StreamCancelled` + **流式回调里查中止标志**（每个 chunk 查一次，命中就打断这次模型调用）；子 Agent 那一圈也接上同一套中止检查 |
> | `core/oryx/agentd.py` | `chat_run` 收到就 `mark_run_pending()`（登记「正要开始跑」），并把那个槽位交接给 `run()`（`pending_slot=True`，交接在同一个锁里做完，中间不留「两边都是 0」的缝） |
> | `core/oryx/server.py` | `_DispatchTracker`（三态 inflight/landed/unknown）：发起在途就等它落地再把中止转给内核；发起还没到就先记下这笔中止、等 chat 到了补发；`ChatRequest.seq` / `CancelRequest.seq` |
> | `core/web/index.html` | `S.sendSeq` / `S.inflightSeq`：点发送时 +1 得序号，停止请求带同一个序号 |
> | `core/tests/test_cancel.py` | 19 例（含 4 个「改坏了也不变红」的守门用例） |
> | `core/tests/test_dispatch_tracker.py`（新，9 例） | `_DispatchTracker` 的三态与幂等性 |
> | `core/tests/verify_ui_cancel_race.py`（新，9 例） | headless Chrome **真点一次**：点发送 → 立刻点停止 → 界面出现「（已停止）」+ **回库核对 status == cancelled** |
>
> **这一轮挖出的四个漏网**（症状各不相同，所以要一个一个测）：
> 1. **停止请求先到内核** —— 「发送」与「停止」是两条独立请求，可以并发。
>    停止先到时内核还没登记任务，那一下被判成「没有正在跑的任务」丢掉。
> 2. **模型一直不返回** —— 本机挂的 Base 模型不会终止生成，
>    一次 `chat()` 能连吐好几分钟。只在轮首/轮末查标志的话，
>    中间那次调用要等它**整个跑完**才有机会收手 → 状态永远停在 `running`，界面一直转圈。
> 3. **模型一轮说完就结束** —— 「这轮没有工具调用，直接收尾」那条路径绕过了检查。
> 4. **子 Agent 还在跑** —— 主 Agent 收手了，子 Agent 还在后台接着调模型、发工具调用。
>
> **顺带修掉一个隐藏很久的问题**：「有任务在跑」原先看的是 `_active_task_id` ——
> 那个字段跑完**从不清**（它是「当前/最近一个任务」的句柄）。
> 后果是 `is_busy` 恒为真 → **自动任务永远排不上队**（没有任何报错），
> 而且用户在旧任务上误点停止会把下一条消息也停掉。
>
> **这一轮踩的三个验收坑**：
> 1. **守门用例「改坏了也不变红」**：取消时刻落在**模型调用之前**的话，
>    轮首那次检查就命中了，压根走不到轮末 —— 于是漏检的那条路径测不出来。
>    修法：provider 的 delay 要**足够长**（比取消时刻晚不少），
>    让取消落在**模型调用期间**，并把这个时序约束写进测试的 docstring。
> 2. **测试 fixture 删 `sys.modules` 会毁掉 conftest 的路径重定向**：
>    `conftest.py` 在**模块导入前**把向量模型目录指到 `ORYX_MODEL_DIR`，
>    fixture 里一句 `del sys.modules["oryx.*"]` 让重定向失效 →
>    模型目录指回真实路径 → **19 个测试全红**，而报错完全指不到真凶。
>    症状离根因十万八千里。修法：fixture 不删 `sys.modules`，只 `importlib.import_module`。
> 3. **Base 模型的输出全在 reasoning 通道**：库里没有 assistant 消息是**正常的**
>    （不中止地跑完也一样没有）。验收不能拿「有没有正文」当判据，
>    该判的是「中止后状态落定」与「中止后不再有新内容冒出来」。
>
> **打断的物理边界**：模型调用**没法从外面硬杀**（正在等 HTTP 响应），
> 打断靠的是「流式响应下一个 chunk 到达时」的那个检查点。
> 极少数模型一次只吐一大段（中间没有 chunk 边界）时，
> 停止要等到那一大段吐完才生效 —— 但不会再等到整轮跑完。

---

## 一、值得抄（按投入产出比排序）

### P0 — 安全缺口，能导致真实泄密或越权

| # | 能力 | Mavis 出处 | Oryx 现状 | 落点 | 状态 |
|---|---|---|---|---|---|
| 1 | **统一密钥打码层**（日志正文 / 命令行 / 子代理产出 / 工具返回） | `marvis/core/log_summary.py:430` `redact_log_text`（8 条正则 + `_LEFTOVER` 兜底整行抹除）、`marvis/core/system_manager.py:27` `_redact` | `core/oryx/manage.py:169` `mask_key()` 只打码 API key（首6+末4）；`core/oryx/sanitize.py` 只防提示注入，不打码 | 新增 `core/oryx/redact.py`，在 shell 输出、子代理输出、工具返回值三处接入 | 缺口 |
| 2 | **符号链接 / 越界逃逸防护** | `marvis/core/disk_inspect.py:126,158,167`（`follow_symlinks=False`）、`:415-427` `_classify_symlink`（`os.readlink` + `commonpath` 比对）、`:453`；`marvis/core/log_summary.py:217,243,285,728`（`O_NOFOLLOW` + `fstat` 确认普通文件）、`:809,844` | `core/oryx/localfs.py:97` `safe_browse` 只做 device path / UNC / 禁区 / 盘符根判定，**不防符号链接逃逸**；`core/oryx/indexer.py` 扫全盘无此防护 | 抽出 `core/oryx/symlink_guard.py`，在 `localfs.py` 与 `indexer.py` 共用 | 缺口 |
| 3 | **输入侧提示注入隔离** | 无（Mavis 主要是 CLI，无多 Agent 上下文注入面） | `core/oryx/sanitize.py` 只清洗**子代理输出**；浏览器抓取 / 文件读取的原始文本进上下文前**无标记化** | 新增 `core/oryx/taint.py`：外部来源文本包 `<untrusted-source>` 边界标记再入上下文 | 部分 |

> 第 3 条 Mavis 没有等价物，属于 Oryx 自主增强。保留在清单里是为了不被「照抄 Mavis」的思路漏掉。

### P1 — 可验证性与执行纪律

| # | 能力 | Mavis 出处 | Oryx 现状 | 落点 | 状态 |
|---|---|---|---|---|---|
| 4 | **断言式安全测试**（样本目录 + 逐字节快照 + FORBIDDEN 明文清单） | `marvis/tests/test_safety_and_cli.py:59,181-183,265-267`（密钥不外泄 / 逐字节未改动）、`test_disk_inspect.py:139-167`（符号链接不跟随）、`test_log_summary.py:15-28`（FORBIDDEN 12 串）、`:128-131`（签名归一化） | 已有 562 用例 + 296 断言，但**无**「子代理输出是否泄密」「是否越界写文件」的断言式测试 | `core/tests/test_redaction.py`、`core/tests/test_symlink_guard.py` | 缺口 |
| 5 | **只读自证字段**（`preview` / `deleted` / `modified` / `moved`） | `marvis/core/disk_inspect.py` 与 `log_summary.py` 每个只读结果都带这四个字段 | 工具返回无统一「我没动过」标志 | `core/oryx/tools.py` 的 `ToolSpec` 增加 `mutates: bool`，工具结果统一带 `mutated` | 缺口 |
| 6 | **扫描三重上限 + 自述不完整**（depth / entries / timeout → `partial=True` + `limit_hits`） | `marvis/core/disk_inspect.py`（BFS + visited 防环 + 三个上限） | `core/oryx/indexer.py` 扫全盘、`core/oryx/imagegallery.py` 扫图片，均无上限与 `partial` 自述 | `indexer.py` / `imagegallery.py` 加 `depth`/`entries`/`timeout` 与 `partial`/`limit_hits` | 部分 |
| 7 | **预览清单模式**（执行前先返回可读的将要执行清单） | `marvis/core/system_manager.py:327` `net_repair(execute=False)` 返回 6 条 `status:"preview"` 步骤说明 | `core/oryx/policy.py:404` L2 走 `ExecutionPlan` 审批，粒度粗 | L2 审批的 `ExecutionPlan` 增加人类可读 `steps` 清单 | 部分 |
| 8 | **参数注入白名单**（`{{param}}` 值校验 `[\w.\- \u4e00-\u9fff]{0,120}`） | `marvis/core/workflow.py:42-53` `_subst()` | `core/oryx/workflow.py` 声明式编排，参数走数据不拼命令，风险较小 | 核对 `workflow.py` 参数落库前是否做边界校验 | 部分 |

### P2 — 体验与工程纪律

| # | 能力 | Mavis 出处 | Oryx 现状 | 落点 | 状态 |
|---|---|---|---|---|---|
| 9 | **`oryx doctor` 自检 + hints**（版本/依赖/模式/破坏性操作模式 + 「你接下来能干什么」的可执行命令列表） | `marvis/core/doctor.py:29-96`（`network_allowed`、11 个可选依赖的 installed+purpose、`hints` 列表） | 有 `core/oryx/prewarm.py`、`indexer.py`，但**无统一自检入口**；前端只有 `/api/capabilities` 能力矩阵 | 新增 `core/oryx/doctor.py` + `/api/doctor` 路由 | 缺口 |
| 10 | **workflow 模板只读分级 + 别名 + 报告不覆盖** | `marvis/core/workflow.py:474-495`（`runnable` 标记 + `aliases`）、`:429-437`（拒绝不可直跑模板并列出可跑清单）、`:145-160` `_write_new_report`（`open(dest, "x")` 独占创建，已存在则拒） | `core/oryx/workflow.py` 是模型驱动的 stage/step 编排，**语义强于 Mavis**，但缺这三项 | `workflow.py` 加 `read_only` 标记 + 别名表；报告落库用独占创建 | 缺口 |
| 11 | **单一输出出口 + dict-error 契约**（CLI 统一 `_output`，错误是结果里的 key 不是异常） | `marvis/cli.py:427` `_output`、`:156` `_dispatch`、`:143` `--local` 前置写环境变量 | Oryx 工具返回 `{"ok":bool,...}`、HTTP 层有异常处理，两边各自为政 | 统一工具结果构造器 `core/oryx/tools.py` | 部分 |
| 12 | **CHANGELOG 写法**（写「用户现在能敲什么命令 + 该命令的行为边界」，不是 commit 标题） | `Mavis-main/CHANGELOG.md`（2.1.3 整条描述一条只读命令的完整语义） | `D:\项目\Oryx\CHANGELOG.md` **不存在**，需新建 | 项目根新建 CHANGELOG.md | 缺口 |
| 13 | **CI 冒烟跑只读模板**（把「只读能力能跑」当门禁） | `Mavis-main/.github/workflows/ci.yml`（pytest + 8 条 CLI 冒烟） | 无 CI 配置 | `core/tests/test_smoke_readonly.py` + 后续接 CI | 缺口 |
| 14 | **samples/ 样本目录**（给每个能力配可复现的样本，测试逐字节比对） | `Mavis-main/samples/{disk,hygiene,logs}` | 无 | `core/tests/samples/` | 缺口 |

---

## 二、不抄（形态或定位不符）

| Mavis 能力 | 不抄原因 |
|---|---|
| MCP **Server**（对外暴露 41 工具） | Oryx 是 MCP **客户端** + 自带前端，方向相反 |
| `install.py` 一键 pip 安装 + 6 平台配置打印 | Oryx 是 Tauri 安装包，没有 pip 分发这一步 |
| Windows 任务计划程序封装 | 超出 Oryx 定位；Oryx 的 `automation.py` 走 cron |
| 系统管理（进程/服务/防火墙/端口巡检） | Oryx 已有 `syssettings.py` 8 个 Windows 设置工具，够了 |
| 网络修复（刷新 DNS / 重置 Winsock） | 超出定位 |
| 文档分析规则引擎（11 条合同风险规则 / 3σ 异常值 / 润色打分） | Oryx 有 `experts/data-analyst` + `doc-manager` 子 Agent 走模型，规则引擎是重复造轮子 |
| OCR / 格式转换 / 图库 / 知识库 | Oryx 已有 `ocr.py`、`office.py`、`indexer.py`、`imagegallery.py` |
| 网页搜索（Bing 抓 HTML） | Oryx 有 `browser.py`（Playwright）更强 |
| `MARVIS_MODE` 断网开关 | Oryx 的 local 是「用本地模型推理」，语义不同，不能直接套；如需断网要另设 `network_allowed` |
| adapters/ 五平台 rules 文件 | Oryx 的 `agents/*/AGENT.md` frontmatter 工具白名单是更强机制 |

---

## 三、Mavis 有、Oryx 也缺、但**不属于本次范围**

Oryx 自身在这些维度的缺口（Mavis 同样没有，属 Oryx 独立演进方向，勿混进 Mavis 吸收任务）：

- 前端 7700 行单文件无模块化；`loop.py` 145KB 单体
- 无 CI、无 lint 配置、无覆盖率门禁
- schema 无版本号（靠 `CREATE TABLE IF NOT EXISTS` 规避迁移）
- 无提示注入隔离层（已列入本次 P0-3）
- 无 fs_move / fs_glob / fs_grep 工具、无目录监听
- 无 workflow 循环 / 条件分支 / try-catch
- team_dispatch 一次只能派一个成员
