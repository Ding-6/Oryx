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

统一走 `domains/_common.py:33` 的 `json()` → `JSONResponse`，`Content-Type: application/json`。
**但目前 215 处直接 `return dict`、约 100 处用 `json()`、100+ 处散落 `raise HTTPException` —— 并未统一。**

### 0.4 请求体校验现状

| 类型 | 数量 | 现状 |
|---|---|---|
| Pydantic 模型 | 46 处 | 有类型校验 |
| `req: dict` | **46 处** | **零校验** —— 直接 `req.get("key")`，类型错就 500 |

**v2 目标**：零个 `req: dict`。全部 Pydantic 模型，见 §1。

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

### 2.1 system 域（`domains/system.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/prewarm` | — | `warming` `pending` `port` | 500 | **否** |
| GET | `/api/state` | — | `mode` `interaction` `style` `styles[]` `expert` `experts[]` `provider` `model` `provider_is_local` `busy` `skills[]` | 内部异常降级为空字段 + 发 `agent.error`；未预期 → 500 | 是 |
| GET | `/api/audit/{task_id}` | path: `task_id: str` | `task_id` `audit[]`（键 `id ts task_id step_id tool args risk decision outcome mutated`） | 500；查不到 → `audit: []` | 是 |
| GET | `/api/usage` | q: `hours: int=720` | `window_hours` `total{calls,input_tokens,output_tokens}` `today{…}` `by_model[]{provider,model,calls,inp,outp}` | 500 | 是 |
| GET | `/api/tasks` | — | `tasks[]`（键 `id goal mode provider status created_at updated_at title`） | 500 | **否** |
| GET | `/api/capabilities` | — | `total` `groups[]{group,tools[]{name,description,risk,risk_label},count}` `runtime_tools[]` `agents[]` | 409/404/400 | 是 |
| GET | `/api/sys/switches` | — | `power{label,items[]{name,label,action}}` `display{…}` `input{…}` | 500 | 是 |
| POST | `/api/sys/query` | body: `req: dict`，只读 `name` | `ok` `content` | **400**「不支持或需要审批的系统设置：{name}」／**500**「读取失败：{exc}」／422 body 非对象 | 是 |
| GET | `/api/doctor` | — | `version` `python` `platform` `mode` `network_note` `destructive_ops` `providers{}` `active_provider` `local_provider` `dependencies{}` `data{}` `checks[]{name,ok,detail}` `hints[]` | 500 | 是 |
| POST | `/api/prompt/optimize` | body: `OptimizeRequest{text: str 必填, skills: str="", expert: str="", style: str=""}` | `original` `optimized` `notes[]` `critique[]{id,advice}` | 409/404/400；422 | 是 |
| GET | `/api/fs/list` | q: `path: str` 必填, `show_hidden: int=0`, `limit: int=500`, `offset: int=0` | `path` `parent` `entries[]{name,path,is_dir,size,mtime,ext,hidden}` `count` `total` `truncated` `offset` `limit` | 400／403／404（`localfs.BrowseError`）；422 | 是 |

### 2.2 preference 域（`domains/preference.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/interaction` | body: `InteractionRequest{interaction: str}` 必填 | `interaction` `cycle[]` | **400**「interaction 只能是 ask / plan / craft / bypass / auto / delegate」；**400**「开发者模式已被禁用」；422 | 是 |
| POST | `/api/style` | body: `StyleRequest{style: str}` 必填 | `style` | **400**「未知的风格」；422 | 是 |
| POST | `/api/mode` | body: `ModeRequest{mode: str}` 必填 | `mode` `provider` `model` | **400**「mode 只能是 efficiency 或 local」；422；KeyError→500 | 是 |

