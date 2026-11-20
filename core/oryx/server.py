"""本地 HTTP + SSE 服务：前端与 Agent 内核之间唯一的通道。

进程分工（对齐 Marvis 的「壳 → 网关 → 内核」三段式）：

    server.py   本进程。只做控制面：HTTP、鉴权、静态资源、SSE、SQLite
    agentd.py   独立进程。做执行面：Agent、工具、索引、终端、调度器、团队

事件出口没变：Agent 内核把事件经 IPC 推回来，由这里的 bus 统一发 SSE，
所以前端一行都不用改。

接口：
    GET  /                     UI
    GET  /api/events           SSE 事件流
    POST /api/chat             发起任务
    POST /api/approve          确认/拒绝 L2 动作
    GET  /api/state            当前状态（模式、provider、是否忙碌）
    POST /api/mode             切换效率/本地模式
    GET  /api/audit/{task_id}  导出审计日志
    GET  /api/skills           技能列表
    GET  /api/extensions       扩展总览（注册表 / profile / 工具加载报告）
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .agentlink import AgentLink
from .api.auth import COOKIE_NAME as _AUTH_COOKIE_NAME  # noqa: F401
from .api.auth import resolve_token_candidate
from .config import load_config
from .events import EventBus
from .ipc import IpcError
from .middleware.errors import error_body as _error_body
from .store import Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

config = load_config()
# 桌面壳会通过环境变量把自己的令牌传进来，保证壳和内核用同一个
API_TOKEN = os.environ.get("ORYX_API_TOKEN") or config.api_token
store = Store()
bus = EventBus()

def _relay_kernel_event(ev: dict[str, Any]) -> None:
    """内核事件转成本进程事件，出 SSE。

    顺便处理两件内核管不了、只能控制面知道的事：
    - RUN_FINISHED：这轮跑完了，清掉"崩溃时补跑"的记录
    - agent.kernel.health：内核自愈的状态，直接透给前端提示用户

    事件名保持原样透传 —— 内核与前端约定的是 AG-UI 家族的大写名
    （RUN_STARTED / RUN_FINISHED / RUN_ERROR / RUN_CANCELLED / RUN_CANCELLING），
    这里要是自作聪明改大小写，前端的 case 就全匹配不上了。
    """
    kind = ev.get("type", "agent.event")
    if kind == "RUN_FINISHED":
        agent.clear_run()
    payload = {
        k: v for k, v in ev.items() if k not in ("seq", "trace_id", "type", "ts")
    }
    bus.publish(kind, **payload)


# Agent 内核的代理。bus.add_sync_sink 会在内核事件到达时把它们灌进本进程的 bus，
# 于是 SSE 那条链路完全不用改。
agent = AgentLink(on_event=_relay_kernel_event, config_provider=lambda: config)

# ---------------------------------------------------------------- 发起在途登记
#
app = FastAPI(title="Oryx", docs_url=None, redoc_url=None)

# CORS 只放开本机来源，不用通配符。
#
# 令牌改走 HttpOnly cookie 之后，这道闸的作用变了：
# 以前它防的是「别的网页读响应」，现在防的是「别的网页**带凭据**发请求」。
#
# **allow_credentials 必须保持 False**（Starlette 的默认值，这里显式写出来）：
# 设成 True 的话，浏览器就会在跨源请求里带上 cookie，
# 于是同机任意网页都能冒充用户调 API —— 那比令牌在 JS 里还糟。
#
# 页面自己能调 API 靠的是**同源**（同 host:port），同源请求不受 CORS 管辖。
ALLOWED_ORIGIN_REGEX = r"^(tauri://localhost|http://tauri\.localhost|https://tauri\.localhost|http://(127\.0\.0\.1|localhost)(:\d+)?)$"

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Oryx-Token", "X-Oryx-Request"],
    # 显式关闭：跨源请求**不带** cookie。
    # 跨源场景（tauri 桌面端）用的是 X-Oryx-Token 头，不是 cookie。
    allow_credentials=False,
)

# 写操作必须带这个自定义头。原理：自定义头让请求变成"非简单请求"，
# 浏览器会先发 CORS 预检，来源不在白名单里就被拦下，请求根本不会送达。
# 即便有人用 mode:'no-cors' 绕过，浏览器也不允许 no-cors 请求带自定义头 → 403。
# 比"只靠令牌"更强一层：令牌防的是"读到响应"，这个头防的是"请求被送达"。
REQUEST_HEADER = "x-oryx-request"
REQUEST_HEADER_VALUE = "1"
# 排障时可以在环境变量里关掉这道闸（对齐 WorkBuddy 的 DISABLE_REQUEST_VALIDATION）
DISABLE_REQUEST_VALIDATION = os.environ.get("ORYX_DISABLE_REQUEST_VALIDATION") == "1"

# 这些路径不校验来源：静态资源与探活没有副作用。
#
# `/newui` 也在里面 —— 它和 `/` 一样，**自己**会下发 HttpOnly cookie，
# 所以「访问它」就是「拿到凭据」的那一步。
# 不放行的话会死锁：中间件先挡掉请求，路由根本没机会下发 cookie，
# 用户打开新界面只会看到一个 401。
_PUBLIC_PATHS = ("/", "/newui", "/api/ping", "/favicon.ico")
# 只读方法不需要来源标记（EventSource 之类的被动请求带不了自定义头）。
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# 令牌的 cookie 名。
#
# 为什么用 cookie 而不是放在 JS 里（`__ORYX_TOKEN__` 占位符）：
# **HttpOnly cookie 是 JS 读不到的唯一凭据通道**。
# 放在 JS 文本里的话，任何能读到那个 .js 的东西都拿到了令牌 ——
# 包括前端扩展（`import { ORYX_TOKEN } from '/web/modules/core.js'`）
# 和同机任意网页（`/web/**` 不需要令牌就能读，CORS 又放开 localhost 任意端口）。
#
# 定义在 api/auth.py —— 域里若有路由要自己再校验一次令牌，
# 两边必须用**同一个** cookie 名。
COOKIE_NAME = _AUTH_COOKIE_NAME

# 前端静态资源分发（S1）。
#
# 为什么要单独做一层：前端从「6791 行单文件」拆成 ES Module 后，
# 浏览器要按 `import` 去取多个 .js 文件。FastAPI 默认不提供静态目录，
# 而首页那条路由是 `read_text()` + 字符串替换（注入令牌）—— 那条路
# 只服务 index.html，给不了 .js。
#
# 安全边界（和 /api/files/open 一样的思路）：
#   · 只读 WEB_DIR 目录内的文件，且必须真实存在、是普通文件；
#   · 路径穿越（../）一律拒绝 —— resolve 之后必须仍在 WEB_DIR 之下；
#   · 只放行已知扩展名，不做「任意文件读取器」；
#   · 静态资源本身不含用户数据，所以**不要求令牌**——否则 <script type="module">
#     这种标签带不了自定义头，整个前端会直接加载失败。
#     但页面里注入的令牌走的是 /api/*，那些接口照旧要令牌。
# 令牌注入方式也要跟着改：模块文件里拿不到 index.html 的字符串替换，
# 所以改成由 /api/session-token 下发（见下）。
WEB_MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=utf-8",
}
WEB_MAX_BYTES = 8 * 1024 * 1024


@app.middleware("http")
async def _require_token(request: Request, call_next):
    """除了首页、静态资源和探活，所有接口都要带令牌。

    没有这道闸，同机任意网页都能 POST /api/chat 执行命令——
    CORS 挡得住读响应，挡不住发请求。

    令牌的来源有三种（都认）：
      · **cookie** —— 页面自己用的（HttpOnly，JS 读不到）
      · `X-Oryx-Token` 头 —— 命令行 / 测试 / 跨端 relay
      · `?t=` 查询参数 —— EventSource 与 CSS url() 带不了自定义头
    """
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/web/"):
        return await call_next(request)

    cookie_token = request.cookies.get(COOKIE_NAME)
    # 三个来源的取法统一走 api/auth.py ——
    # 域里若有路由要自己再校验一次（背景图那条就是），
    # 两边的规则必须一致，否则会出现「中间件放行、域里又拒」。
    provided = resolve_token_candidate(request)
    if not provided or not _tokens_match(provided, API_TOKEN):
        # 这里**必须 return 而不是 raise** ——
        # 用户中间件里 raise 的异常走不到 `ExceptionMiddleware`
        #（它在最内层、只包着路由），会一路冒到最外层的 ServerErrorMiddleware，
        # 结果 401 被当成未捕获异常 → 变成 500。
        # 之前就踩过：把 return 换成 raise，六个安全测试全红。
        #
        # 但 body 形状要走统一的 `_error_body` ——
        # 否则这个接口的错误体跟别的接口长得不一样（只有 detail，没有 code/message）。
        return JSONResponse(
            status_code=401,
            content=_error_body(401, "缺少或错误的访问令牌（X-Oryx-Token）"),
        )

    # cookie 鉴权 → 必须同源。
    #
    # 为什么要自己查 Origin：cookie 是浏览器自动带的，
    # 服务端看到「有 cookie」分不清是谁发的。
    # 同机另一个网页（任意 localhost 端口）发来的请求会带着 cookie 过来 ——
    # SameSite 挡不住（同一个 registrable domain 算同站）。
    #
    # 令牌走 header / 查询参数的不受此限：知道令牌就等于有凭据，
    # 那种调用本来就是给命令行与 relay 用的。
    if cookie_token and not _same_origin(request):
        return JSONResponse(
            status_code=403,
            content=_error_body(403, "跨源请求不接受 cookie 鉴权"),
        )

    # 写操作还要过来源标记这道闸
    if not DISABLE_REQUEST_VALIDATION and request.method not in _SAFE_METHODS:
        if request.headers.get(REQUEST_HEADER) != REQUEST_HEADER_VALUE:
            return JSONResponse(
                status_code=403,
                content=_error_body(
                    403, f"缺少来源标记（{REQUEST_HEADER}: {REQUEST_HEADER_VALUE}）"
                ),
            )

    # 每次请求都同步一次磁盘配置，避免长驻进程用旧数据覆盖用户手改的内容
    config.refresh()
    return await call_next(request)


# ── 全局异常处理 + 请求日志 ─────────────────────────────────────────
#
# **必须装在 `_require_token` 之后** ——
# Starlette 里 `add_middleware` 是「后加的在外层」，
# 所以放在这里才能让请求日志**包住**鉴权中间件：
# 连 401/403 也会留下日志。
# 而排障时最需要的恰恰是「这个请求到底有没有到后端」。
#
# 之前项目里**一处 logging 配置都没有** —— 未捕获异常只在前端表现为
# 「没反应」，服务端不留任何痕迹。详见 oryx/middleware/errors.py。
from .middleware import errors as _errors  # noqa: E402

_errors.install(app)


def _same_origin(request: Request) -> bool:
    """这个请求是不是同源的？

    判定依据是 `Origin` 头：
      · 没有 Origin → 不是浏览器发起的（或同源的某些请求）→ 放行
      · 有 Origin → 必须等于本服务的 origin

    为什么「没有 Origin 就放行」：同源的 GET 请求（如 `<img>`、CSS url()）
    浏览器**不带** Origin 头（Origin 只在跨源或写操作时发）。
    跨源请求则一定带 Origin —— 所以「带了且不匹配」才是攻击特征。
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    # 本服务的 origin：请求本身的 scheme://host
    try:
        base = str(request.base_url).rstrip("/")
    except Exception:
        return False
    if origin.rstrip("/") == base:
        return True
    # 桌面端（tauri）跨源访问 —— 单独放行
    return origin in ("tauri://localhost", "http://tauri.localhost",
                      "https://tauri.localhost")


