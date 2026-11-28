"""模块 12：账号与权限 —— 改密码、登录报错收敛、会话失效的 HTTP 契约。

取证时钉住的三件事，改代码最容易悄悄退回去：

  1. **登录短密码曾经是 422**。`/api/auth/login` 复用了注册用的
     `Credentials`（密码 ≥10 位），输错一个短密码得到的是
     「密码至少需要 10 位」而不是「账号或密码不正确」——
     既答非所问，又把注册规则泄露给正在试密码的人。
     登录只有「对 / 不对」两种结果，一律 401；只有 body 形状脏了才 422。
  2. **改密码必须让其它会话失效**，否则「改了密码」只是换个登录口令，
     已经拿到的会话 cookie 照样能进。当前会话要保留，否则用户一改完
     就被踢回登录页，体验像失败。
  3. **原密码错误不能用 401**。401 在前端是「登录态失效」的信号；
     虽然 `shouldHandleUnauthorized()` 会挡掉 `/api/auth/*`，但语义上
     这是表单填错，不该复用认证态错误码，将来放宽那条判断就会误踢人。

跑法：
    python -m pytest core/tests/test_account_password.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx.domains import account as account_domain
from oryx.domains.account import SESSION_COOKIE, token_digest
from oryx.store import Store

PWD1 = "long-password-1"
PWD2 = "another-long-password-2"


def _app(store: Store) -> FastAPI:
    """独立 app + 真实 Store，避免污染生产 app 的全局 router 单例。

    沿用 `test_account_ownership.py` 的做法：每次临时换一个空 APIRouter，
    `register()` 里 `@router.xxx` 解析的是模块级名字，注册完再换回来。
    """
    app = FastAPI()

    @app.middleware("http")
    async def bind_user(request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE, "")
        user = store.user_for_login_session(token_digest(token)) if token else None
        request.state.user_id = user["id"] if user else None
        return await call_next(request)

    original = account_domain.router
    account_domain.router = APIRouter(prefix="/api/auth", tags=["账号"])
    try:
        account_domain.register(app, store)
    finally:
        account_domain.router = original
    return app


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "oryx.db")
    return store, _app(store)


def _register(client, username="alpha", password=PWD1):
    resp = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _change(client, old_password, new_password):
    return client.post(
        "/api/auth/password",
        json={"old_password": old_password, "new_password": new_password},
    )


def _err(resp, status: int, needle: str = ""):
    """兼容生产 app 的统一错误体与 FastAPI 默认的 {detail}。"""
    assert resp.status_code == status, resp.text
    body = resp.json()
    msg = body.get("message") or body.get("detail") or ""
    if needle:
        assert needle in str(msg), body
    return body


# ── 登录报错收敛 ────────────────────────────────────────────────────


def test_register_keeps_strong_validation(env):
    """注册仍要挡住弱密码与非法账号名 —— 这轮只放松登录。"""
    _, app = env
    client = TestClient(app)
    _err(client.post(
        "/api/auth/register", json={"username": "alpha", "password": "short"}
    ), 422, "至少需要 10 位")
    _err(client.post(
        "/api/auth/register", json={"username": "中文名", "password": PWD1}
    ), 422, "账号需为")
    assert _register(client)["username"] == "alpha"


def test_login_short_password_is_401_not_422(env):
    """核心回归：短密码不再是 422「密码至少需要 10 位」。"""
    _, app = env
    client = TestClient(app)
    _register(client)
    body = _err(_login(client, "alpha", "short"), 401, "账号或密码不正确")
    assert "至少需要 10 位" not in str(body), body


def test_login_unknown_or_malformed_username_is_401(env):
    """账号不存在 / 名字格式不对，也一律 401，不泄露校验规则。"""
    _, app = env
    client = TestClient(app)
    _register(client)
    for username in ("nobody", "中文名", "", "x" * 200):
        _err(_login(client, username, PWD1), 401, "账号或密码不正确")
    # 前后空格仍要能正常登录（登录模型只 trim，不做格式校验）
    assert _login(client, "  alpha  ", PWD1).status_code == 200


def test_login_dirty_body_is_422(env):
    """脏 body 仍由 Pydantic 挡住 —— 收的是「业务校验」，不是形状校验。"""
    _, app = env
    client = TestClient(app)
    _register(client)
    _err(client.post("/api/auth/login", json=["alpha", PWD1]), 422)
    _err(client.post("/api/auth/login", json={"username": "alpha"}), 422)
    _err(client.post("/api/auth/login", json="alpha"), 422)


# ── 改密码 ──────────────────────────────────────────────────────────


def test_change_password_success(env):
    store, app = env
    client = TestClient(app)
    _register(client)
    resp = _change(client, PWD1, PWD2)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["changed"] is True
    assert isinstance(data["revoked_sessions"], int)

    record = store.user_by_username("alpha")
    assert record is not None
    assert PWD1 not in record["password_hash"] and PWD2 not in record["password_hash"]

    other = TestClient(app)
    _err(_login(other, "alpha", PWD1), 401)
    assert _login(other, "alpha", PWD2).status_code == 200


def test_change_password_requires_login(env):
    _, app = env
    client = TestClient(app)
    _register(TestClient(app))  # 系统里有账号，但这个 client 没登录
    _err(_change(client, PWD1, PWD2), 401, "请先登录")


def test_change_password_wrong_old_is_400_and_keeps_hash(env):
    """原密码错误是 400，且绝不能把密码改掉。"""
    store, app = env
    client = TestClient(app)
    _register(client)
    before = store.user_by_username("alpha")["password_hash"]
    _err(_change(client, "wrong-old-password", PWD2), 400, "原密码不正确")
    assert store.user_by_username("alpha")["password_hash"] == before
    assert _login(TestClient(app), "alpha", PWD1).status_code == 200


def test_change_password_same_password_is_400(env):
    _, app = env
    client = TestClient(app)
    _register(client)
    _err(_change(client, PWD1, PWD1), 400, "新密码不能与原密码相同")


def test_change_password_weak_new_is_422(env):
    _, app = env
    client = TestClient(app)
    _register(client)
    _err(_change(client, PWD1, "short"), 422, "新密码至少需要 10 位")
    _err(_change(client, PWD1, "x" * 300), 422, "新密码不能超过 256 位")


def test_change_password_dirty_body_is_422(env):
    _, app = env
    client = TestClient(app)
    _register(client)
    _err(client.post("/api/auth/password", json=[PWD1, PWD2]), 422)
    _err(client.post("/api/auth/password", json={"old_password": PWD1}), 422)
    _err(client.post("/api/auth/password", json="whatever"), 422)


def test_change_password_revokes_other_sessions_keeps_current(env):
    """改完密码：其它设备立刻失效，当前这台还能继续用。"""
    _, app = env
    current = TestClient(app)
    _register(current)
    other = TestClient(app)
    assert _login(other, "alpha", PWD1).status_code == 200

    data = _change(current, PWD1, PWD2).json()["data"]
    assert data["revoked_sessions"] == 1, data
    assert current.get("/api/auth/me").status_code == 200
    _err(other.get("/api/auth/me"), 401)
    # 旧会话 cookie 已经作废，重新用旧密码也登不进去
    _err(_login(other, "alpha", PWD1), 401)


def test_change_password_does_not_touch_other_users(env):
    """按 user_id 过滤：改 A 的密码不能把 B 踢下线，也不能改掉 B 的口令。"""
    _, app = env
    a = TestClient(app)
    _register(a, "alpha", PWD1)
    b = TestClient(app)
    _register(b, "beta", PWD1)
    _change(a, PWD1, PWD2)
    assert b.get("/api/auth/me").status_code == 200
    assert _login(TestClient(app), "beta", PWD1).status_code == 200


def test_change_password_response_is_v2_and_me_has_no_hash(env):
    """账号域用 ok() 包壳；`/me` 不能把 password_hash 带出去。"""
    _, app = env
    client = TestClient(app)
    _register(client)
    body = _change(client, PWD1, PWD2).json()
    assert body["code"] == 0 and "data" in body and "message" in body
    me = client.get("/api/auth/me").json()["data"]
    assert "password_hash" not in me and "password" not in me


def test_logout_still_works_after_change(env):
    _, app = env
    client = TestClient(app)
    _register(client)
    _change(client, PWD1, PWD2)
    assert client.post("/api/auth/logout").status_code == 200
    _err(client.get("/api/auth/me"), 401)


# ── store 层 ────────────────────────────────────────────────────────


def test_store_helpers_are_scoped(tmp_path):
    store = Store(tmp_path / "oryx.db")
    a = store.create_user("alpha", "hash-a")
    b = store.create_user("beta", "hash-b")
    store.create_login_session("tok-a1", a["id"], 9e18)
    store.create_login_session("tok-a2", a["id"], 9e18)
    store.create_login_session("tok-b1", b["id"], 9e18)

    assert store.user_credentials(a["id"])["password_hash"] == "hash-a"
    assert store.user_credentials("missing") is None
    assert store.update_password_hash(a["id"], "hash-a-new") is True
    assert store.update_password_hash("missing", "x") is False
    assert store.delete_login_sessions_except(a["id"], "tok-a1") == 1
    assert store.user_for_login_session("tok-a1") is not None
    assert store.user_for_login_session("tok-a2") is None
    assert store.user_for_login_session("tok-b1") is not None
