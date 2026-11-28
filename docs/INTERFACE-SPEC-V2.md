# 接口规格 v2

> **这份文档是「145 条路由全推倒重写」的契约。**
>
> - **来源**：不是拍脑袋设计的 —— 由我逐条读 `core/oryx/domains/*.py` 的实现反推，
>   并用运行时实测交叉验证（装配 FastAPI 后真发请求）。
>   每条都标了来源文件与行号，便于你核对，也便于重写时对照。
> - **状态**：**§4.2 的五个问题已拍板（2026-11-19）**；session 域与大屏三接口已按本文改造落地。
> - **采集日期**：2026-11-19 ／ 代码基线 `9f6e219` + `c122b79` + `8d434f6`

---

## 0. 全局约定（对所有接口生效）

### 0.1 鉴权

中间件 `_require_token`（`core/oryx/server.py:162`）：

| 项 | 规则 |
|---|---|
| 放行路径 | `/`、`/api/ping`、`/favicon.ico`，以及**所有** `/web/` 开头的路径 |
| 令牌来源（三选一） | cookie `oryx_token`（HttpOnly，首页下发）→ `X-Oryx-Token` 头 → `?t=` 查询参数 |
| 取法 | 统一在 `core/oryx/api/auth.py::resolve_token_candidate` —— 中间件与域内路由共用（见 §4.3） |
| 401 | `{"detail": "缺少或错误的访问令牌（X-Oryx-Token）"}` |
| 403（跨源 cookie） | `{"detail": "跨源请求不接受 cookie 鉴权"}` |
| 403（缺来源标记） | `{"detail": "缺少来源标记（x-oryx-request: 1）"}` —— 仅非 `GET/HEAD/OPTIONS` 生效 |

> **注意**：cookie 鉴权额外要求**同源**。同源白名单含 `tauri://localhost`、`http://tauri.localhost`、
> `https://tauri.localhost`。这是现有的安全设计，**重写时不要动**。

**多用户账号模式（新增）**：账号会话使用 HttpOnly cookie `oryx_session`，30 天有效；
注册、登录、状态查询为公开入口，其他接口要求有效账号会话。首个账号创建时，
旧单用户库中尚未归属的会话、用量与长期记忆归入该账号。存在账号后，机器级
`oryx_token` 不再作为 API 凭据接受，避免绕过数据归属。Agent 与专家人设仍为平台共享能力；
会话、运行事件、用量与长期记忆按账号隔离。

### 0.2 错误语义

内核异常经 IPC 传输后**带类型前缀**（如 `ValueError: 没有这个会话`），
所以按子串匹配状态码。三套映射并存，**这是要统一的重点**：

| 映射函数 | 位置 | 规则 |
|---|---|---|
| `rpc_failure` | `domains/_common.py:19` | 含「内核未就绪」→ **503**；含「已有扫描/扫描进行中/已释放」→ **409**；含「没有这个/不在/已被」→ **404**；其余 → **400** |
| `_map`（workflow） | `domains/workflow.py:158` | 含「内核未就绪」→ **503**；含「没有这次运行/没有正在跑/没有这个模板」→ **404**；其余 → **400** |
| `_map`（checkpoint） | `domains/checkpoint.py:61` | 含「内核未就绪」→ **503**；含「没有这个检查点」→ **404**；其余 → **400** |
| `_map`（schedule） | `domains/schedule.py:88` | 含「内核未就绪」→ **503**；含「没有这个」→ **404**；其余 → **400** |

> **「内核未就绪」为什么是 503 而不是 400**
> ----------------------------------------
> 内核没就绪是**服务端依赖**的问题，不是请求的问题 ——
> 用户的请求完全正确，只是 Agent 内核还在启动（或崩了正在重启）。
> 报 400 等于告诉前端「你请求写错了」，前端于是把它当用户的错来提示。
>
> 实测后果：内核没起来时首屏会连打 4 个 400
> （`/api/teams`、`/api/workflows` 三条），控制台一片红。
> 现在改成 503，并且 `/api/state` 会报 `kernel_ready`，
> 前端在内核就绪前**不调**那些接口（见 §4.3）。

**v2 目标**：统一成一套 —— 见 §1。

### 0.3 出参

统一走 `domains/_common.py` 的 `json()` → `JSONResponse`，`Content-Type: application/json`。
**但目前仍有直接 `return dict`、约 100 处用 `json()`、100+ 处散落 `raise HTTPException` —— 并未统一。**

**2026-11-26 结论（模块 10）：成功响应不逐域包壳。**
老前端 `core/web/modules/*.js` 直接读裸对象（`/api/state` 的 `s.mode`、
`/api/clipboard` 的 `d.kind`、`/api/prompt/optimize` 的 `d.optimized`），
`core/tests/test_error_contract.py` 也明确钉住「成功保持裸 dict，不包壳」。
所以 §1.1 的 v2 壳只用于**新写的域**；新前端两种形状都吃，边界在
`apps/web/src/lib/apiContracts.ts:unwrap()`（`code`/`message`/`data` 三特征
同时成立才剥，避免误剥「恰好带 data 键」的裸对象）。
`BARE_ENDPOINTS` 列出改动护栏：谁要把其中一条改成 `ok()`，先确认老前端已退役。
错误响应则已由 `middleware/errors.py:error_body()` 统一为
`{code, data, message, detail}`（`detail` 保留给老前端）。

### 0.4 请求体校验现状

| 类型 | 数量 | 现状 |
|---|---|---|
| Pydantic 模型（HTTP 边界） | 100% | 有类型校验 |
| 裸 `dict` 请求体 | **0 处** | 已清零；`test_req_dict_migration.py` 静态守门钉住 |

**v2 目标**：零个裸 `dict` 请求体 —— **2026-11-26 已达成**。

**2026-11-26（模块 10）**：先修 5 条高频 / 高风险路径 —— `/api/paste`、
`/api/terminal/run`、`/api/terminal/interrupt`、`/api/sys/query`、
`POST /api/checkpoints/{id}/rewind` 换成 Pydantic 模型；脏 body（数组 /
字符串）由 **422** 挡在业务层之外，以前 `req.get` 会 `AttributeError` → 500。
空值仍由业务层给中文 400（如 `command 不能为空`），不改成 422，
免得老前端的错误提示路径变样。

**2026-11-26（模块 13）**：剩余 **9 个 HTTP 端点**全量迁移，新增 8 个模型
（`LaunchAppRequest` / `CustomAppRequest` / `SkillCreateRequest` /
`SkillUpdateRequest` / `OpenFileRequest` / `McpToggleRequest` /
`TemplateRunRequest` / `ToolPackImportRequest`）。边界同模块 10：
**body 形状脏 → 422，业务字段空 → 中文 400**，老前端 `e.detail` 路径不变。
`/api/tool-packs/import` 的 body 是清单本体，用 `RootModel[dict[str, Any]]`
保证最外层必须是对象；`/api/workflows/templates/{id}/run` 的 body 仍可整个省略。
`persona` 的 5 个内部纯函数（路由层早有 `extra="forbid"` 模型保护）参数
改名 `data: dict[str, Any]`，不再冒充请求体；其余内部裸 `dict` 注解精确化。
`core/tests/test_req_dict_migration.py` 断言：域层零裸 dict 参数注解、
`core/oryx/` 运行时源码零裸 dict 字面、9 个端点 OpenAPI `$ref` 都指向本轮模型。
测试 / 文档 / `apps/desktop/target/release` 打包副本里的历史描述不在守门范围。

---

## 1. v2 目标形态（重写时按这个来）

### 1.1 统一响应体

```python
class Response(BaseModel, Generic[T]):
    code: int = 0        # 0 = 成功；非 0 = 业务错误码
    data: T | None = None
    message: str = ""
```

- HTTP 状态码仍按语义给（400/403/404/409/422/503/500），**不靠 body 里的 code 表达错误**
- `data` 恒为该接口的**领域对象**。具体形状分三种：

  | 领域对象是什么 | `data` 的形状 | 例子 |
  |---|---|---|
  | 一个列表 | **数组** | `GET /api/sessions` → `Session[]`（**不是** `{"sessions": [...]}`） |
  | 一个带上下文的对象 | **对象** | `GET /api/sessions/{id}` → `{task, title, messages, steps, todos, usage}` |
  | 一屏聚合视图 | **对象**（视图内的列表字段用复数命名） | `GET /api/overview/runs` → `{runs, total, active, today, window}` |

- 多包一层没有信息量，前端却要为此多写一次 `.sessions` —— 不包。
- 分页统一：`{"items": [...], "total": int, "limit": int, "offset": int}`
  （`total` 是**全量**条数，不是本页条数）

### 1.2 命名规范

```
/api/{域}/{资源}[/{id}][/{子资源}[/{id}]]
```

- 域用**复数名词**（`sessions` `skills` `experts` `teams` `workflows` `providers` `extensions`）
- 动词只出现在**动作型**子路径：`/enable` `/disable` `/rewind` `/cancel` `/activate` `/restore`
- **静态路径必须注册在同方法的参数化路径之前**（`/workflows/templates` 早于 `/workflows/{run_id}`）
- 查询参数用 `limit` / `offset`，不用 `limit`+`count` 混搭

### 1.3 错误码

| 语义 | HTTP | v2 的 `code` |
|---|---|---|
| 参数不合法 | 400 | 400 |
| 未鉴权 | 401 | 401 |
| 无权限 / 越界 | 403 | 403 |
| 资源不存在 | 404 | 404 |
| 状态冲突（重复/在跑） | 409 | 409 |
| 参数模式不匹配 | 422 | 422 |
| 依赖未就绪（内核没起来） | 503 | 503 |
| 服务端异常 | 500 | 500 |

---

## 2. 接口清单（按域）

> 「前端用」列：**是** = `core/web/modules/*.js` 里有路径字面量；**否** = 后端有、前端零调用。
> 「A」= AI 生成代码的当前实现（B = 另一 subagent 采集）。

### 2.0 account 域（`domains/account.py`，prefix `/api/auth`）