def _tokens_match(provided: str, expected: str) -> bool:
    return secrets.compare_digest(provided, expected)


@app.on_event("startup")
async def _on_startup() -> None:
    bus.bind_loop(asyncio.get_running_loop())
    # 内核是子进程，起步失败不应该拖垮 HTTP 层 —— 只记状态，由端点按需报错
    try:
        await asyncio.to_thread(agent.start)
    except IpcError as exc:
        bus.publish("agent.error", error=str(exc))
    # 应用图标预热 —— 见 _warm_app_icons
    asyncio.create_task(_warm_app_icons())


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    await asyncio.to_thread(agent.stop)
    # 预热池里可能还挂着一个没人要的待命内核 —— 一起收掉，别留孤儿进程
    from .prewarm import get_pool

    await asyncio.to_thread(get_pool().shutdown)


async def _warm_app_icons() -> None:
    """后台预热应用图标。

    为什么：批量提取 258 个图标要 9.5 秒（一个 PowerShell 进程循环，
    占总耗时的 83%）。用户第一次打开应用库就得等这 9.5 秒。

    做了两件事让它变快：
      1. 图标落磁盘缓存 —— 第二次打开只要 210 毫秒
      2. 启动时后台跑一遍 —— 把这 9.5 秒挪到没人等的时候

    磁盘缓存在 D 盘（app_dir()），所以**重启一次就不用再预热了**。

    不阻塞启动：跑在后台任务里，用户该干嘛干嘛。
    """
    try:
        await asyncio.to_thread(_extract_app_icons_now)
    except Exception:
        pass  # 预热失败不影响别的功能


