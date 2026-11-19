"""回归测试：两个真实 bug（都实测复现过，不是假想）。

1. `POST /api/memory {"text": null}` 会把字符串 "None" 写进库
2. `GET /api/appearance/background.png` 在带 cookie 时仍返回 401

这两个 bug 的共同点：**症状在前端，根因在后端**。
所以回归测试盯的是**后端接口的真实行为**，不是前端代码长什么样。

---

为什么单独一个文件
------------------
它们都是「已修、修完要钉住」的 bug。
混进各域的测试文件里，将来重构那个域时容易连着一起删掉 ——
回归测试的价值恰恰在于**重构之后还在**。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 与 conftest.py 同样的隔离原则：测试绝不碰用户真实数据。
# 这里用**独立**的临时目录，避免和 pytest 主进程抢同一个 oryx.db。
_TMP_HOME = Path(tempfile.mkdtemp(prefix="oryx-regress-"))
os.environ.setdefault("ORYX_HOME", str(_TMP_HOME))


@pytest.fixture(scope="module")
def client():
    """起一个真实的 app（TestClient）。

    用 `from oryx.server import app` 而不是新建 app ——
    鉴权中间件是在 server.py 里挂的，绕开它就测不到「中间件放行、
    域里又拒」这种问题（背景图那个 bug 正是这种）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        # X-Oryx-Request 是写操作的来源标记（server.py 的 REQUEST_HEADER）
        # —— 少了它，所有 POST/PUT/DELETE 都会被 403 挡在门外。
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


# ── bug 1：memory 收到 null 会写进字符串 "None" ─────────────────────


def test_memory_null_text_does_not_write_none(client):
    """`{"text": null}` 必须被拒，而不是往库里写一条内容为 "None" 的记忆。

    修复前：200 + {"id": "...", "text": "None"}，库里多一条垃圾。
    """
    resp = client.post("/api/memory", json={"text": None})
    assert resp.status_code == 400
    assert "text 不能为空" in resp.json()["detail"]

    listed = client.get("/api/memory?limit=50").json()["memories"]
    assert not [m for m in listed if m.get("text") == "None"], (
        f"库里仍然有内容为 'None' 的记忆：{listed}"
    )


def test_memory_null_tags_becomes_empty_string(client):
    """`{"tags": null}` 之前会把 tags 写成字符串 "None"。"""
    resp = client.post("/api/memory", json={"text": "回归测试条目", "tags": None})
    assert resp.status_code == 200

    listed = client.get("/api/memory?limit=50").json()["memories"]
    mine = [m for m in listed if m.get("text") == "回归测试条目"]
    assert mine, "写入的记忆找不到"
    assert mine[0].get("tags") == "", f"tags 被写成了 {mine[0].get('tags')!r}"


def test_memory_rejects_non_string_text(client):
    """类型不对要 422，而不是静默转成字符串。"""
    resp = client.post("/api/memory", json={"text": 123})
    assert resp.status_code == 422


# ── bug 2：背景图在 cookie 鉴权下被自己的路由挡回 401 ───────────────


def test_background_png_accepts_cookie_auth(client):
    """CSS `url()` 发起的请求**带 cookie、不带 `?t=`**，必须能过鉴权。

    浏览器对同源子资源会自动带 HttpOnly cookie；
    中间件放行了，但路由自己又校验了一遍、且只认 `?t=` → 401 → 背景图永远加载不出来。
    """
    from oryx.server import API_TOKEN

    # 先 GET / 拿到服务端下发的 HttpOnly cookie
    client.get("/")
    assert client.cookies.get("oryx_token")

    # 不带任何查询参数、不带令牌头 —— 只靠 cookie
    resp = client.get("/api/appearance/background.png")
    assert resp.status_code != 401, (
        f"带 cookie 仍然 401：{resp.text} —— "
        "背景图永远加载不出来"
    )


def test_background_png_still_rejects_missing_token(client):
    """修好了「放行」，不能顺手把「拦」也拆了。"""
    from oryx.server import app

    with TestClient(app) as bare:
        # 不带 cookie、不带头、不带 ?t=
        resp = bare.get("/api/appearance/background.png")
        assert resp.status_code == 401


def test_background_png_accepts_query_token(client):
    """`?t=` 这条路不能被拆掉 —— 跨端 relay 与部分 CSS 场景靠它。"""
    from oryx.server import API_TOKEN

    resp = client.get(f"/api/appearance/background.png?t={API_TOKEN}")
    assert resp.status_code != 401