| 方法 | 路径 | 入参 | 出参 `data` | 错误 |
|---|---|---|---|---|
| GET | `/api/auth/status` | — | `{account_count, registration_open}` | — |
| POST | `/api/auth/register` | `{username, password}` | `{id, username}`；设置 `oryx_session` | 409 重名；422 字段格式错误 |
| POST | `/api/auth/login` | `LoginCredentials{username, password}`（**只做形状校验**） | `{id, username}`；设置 `oryx_session` | **401 账号或密码不正确**（含短密码、账号不存在、账号名格式不对）；422 body 形状错误 |
| GET | `/api/auth/me` | — | `{id, username, created_at}` | 401 未登录 |
| POST | `/api/auth/logout` | — | `{logged_out}`；撤销当前会话 | 401 未登录 |
| POST | `/api/auth/password` | `PasswordChangeRequest{old_password, new_password}` | `{changed, revoked_sessions}`；**保留当前会话** | 401 未登录；**400**「原密码不正确」/「新密码不能与原密码相同」；422 字段形状错误或新密码长度不在 10–256 位 |

账号名为 3–32 位字母、数字、下划线或短横线；密码至少 10 位。注册当前保持开放，
没有租户、角色或资源范围。个人工具的归属规则在工具管理模块接入时继续落地。

**登录与注册用不同的请求模型（2026-11-26，模块 12）**：以前 `/api/auth/login`
复用注册的 `Credentials`，密码输短了直接 422「密码至少需要 10 位」——
既答非所问，又把注册规则泄露给正在试密码的人。现在登录只有「对 / 不对」
两种结果，账号名只 trim、不校验格式，账号不存在 / 密码不匹配一律 **401**；
只有 body 形状脏（数组、缺字段、非字符串）才由 Pydantic 挡成 422。
注册仍按上面的规则做强校验，不受影响。

**改密码的会话语义**：成功后按 `user_id` 删除**其它**全部 `login_sessions`
记录，`revoked_sessions` 是实际注销条数；发起请求的那个会话保留
（token 是随机值，与密码无关），用户不会一改完就被踢回登录页。
原密码错误刻意用 **400** 而不是 401 —— 401 在前端是「登录态失效」的信号，
虽然 `shouldHandleUnauthorized()` 会挡掉 `/api/auth/*`，但语义上这是表单填错，
不该复用认证态错误码。改密码不影响其它账号的会话，也不改它们的口令。
本轮仍**不做角色体系**，账号之间保持「创建者可改、他人只读」两档。

### 2.1 system 域（`domains/system.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/prewarm` | — | `warming` `pending` `port` | 500 | **否** |
| GET | `/api/state` | — | `mode` `interaction` `style` `styles[]` `expert` `experts[]` `provider` `model` `provider_is_local` `busy` `skills[]` **`kernel_ready`** | 内部异常降级为空字段 + 发 `agent.error`；未预期 → 500 | 是 |

> **`kernel_ready`**：Agent 内核起没起来（true/false）。
> 内核没就绪时，走 RPC 的接口（团队、工作流…）会返回 **503**；
> 前端靠这个字段决定**先别调**那些接口 —— 否则首屏控制台就是一排 503，
> 用户以为页面坏了，实际只是内核还在启动。
> 详见 §0.2。
| GET | `/api/audit/{task_id}` | path: `task_id: str` | `task_id` `audit[]`（键 `id ts task_id step_id tool args risk decision outcome mutated`） | **404「没有这个会话」**（先校验归属，再查审计；无记录时是空数组）；500 | 是 |
| GET | `/api/usage` | q: `hours: int=720`（**clamp 到 1–8760**） | `window_hours`（回显 clamp 后的值）`total{calls,input_tokens,output_tokens}` `today{…}` `by_model[]{provider,model,calls,inp,outp}` | 500；`hours` 非整数 → 422 | 是 |
| GET | `/api/tasks` | — | `tasks[]`（键 `id goal mode provider status created_at updated_at title`） | 500 | **否** |
| GET | `/api/capabilities` | — | `total` `groups[]{group,tools[]{name,description,risk,risk_label},count}` `runtime_tools[]` `agents[]` | 409/404/400 | 是 |
| GET | `/api/sys/switches` | — | `power{label,items[]{name,label,action}}` `display{…}` `input{…}` | 500 | 是 |
| POST | `/api/sys/query` | `SysQueryRequest{name}` | `ok` `content` | **400**「不支持或需要审批的系统设置：{name}」／**500**「读取失败：{exc}」／422 body 形状错误 | 是 |
| GET | `/api/doctor` | — | `version` `python` `platform` `mode` `network_note` `destructive_ops` `providers{}` `active_provider` `local_provider` `dependencies{}` `data{}` `checks[]{name,ok,detail}` `hints[]` | 500 | 是 |
| POST | `/api/prompt/optimize` | body: `OptimizeRequest{text: str 必填, skills: str="", expert: str="", style: str=""}` | `original` `optimized` `notes[]` `critique[]{id,advice}` | 409/404/400；422 | 是 |
| GET | `/api/fs/list` | q: `path: str` 必填, `show_hidden: int=0`, `limit: int=500`, `offset: int=0` | `path` `parent` `entries[]{name,path,is_dir,size,mtime,ext,hidden}` `count` `total` `truncated` `offset` `limit` | 400／403／404（`localfs.BrowseError`）；422 | 是 |

> **日志监控的边界（模块 9）**：新前端「系统」页消费 `/api/usage`（用量）、
> `/api/doctor`（环境自检）、`/api/audit/{task_id}`（审计明细，导出文件由
> **前端本地生成**，不新增后端路由）。**「请求日志」没有进界面** —— 后端只有
> `middleware/errors.py` 里一行 Python logging（方法 / 路径 / 状态码 / 耗时 /
> 请求 id 写到服务端日志），没有持久化也没有查询接口；要开放需另做采集与存储，
> 属待确认问题。`/api/usage` 的 `today` 是**过去 24 小时**、`total` 是
> `window_hours` 小时，都不是自然日 / 自然月，界面必须照实标注。

### 2.2 preference 域（`domains/preference.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/interaction` | body: `InteractionRequest{interaction: str}` 必填 | `interaction` `cycle[]` | **400**「interaction 只能是 ask / plan / craft / bypass / dontAsk / auto / delegate」；**400**「开发者模式已被禁用」；422 | 是 |
| POST | `/api/style` | body: `StyleRequest{style: str}` 必填 | `style` | **400**「未知的风格」；422 | 是 |
| POST | `/api/mode` | body: `ModeRequest{mode: str}` 必填 | `mode` `provider` `model`（未配模型服务时后两者为 `null`） | **400**「mode 只能是 efficiency 或 local」；422；模型服务未配置时**降级为 200**（模式已保存，不报 500 造成「切换失败」假状态） | 是 |
| GET | `/api/preferences/catalog` | — | **v2 统一响应** `data{interactions[] styles[] compute_modes[]}` | — | 是 |

> **`GET /api/preferences/catalog`**：三份偏好目录的唯一数据源，前端不硬编码选项。
> `interactions[]` 每项 `{name, label, hint, read_only, skip_approval, unattended, coordination_only}`
> （7 项，`label` 与能力标记分别取自 `InteractionMode.label` / `ModePolicy`，不把系统提示词发给前端）；
> `styles[]` 每项 `{name, label, hint}`；`compute_modes[]` 每项 `{name, label, hint}`（仅 `efficiency` / `local`）。
> **`mode` 是全局模型路由**：`/api/chat` 单次传 `mode` 只会记录进会话快照，
> 不会换 provider；真正切换要走 `POST /api/mode`（它会同步 `active_provider`）。

### 2.3 appearance 域（`domains/appearance.py`，prefix `/api/appearance`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/appearance` | — | `theme` `background`（磁盘路径）`background_exists` `background_fit` | 500 | 是 |
| POST | `/api/appearance/background-fit` | body: `BackgroundFitRequest{fit: str="cover"}` | `background_fit` | **400**「fit 只能是 cover / contain / tile / center」；422 | 是 |
| POST | `/api/appearance/theme` | body: `ThemeRequest{theme: str="light"}` | `theme` | **400**「theme 只能是 light 或 dark」；422 | 是 |
| POST | `/api/appearance/background` | body: `BackgroundRequest{data_url: str=""}` | 保存：`path` `bytes`；清空：`background:""` `cleared:true` | **400** ×4（格式/扩展名/损坏/超 12MB）；422 | 是 |
| GET | `/api/appearance/background.png` | 令牌走 `api/auth.resolve_token_candidate`：cookie / 请求头 / `?t=` 都认（CSS `url()` 同源子资源会自动带 HttpOnly cookie，不必再拼 `?t=`） | **二进制** `FileResponse`（png/jpeg/webp/gif/bmp） | **401**「缺少访问令牌」；**404**「没有设置背景」 | 是 |

### 2.4 session 域（`domains/session.py`，prefix `/api/sessions`）

> **v2 样板域** —— 第一个按新契约改的域。
> 契约层（pydantic）+ 统一响应 + 前端接入三件事都在这里跑通了，
> 其余 21 个域照这个改。
> 契约测试：`core/tests/test_session_contract.py`（20 条）。

