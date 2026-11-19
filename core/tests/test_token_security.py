"""令牌暴露漏洞的回归测试。

这个漏洞是怎么发现的
------------------
审计前端扩展依赖时顺手验证「扩展拿不到令牌」这条承诺，结果**不成立**：

    扩展 import { ORYX_TOKEN } from '/web/modules/core.js'
    → 拿到完整令牌（真实浏览器验证）

顺着这条线查下去，发现漏洞比扩展大得多：

    · 令牌明文在 `/web/modules/core.js` 里
    · 而 `/web/**` 路径**不需要令牌**就能读（同源资源）
    · CORS 又放开任意 `localhost:PORT` 来源
    → 同机任意网页 fetch 一下就拿到令牌，再带令牌调任意 API
      （含 `/api/chat`，那能间接执行命令）

根因：**令牌放在 JS 文本里**。JS 文本是任何能读到那个文件的东西都能读的。

修法
----
令牌改走 **HttpOnly cookie** —— 唯一 JS 读不到的凭据通道。
再加一道服务端闸门：cookie 鉴权的请求必须**同源**。

为什么还要服务端那道闸
--------------------
cookie 是浏览器自动带的，服务端看到「有 cookie」分不清是谁发的。
`localhost:9999` 与 `localhost:8765` 是**同站**（同 registrable domain），
`SameSite=Strict` 挡不住 —— 所以要自己看 `Origin`。

要锁住的
--------
1. 任何 JS 响应里都没有令牌
2. 令牌通过 HttpOnly cookie 下发
3. 跨源请求（带 cookie）被拒
4. 同源请求（带 cookie）通过
5. 知道令牌（header）就能调 —— 命令行与 relay 靠这个
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("httpx"), reason="未安装 httpx"
)

TOKEN = "unit-test-token-abcdef"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """起一个真实的 uvicorn 服务（独立配置目录，不碰用户配置）。"""
    import socket
    import threading

    import uvicorn

    home = tmp_path_factory.mktemp("toksec") / "home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"providers": {}, "api_token": TOKEN}), encoding="utf-8"
    )

    mp = pytest.MonkeyPatch()
    mp.setenv("ORYX_HOME", str(home))
    mp.setenv("ORYX_API_TOKEN", TOKEN)

    saved = {n: m for n, m in sys.modules.items() if n.startswith("oryx")}
    for name in list(saved):
        del sys.modules[name]

    from oryx import server as server_mod

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(server_mod.app, host="127.0.0.1", port=port,
                             log_level="error")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail("服务没能在 15 秒内起来")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        mp.undo()
        for name in list(sys.modules):
            if name.startswith("oryx"):
                del sys.modules[name]
        sys.modules.update(saved)


def _get(base: str, path: str, **kw):
    import httpx
    return httpx.get(base + path, timeout=10, **kw)


# ── 1. JS 里没有令牌 ─────────────────────────────────────────────────
def test_no_token_in_any_js(server):
    """**任何** JS 响应里都不该出现令牌。

    这是漏洞的要害：令牌一旦出现在 JS 文本里，
    任何能读那个 .js 的东西都拿到了凭据。
    """
    for path in ("/web/modules/core.js", "/web/modules/models.js",
                 "/web/modules/agui.js", "/web/modules/theme.js",
                 "/web/modules/uiext.js", "/web/modules/layout.js"):
        r = _get(server, path)
        assert r.status_code == 200, path
        assert TOKEN not in r.text, f"{path} 里有令牌 —— JS 读得到就等于没有安全边界"


def _strip_js_comments(src: str) -> str:
    """去掉 JS 里的注释，只留代码。

    为什么需要：core.js 的注释里会**提到** `export const ORYX_TOKEN`（解释旧做法），
    直接对原文断言会误判。要判的是「代码里有没有这个导出」，
    所以必须先把注释剥掉。

    走状态机而不是正则 —— 正则会分不清 `//` 在字符串里
    （`'http://x'` 这种），一不留神就把真代码也吃掉了，
    那样测试就成了「永远通过」的假货。
    """
    out: list[str] = []
    i, n = 0, len(src)
    quote = ""      # 当前所在的字符串引号（'' / "" / ``）
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def test_extension_cannot_import_token(server):
    """前端扩展**拿不到令牌** —— 这是扩展系统一直宣称的边界。

    扩展能 `import { ORYX_TOKEN } from '/web/modules/core.js'`，
    但 core.js 里根本没有令牌了 —— 令牌在 HttpOnly cookie 里，JS 读不到。

    判据是**代码里有没有导出**（注释里提到这个名字不算）：
    没导出 → 扩展那行 import 直接抛 SyntaxError，压根拿不到。
    """
    r = _get(server, "/web/modules/core.js")
    code = _strip_js_comments(r.text)
    assert "export const ORYX_TOKEN" not in code, (
        "core.js 还在导出令牌 —— 扩展 import 一下就拿到了"
    )
    assert "export function ORYX_TOKEN" not in code
    assert TOKEN not in r.text, "令牌明文还在 JS 里"


# ── 2. 令牌通过 HttpOnly cookie 下发 ─────────────────────────────────
def test_token_delivered_as_httponly_cookie(server):
    r = _get(server, "/")
    assert r.status_code == 200
    cookie = r.headers.get("set-cookie", "")
    assert "oryx_token=" in cookie, "令牌没有通过 cookie 下发"
    assert "httponly" in cookie.lower(), "cookie 必须 HttpOnly"
    assert "samesite=strict" in cookie.lower(), "cookie 应该 SameSite=Strict"


def test_token_not_in_html(server):
    """首页 HTML 里也不该有令牌。"""
    r = _get(server, "/")
    assert TOKEN not in r.text


# ── 3. 跨源请求被拒 ─────────────────────────────────────────────────
def test_cross_origin_cookie_request_rejected(server):
    """跨源带 cookie 的请求**必须被拒**。

    cookie 是浏览器自动带的，服务端分不清是谁发的。
    `localhost:9999` 与 `localhost:8765` 是同站（SameSite 挡不住），
    所以要自己看 Origin。
    """
    r = _get(server, "/api/state",
             headers={"Origin": "http://127.0.0.1:9999",
                      "Cookie": f"oryx_token={TOKEN}"})
    assert r.status_code == 403, "跨源带 cookie 的请求不该通过"


def test_cross_origin_no_credentials_rejected(server):
    """跨源不带凭据 —— 401。"""
    r = _get(server, "/api/state", headers={"Origin": "http://127.0.0.1:9999"})
    assert r.status_code == 401


# ── 4. 同源请求通过 ─────────────────────────────────────────────────
def test_same_origin_cookie_request_allowed(server):
    """页面自己（同源 + cookie）要能正常调 API。"""
    r = _get(server, "/api/state", headers={"Cookie": f"oryx_token={TOKEN}"})
    assert r.status_code == 200


def test_same_origin_with_origin_header_allowed(server):
    """同源请求带 Origin 头（浏览器发 POST 时会带）也要通过。"""
    import httpx

    base = httpx.get(server + "/", timeout=10)
    origin = str(base.url).rstrip("/")
    r = httpx.get(server + "/api/state", timeout=10,
                   headers={"Origin": origin, "Cookie": f"oryx_token={TOKEN}"})
    assert r.status_code == 200


# ── 5. 知道令牌就能调（命令行 / relay 靠这个）────────────────────────
def test_token_header_still_works(server):
    """令牌走 header 仍然有效 —— 命令行、测试、跨端 relay 靠这个。"""
    r = _get(server, "/api/state", headers={"X-Oryx-Token": TOKEN})
    assert r.status_code == 200


def test_token_query_param_still_works(server):
    """`?t=` 查询参数仍然有效 —— 外部工具（EventSource 带不了自定义头）。"""
    r = _get(server, f"/api/state?t={TOKEN}")
    assert r.status_code == 200


def test_cross_origin_with_explicit_token_allowed(server):
    """**知道令牌**的跨源调用仍然放行。

    令牌就是凭据 —— 知道它就等于有权限。
    这条路径是给桌面端（tauri 跨源）用的。
    """
    r = _get(server, "/api/state",
             headers={"Origin": "tauri://localhost",
                      "X-Oryx-Token": TOKEN})
    assert r.status_code == 200