### 2.3 appearance 域（`domains/appearance.py`，prefix `/api/appearance`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/appearance` | — | `theme` `background`（磁盘路径）`background_exists` `background_fit` | 500 | 是 |
| POST | `/api/appearance/background-fit` | body: `BackgroundFitRequest{fit: str="cover"}` | `background_fit` | **400**「fit 只能是 cover / contain / tile / center」；422 | 是 |
| POST | `/api/appearance/theme` | body: `ThemeRequest{theme: str="light"}` | `theme` | **400**「theme 只能是 light 或 dark」；422 | 是 |
| POST | `/api/appearance/background` | body: `BackgroundRequest{data_url: str=""}` | 保存：`path` `bytes`；清空：`background:""` `cleared:true` | **400** ×4（格式/扩展名/损坏/超 12MB）；422 | 是 |
| GET | `/api/appearance/background.png` | q: **`t`** 必填（CSS url() 带不了自定义头） | **二进制** `FileResponse`（png/jpeg/webp/gif/bmp） | **401**「缺少访问令牌」；**404**「没有设置背景」 | 是 ⚠️ 见 §3 缺陷 3 |

### 2.4 session 域（`domains/session.py`，prefix `/api/sessions`）

> **v2 样板域** —— 第一个按新契约改的域。
> 契约层（pydantic）+ 统一响应 + 前端接入三件事都在这里跑通了，
> 其余 21 个域照这个改。
> 契约测试：`core/tests/test_session_contract.py`（18 条）。

| 方法 | 路径 | 入参 | `data` | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/sessions` | q: `limit: int=50`（上限 200） | **数组** `Session[]`（键 `id goal title status mode provider created_at updated_at msg_count step_count todo_open renamed`） | — | 是 |
| GET | `/api/sessions/{task_id}` | path: `task_id: str` | **对象** `{task, title, messages[], steps[], todos[], usage{calls,input_tokens,output_tokens}}` | 404「没有这个会话」 | 是 |
| DELETE | `/api/sessions/{task_id}` | path | **对象** `{deleted}` | 404「没有这个会话」 | 是 |
| PUT | `/api/sessions/{task_id}` | body: `RenameRequest{title: str=""}`（null 归一成空串） | **对象** `{renamed, title}` | 404「没有这个会话」；422（title 不是字符串） | 是 |
| PUT | `/api/sessions/{task_id}/todos/{todo_id}` | path ×2；body: `TodoStatusRequest{status: str}` | **对象** `{updated, todo}` | **400**「status 只能是 pending / in_progress / done」；404「没有这个待办」；422（缺 status / 类型不对） | 是 |
| GET | `/api/sessions/{task_id}/steps` | path | **数组** `Step[]` | — | **否** |
| GET | `/api/sessions/{task_id}/todos` | path | **数组** `Todo[]` | — | **否** |
| GET | `/api/sessions/{task_id}/usage` | path | **对象** `{task_id, usage{}, steps:int, todos:int, messages:int}` | 404 | **否** |

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
| POST | `/api/chat` | body: `ChatRequest{goal: str 必填, mode?, interaction?, style?, expert?, session_id?, seq: int=0}` | `status` `resumed` | **400**「goal 不能为空」；409/404/400 | 是 |
| POST | `/api/chat/retry` | body: `RetryRequest{task_id: str}` 必填 | `status` `resumed` `ok:true` `task_id` `removed:int` | **400**「task_id 不能为空」；**500**「准备重跑失败」；**404** 内核 reason；409/404/400 | 是 |
| POST | `/api/chat/cancel` | body: `CancelRequest?{seq: int=0}`（整个 body 可省） | `ok` `task_id` `pending?` | **409**「现在没有正在跑的任务」；409/404/400 | 是 |
| GET | `/api/clipboard` | — | `kind`(files/image/text/empty) + 对应字段 | 读失败降级为 `kind:"empty"` | 是 |
| POST | `/api/paste` | body: `req: dict`，读 `kind` `data_url` `text` `name` | `ok:true` `item{path,name,size,kind,mime}` | **400** ×4；422 | 是 |
| POST | `/api/approve` | body: `ApprovalRequest{step_id: str, approved: bool}` 必填 | `step_id` `approved` | 404「该步骤不在等待确认状态」；409/404/400 | 是 |

### 2.6 persona 域（`domains/persona.py`，prefix `/api`）

> id 校验规则：`[A-Za-z0-9_\-一-鿿 ]{1,64}`

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/persona` | — | `expert` `persona` `available[]` `style` | — | 是 |
| POST | `/api/expert` | body: `ExpertRequest{expert: str=""}` | `expert` | 404「没有这个专家」；409/404/400 | 是 |
| GET | `/api/experts/{expert_id}` | path | `{id,name,description,scenarios,tags,personality,speaking_style,use_cases,self_intro,body}` | 404；**400** id 非法 | 是 |
| POST | `/api/experts` | body: `req: dict`，读 `id` `name` `description` `scenarios` `tags` `personality` `speaking_style` `use_cases` `self_intro` `body`（**只有 `id` 有校验**） | `created` | **400**「id 不能为空」；**400** id 非法；**409**「已存在同名人设」 | 是 |
| PUT | `/api/experts/{expert_id}` | body: `req: dict`，**只采纳 9 个键**，其余静默忽略 | `updated` | 404；**400** id 非法 | 是 |
| DELETE | `/api/experts/{expert_id}` | path | `deleted` | 404；**400** id 非法 | 是 |
| GET | `/api/agents/{agent_id}` | path | `{id,name,description,tools,max_turns,model,permission_mode,skills,disallowed_tools,memory,initial_prompt,body}` | 404；**400** id 非法 | 是 |
| POST | `/api/agents` | body: `req: dict`，读 `id` `name` `description` `tools` `max_turns` `body`（`id`/`tools` 有校验） | `created` | **400** ×3；**409**「已存在同名 Agent」 | 是 |
| PUT | `/api/agents/{agent_id}` | body: `req: dict`，读 `tools` + 5 个键 | `updated` | 404；**400** ×2 | 是 |
| DELETE | `/api/agents/{agent_id}` | path | `deleted` | 404；**400** id 非法 | 是 |