| 方法 | 路径 | 入参 | `data` | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/sessions` | body: `CreateSessionRequest{title: str=""}`（**整个 body 可省**） | **对象** `{task_id, title, status:'idle'}` | **400**「会话名太长了（最多 60 字）」；422 | 是 |
| GET | `/api/sessions` | q: `limit: int=50`（上限 200）, `offset: int=0`, `q: str\|null=null` | **数组** `Session[]`（键 `id goal title status mode provider created_at updated_at msg_count step_count todo_open renamed`） | — | 是 |
| GET | `/api/sessions/{task_id}` | path: `task_id: str` | **对象** `{task, title, messages[], steps[], todos[], usage{calls,input_tokens,output_tokens}}` | 404「没有这个会话」 | 是 |
| DELETE | `/api/sessions/{task_id}` | path | **对象** `{deleted}` | 404「没有这个会话」；**连检查点一起删**（见下） | 是 |
| PUT | `/api/sessions/{task_id}` | body: `RenameRequest{title: str\|null=null, team: str\|null=null}` | **对象** `{task_id, title, team, renamed}`（**改完之后**的实际值） | 404「没有这个会话」；422 | 是 |
| POST | `/api/sessions/{task_id}/todos` | body: `TodoCreateRequest{title: str="", status: str="pending"}` | **对象** `{id, title, status, seq}` | **400**「待办得有个标题」/「标题太长了」/「status 只能是…」；**404**「没有这个会话」；422 | 是 |
| PUT | `/api/sessions/{task_id}/todos/{todo_id}` | path ×2；body: `TodoStatusRequest{status: str}` | **对象** `{updated, todo}` | **400**「status 只能是 pending / in_progress / done」；404「没有这个待办」；422（缺 status / 类型不对） | 是 |
| GET | `/api/sessions/{task_id}/steps` | path | **数组** `Step[]` | — | **否** |
| GET | `/api/sessions/{task_id}/todos` | path | **数组** `Todo[]` | — | **否** |
| GET | `/api/sessions/{task_id}/approvals` | path | **数组** `PendingApproval[]`（`step_id task_id tool plan{r tool summary reversible steps[] actions[] findings[]} risk started_at`） | 404「没有这个会话」；内核未就绪时 `[]` | 是 |
| GET | `/api/sessions/{task_id}/usage` | path | **对象** `{task_id, usage{}, steps:int, todos:int, messages:int}` | 404 | **否** |

**`Step` 字段**（详情里的 `steps[]` 与 `/steps` 同源，`store.steps_for_task()` 直接 `SELECT *`）：

| 字段 | 形态 | 说明 |
|---|---|---|
| `id` `task_id` `seq` | str / str / int | 步骤 id、所属会话、顺序 |
| `agent` | str\|null | **当前恒为 null** —— `create_step()` 不写这列；子 Agent 看会话级 `task.agent_id` |
| `tool` `state` `risk` | str / str / int\|null | `state` 取 `running` / `done` / `failed` / `denied`；`risk` 0-3 对应后端 `policy.Risk` |
| `args` | **JSON 字符串**\|null | `json.dumps(工具参数)`，展示前需 `JSON.parse` 还原 |
| `result` | **JSON 字符串**\|null | `json.dumps(工具结果 content)`，成功时有值 |
| `error` | 纯文本\|null | 失败 / 拒绝原因，**没有**经过 json.dumps |
| `started_at` `ended_at` | float\|null | 秒级时间戳，耗时由展示侧计算 |
| `error_class` `artifact_path` `mutated` | str / str / int\|null | 「步骤详情」模块补的落库字段：失败分类 E0-E5、产物路径、是否改动（0/1）。老库由 `Store._ensure_columns` 幂等补列；旧调用点写入的行这三列为 null |

`args` / `result` 的「JSON 字符串」是历史约定，展示侧必须容错
（解析失败按纯文本显示），见 `apps/web/src/features/sessions/stepFormat.ts`。
契约测试：`core/tests/test_step_details.py`（5 条）。

**新建空会话（2026-11-26）**：`POST /api/sessions` 建一条还没发过第一句话的会话，
`status='idle'`（**不是** `running` —— 内核续聊时自己会置成 running，预先置位会让界面
误以为「正在运行」而禁止发送），`config_snapshot` 留空，等首次 `/api/chat` 才固化配置。
`goal` 为空且未改名时列表标题兜底为「新会话」。前端状态文案要认得 `idle`（未开始）。

**列表的分页与搜索（2026-11-26）**：`offset` 配合 `limit` 翻页，`q` 在 **SQL 里**
过滤全部会话（`instr(lower(title|goal), lower(q))`，不是先 LIMIT 再让前端筛 —— 否则
老会话永远搜不到）。用 `instr` 而非 `LIKE`：用户搜 `%` / `_` 是字面量，不能被当通配符。
`data` 仍是数组，v2 形状不变；是否还有下一页由「本页条数 == limit」判断。

**删除会话会连检查点一起清（2026-11-26）**：`checkpoint_files` → `checkpoints` →
`messages / steps / audit / todos / tasks`，以及 metrics 库的 `usage`。
此前漏了前两张表，删完会话后孤儿检查点仍能被 `/api/checkpoints/{id}/rewind` 命中，
对着已删会话还原文件、裁剪消息。现在删完再回退会得到 404「没有这个检查点」。

**改名语义**：`title` 传空串**或**不传 = 撤掉自定义名字（退回用首条提问当标题）。
这是 PUT 的自然语义（「把标题换成这个」），所以 `title` 有默认值而不是必填。

**契约层做了什么**
- `null` 归一成空串 —— 以前 `str(req.get("title"))` 会把字面量 `"None"` 存成会话名
- 类型收紧 —— `{"title": 123}` 现在是 422，不是静默存成 `"123"`
- 状态值仍在业务层校验（400 + 中文提示）—— 用 `Literal` 会变成 422，
  错误体里只剩字段路径和英文校验消息，用户看到的是机器话

### 2.5 chat 域（`domains/chat.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/chat` | body: `ChatRequest{goal: str 必填, mode?, interaction?, style?, expert?, agent_id?, session_id?, seq: int=0}` | `{status,resumed,task_id}`；运行配置快照写入 task | **400**「goal 不能为空」；**409** 会话配置已固定；**422** 未知的 `interaction` / `style` / `mode`（大小写不敏感，合法值规范化成 canonical 名；空串按未设置处理）；404/400/503 | 是 |

> **单次运行的偏好覆盖**：`interaction` / `style` 只为这一次运行生效，
> 与 `agent_id` / `expert` 一起固化进 `task.config_snapshot.runtime`；
> **续聊（`session_id` 指向已有快照的会话）会忽略本次覆盖**，
> 保证同一会话前后轮规则一致。`mode` 只记录到快照，不换 provider（见 §2.2）。
> 三个字段都做了枚举校验 —— 权限相关入参拼错不能静默回退成 `craft`。
| POST | `/api/chat/retry` | body: `RetryRequest{task_id: str}` 必填 | `{status,resumed,task_id,ok:true,removed:int}`；复用 task 快照 | **400**「task_id 不能为空」；**500**「准备重跑失败」；**404** 内核 reason；409/404/400 | 是 |
| POST | `/api/chat/cancel` | body: `CancelRequest?{seq: int=0, task_id?: str}`（整个 body 可省）；传 `task_id` 时只停止该会话且校验账号归属 | `ok` `task_id` `pending?` | **409** 当前没有匹配的运行；**404** 会话不存在或不属于当前账号；400 | 是 |

> **2026-11-26（模块 11）**：中止信号改成**每个运行实例一份**
> （`core/oryx/loop.py` 的 `_RunAbort` / `_RunSlot`），
> 一次停止只停自己那一轮 —— 以前中止标志是全局共享的，
> 两轮并发时停止 A 会把 B 一起停掉（实测复现）。
> 传 `task_id` 时按 `(task_id, owner_id)` 精确定位运行实例；
> **不传 `task_id`** 会退化成「按 dispatch 跟踪器猜当前那一个」
> （老前端按 `seq` 停的路径），并发下可能停错 —— 新前端一律带 `task_id`。
| GET | `/api/clipboard` | — | `kind`(files/image/text/empty) + 对应字段 | 读失败降级为 `kind:"empty"` | 是 |
| POST | `/api/paste` | `PasteRequest{kind,data_url,text,name}` | `ok:true` `item{path,name,size,kind,mime}` | **400** ×4；422 body 形状错误 | 是 |
| POST | `/api/approve` | body: `ApprovalRequest{step_id: str, approved: bool}` 必填 | `step_id` `approved` | 404「该步骤不在等待确认状态」；409/404/400 | 是 |

### 2.6 persona 域（`domains/persona.py`，prefix `/api`）

> id 校验规则：`[A-Za-z0-9_\-一-鿿 ]{1,64}`

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/persona` | — | `expert` `persona` `available[]` `style` | — | 是 |
| POST | `/api/expert` | body: `ExpertRequest{expert: str=""}` | `expert` | 404「没有这个专家」；409/404/400 | 是 |
| GET | `/api/experts` | — | **数组** `Expert[]`，含 `body,can_edit,shared` | 503 内核未就绪 | 是 |
| GET | `/api/experts/{expert_id}` | path | `{id,name,description,scenarios,tags,personality,speaking_style,use_cases,self_intro,body,can_edit,shared}` | 404；**400** id 非法 | 是 |
| POST | `/api/experts` | `ExpertPayload{id,name,description,scenarios[],tags[],personality,speaking_style,use_cases,self_intro,body}` | `{id,created}` | 400 必填/非法 id；409 重名；422 字段校验 | 是 |
| PUT | `/api/experts/{expert_id}` | `ExpertPayload` 部分字段 | `{id,updated}` | 403 非创建者；404；422 | 是 |
| DELETE | `/api/experts/{expert_id}` | path | `{id,deleted}` | 403 非创建者；404 | 是 |
| GET | `/api/agents` | — | **数组** `Agent[]`，含 `body,can_edit,shared` | 503 内核未就绪 | 是 |
| GET | `/api/agents/catalog` | — | **对象** `tools[]{name,group,description,risk_label}` `skills[]{name,description}` `models[]{name,model,type}` `permission_modes[]` `kinds[]` `available{tools,skills,models}` | —（内核未就绪逐项降级，不返回 503） | 是 |
| GET | `/api/agents/{agent_id}` | path | `{id,name,description,tools,max_turns,model,permission_mode,skills,disallowed_tools,memory,initial_prompt,body,can_edit,shared}` | 404；**400** id 非法 | 是 |
| POST | `/api/agents` | `AgentPayload{id,name,description,tools[],max_turns,model,permission_mode,skills[],disallowed_tools[],memory,initial_prompt,icon,subtitle,kind,body}` | `{id,created}` | 400 必填/非法 id/未知工具/未知额外禁用工具/未知技能/未知模型服务；409 重名；422 字段校验 | 是 |
| PUT | `/api/agents/{agent_id}` | `AgentPayload` 部分字段 | `{id,updated}` | 403 内置定义或由其他创建者维护；404；400 同 POST；422 | 是 |
| DELETE | `/api/agents/{agent_id}` | path | `{id,deleted}` | 403 同 PUT；404 | 是 |

**候选目录**（`GET /api/agents/catalog`）：`catalog` 是保留字，路由必须注册在
`/api/agents/{agent_id}` 之前。三份清单各自降级 —— 内核未就绪时 `tools` / `skills`
给空数组并把 `available` 对应项置 `false`，界面照常能开；`models` 直接读配置，
永远可用（`model` 字段的取值是**模型服务名**，`inherit` = 跟随主 Agent）。
`permission_modes` / `kinds` 由后端下发，前端不再硬编码枚举。

