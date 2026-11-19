# 后端结构

一句话：**控制面只做转发，执行面才干活**。

```
core/oryx/
├── server.py          控制面：HTTP、鉴权、静态资源、SSE —— 只留「壳」（535 行）
├── agentd.py         执行面：Agent、工具、索引、终端、调度器、团队
├── domains/          业务路由，按领域分文件
├── toolsets/         工具注册，按能力分 mixin
└── store.py          SQLite 持久化
```

## 一、进程分工

| 进程 | 干什么 | 不干什么 |
|---|---|---|
| `server.py`（本进程） | HTTP 接口、鉴权、静态资源、SSE、事件转发 | 不跑 Agent、不碰工具实现 |
| `agentd.py`（独立进程） | Agent 循环、工具执行、索引、终端、调度 | 不直接对外暴露 HTTP |

两边通过 **IPC** 通信：控制面 `agent.call_async("catalog.xxx")` → 执行面处理 → 回结果。

事件方向相反：执行面把事件经 IPC 推回控制面，由 `server.py` 的 bus 统一发 SSE，
所以**前端一行都不用改**。

## 二、领域划分（`domains/`）

业务路由按领域收敛到 `domains/`，一个领域一个文件：

| 域 | 文件 | 负责 |
|---|---|---|
| 会话 | `session.py` | 会话列表、详情、改名、删除 |
| 模型配置 | `model_config.py` | provider 的增删改查、密钥 |
| 技能 | `skill.py` | 技能目录 |
| 自动任务与终端 | `schedule.py` | 定时任务、内嵌终端 |
| 检查点 | `checkpoint.py` | 断点续跑 |
| 工作流编排 | `workflow.py` | 多 Agent 工作流 |
| 外观 | `appearance.py` | 主题、背景图 |
| 特性开关 | `feature.py` | 开关清单与切换 |
| 长期记忆 | `memory.py` | 记、查、忘 |
| 跨端远控 | `remote.py` | 看屏、输入控制 |
| 界面扩展 | `ui.py` | 核心样式子集、扩展清单 |
| 扩展 | `extension.py` | 安装、启停、回收区 |
| 工具检索 | `tools.py` | 语义索引状态、按需取 schema |
| MCP | `mcp.py` | MCP 服务器清单与启停 |
| 文件 | `file.py` | 产物预览/打开/列出、工作区、项目文档 |
| 人设 | `persona.py` | 专家与子 Agent 的增删改查 |
| 团队 | `team.py` | 多 Agent 编队 |
| 索引 | `index.py` | 本地索引、磁盘、图库 |
| 偏好 | `preference.py` | 交互模式、回答风格、计算模式 |
| 对话 | `chat.py` | 发送/重试/中止、剪贴板、粘贴、审批 |
| 应用 | `app.py` | 应用安装、启动、图标、自定义应用 |
| 系统 | `system.py` | 状态、自检、提示词优化、环境查询、用量 |

**22 个域，覆盖 119 个 API 端点。**

**一个域可以管多个前缀**（`persona` 管 `/api/persona` 与 `/api/experts`，
`index` 管 `/api/index` 与 `/api/drives`）——
那种情况 router 用 `/api` 前缀 + `tags` 区分。

### 每个域长什么样

```python
router = APIRouter(prefix="/api/appearance", tags=["外观"])


def read_appearance(config) -> dict:
    """业务逻辑 —— 纯函数，不碰 HTTP。"""


def register(app, config, api_token: str) -> None:
    """把路由挂到 app。依赖用闭包注入。"""

    @router.get("")
    async def _get():
        return json(read_appearance(config))

    app.include_router(router)
```

**两条规矩**：

1. **业务函数是纯函数** —— `read_appearance(config)` 而不是
   `async def route()` 里直接读全局。好处：能直接测，不用起 HTTP。
2. **依赖用闭包注入** —— `register(app, store, config, agent)`。
   不在域里 `import server`（**反向依赖 = 循环依赖**）。

### 域之间不互相依赖

需要跨域时用**延迟导入**：

```python
# ui 域要用扩展域的索引函数
from .extension import backend_ext_index
```

放在函数体里 —— 避免模块级循环导入。

## 三、加一个新域

1. 建 `core/oryx/domains/<名字>.py`，照着上面的骨架写
2. 在 `core/oryx/domains/__init__.py` 里：
   - `from . import <名字>` 加进 import
   - `DOMAINS` 里加一行 `(名字, "说明")`
   - `register_all()` 里加一行 `名字.register(app, ...)`
3. 把 `server.py` 里对应的路由**删掉**（否则重复注册）
4. 在 `core/tests/test_domains.py` 的 `EXPECTED_DOMAINS` / `EXPECTED_PREFIXES` 里登记

第 3、4 步忘了会**测试红** —— 这是有意的：宁可测试红，也不要悄悄少注册一个域。

## 四、工具注册（`toolsets/`）

`loop.py` 一度 4859 行，其中 21 个 `_register_*` 方法占了 3400 行。
现在拆到 `oryx/toolsets/` —— **19 个 mixin，一个能力一个文件**
（Office、浏览器、MCP、定时任务、索引、远控、终端、记忆、工作流…）。

**为什么用 mixin 而不是「函数 + runtime 参数」**
后者要把方法体里所有 `self.` 换成 `runtime.` ——
`_register_mcp_tools` 里有 **164 处**，机械替换容易改坏。

mixin 把方法**原样搬走**，`self.` 一个字都不用改 ✓ **语义零变动**。

```python
class AgentRuntime(
    McpToolsMixin,
    WorkflowToolsMixin,
    OfficeToolsMixin,
    ...):
```

> **拆分的真正价值不是「文件变小了」**，而是它会**逼出隐藏的耦合**。
> 拆 `loop.py` 时就逼出了两个：`MAX_SKILL_CHARS` 原来定义在 loop.py，
> 搬走的方法还要用；`Path(__file__).parents[N]` 的索引要跟着加一
> （文件多了一层目录）。

## 五、共享辅助放 `_common.py`

`domains/_common.py` 放跨域要用的小工具（`rpc_failure`、`json`）。

**不能放 `server.py`** —— 域从 server import 会形成循环：
server 装配域，域又依赖 server。

## 六、几条容易踩的坑

**FastAPI 新版的 `include_router` 变了**
它把子路由收进一个 `_IncludedRouter` 对象，**直接遍历 `app.routes` 看不到它们**。
要枚举真实端点，用 `app.openapi()["paths"]` —— 那是权威来源。

**端口探测用 `connect()` 会误判 TIME_WAIT**
该用 `bind()`：能 bind = 没人监听。

**WebView/浏览器进程退不干净**
Chrome 的启动器进程会先退出，靠它杀进程树**杀不到真正的浏览器**。
要让浏览器自己退（CDP `Browser.close`），子进程会一起走。
