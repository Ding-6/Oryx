"""本地 API 安全：令牌闸门必须真的挡住"同机网页"。

威胁模型：用户在浏览器里打开任意网站，该网站可以向 http://127.0.0.1:8765
发请求（CORS 挡得住读响应，挡不住发请求）。凡是能改变系统的接口，
都必须要求一个外部网页猜不到的令牌。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("httpx"), reason="未安装 httpx"
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """起一个真实的 uvicorn 服务（独立配置目录，不碰用户配置）。

    **为什么是 module scope**：这个 fixture 每调用一次就重建 uvicorn +
    重载全部 oryx 模块（下面那句 del sys.modules）。
    本文件有 19 个测试 —— 用 function scope 就是连着重建 19 次服务，
    端口进 TIME_WAIT、线程不断累积，跑着跑着就**卡死**（不是失败，是挂住）。
    改成 module scope：服务只起一次，用例之间复用。

    这些测试只读接口、不改服务状态，所以复用同一个服务是安全的。

    **环境变量必须在 teardown 恢复** —— 这是踩过的坑：
    module scope 下不能用 monkeypatch（它只活到函数结束），
    直接改 os.environ 的话，ORYX_HOME 会**泄漏到后续测试** ——
    表现为 test_semantic / test_toolindex 莫名其妙全挂（向量写到临时目录去了）。
    所以这里自己用 MonkeyPatch 上下文管理器，退出时自动还原。
    """
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    home = tmp_path_factory.mktemp("sec") / "home"
    home.mkdir()
    (home / "config.json").write_text(
        '{"providers":{}, "api_token": "test-token-1234567890"}', encoding="utf-8"
    )

    # 自己管环境变量的生命周期：module scope 下 monkeypatch fixture 不可用
    mp = pytest.MonkeyPatch()
    mp.setenv("ORYX_HOME", str(home))
    mp.setenv("ORYX_API_TOKEN", "test-token-1234567890")

    saved_modules = {n: m for n, m in sys.modules.items() if n.startswith("oryx")}
    for name in list(saved_modules):
        del sys.modules[name]

    from oryx import server

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
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

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        c.headers["X-Oryx-Token"] = "test-token-1234567890"
        # 写操作还需要来源标记头（第二道闸：防"请求被送达"，令牌只防"读到响应"）
        c.headers["X-Oryx-Request"] = "1"
        base_headers = dict(c.headers)
        try:
            yield _ClientWithRestore(c, base_headers)
        finally:
            # 收尾必须把环境与模块还原 —— 泄漏到后续测试会表现成
            # 「别的测试莫名其妙全挂」，很难往这边联想。
            instance.should_exit = True
            thread.join(timeout=5)
            mp.undo()
            for name in list(sys.modules):
                if name.startswith("oryx"):
                    del sys.modules[name]
            sys.modules.update(saved_modules)


class _ClientWithRestore:
    """给共享的 httpx.Client 包一层：**每个用例结束后恢复默认请求头与 cookie**。

    为什么需要：服务改成 module scope 后多个用例共用一个 client，
    而这些用例会 `client.headers.pop("X-Oryx-Token")` 来验证「没令牌被拒」。
    不恢复的话，第一个用例 pop 掉之后，后面所有用例都带着「无令牌」跑 ——
    断言会莫名其妙地全挂。

    **恢复的时机是「用例结束」，不是「每个请求结束」** ——
    test_dangerous_endpoints_all_gated 会在一个用例里连发 13 个请求，
    每个请求都恢复的话，第 2 个请求就带上令牌了，401 的断言直接失败。

    只恢复 headers 与 cookies，不动 base_url / timeout。

    **cookie 也要恢复**：令牌现在走 HttpOnly cookie，
    请求 `/` 会把 cookie 存进客户端的 cookie jar ——
    不清掉的话，后面「没令牌应当被拒」的用例会带着 cookie 通过鉴权，
    断言全挂（而且单跑时是过的，排查起来很绕）。
    """

    def __init__(self, client, base_headers: dict):
        self._c = client
        self._base = dict(base_headers)

    @property
    def headers(self):
        return self._c.headers

    @property
    def cookies(self):
        return self._c.cookies

    def restore(self):
        """把请求头与 cookie 恢复成默认（带正确令牌）。"""
        self._c.headers.clear()
        self._c.headers.update(self._base)
        # cookie jar 清空 —— 免得上一个用例的 cookie 漏到下一个
        self._c.cookies.clear()

    def request(self, *args, **kwargs):
        return self._c.request(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self._c.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._c.post(*args, **kwargs)

    def put(self, *args, **kwargs):
        return self._c.put(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._c.delete(*args, **kwargs)


@pytest.fixture(autouse=True)
def _restore_client_headers(request):
    """每个用例**开始前与结束后**都把请求头恢复成默认。

    有了这个，用例里 pop 掉的令牌不会漏到下一个用例。
    """
    if "client" in request.fixturenames:
        c = request.getfixturevalue("client")
        c.restore()
        yield
        c.restore()
    else:
        yield


def test_ping_is_public(client):
    client.headers.pop("X-Oryx-Token")
    assert client.get("/api/ping").status_code == 200


def test_index_page_sets_httponly_cookie(client):
    """令牌必须到达浏览器 —— 但**只能**以 HttpOnly cookie 的形式。

    以前是注入到 core.js 的 `__ORYX_TOKEN__` 占位符里。那有个致命漏洞：

    · `/web/modules/core.js` 不需要令牌就能读（同源资源）
    · CORS 又放开任意 `localhost:PORT` 来源
    → 同机任意网页 fetch 一下就拿到令牌，再带令牌调任意 API
    → 前端扩展 `import { ORYX_TOKEN } from '/web/modules/core.js'` 也拿得到

    HttpOnly cookie 是**唯一 JS 读不到**的凭据通道。
    """
    response = client.get("/")
    assert response.status_code == 200
    # 首页本身不含令牌（它只剩壳），但必须引用模块入口
    assert 'type="module"' in response.text

    # 令牌通过 cookie 下发，且是 HttpOnly
    cookie = response.headers.get("set-cookie", "")
    assert "oryx_token=" in cookie, "令牌没有通过 cookie 下发"
    assert "httponly" in cookie.lower(), "cookie 必须是 HttpOnly —— JS 读不到才安全"
    assert "test-token-1234567890" in cookie


def test_static_js_does_not_contain_token(client):
    """**任何** JS 响应里都不该出现令牌。

    这是上面那个漏洞的要害：令牌一旦出现在 JS 文本里，
    任何能读那个 .js 的东西都拿到了凭据。

    只查**代码行** —— 注释里提到 `__ORYX_TOKEN__`（解释历史）是正常的，
    那是给人看的，不是给 JS 用的。
    """
    for path in ("/web/modules/core.js", "/web/modules/models.js",
                 "/web/modules/agui.js", "/web/modules/theme.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "test-token-1234567890" not in resp.text, (
            f"{path} 里有令牌 —— JS 读得到就等于没有安全边界"
        )
        # 去掉注释与块注释后再查占位符
        code = "\n".join(
            line for line in resp.text.splitlines()
            if not line.strip().startswith(("*", "//", "/*"))
        )
        assert "__ORYX_TOKEN__" not in code, (
            f"{path} 的代码里还有令牌占位符"
        )


def test_cookie_authenticates_api_requests(client):
    """带 HttpOnly cookie 的请求应当通过鉴权。"""
    client.cookies.set("oryx_token", "test-token-1234567890")
    assert client.get("/api/state").status_code == 200


def test_wrong_cookie_rejected(client):
    client.cookies.set("oryx_token", "wrong")
    assert client.get("/api/state").status_code == 401


def test_state_requires_token(client):
    client.headers.pop("X-Oryx-Token", None)
    client.cookies.clear()
    assert client.get("/api/state").status_code == 401


def test_state_with_token(client):
    assert client.get("/api/state").status_code == 200


def test_wrong_token_rejected(client):
    client.headers["X-Oryx-Token"] = "wrong"
    assert client.get("/api/state").status_code == 401


def test_terminal_run_requires_token(client):
    """同机网页最危险的入口：不能不带令牌执行命令。"""
    client.headers.pop("X-Oryx-Token")
    response = client.post("/api/terminal/run", json={"command": "echo pwned"})
    assert response.status_code == 401
    assert "令牌" in response.text


def test_dangerous_endpoints_all_gated(client):
    client.headers.pop("X-Oryx-Token")
    cases = [
        ("post", "/api/chat", {"goal": "x"}),
        ("post", "/api/terminal/run", {"command": "echo x"}),
        ("post", "/api/providers", {"name": "x", "type": "openai", "base_url": "u", "model": "m"}),
        ("post", "/api/workspace", {"path": "C:/"}),
        ("post", "/api/install", {"source": "C:/tmp"}),
        ("post", "/api/automations", {"cron": "* * * * *", "goal": "x"}),
        ("post", "/api/memory", {"text": "x"}),
        ("post", "/api/remote/start", {}),
        ("post", "/api/approve", {"step_id": "x", "approved": True}),
        ("delete", "/api/memory/abc", None),
        # 目录浏览：能看整机的目录结构，不能没有令牌
        ("post", "/api/fs/list", None),
        # 启动应用：能跑程序，不能没有令牌
        ("post", "/api/apps/launch", {"id": "x"}),
        ("post", "/api/apps/custom", {"name": "x", "target": "C:/x.exe"}),
    ]
    for method, path, body in cases:
        if method == "post":
            status = client.post(path, json=body).status_code
        else:
            status = client.delete(path).status_code
        assert status == 401, f"{path} 没有被令牌闸门拦住"


def test_fs_list_get_requires_token(client):
    client.headers.pop("X-Oryx-Token")
    response = client.get("/api/fs/list", params={"path": "C:/"})
    assert response.status_code == 401


def test_apps_icons_requires_token(client):
    """批量图标接口同样不能对外裸奔 —— 它能读全机应用清单。"""
    client.headers.pop("X-Oryx-Token")
    assert client.get("/api/apps/icons").status_code == 401


def test_fs_list_blocks_forbidden_path(client):
    """禁区目录不给看。Windows、Program Files 这些是系统自己的。"""
    response = client.get("/api/fs/list", params={"path": "C:\\Windows"})
    assert response.status_code == 403, response.text


def test_fs_list_rejects_device_path(client):
    """device path 能绕过路径语义，必须挡掉。"""
    response = client.get("/api/fs/list", params={"path": "\\\\?\\C:\\Windows"})
    assert response.status_code == 403, response.text


def test_fs_list_rejects_unc_path(client):
    response = client.get("/api/fs/list", params={"path": "\\\\server\\share"})
    assert response.status_code == 403, response.text


def test_apps_launch_only_accepts_registered_id(client):
    """launch 只接受已登记的 id —— 不接受路径，否则就是"运行任意程序"。

    传一个真实的 exe 路径过去，必须被拒。
    """
    response = client.post(
        "/api/apps/launch", json={"id": "C:\\Windows\\System32\\cmd.exe"}
    )
    assert response.status_code == 400, response.text


def test_apps_launch_requires_id(client):
    response = client.post("/api/apps/launch", json={})
    assert response.status_code == 400


def test_cors_not_wildcard(client):
    """CORS 不能是通配符，否则任意源都被允许带凭据访问。"""
    response = client.get(
        "/api/state", headers={"Origin": "https://evil.example.com"}
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_localhost(client):
    response = client.get(
        "/api/state", headers={"Origin": "http://127.0.0.1:8765"}
    )
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:8765"


def test_cors_allows_tauri_origin(client):
    response = client.get(
        "/api/state", headers={"Origin": "tauri://localhost"}
    )
    assert response.headers.get("access-control-allow-origin") == "tauri://localhost"


def test_background_image_requires_query_token(client):
    """背景图由 CSS url() 引用，走查询参数传令牌。"""
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    upload = client.post("/api/appearance/background", json={"data_url": data_url})
    assert upload.status_code == 200

    ok = client.get("/api/appearance/background.png", params={"t": "test-token-1234567890"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"

    assert client.get("/api/appearance/background.png").status_code == 401
    assert client.get("/api/appearance/background.png", params={"t": "bad"}).status_code == 401

    client.post("/api/appearance/background", json={"data_url": ""})


def test_theme_switching(client):
    assert client.post("/api/appearance/theme", json={"theme": "dark"}).status_code == 200
    assert client.get("/api/appearance").json()["theme"] == "dark"
    assert client.post("/api/appearance/theme", json={"theme": "light"}).status_code == 200
    assert client.post("/api/appearance/theme", json={"theme": "neon"}).status_code == 400