**写盘前校验**：`tools` / `disallowed_tools` 对工具注册表、`skills` 对技能目录、
`model` 对已配置的模型服务名，四类都在写盘前校验。内核拿不到清单时**只跳过那一项**
（不阻断合法编辑），但模型服务来自配置，这项永远校验。错误文案：
`这些工具不存在：X`、`额外禁用工具里有不存在的：X`、`这些技能不存在：X`、
`模型服务「X」不存在，请先到设置里添加`。

**403 的两种情形**：内置/旧定义没有创建者记录 →
`这是内置定义，没有创建者记录，只能查看；如需修改请新建一个自己的定义`；
别人创建的定义 → `这条定义由其他创建者维护，只有创建者可以修改或删除`。
前端应按 `creator_id` 是否为空区分说明，不能一律写「由其他创建者维护」。

Agent 与专家定义对所有账号共享；创建者可编辑、删除，其他账号只读。早于账号系统的
内置/旧定义没有创建者记录，继续共享只读。`POST /api/chat` 可同时选一个子 Agent 与一个
专家人设；新会话启动时将两份定义正文及运行选项写入 `tasks.config_snapshot`，续聊及手动重试
复用快照。更改定义不会影响已经开始的会话；想改运行配置需新建会话。

### 2.7 team 域（`domains/team.py`，prefix `/api/teams`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/teams` | — | 统一响应；`data.teams[]{id,name,goal,members[],creator_id,shared,can_edit,thread[]}`（最近 12 条） | 503/404/400 | 是（新前端） |
| GET | `/api/teams/{name}` | path | 统一响应；团队对象 + `thread[]`（最近 **50** 条） | 404「没有这个团队」 | 是（新前端） |
| POST | `/api/teams` | `TeamCreateRequest{name,goal,members[1..8]}` | 统一响应；团队对象 | 422；400（成员不存在） | 是（新前端） |
| DELETE | `/api/teams/{name}` | path | 统一响应；`deleted` | 403 非创建者；404 | 是（新前端） |
| PUT | `/api/teams/{name}` | `TeamUpdateRequest{name?,goal?,member?,role?,members?}` | 统一响应；`ok` `team` | 404 团队不存在；403 非创建者；409 改名撞名；400 业务规则；422 字段校验 | 是（新前端） |
| POST | `/api/teams/{name}/members` | `TeamMemberRequest{agent,role}` | 统一响应；`ok` `content` | 403 非创建者；404/400 | 是（新前端） |
| DELETE | `/api/teams/{name}/members/{agent_id}` | path ×2 | 统一响应；`ok` `removed` | 404 团队/成员不存在；403 非创建者；**400**「团队至少要留一个成员」 | 是（新前端） |
| POST | `/api/teams/{name}/message` | `TeamMessageRequest{to,content}` | 统一响应；`ok` `content` | 404/400 | 是（新前端） |
| POST | `/api/teams/{name}/dispatch` | `TeamDispatchRequest{agent}` | 统一响应；`ok` `content` | 404/400/503 | 是（**老前端**，同步） |
| POST | `/api/teams/{name}/dispatch/jobs` | `TeamDispatchRequest{agent}` | 统一响应；`{job_id,team,agent,status,content,error,started_at,finished_at}`，`status=running` | 503 内核未就绪（由作业 `failed` 承载） | 是（新前端） |
| GET | `/api/teams/{name}/dispatch/jobs/{job_id}` | path ×2 | 统一响应；同上，`status=running/done/failed` | **404**「没有这个派发作业」（别人的作业、别的团队、不存在的 id） | 是（新前端） |

团队定义对所有账号共享，创建者可编辑，其他账号只读；团队信箱和消息记录按账号隔离。团队在 SQLite 持久化，重启后恢复。

**整体保存**：`PUT /api/teams/{name}` 带 `members`（1–8 项 `{agent_id,role}`）时走一次
`team.save`，名字 / 目标 / 成员名单**一起替换**，内核先全部校验再一次落地 ——
校验没过内存与 SQLite 都不动，单成员团队换人也合法（旧的先删后加会撞「至少要留一个成员」）。
不带 `members` 时保持旧语义（改名 / 改目标 / 改某一个成员分工）。只校验**新加入**的成员
必须在子 Agent 目录里；已保存但目录里查不到的成员标红保留，不自动清空。
信箱按 `team_id` 关联，改成员、改名都不丢消息。

**状态码**：内核的 `ok=False` 带 `reason`（`not_found`→404 / `forbidden`→403 /
`conflict`→409 / `invalid`→400）；老内核没带时按文案兜底。不再靠
「error 里有没有『创建者』」二分 —— 那会把「至少要留一个成员」压成 404、把重名压成 400。

**派发异步化**：`POST .../dispatch/jobs` 立刻回 `job_id`，后台线程跑 IPC，前端轮询
`GET .../dispatch/jobs/{job_id}` 直到 `status` 变成 `done`（`content` 是成员交回的结论）
或 `failed`（`error` 是原因）。同一个 (团队, 成员, 账号) 已有在途作业时重复提交返回**同一个**
`job_id`，不会真的派两遍活。作业表在控制面内存里，服务重启会丢，但内核派发结束会把结论
`post` 回团队信箱（持久化），刷新页面仍看得到。老的同步 `/dispatch` 保留给
`core/web/modules/models.js`，**不要改它的语义**（它读裸 `content`）。

### 2.8 workflow 域（`domains/workflow.py`，prefix `/api/workflows`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/workflows/run` | body: `WorkflowRunRequest{plan: dict 必填, args?: dict}` | `ok` `content` `run_id` `status` `report_path` | 400/404 | **否**（前端只走模板入口） |
| GET | `/api/workflows` | q: `limit: int=20` | `runs[]` | 404/400 | 是 |
| GET | `/api/workflows/failed-count` | — | `count` | 404/400 | 是 |
| POST | `/api/workflows/prune-failed` | — | `ok:true` `pruned:int` | 404/400 | 是 |
| GET | `/api/workflows/templates` | — | `templates[]` | 404/400 | 是 |
| POST | `/api/workflows/templates` | body: `WorkflowTemplateRequest{id="",name="",description="",plan:dict 必填,args?,created_at?,aliases?}` | `ok:true` `content` | **400**「模板要有名字」；**400** 内核「模板有问题」 | 是 |
| GET | `/api/workflows/templates/{template_id}` | path | `ok:true` `content` | 404 | 是 |
| PUT | `/api/workflows/templates/{template_id}` | body: 同上（**body 的 `id` 被路径参数覆盖**） | `ok:true` `content` | 400 ×2 | 是 |
| DELETE | `/api/workflows/templates/{template_id}` | path | `deleted` | 404 | 是 |
| POST | `/api/workflows/templates/{template_id}/run` | `TemplateRunRequest{args?}`（**body 可整个省略**） | `ok` `content` `run_id` `status` `report_path` | 404/400；422 body 非对象 | 是 |
| GET | `/api/workflows/{run_id}` | path | `ok:true` `content` | 404 | 是 |
| GET | `/api/workflows/{run_id}/plan` | path | `ok:true` `content` | 404 | 是 |
| POST | `/api/workflows/{run_id}/cancel` | path | 内核原始返回 | 404/400 | 是 |
| DELETE | `/api/workflows/{run_id}` | path | `deleted` | 404；**400**「这次工作流还在跑，先中止再删」 | 是 |

### 2.9 checkpoint 域（`domains/checkpoint.py`，prefix `/api/checkpoints`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/checkpoints/{task_id}` | path | `task_id` `current` `checkpoints[]{id,seq,label,created_at,files,msg_count,current}` | 404/400 | 是 |
| POST | `/api/checkpoints/{checkpoint_id}/rewind` | path；`RewindRequest{scope}`（默认 `CodeAndConversation`） | `ok:true` `content{scope,restored[],removed[],skipped[],errors[],messages_removed,steps_removed}` | **400**「scope 只能是 Code / Conversation / CodeAndConversation」；**400**「checkpoint 不能为空」；**400**「这个会话正在运行，先停止或等它结束再回退」（只拦目标会话自己的运行）；404/400；422 body 非对象 | 是 |

### 2.10 skill 域（`domains/skill.py`，prefix `/api/skills`）

> 名称校验：`[A-Za-z0-9_\-一-鿿 ]{1,64}`。**本域不产生 5xx**（内核异常全被吞）。

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/skills` | — | `skills[]{name,description,path}` | — | 是 |
| GET | `/api/skills/{name}` | path | `name` `description` `body` | 404「没有这个技能」 | 是 |
| POST | `/api/skills` | `SkillCreateRequest{name,description,body}`（额外字段忽略） | `created` | **400**「name 不能为空」；**400**「技能名只能含中英文、数字、-、_、空格」；**409**「已存在同名技能」；422 body 形状错误 | 是 |
| PUT | `/api/skills/{name}` | `SkillUpdateRequest{description?,body?}`（**未传字段沿用旧值**，`exclude_unset`） | `updated` | 404；422 body 形状错误 | 是 |
| DELETE | `/api/skills/{name}` | path | `deleted` | 404 | 是 |

### 2.11 schedule 域（`domains/schedule.py`，**无 prefix**，路径写全）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/automations` | — | 统一响应；`data.automations[]{id,name,cron,goal,enabled,next_run,last_run,run_count,retry_count,retrying,timezone}` | 404/400 | 是（新前端） |
| POST | `/api/automations` | `AutomationRequest{name,cron,goal}` | 统一响应；automation 对象 | 422；400（cron 语法错） | 是（新前端） |
| PUT | `/api/automations/{item_id}` | `AutomationUpdateRequest{name="",cron="",goal=""}` | 统一响应；`updated` + automation 对象 | 404；400 | 是（新前端） |
| DELETE | `/api/automations/{item_id}` | path | 统一响应；`deleted` | 404 | 是（新前端） |
| POST | `/api/automations/{item_id}/toggle` | `{enabled:boolean}` | 统一响应；任务对象 | 404 | 是（新前端） |
| GET | `/api/automations/{item_id}/runs?limit=50` | path；limit 1–200 | 统一响应；`data.runs[]` 含状态、产出、错误、尝试次数和时间 | 404/400 | 是（新前端） |
| POST | `/api/automation-runs/{run_id}/retry` | path | 统一响应；`{queued,parent_run_id}` | 404 非本人失败记录 | 是（新前端） |
| POST | `/api/terminal/run` | `TerminalRunRequest{command,cwd}` | `id` `status` `command` `cwd` | **400**「command 不能为空」；422 body 形状错误 | 是 |
| GET | `/api/terminal` | — | `sessions[]{id,command,cwd,started_at,finished_at,exit_code,running,lines[]}` | 404/400 | **否** |
| POST | `/api/terminal/interrupt` | `TerminalInterruptRequest{id}` | `ok` `detail` | 404/400；422 body 形状错误 | 是 |