### 2.7 team 域（`domains/team.py`，prefix `/api/teams`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/teams` | — | `teams[]{id,name,goal,members[]{agent_id,role},messages,created_at,thread[]}`（thread 取最近 12 条） | 409/404/400 | 是 |
| GET | `/api/teams/{name}` | path | 同上 + `thread[]`（最近 **50** 条） | 404「没有这个团队」 | 是 |
| POST | `/api/teams` | body: `req: dict`，读 `name` `members` `goal`（**全无校验**） | 团队对象 | 409/404/400 | 是 |
| DELETE | `/api/teams/{name}` | path | `deleted` | 409/404/400 | 是 |
| PUT | `/api/teams/{name}` | body: `req: dict`，读 `name` `goal` `member` `role`（**全无校验**） | `ok` `team` | **400** 内核错误文案 | 是 |
| POST | `/api/teams/{name}/members` | body: `req: dict`，读 `agent` `role` | `ok` `content` | 409/404/400 | 是 |
| DELETE | `/api/teams/{name}/members/{agent_id}` | path ×2 | `ok` `removed` | **404** 内核错误文案 | 是 |
| POST | `/api/teams/{name}/message` | body: `req: dict`，读 `to` `content` | `ok` `content` | 409/404/400 | 是 |
| POST | `/api/teams/{name}/dispatch` | body: `req: dict`，读 `agent` | `ok` `content` | 409/404/400 | 是 |

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
| POST | `/api/workflows/templates/{template_id}/run` | body: `req: dict?`，读 `args` | `ok` `content` `run_id` `status` `report_path` | 404/400 | 是 |
| GET | `/api/workflows/{run_id}` | path | `ok:true` `content` | 404 | 是 |
| GET | `/api/workflows/{run_id}/plan` | path | `ok:true` `content` | 404 | 是 |
| POST | `/api/workflows/{run_id}/cancel` | path | 内核原始返回 | 404/400 | 是 |
| DELETE | `/api/workflows/{run_id}` | path | `deleted` | 404；**400**「这次工作流还在跑，先中止再删」 | 是 |