def _extract_app_icons_now() -> None:
    """实际提取图标（同步，会跑 9.5 秒）。"""
    from . import appicon, manage

    apps = manage._cached_installed_apps(1000)["apps"]
    items = [(a.get("name", ""), a) for a in apps if a.get("name")]
    appicon.extract_batch(items)


# ---------------------------------------------------------------- 领域装配
# 业务路由按域收敛到 domains/，这里集中登记。
# server.py 只留与框架相关的端点（首页、事件流、状态、能力清单）。
from . import domains as _domains  # noqa: E402

_domains.register_all(
    app, store=store, config=config, agent=agent, bus=bus, api_token=API_TOKEN)


# IPC 错误 → HTTP 状态码的映射放在 domains/_common.py ——
# 域里的路由也要用它，放在这里会让域反向依赖 server（循环）。


@app.get("/")
async def index() -> Any:
    """界面。

    **令牌走 HttpOnly cookie，不进 JS 文本。**

    以前是把令牌注入到 core.js 里（`__ORYX_TOKEN__` 占位符）。
    那个做法有个致命漏洞：

    · `/web/**` 路径**不需要令牌**就能读（同源资源，浏览器要拿它加载页面）
    · CORS 又放开任意 `localhost:PORT` 来源
    → 同机任意网页都能 `fetch('/web/modules/core.js')` 读到令牌，
      再带令牌调任意 API（含 `/api/chat`，那能间接执行命令）。

    HttpOnly cookie 是**唯一 JS 读不到**的凭据通道：
    扩展 `import { ORYX_TOKEN } from '/web/modules/core.js'` 拿不到，
    别的网页 fetch 同一个 URL 也拿不到。

    页面自己怎么鉴权？靠 cookie 自动携带（`credentials: 'same-origin'`）。
    """
    html = (WEB_DIR / "index.html").read_text("utf-8")
    response = HTMLResponse(html)
    # HttpOnly：JS 读不到 document.cookie
    # SameSite=Strict：别的站点发起的请求**不带**这个 cookie
    response.set_cookie(
        COOKIE_NAME,
        API_TOKEN,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@app.get("/newui")
async def new_ui() -> Any:
    """新界面（React 19 + TS + Tailwind v4）的入口。

    与 `/` 的关系：两者**并存** —— `/` 是老前端（原生 ES Module，已上线），
    这个是新前端（正在逐域替换老前端）。替换完再撤老的。

    为什么单独一条路由而不是直接把新前端放根上：
    老前端还在跑着，用户现在用的就是它 —— 直接换掉等于把在用的东西砸了。

    **为什么要在这里下发 cookie**
    ------------------------------
    新前端的 API 调用要带 HttpOnly cookie（令牌不在 JS 里）。
    cookie 由服务端在响应里下发，所以**打开这个地址本身**就要把 cookie 带上，
    否则页面能渲染、但每个 API 都 401（前端会显示「连不上内核服务」）。

    静态资源在 `/web/newui/`（构建时 base 就是这么定的）。
    """
    index = WEB_DIR / "newui" / "index.html"
    if not index.is_file():
        raise HTTPException(404, "新界面还没构建 —— 先在 apps/web 跑 npm run build")
    response = HTMLResponse(index.read_text("utf-8"))
    response.set_cookie(
        COOKIE_NAME,
        API_TOKEN,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@app.get("/web/ui-extensions/{path:path}")
async def ui_extension_asset(path: str) -> Response:
    """前端扩展的静态资源。

    扩展住在**用户目录**（`app_dir()/ui-extensions`），不在 WEB_DIR 里，
    所以要单独一条路由。

    安全边界：
      · 只服务 `app_dir()/ui-extensions` 之下 —— resolve 后必须还在里面，
        挡住 `../` 路径穿越与符号链接；
      · 只放行已登记的扩展名；
      · 大小上限同核心资源；
      · **不注入令牌** —— 扩展拿不到 token，只能通过 ctx.api 调后端。
        这样它绕不过 ctx，能力是受限的（见 uiext.py 的模块说明）。

    不要求令牌：`<script type="module">` 带不了自定义头，
    要求令牌会让整个界面加载失败（同 web_asset 的理由）。
    """
    from .config import app_dir

    raw = (path or "").strip()
    if not raw:
        raise HTTPException(400, "缺少资源路径")
    root = (app_dir() / "ui-extensions").resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(403, "越界的资源路径")
    if not target.is_file():
        raise HTTPException(404, f"没有这个资源：{raw}")
    media = WEB_MIME.get(target.suffix.lower())
    if not media:
        raise HTTPException(415, f"不支持的资源类型：{target.suffix}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(500, f"读取失败：{exc}") from None
    if size > WEB_MAX_BYTES:
        raise HTTPException(413, "资源太大")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise HTTPException(500, f"读取失败：{exc}") from None
    # 刻意**不**替换 __ORYX_TOKEN__ —— 扩展不该拿到令牌。
    return Response(content=data, media_type=media)


@app.get("/web/{path:path}")
async def web_asset(path: str) -> Response:
    """前端静态资源（ES Module / CSS / 图片）。

    为什么需要这条路由：前端拆成 ES Module 后，浏览器要按 import 去取
    多个 .js 文件；首页那条路由是 read_text() + 字符串替换，只服务 index.html。

    安全边界：
      · resolve 之后必须仍在 WEB_DIR 之下 —— 挡住 ../ 路径穿越；
      · 必须是真实存在的普通文件（不给目录）；
      · 只放行 WEB_MIME 里登记的扩展名；
      · 超过 WEB_MAX_BYTES 直接拒。

    不要求令牌：<script type="module" src="..."> 这类标签带不了自定义头，
    要求令牌会让整个前端加载失败。静态资源里没有任何用户数据 ——
    页面本身就带令牌下发逻辑（见 index 路由），这是同一套信任模型。
    """
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(400, "缺少资源路径")
    # 关键：用 resolve() 消解 ../ 与符号链接，再判断是否还在 WEB_DIR 内
    target = (WEB_DIR / raw).resolve()
    web_root = WEB_DIR.resolve()
    if target != web_root and web_root not in target.parents:
        raise HTTPException(403, "越界的资源路径")
    if not target.is_file():
        raise HTTPException(404, f"没有这个资源：{raw}")
    media = WEB_MIME.get(target.suffix.lower())
    if not media:
        raise HTTPException(415, f"不支持的资源类型：{target.suffix}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(500, f"读取失败：{exc}") from None
    if size > WEB_MAX_BYTES:
        raise HTTPException(413, "资源太大")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise HTTPException(500, f"读取失败：{exc}") from None
    # **不注入令牌**。
    #
    # 以前这里会把 `__ORYX_TOKEN__` 替换成真令牌 ——
    # 而 `/web/**` 路径不需要令牌就能读，CORS 又放开任意 localhost 端口，
    # 于是同机任意网页 fetch 一下就能拿到令牌，再带令牌调任意 API。
    # 前端扩展也一样（`import { ORYX_TOKEN } from '/web/modules/core.js'`）。
    #
    # 现在令牌走 **HttpOnly cookie**（见 `/` 那条路由），
    # JS 读不到，别的网页也读不到。
    return Response(
        content=data,
        media_type=media,
        headers={
            # 开发期改完刷新就生效，不缓存 —— 桌面端是本地文件，改动频繁
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/ping")
async def ping() -> JSONResponse:
    """探活。公开、无副作用、不泄露信息。"""
    return JSONResponse({"ok": True})


@app.get("/api/events")
async def events(request: Request, since: int = -1) -> StreamingResponse:
    """AG-UI 事件流，支持断点续传。

    前端带上 ?since=<收到的最后一个 seq> 重连时，先补发缓冲里 seq > since 的事件，
    再转入实时推送。缓冲已滚过 since 时回一条 resync 事件，让前端整页刷新 ——
    宁可少补也不能乱序。
    """
    queue = bus.subscribe()

    async def generator():
        try:
            if since >= 0:
                if bus.has_gap_since(since):
                    yield f"data: {json.dumps(bus.control('resync'), ensure_ascii=False)}\n\n"
                else:
                    for ev in bus.replay_since(since):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(bus.control('ready'), ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- 管理


# ---------------------------------------------------------------- 外观


# ---------------------------------------------------------------- 能力清单


# ---------------------------------------------------------------- 人设（专家）CRUD
# 人设与子 Agent 的增删改查是纯文件操作，留在控制面直接做；
# 内核只负责读取与重载，所以写完要调catalog.reload 让它重新扫描。


# ---------------------------------------------------------------- 子 Agent CRUD


# ---------------------------------------------------------------- 团队
# 团队是纯内存态（这一轮的临时编队），所以整套都在 Agent 内核里。


# ---------------------------------------------------------------- 本地索引


# ------------------------------------------------------------------ 产物


# ------------------------------------------------------------------ MCP


# ---------------------------------------------------------------- 目录浏览


# ---------------------------------------------------------------- 应用库


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    # 日志配置放在最前面 —— uvicorn.run 会自己装 handler，
    # 我们要在这之前把根 logger 配好，否则启动阶段的日志就丢了。
    from .logging_setup import setup_logging
    from .paths import data_root

    setup_logging(data_root())
    uvicorn.run(app, host=host, port=port, log_level="warning")


# ---------------------------------------------------------------- 特性开关