自动任务按账号私有，调度和界面时间均为北京时间（`Asia/Shanghai`）。失败最多自动重试 3 次，间隔为 1、2、4 分钟；每次尝试均单独进入历史，历史失败项可手动重试。启停和任务定义在 SQLite 持久化，重启后恢复。

**启停幂等与防重复（2026-11-26）**：`POST .../toggle` 传的是**目标态** `enabled`，
内核按值设置，重复提交同一目标态不会出错也不会产生额外历史。前端按
「动作 + 任务 id」上锁，在途时禁用按钮并转圈；删除、手动重试各自独立上锁，
互不误伤。后端本轮未加额外的去重请求（HTTP 无状态，幂等由内核保证）。

### 2.12 app 域（`domains/app.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/install` | body: `InstallRequest{source: str 必填, kinds?: list[str]}` | `installed[]` `skipped[]` `errors[]` `count` `reloaded{skills,experts,agents}` | 源目录不存在 → 进 `errors`（200）；409/404/400 | 是 |
| GET | `/api/apps` | q: `limit: int=200` | `count` `apps[]{name,version,publisher,source,path}` | — | 是 |
| GET | `/api/app/icon` | q: `id: str` 必填 | **二进制** PNG | 404「没有这个应用」；404「取不到图标」 | **否**（前端改用批量接口） |
| GET | `/api/apps/icons` | — | `icons{name: dataURL}` `count` | — | 是 |
| POST | `/api/apps/launch` | `LaunchAppRequest{id}`（**只接受 id，不接受路径**） | `ok:true` `launched` `target` | **400**「id 不能为空」；**400** 内核文案；422 body 形状错误 | 是 |
| GET | `/api/apps/custom` | — | `apps[]{id,name,target,note,created_at}` | — | 是 |
| POST | `/api/apps/custom` | `CustomAppRequest{name,target,note,id?,created_at?}` | `ok:true` `app` | **400** ×3；422 body 形状错误 | 是 |
| PUT | `/api/apps/custom/{app_id}` | path；`CustomAppRequest`（**路径 id 覆盖 body id**） | `ok:true` `app` | **400** ×3；422 body 形状错误 | 是 |
| DELETE | `/api/apps/custom/{app_id}` | path | `deleted` | 404「没有这个应用」 | 是 |

### 2.13 file 域（`domains/file.py`，**三个 router**：`/api/files` `/api/workspace` `/api/doc`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/files/preview` | q: `path: str=""` | **二进制**（按后缀定 MIME；html/svg 追加 CSP 沙箱头） | **400**「path 不能为空」／**404**「文件不存在」／**403** ×2／**400**「只能预览单个文件」／**415**「不支持预览这种格式」／**413**「文件太大」／**500** | 是 |
| POST | `/api/files/open` | `OpenFileRequest{path,reveal:bool=false}` | `ok:true` `path` `reveal` | 400／404／403／**500**「打开失败」；422 body 形状错误 | 是 |
| GET | `/api/files/list` | q: `prefix: str=""` `limit: int=200` | `files[]{path,name,size,mtime}` `count` | **403**；**500**；`allowed_roots` 空时返回 `{"files": []}`（**无 `count` 键**） | **否** |
| GET | `/api/workspace` | — | `workspace` `allowed_roots[]` `forbidden_paths[]` | — | 是 |
| POST | `/api/workspace` | body: `WorkspaceRequest{path: str=""}` | `workspace` `allowed_roots[]` | **400**「目录不存在」／「不是目录」；409/404/400 | 是 |
| GET | `/api/doc` | — | `stats{path,exists,requirements,decisions,changes,todos,bytes}` `content` | 409/404/400 | 是 |

### 2.14 index 域（`domains/index.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/index/status` | — | `files` `known_files` `chunks` `vectors` `embedding_ready` `db_mb` `scanning` `last_scan` | 409/404/400 | 是 |
| POST | `/api/index/scan` | body: `ScanRequest{roots?: list[str]}` | `status` `roots[]` | **409**（扫描中） | 是 |
| POST | `/api/index/search` | body: `SearchRequest{query: str 必填, limit: int=20, mode: str="hybrid"}` | `query` `mode` `hits[]` `count` | 409/404/400；422 | 是 |
| POST | `/api/index/clear` | — | `status` | **409**（扫描中） | 是 |
| GET | `/api/index/libraries` | q: `limit: int=60` | `libraries[]{path,name,file_count,total_bytes,last_mtime,files[]}` | 409/404/400 | 是 |
| GET | `/api/index/library` | q: `path: str` 必填, `limit: int=200` | `files[]{name,path,size,mtime,ext}` | 409/404/400；422 | **否** |
| GET | `/api/drives` | — | `drives[]{name,label,root,kind,total_gb,used_gb,free_gb,percent}` | 500 | 是 |
| GET | `/api/images` | q: `limit: int=400` | `images[]{name,path,dir,ext,mime,bytes,mtime,source}` `counts{chat,common,recent}` `total` `partial` `limit_hits[]` | — | 是 |
| GET | `/api/image/thumb` | q: `path: str` 必填, `w: int=320` | **二进制**（缩略图） | **403**；**404**；**415** | 是 |

### 2.15 extension 域（`domains/extension.py`，**两个 router**：`/api/extensions` `/api/ui-extensions`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/extensions/reload` | — | `reloaded[]` `errors[]` `tools:int` | 409/404/400 | 是 |
| POST | `/api/extensions/{ext_id}/enable` | path | `ok:true` `changed` `id` | 409/404/400 | 是 |
| POST | `/api/extensions/{ext_id}/disable` | path | `ok:true` `changed` `id` | 409/404/400 | 是 |
| POST | `/api/extensions/install` | body: `ExtensionActionRequest{id:""（**未使用**）, source:""}` | `ok:true` `extension{…}` | **400**「安装失败」；409/404/400；422（不传 body） | 是 |
| DELETE | `/api/extensions/{ext_id}` | path | `ok:true` `trashed` `id` | **400**「删除失败」 | 是 |
| GET | `/api/extensions/trash` | — | `ok:true` `items[]` `retention_days` | 409/404/400 | 是 |
| POST | `/api/extensions/trash/restore` | body: `TrashRequest{name:""（部分端点未使用）, kind:"backend"}` | `ok:true` `restored` `id` | **400**「恢复失败」；422 | 是 |
| POST | `/api/extensions/trash/purge` | body: 同上 | `ok:true` `purged` | **400**「删除失败」；422 | 是 |
| POST | `/api/extensions/trash/cleanup` | — | `ok:true` `purged:int` `retention_days` | 409/404/400 | 是 |
| POST | `/api/extensions/trash/purge-all` | body: `TrashRequest`（**只用 `kind`**） | `ok:true` `purged[]` `kind` | 409/404/400；422 | 是 |
| GET | `/api/extensions` | — | 大对象：`ok` `profile` `stats` `digest` `extensions[]` `plugin_tools[]` `dependencies` `tool_report` `tool_table` `ui_extensions[]` `capability_catalog` `user_dir` `trash_stats` | 409/404/400 | 是 |
| DELETE | `/api/ui-extensions/{ext_id}` | path | `ok:true` `trashed` `id` | **400**「删除失败」 | 是 |
| POST | `/api/ui-extensions/install` | body: `ExtensionActionRequest` | `ok:true` `extension` | **400**「安装失败」；422 | 是 |

### 2.16 tools 域（`domains/tools.py`，prefix `/api/tools`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/tools/index` | — | `ready` `count` `dim` `cache` `cached` `error` `model_available` | 409/404/400 | **否** |
| POST | `/api/tools/search` | body: `ToolSearchRequest{query: str="", limit: int=12}` | `query` `count` `tools[]` | 409/404/400；422（不传 body） | 是 |

### 2.16b toolpack 域（`domains/toolpack.py`，prefix `/api/tool-packs`）