### 2.9 checkpoint 域（`domains/checkpoint.py`，prefix `/api/checkpoints`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/checkpoints/{task_id}` | path | `task_id` `current` `checkpoints[]{id,seq,label,created_at,files,msg_count,current}` | 404/400 | 是 |
| POST | `/api/checkpoints/{checkpoint_id}/rewind` | path；body: `req: dict`，读 `scope`（默认 `CodeAndConversation`） | `ok:true` `content{scope,restored[],removed[],skipped[],errors[],messages_removed,steps_removed}` | **400**「scope 只能是 Code / Conversation / CodeAndConversation」；**400**「checkpoint 不能为空」；404/400 | 是 |

### 2.10 skill 域（`domains/skill.py`，prefix `/api/skills`）

> 名称校验：`[A-Za-z0-9_\-一-鿿 ]{1,64}`。**本域不产生 5xx**（内核异常全被吞）。

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/skills` | — | `skills[]{name,description,path}` | — | 是 |
| GET | `/api/skills/{name}` | path | `name` `description` `body` | 404「没有这个技能」 | 是 |
| POST | `/api/skills` | body: `req: dict`，读 `name` `description` `body`（只有 `name` 有校验） | `created` | **400**「name 不能为空」；**400**「技能名只能含中英文、数字、-、_、空格」；**409**「已存在同名技能」 | 是 |
| PUT | `/api/skills/{name}` | body: `req: dict`，读 `description` `body`（有默认值沿用原值） | `updated` | 404 | 是 |
| DELETE | `/api/skills/{name}` | path | `deleted` | 404 | 是 |

### 2.11 schedule 域（`domains/schedule.py`，**无 prefix**，路径写全）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/automations` | — | `automations[]{id,name,cron,goal,enabled,recurring,last_run,next_run,run_count,created_at,description}` | 404/400 | 是 |
| POST | `/api/automations` | body: `AutomationRequest{name,cron,goal}` 全必填 | 单个 automation 对象 | 422；400（cron 语法错） | 是 |
| PUT | `/api/automations/{item_id}` | path；body: `AutomationUpdateRequest{name="",cron="",goal=""}`（**三个字段全有默认值**） | `updated` + automation 对象 | 404；400 | 是 |
| DELETE | `/api/automations/{item_id}` | path | `deleted` | 404 | 是 |
| POST | `/api/terminal/run` | body: `req: dict`，读 `command`（有校验）`cwd`（无校验） | `id` `status` `command` `cwd` | **400**「command 不能为空」 | 是 |
| GET | `/api/terminal` | — | `sessions[]{id,command,cwd,started_at,finished_at,exit_code,running,lines[]}` | 404/400 | **否** |
| POST | `/api/terminal/interrupt` | body: `req: dict`，读 `id`（**无校验**） | `ok` `detail` | 404/400 | 是 |

### 2.12 app 域（`domains/app.py`，prefix `/api`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| POST | `/api/install` | body: `InstallRequest{source: str 必填, kinds?: list[str]}` | `installed[]` `skipped[]` `errors[]` `count` `reloaded{skills,experts,agents}` | 源目录不存在 → 进 `errors`（200）；409/404/400 | 是 |
| GET | `/api/apps` | q: `limit: int=200` | `count` `apps[]{name,version,publisher,source,path}` | — | 是 |
| GET | `/api/app/icon` | q: `id: str` 必填 | **二进制** PNG | 404「没有这个应用」；404「取不到图标」 | **否**（前端改用批量接口） |
| GET | `/api/apps/icons` | — | `icons{name: dataURL}` `count` | — | 是 |
| POST | `/api/apps/launch` | body: `req: dict`，读 `id`（有校验） | `ok:true` `launched` `target` | **400**「id 不能为空」；**400** 内核文案 | 是 |
| GET | `/api/apps/custom` | — | `apps[]{id,name,target,note,created_at}` | — | 是 |
| POST | `/api/apps/custom` | body: `req: dict`，读 `name` `target` `note` `id`（`name`/`target` 有校验） | `ok:true` `app` | **400** ×3 | 是 |
| PUT | `/api/apps/custom/{app_id}` | path；body 同上（**`id` 被路径参数覆盖**） | `ok:true` `app` | **400** ×3 | 是 |
| DELETE | `/api/apps/custom/{app_id}` | path | `deleted` | 404「没有这个应用」 | 是 |