**自定义工具包** —— 用户自己定义的「名字 + 描述 + 功能 + 由哪些工具组成」。

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/tool-packs` | — | `packs[]{id,name,description,function,tools[]}` | 409/404/400 | 是 |
| POST | `/api/tool-packs` | body: `ToolPack{id,name,description,function,tools[]}` | `id` `name` `description` `function` `tools[]` | **400**「工具包要有名字」/「一个工具包至少要有一个工具」；**409**「已经有叫 X 的工具包了」；422 | 是 |
| PUT | `/api/tool-packs/{pack_id}` | 同上 | 同上 | **404**「没有这个工具包」；400/409；422 | 是 |
| DELETE | `/api/tool-packs/{pack_id}` | — | `deleted` | **404**「没有这个工具包」 | 是 |
| GET | `/api/tool-packs/{pack_id}/export` | — | 插件清单（`oryx.json` 形状） | **404** | 是 |
| POST | `/api/tool-packs/import` | `ToolPackImportRequest`（`RootModel[dict]`，**body 就是清单本体**，不是 `{manifest:...}` 包装） | 新建的工具包 | **400**「清单里没有名字」/「一个工具都没有」/「这是 kind=X 的插件，不是工具包」；409；422 body 非对象 | 是 |

**要点**
- **每个会话可以挂一个自己的团队**：`tasks.team` 列（用 `_ensure_columns` 幂等补列）。
  团队是全局的，会话是私人的 —— 靠这一列把两者挂起来。
  `PUT /api/sessions/{task_id}` 的 `team` 传 None = 不动它，传空串 = 摘掉。
  改名和挂团队合并在同一个 PUT 里（改的是同一行 `tasks` 的属性）。
  **三种语义共存**（靠「字段有没有出现」区分，不是靠值）：
  `{title:"新名"}` 改名 · `{title:""}` 清空名字 ·
  `{team:"甲组"}` 只挂团队（名字一个字都不动）· `{}` 老约定=清空名字。
- `renamed` 是**老字段，保留着** —— 这个接口以前只管改名，返回体是
  `{renamed, title}`。直接删掉会**静默破坏**读它的调用方（拿到 undefined）。
- 存在配置里（`config.data["tool_packs"]`），跟着应用数据走。
  不落成磁盘插件包：那些是**带实现**的扩展（要写代码），
  这里的工具包只是**对现有工具的分组与说明**。
- `id` 只允许小写字母、数字、下划线、短横线（会进 URL，也当导出文件名用）。
  留空时从名字生成；**全中文的名字用名字的哈希兜底** ——
  否则每个中文名都退化成同一个 `pack`，第二个就撞 id。
- `description` 与 `function` 是给**模型**看的（据此判断该不该用这套工具），
  不是装饰 —— 这两个字段才是这个功能的核心。
- **导出/导入用插件清单格式**（`oryx.json`，与扩展系统同一套）：
  `apiVersion` / `kind: "tool"` / `id` / `version` / `displayName` /
  `description` / `function` / `tools[]`。
  跟 DeepSeek Harness 那一套对齐 —— 工具包不只是配置里的一条记录，
  而是一个**能分享、能装到别的机器上的插件包**：
  存成 `plugins/<包名>/oryx.json` 就能被注册表发现。
- **导出/导入用插件清单格式**（`oryx.json`，与扩展系统同一套）：
  `apiVersion` / `kind: "tool"` / `id` / `version` / `displayName` /
  `description` / `function` / `tools[]`。
  跟 DeepSeek Harness 那一套对齐 —— 工具包不只是配置里的一条记录，
  而是一个**能分享、能装到别的机器上的插件包**：
  存成 `plugins/<包名>/oryx.json` 就能被注册表发现。

### 2.17 mcp 域（`domains/mcp.py`，prefix `/api/mcp`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/mcp` | — | `servers[]{name,type,target,defer_loading,tool_count,enabled,running,live_tools,server_info}` `warnings[]` `errors[]` | 409/404/400 | 是 |
| POST | `/api/mcp/toggle` | `McpToggleRequest{name,enabled:bool=false}` | `ok:true` `content` | **400**「name 不能为空」；409/404/400；422 body 形状错误 | 是 |

### 2.18 memory 域（`domains/memory.py`，prefix `/api/memory`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/memory` | q: `limit: int=50` | `memories[]{id,text,tags,updated_at}` | — | 是 |
| POST | `/api/memory` | `MemoryRequest{text,tags}`（null 在契约层归一成空串） | `id` `text` `deduped:bool` | **400**「text 不能为空」；422 类型错 | 是 |
| DELETE | `/api/memory/{memory_id}` | path | `deleted` | 404「没有这条记忆」 | 是 |

### 2.19 model_config 域（`domains/model_config.py`，prefix **`/api/providers`**）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/providers` | — | `active` `local` `cloud` `items[]{name,type,base_url,model,api_key(打码),has_key,key_storage,capabilities{…},is_local}` | — | 是 |
| GET | `/api/providers/local` | — | `models[]{name,size,family,parameter_size,quantization,provider}` `base_url` | — | 是 |
| POST | `/api/providers/local` | body: `LocalModelRequest{model: str 必填, base_url: str=""}` | `name` `model` `type` `created:bool` | **400**「模型名不能为空」；**400** 内核文案；422 | 是 |
| POST | `/api/providers` | body: `ProviderRequest{name 必填, type="openai", base_url 必填, model 必填, api_key=""}` | `name` `type` | **409**「已经有同名模型服务…」（不静默覆盖）；**400**「名称不能为空 / 不能包含斜杠 / 太长」「接口地址要以 http(s):// 开头」「模型名不能为空」；**400** 内核文案；422 | 是 |
| PUT | `/api/providers/{name}` | path；body: `ProviderEditRequest{type?,base_url?,model?,api_key?}` 全可选 | `name` `type` | 404；**400**「没有要改的内容」；**400** 地址 / 模型名校验；**400** 内核文案 | 是 |
| POST | `/api/providers/{name}/activate` | path | `active_provider` `local_provider` `cloud_provider` | **400**「没有这个 provider：{name}」 | 是 |
| DELETE | `/api/providers/{name}` | path | `deleted` | **400** 内核文案；404 | 是 |

> 激活只写 `active_provider` 与对应槽位（本地 / 云端）；**实际生效的服务由计算模式决定**：效率模式用云端槽位，本地模式用本地槽位。单次 `POST /api/providers/{name}/activate` 不会替你切模式。

### 2.20 remote 域（`domains/remote.py`，prefix `/api/remote`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/remote/start` | body: `RemoteStartRequest{relay_url="",code="",policy:int=0}` | `code` `relay` `policy` `policy_label` `hint` | **400**「启动失败」/内核文案；409/404/400；422 | 是 |
| POST | `/api/remote/stop` | — | `ok` `detail` | 409/404/400 | 是 |
| GET | `/api/remote/status` | — | `running` +（运行时）`code` `relay` `connected` `peer_ready` `fps` `frames_sent` `inputs_applied` `inputs_blocked` `policy` `policy_label` `error` | 409/404/400 | 是 |

### 2.21 ui 域（`domains/ui.py`，prefix `/api/ui`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/ui/base-css` | q: `classes: str=""` | **CSS 文本**（`Response`，非 JSON） | 500 | 是 |
| GET | `/api/ui/core-classes` | — | `classes[]` | 500 | **否** |
| GET | `/api/ui/modules` | — | `extensions[]{id,name,version,description,mount,icon,url,enabled,order,caps,base_css,requires,blocked_reason}` `skipped[]` `disabled[]` `mount_points[]` `cap_rules{}` `cap_notes{}` | 500 | 是 |

### 2.22 feature 域（`domains/feature.py`，prefix `/api/features`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/features` | — | `profile`（str，默认 `personal`）`flags[]`（`feature_catalog()` 的结果，每项 `key description enabled value implemented`）`raw`（`FeatureFlags.snapshot()`） | — | 是 |
| POST | `/api/features/toggle` | body: `FeatureToggleRequest{key: str 必填, value: bool 必填}`，`extra="forbid"` | `key` `value` | **400**「key 不能为空」／**400**「没有这个特性开关：{key}」；**403**「EnableFeatureFlags 已关闭，禁止运行时调整」；**422** 缺 `value` / 多字段 / `value` 不是布尔可解析值；**500**「写开关文件失败：{exc}」 | 是 |

> `value` 走 Pydantic 的标准布尔解析：`"false"` → `false`（以前是
> `bool("false") == true`，想关结果开了）。`key` 的白名单取自
> `feature_catalog()`，不另维护一份；目录外的一律 400，不写盘。
> `implemented=false` 的开关**后端仍允许切换**（与老前端一致），界面禁用并说明
> 「本项目还没有这个能力」。`EnableFeatureFlags` 一旦关掉，本接口即自锁：
> 之后连它自己都不能再从界面打开，只能改 `features.yaml`，界面要如实提示。
>
> 注：本域**没有**用 `rpc_failure`（不调内核），错误直接是 `HTTPException`；
> 返回的是**裸对象**，不是 v2 统一响应。

### 2.23 overview 域（`domains/overview.py`，prefix `/api/overview`）

> **大屏（多 Agent 一屏总览）的服务端真相接口。**
> 大屏此前**一个接口都不调** —— 纯渲染 SSE 攒出来的内存状态，
> 刷新一下页面总览就空了。这三个接口是那个取口。
> 前端仍然 SSE 驱动（实时性靠它），这三个接口负责**冷启动与恢复**：
> SSE 说「现在」，接口说「本来」。

| 方法 | 路径 | 入参 | `data` | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/overview/teams` | — | **对象** `{teams[], team_count, member_count, agents[], agent_count, degraded[]}` | —（内核没起来时**降级**成空列表，不是 503） | 是（新前端） |
| GET | `/api/overview/runs` | q: `limit: int=20`（上限 100） | **对象** `{runs[], window, total, active, today}` | — | 是（新前端） |
| GET | `/api/overview/suggestions` | q: `task_id: str=""` | **对象** `{suggestions[], count, source{task_id,title,status,steps,artifacts[],artifact_count}}` | — | 是（新前端） |

**几个刻意的决定**

- `total` / `active` / `today` 走**库里的聚合查询**，不是前端从列表数出来的 ——
  列表限量 20，数出来是「最近 20 个里有几个」，不是「一共跑了多少」。
- 内核没起来时 `teams` 降级成空列表并把原因写进 `degraded` ——
  大屏是锦上添花的视图，它挂了不该拖垮整屏。
- 建议走**确定性规则**（按产物后缀查表），不调模型 ——
  建议必须可执行、路径明确、可回归；模型容易编出不存在的文件名。
- `suggestions[]` 每条是 `{label, kind, path, prompt | action}`：`prompt` 型发起新一轮
  对话，`action=preview` 型由前端直接打开预览。**两类都必须给可点按钮**（2026-11-26
  模块 8 修复：此前只有 `action` 型显示按钮且没绑事件，而多数建议是 `prompt` 型）。
  新前端的 `prompt` 型只预填到「新建运行」页、由用户确认后提交，不自动发起；
  `preview` 走 `GET /api/files/preview?path=`。总览另外订阅 SSE，
  `RUN_*` / `step.*` / `team.*` / `knowledge.document` / `resync` 触发 400ms 节流重拉。

---

### 2.24 server.py 自身路由

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/` | — | **HTML**（老前端）+ `Set-Cookie: oryx_token=…`（HttpOnly / SameSite=strict / Path=/） | 500 | 是（浏览器入口） |
| GET | `/newui` | — | **HTML**（新前端 React 19）+ 同样的 `Set-Cookie` | 404（新界面没构建）／500 | 是（新界面入口） |
| GET | `/web/{path:path}` | path | **静态资源**（`no-store` + `nosniff`） | 400／403／404／415／413／500 | 是 |
| GET | `/web/ui-extensions/{path:path}` | path | **静态资源** | 同上 | 是（间接：URL 由 `/api/ui/modules` 下发） |
| GET | `/api/ping` | — | `{"ok": true, "service": "oryx"}` | — | **否**（桌面壳 `apps/desktop/dist/index.html` 与 Rust 侧 `probe_port()` 都在用，且都校验 `service` 身份；端口被其它服务占用时不再静默连错） |
| GET | `/api/events` | q: `since: int=-1` | **SSE**：`data: {json}\n\n` | 401／403 | 是 |