### 2.13 file 域（`domains/file.py`，**三个 router**：`/api/files` `/api/workspace` `/api/doc`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/files/preview` | q: `path: str=""` | **二进制**（按后缀定 MIME；html/svg 追加 CSP 沙箱头） | **400**「path 不能为空」／**404**「文件不存在」／**403** ×2／**400**「只能预览单个文件」／**415**「不支持预览这种格式」／**413**「文件太大」／**500** | 是 |
| POST | `/api/files/open` | body: `req: dict`，读 `path` `reveal`（`reveal` 无校验） | `ok:true` `path` `reveal` | 400／404／403／**500**「打开失败」 | 是 |
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

### 2.17 mcp 域（`domains/mcp.py`，prefix `/api/mcp`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/mcp` | — | `servers[]{name,type,target,defer_loading,tool_count,enabled,running,live_tools,server_info}` `warnings[]` `errors[]` | 409/404/400 | 是 |
| POST | `/api/mcp/toggle` | body: `req: dict`，读 `name`（有校验）`enabled`（无校验） | `ok:true` `content` | **400**「name 不能为空」；409/404/400 | 是 |

### 2.18 memory 域（`domains/memory.py`，prefix `/api/memory`）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/memory` | q: `limit: int=50` | `memories[]{id,text,tags,updated_at}` | — | 是 |
| POST | `/api/memory` | body: `req: dict`，读 `text`（有校验）`tags` | `id` `text` `deduped:bool` | **400**「text 不能为空」 | 是 |
| DELETE | `/api/memory/{memory_id}` | path | `deleted` | 404「没有这条记忆」 | 是 |