**新界面（`/newui`）**
- 与 `/` **并存**：`/` 是老前端（原生 ES Module，已上线），`/newui` 是新前端（React 19 + TS + Tailwind v4），正在逐域替换老前端。
- 静态资源在 `/web/newui/`（构建时 `base` 就是这么定的，见 `apps/web/vite.config.ts`）。
- **在这里下发 cookie**：新前端的 API 调用要带 HttpOnly cookie（令牌不在 JS 里），cookie 由服务端在响应里下发 —— 打开这个地址本身就得把 cookie 带上，否则页面能渲染但每个 API 都 401。

**SSE 细节**（`server.py:457`）：
- 事件对象：`{seq:int, trace_id:str, type:str, ts:float, …业务字段}` —— `seq` 由 **server 侧** EventBus 重新分配
- 控制帧：`ready`（连接后必发）、`resync`（`?since=` 已被环形缓冲滚过 → 前端整页刷新）
- 心跳：15 秒一次 `: ping\n\n`
- 鉴权：走全局中间件（cookie / 头 / `?t=` 三选一）；**GET 是安全方法，不需要 `X-Oryx-Request`**
- 账号隔离：`task_id` 归属当前账号的事件才下发；文本与思考事件都带 `task_id`

**流式正文 / 思考过程事件**（`events.py` 定义，`loop.py` 发布；新前端
`apps/web/src/features/sessions/useStreamText.ts` 消费，契约由
`core/tests/test_stream_events.py` 守着）：

| 事件 | 必带字段 | 语义 |
|---|---|---|
| `TEXT_MESSAGE_START` | `task_id`, `message_id`, `role="assistant"` | 开一条助手消息气泡 |
| `TEXT_MESSAGE_CONTENT` | `task_id`, `message_id`, `delta:str` | 追加一段正文；全部 delta 顺序拼起来 == 随后落库的 `content` |
| `TEXT_MESSAGE_END` | `task_id`, `message_id` | 正文写完 |
| `REASONING_START` | `task_id` | 开始一段思考（独立于正文的通道） |
| `REASONING_CONTENT` | `task_id`, `delta:str` | 追加思考文本 |
| `REASONING_END` | `task_id` | 思考收尾 |

消费方必须知道的三个边界：

- `message_id` 在 `loop.py` 的 `_run_loop` **循环外**生成，一轮里多次模型调用（工具调用循环）复用同一个值，但每轮各发一组 START/CONTENT/END、各落库一条消息。因此**不能把 `message_id` 当唯一键**；前端按「每次 START 新建一条草稿」处理。
- `TEXT_MESSAGE_END` **早于** `store.add_message`，END 时立刻拉详情会拿不到新消息。前端在 END 后延迟刷新，并按「内容相等」把流式草稿认领成正式消息，避免同一段文字显示两条。
- `TEXT_MESSAGE_CONTENT` 也进环形缓冲（`?since=` 可补发断线期间的正文）。一次长回复会占用较多缓冲位；缓冲滚过前端水位时发 `resync`，此时草稿作废、整页重拿。

**步骤事件**（`loop.py` 发布；新前端 `SessionDetail.tsx` + `StepList.tsx` 消费）：

| 事件 | 主要字段 | 语义 |
|---|---|---|
| `step.created` | `task_id`, `step_id`, `step_seq`, `tool`, `args`, `risk`, `risk_label` | 工具调用已落库为 `running` |
| `step.running` | `task_id`, `step_id`, `tool` | 开始执行 |
| `step.awaiting_approval` | `task_id`, `step_id`, `tool`, `plan`, `risk`, `risk_label` | 等人工确认（审批卡） |
| `step.approval_resolved` | `task_id`, `step_id`, `approved` | 已批准 / 拒绝，各端撤卡 |
| `step.done` | `task_id`, `step_id`, `tool`, `result`, `elapsed_ms`, `args`, `mutated` + 只读自证四字段，可选 `artifact_path` | 成功收尾 |
| `step.failed` | 同 `step.done` + `error`, `error_class` | 失败收尾 |
| `step.denied` | `task_id`, `step_id`, `tool`, `reason` | 安全策略 / 模式闸门拒绝 |
| `step.autoclass` | `task_id`, `step_id`, `tool`, `verdict`, `reason` | 自动模式判定（`allow` / `ask` / `deny`） |

消费边界：

- 落库的 `state` / `args` / `result` / `error` **一律以详情接口为准**；事件只负责触发刷新，`?since=` 补发的旧事件不能把已完成的步骤改回「执行中」。
- `error_class` / `artifact_path` / `mutated` 现在同时落库（`loop.py:3094` 随 `finish_step()` 写入），刷新前后一致；事件里的 `elapsed_ms`、`risk_label`、`reason`、`verdict` 只作实时补充，前端按 `step_id` 临时保存，刷新后回退到落库值。

---

## 3. 体检中发现的缺陷（重写时必须处理）

| # | 缺陷 | 位置 | 状态 |
|---|---|---|---|
| 1 | `GET /api/drives` → **500**（`JSONResponse` 未导入 + `json()` 双重包装） | `domains/index.py:99,148` | ✅ **已修**（实测 200） |
| 2 | `GET /api/image/thumb` → **500**（`StreamingResponse` 未导入）→ **图片网格每张缩略图都 500** | `domains/index.py:224` | ✅ **已修**（实测 200 image/png） |
| 3 | `GET /api/appearance/background.png` 前端引用**未带 `?t=`**，路由要求它 → 401 | `web/modules/theme.js:35`、`models.js:5345` | ✅ **已修**：根因在后端自己又校验了一遍令牌；`domains/appearance.py:90` 改用 `resolve_token_candidate` 认同源 cookie（2026-11-26 复核，规格 §2.3 同步更正） |
| 4 | `POST /api/memory` 传 `{"text": null}` → 绕过空值校验，把字符串 `"None"` 写库 | `domains/memory.py:47` | ✅ **已修**：`MemoryRequest` 在契约层把 null 归一成空串 → 400「text 不能为空」（2026-11-26 复核，`test_settings_ops.py` 有回归） |
| 5 | `POST /api/workspace` 传 `path=""` → 静默切到服务进程 cwd | `domains/file.py` | ⬜ 待确认是否算 bug |
| 6 | `POST /api/prompt/optimize` 传 `text=""` → 出参退化成 **JSON 字符串**而非对象（前端读 `d.optimized` 会拿到 undefined） | `domains/system.py` | ✅ **已修**（2026-11-26，模块 10）：新增 `optimize_payload()`，两条路径都恒为对象；成功仍保留 `optimized / notes / original / critique`，老前端读法不变 |
| 7 | `schedule.py:50-51` 的 `except IpcError` 是**死代码**（`_call()` 内部已转 HTTPException） | `domains/schedule.py` | ✅ **已核实**（2026-11-26）：`_call()` 里那处 `except IpcError` 是唯一的映射入口、并非死代码；本轮把它委托给全项目共用的 `map_ipc_error()` |
| 8 | `ExtensionActionRequest.id` / `TrashRequest.name` 在部分端点**未被使用** | `domains/extension.py:38,43` | ✅ **已核实**（2026-11-26）：两个模型被 install / trash 多个端点复用，个别端点不读某字段属正常；字段是老前端统一传参的**兼容位**，保留不动 |
| 9 | **3 个文件带 UTF-8 BOM**（`loop.py` `server.py` `workflow.py`）—— 导入没事，但会坑到静态工具 | — | ✅ **已修**（2026-11-26）：二进制级去掉前 3 字节，其余字节不变；`extract_system_prompt.py` 读的是基准快照，逐字节校验不受影响 |
| 10 | `POST /api/teams/{name}/dispatch` 是**同步 `def`**（其余都是 `async def`），FastAPI 会丢进线程池，子 Agent 跑几分钟就占住一条线程 | `domains/team.py` | ✅ **已处理**：老前端仍用这条同步路由（不能改语义），新前端走 `POST/GET .../dispatch/jobs`（提交即回执 + 后台线程 + 轮询）。同步端点保持 `def` 是有意保留 |
| 11 | `GET /api/files/list` 在 `allowed_roots` 为空时返回 `{"files": []}` —— **缺 `count` 键**，前端按 `count` 读会拿到 undefined | `domains/file.py` | ✅ **已修**（2026-11-26）：空范围也返回 `count: 0`，与正常分支形状一致 |

---

## 4. 待处理清单

### 4.1 后端有、前端零调用的接口（17 条）

| 路径 | 处置建议 |
|---|---|
| `GET /api/app/icon` | **删** —— 前端已改用 `/api/apps/icons` |
| `GET /api/files/list` | **删** —— 与 `/api/fs/list` 功能重叠且前端用后者；但两者实现不同（递归 vs 单层），删前确认 |
| `GET /api/index/library` | **删或接** —— 与 `/api/index/libraries` 不是同一查询（单数是下钻视图） |
| `GET /api/prewarm` | **保留但接线** —— 预热状态，前端可在启动页显示「内核预热中」 |
| `GET /api/sessions/{task_id}/steps` | **保留但接线** —— 会话详情已有 `steps`，可省 |
| `GET /api/sessions/{task_id}/todos` | **保留但接线** —— 同上 |
| `GET /api/sessions/{task_id}/usage` | **保留但接线** —— 用量面板可省 |
| `GET /api/tasks` | **删或接** —— 最近任务列表，前端没有任务页 |
| `GET /api/terminal` | **保留但接线** —— 终端会话列表，前端只用了 run/interrupt |
| `GET /api/tools/index` | **保留但接线** —— 工具索引状态，可在设置页显示 |
| `GET /api/ui/core-classes` | **保留但接线** —— 供界面扩展作者查可用类名 |
| `POST /api/workflows/run` | **保留** —— 模板入口最终落到它 |
| `GET /api/` `GET /api/ping` `GET /openapi.json` `GET /web/*` | **基础设施**，保留 |
| `DELETE /api/extensions/{ext_id}` `DELETE /api/ui-extensions/{ext_id}` | **前端在用**（`models.js:5562`），只是被上面的变量解析误判过 —— 现已确认在用 |
| `POST /api/extensions/{ext_id}/enable` `…/disable` | 同上，确认在用 |