### 2.19 model_config 域（`domains/model_config.py`，prefix **`/api/providers`**）

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/api/providers` | — | `active` `local` `items[]{name,type,base_url,model,api_key(打码),has_key,key_storage,capabilities{…},is_local}` | — | 是 |
| GET | `/api/providers/local` | — | `models[]{name,size,family,parameter_size,quantization,provider}` `base_url` | — | 是 |
| POST | `/api/providers/local` | body: `LocalModelRequest{model: str 必填, base_url: str=""}` | `name` `model` `type` `created:bool` | **400**「模型名不能为空」；**400** 内核文案；422 | 是 |
| POST | `/api/providers` | body: `ProviderRequest{name 必填, type="openai", base_url 必填, model 必填, api_key=""}` | `name` `type` | **400** 内核文案；422 | 是 |
| PUT | `/api/providers/{name}` | path；body: `ProviderEditRequest{type?,base_url?,model?,api_key?}` 全可选 | `name` `type` | 404；**400**「没有要改的内容」；**400** 内核文案 | 是 |
| POST | `/api/providers/{name}/activate` | path | `active_provider` `local_provider` `cloud_provider` | **400**「没有这个 provider：{name}」 | 是 |
| DELETE | `/api/providers/{name}` | path | `deleted` | **400** 内核文案；404 | 是 |

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
| GET | `/api/features` | — | `profile`（str，默认 `personal`）`flags[]`（`feature_catalog()` 的结果）`raw`（`FeatureFlags.snapshot()`） | — | 是 |
| POST | `/api/features/toggle` | body: `req: dict`，读 `key`（有校验）`value`（**无校验**，`bool(...)` 转换） | `key` `value` | **400**「key 不能为空」；**403**「EnableFeatureFlags 已关闭，禁止运行时调整」；**500**「写开关文件失败：{exc}」 | 是 |

> 注：本域**没有**用 `rpc_failure`（不调内核），错误直接是 `HTTPException`。

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

---

### 2.24 server.py 自身路由

| 方法 | 路径 | 入参 | 出参 | 错误 | 前端用 |
|---|---|---|---|---|---|
| GET | `/` | — | **HTML**（老前端）+ `Set-Cookie: oryx_token=…`（HttpOnly / SameSite=strict / Path=/） | 500 | 是（浏览器入口） |
| GET | `/newui` | — | **HTML**（新前端 React 19）+ 同样的 `Set-Cookie` | 404（新界面没构建）／500 | 是（新界面入口） |
| GET | `/web/{path:path}` | path | **静态资源**（`no-store` + `nosniff`） | 400／403／404／415／413／500 | 是 |
| GET | `/web/ui-extensions/{path:path}` | path | **静态资源** | 同上 | 是（间接：URL 由 `/api/ui/modules` 下发） |
| GET | `/api/ping` | — | `{"ok": true}` | — | **否**（桌面壳 `apps/desktop/dist/index.html` 在用） |
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

---

## 3. 体检中发现的缺陷（重写时必须处理）

| # | 缺陷 | 位置 | 状态 |
|---|---|---|---|
| 1 | `GET /api/drives` → **500**（`JSONResponse` 未导入 + `json()` 双重包装） | `domains/index.py:99,148` | ✅ **已修**（实测 200） |
| 2 | `GET /api/image/thumb` → **500**（`StreamingResponse` 未导入）→ **图片网格每张缩略图都 500** | `domains/index.py:224` | ✅ **已修**（实测 200 image/png） |
| 3 | `GET /api/appearance/background.png` 前端引用**未带 `?t=`**，路由要求它 → 401 | `web/modules/theme.js:35`、`models.js:5345` | ⬜ 待修 |
| 4 | `POST /api/memory` 传 `{"text": null}` → 绕过空值校验，把字符串 `"None"` 写库 | `domains/memory.py:47` | ⬜ 待修 |
| 5 | `POST /api/workspace` 传 `path=""` → 静默切到服务进程 cwd | `domains/file.py` | ⬜ 待确认是否算 bug |
| 6 | `POST /api/prompt/optimize` 传 `text=""` → 出参退化成 **JSON 字符串**而非对象（前端读 `d.optimized` 会拿到 undefined） | `domains/system.py` | ⬜ 待修 |
| 7 | `schedule.py:50-51` 的 `except IpcError` 是**死代码**（`_call()` 内部已转 HTTPException） | `domains/schedule.py:50` | ⬜ 待清 |
| 8 | `ExtensionActionRequest.id` / `TrashRequest.name` 在部分端点**未被使用** | `domains/extension.py:38,43` | ⬜ 待清 |
| 9 | **3 个文件带 UTF-8 BOM**（`loop.py` `server.py` `workflow.py`）—— 导入没事，但会坑到静态工具 | — | ⬜ 待清 |
| 10 | `POST /api/teams/{name}/dispatch` 是**同步 `def`**（其余都是 `async def`），FastAPI 会丢进线程池 | `domains/team.py:169` | ⬜ 待统一 |
| 11 | `GET /api/files/list` 在 `allowed_roots` 为空时返回 `{"files": []}` —— **缺 `count` 键**，前端按 `count` 读会拿到 undefined | `domains/file.py:157` | ⬜ 待修 |

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
| 4 | 其余 21 个域照样板铺开 | ⬜ 待做 |
| 5 | 老前端整页替换为新前端（React 19 + TS + Tailwind v4） | 🔄 进行中（新外壳已接 session 域 + 大屏） |

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