### 4.2 需要你拍板的（**2026-11-19 已拍板**）

| # | 问题 | 决定 | 落地情况 |
|---|---|---|---|
| 1 | `/api/files/list` 删还是接？ | **接**（保留） | 递归清单与 `/api/fs/list`（单层浏览）功能不同，两个都留 |
| 2 | `/api/index/library`（单数）删还是接？ | **接**（保留） | 它是 `/api/index/libraries` 的下钻视图 |
| 3 | 大屏要哪些新接口？ | **加三个** | ✅ `/api/overview/{teams,runs,suggestions}` 已实现（§2.23） |
| 4 | 统一响应体要不要包 `code`/`message`？ | **要**，逐域改造 | ✅ session 域已改（§2.4）；错误体**保留 `detail`** 兼容旧前端 |
| 5 | 旧路径要不要兼容期？ | **不设**（全推倒） | 已改的域直接换契约，前端同步改；未改的域保持原样 |

### 4.3 顺带修掉的两个问题

这一轮是「写接口 + 做样域版 + 修问题」，问题清单里有两处是**实测复现**的：

**① 背景图永远加载不出来（401）**

- **症状**：设了背景图，界面上一片空白。
- **根因不在前端**：是后端 `GET /api/appearance/background.png` **自己**又校验了一遍令牌，
  且只认 `?t=`。浏览器对同源 CSS 子资源**会**自动带 HttpOnly cookie，
  中间件其实已经放行了 —— 被域里那道多余的校验挡了回来。
- **上一轮记的修法是错的**：「前端补上 `?t=`」走不通 ——
  令牌早就不在 JS 里了（`ORYX_TOKEN` 已移除），补不出来。
- **修法**：新增 `core/oryx/api/auth.py`，三个凭据来源（cookie / 头 / `?t=`）的取法统一；
  中间件与域内路由共用，避免「中间件放行、域里又拒」。

**② 「内核未就绪」被当成 400**

- **症状**：内核没起来时首屏连打 4 个 400，控制台一片红，
  用户以为页面坏了 —— 实际只是内核还在启动。
- **根因**：内核没就绪是**服务端依赖**的问题，请求本身完全正确。报 400 等于说「你写错了」。
- **修法**：
  - 四套 IPC 映射规则统一加上「内核未就绪 → **503**」（§0.2）
  - `/api/state` 新增 `kernel_ready` 字段
  - 前端在内核就绪前**不调**那些走 RPC 的接口；
    `refreshState` 每 6 秒跑一次，状态一翻转就自动补上数据（不丢东西）

---

## 5. 重写顺序与进度

按「先地基后业务、每个模块完成即确认」：

| 步骤 | 内容 | 状态 |
|---|---|---|
| 1 | **地基**：依赖清单、未定义名守卫、接口对齐守门工具、统一响应 + 全局异常 + 日志、质量闸门、CI | ✅ 2026-11-19 |
| 2 | **样板域 `session`**：契约层 + 统一响应 + 前端接入，整条链路跑通 | ✅ 2026-11-19 |
| 3 | **大屏三接口**：`/api/overview/{teams,runs,suggestions}` | ✅ 2026-11-19 |
| 4 | 其余 21 个域照样板铺开 | ⚠️ **不做**（2026-11-26 拍板，见下方说明）：成功响应逐域包 v2 壳会砸掉老前端；改为「错误语义统一 + 请求体校验 + 前端请求层加固」 |
| 5 | 老前端整页替换为新前端（React 19 + TS + Tailwind v4） | 🔄 进行中（新外壳已接 session 域 + 大屏；老界面入口已不可达，但 `core/web/modules/*.js` 仍被部分同步路由依赖） |

**为什么第 4 步改成「不做」**（2026-11-26，模块 10 取证）
--------------------------------------------------------
`session` 域是唯一改成 v2 的样板，其余域全部保持裸对象。这不是偷懒：

- 老前端 `core/web/modules/*.js` 里 `/api/state` `/api/clipboard`
  `/api/paste` `/api/approve` `/api/prompt/optimize` `/api/index/clear`
  都是 `await (await api(...)).json()` 后**直接读业务字段**；
- 一旦改成 `{code,data,message}`，这些调用点全部读到 `undefined`；
- `core/tests/test_error_contract.py` 开篇就写明「成功 → 保持原样（裸 dict），
  **不包壳** —— 包了会砸掉 195 个现有调用点」。

所以「统一」的落点换成三件不碰老前端契约的事（模块 10 已交付）：

1. **错误码**：`_common` / workflow / checkpoint / schedule 四套中文子串映射
   收敛成 `_common.map_ipc_error()` 一套，域特有提示词以参数追加；
   错误体早就由 `middleware/errors.py` 统一，且保留 `detail` 兼容老前端。
2. **请求体**：5 条裸 `dict` 请求体换 Pydantic（§0.4），脏 body 由 422 挡住；
   模块 13 已把剩余 9 个端点全量迁完，HTTP 边界零裸 dict 入参。
3. **前端请求层**：`api.ts` 超时 / 全局 401 / 请求 id / 错误文案归一化；
   `App.tsx` 的 `error` 改成顶部横幅，不再顶掉整个主区。
   两种响应形状的唯一边界是 `apiContracts.ts:unwrap()`。

若将来老前端彻底退役，再按 `BARE_ENDPOINTS` 逐域改造，并同步删除该清单。

**样板的形状**（其余域照这个改）

```
core/oryx/domains/session.py
  ├── 请求体：pydantic 模型（不再是 req: dict）
  ├── null 归一：validator 把 None 变成空串
  ├── 响应：api/response.py 的 ok() → {code, data, message}
  └── 契约测试：core/tests/test_session_contract.py

apps/web/src/
  ├── lib/types.ts        —— 类型从后端代码反推
  ├── lib/api.ts          —— 客户端自动剥壳（api.get 直接给领域对象）
  └── features/sessions/  —— api.ts + SessionList.tsx + SessionDetail.tsx
```

## 6. 知识库模块（已接入）

新增接口统一返回 `{code, data, message}`；文件下载是二进制响应。集合和文档按账号归属：
个人集合仅创建者可见；共享集合所有账号可检索，创建者可修改、重建和删除，其他账号只读。
文件名重复会在集合内递增版本号，不覆盖历史版本。

| 方法与路径 | 用途 | 成功数据 |
|---|---|---|
| `GET /api/knowledge/collections` | 列出本人及共享集合 | `{collections: KnowledgeCollection[]}` |
| `POST /api/knowledge/collections` | 创建集合 | `KnowledgeCollection` |
| `PUT /api/knowledge/collections/{id}` | 修改名称、说明或可见范围（创建者） | `KnowledgeCollection` |
| `DELETE /api/knowledge/collections/{id}` | 删除集合及其文档（创建者） | `{deleted: id}` |
| `POST /api/knowledge/collections/{id}/clear` | 清空集合内全部文档与索引，**保留集合本身**（创建者） | `{collection_id, deleted}` |
| `GET /api/knowledge/collections/{id}/documents` | 列出集合文档版本 | `{documents: KnowledgeDocument[]}` |
| `POST /api/knowledge/collections/{id}/documents` | multipart 上传，字段名 `files`，可多文件 | `{accepted, rejected}`；接收后后台解析 |
| `POST /api/knowledge/documents/{id}/rebuild` | 单篇重新解析和建立索引（创建者） | 文档，状态转为 `queued` |
| `DELETE /api/knowledge/documents/{id}` | 删除单个文档版本及索引（创建者） | `{deleted: id}` |
| `POST /api/knowledge/search` | 检索有权访问的个人与共享集合；body 可选 `collection_id` 限定单个集合 | `{query, hits, count, collection_id}` |
| `GET /api/knowledge/documents/{id}/text?offset=&limit=` | 查看解析文本片段及字符偏移 | `{document, text, offset, total_chars, has_more}` |
| `GET /api/knowledge/documents/{id}/source` | 下载原文件，供来源核对 | 文件流 |

文档状态为 `queued / processing / ready / failed`。解析覆盖 TXT、MD、PDF、DOCX、XLSX、PPTX 和图片 OCR；知识库上传与分块不设置应用级文件大小或片段数量上限。重建失败会保留错误说明；服务重启时中断的任务标记为失败，可手动重建。原文件与解析文本保存在应用数据目录的 `knowledge/` 下。

**2026-11-26（模块 8）补充**：

- `POST /api/knowledge/search` 的 `collection_id` 不传 / 空串 / 全空白 = 检索全部可见集合；
  传了 id 会**先校验可见性再过滤**：别人的私有集合、不存在的集合都返回 **404**
  「没有这个知识库集合」。共享集合对其它账号可限定。返回体新增 `collection_id`
  （回显本次范围，空范围回 `null`）。短查询与 FTS 无结果时的兜底模糊匹配改用
  `instr`，`%` / `_` 按**字面量**处理，不再被 `LIKE` 当通配符把全库捞出来。
- `POST /api/knowledge/collections/{id}/clear` 只删文档、分块、FTS 行与原文件，
  **保留集合的名字 / 说明 / 可见范围**（与 `DELETE /collections/{id}` 的区别）。
  返回删掉的文档版本数。集合中有文档正在解析时 **409**
  「集合中有文档正在解析，请稍后再清空」；非创建者 **403**；集合不存在 **404**。
- 文档解析完成 / 失败时推一条 SSE 事件 `knowledge.document`，字段
  `document_id` `collection_id` `status=ready|failed` `_owner_id`。
  服务端按 `_owner_id` 只下发给本人；前端收到即刷新文档列表，低频轮询仅作
  共享集合与断连时的兜底。事件发布失败不影响解析结果落库。
- 本地目录索引的清空走 `POST /api/index/clear`（§2.14，返回裸对象
  `{status}`；扫描中返回 **409**），前端在「本地目录索引」页加了二次确认入口。
