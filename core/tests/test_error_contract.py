"""错误响应形状统一 —— 而且**不破坏**旧前端。

背景
----
体检发现错误响应有四种形状：
    {"detail": "没有这个会话"}          FastAPI 默认
    {"detail": [{"loc":...}]}            参数校验失败
    {"detail": "缺少访问令牌"}           中间件手写
    纯文本 / HTML                      未捕获异常

前端因此到处写 `x.detail || r.status` —— 有的接口根本没有 `detail`。

统一后的约定（`oryx/middleware/errors.py`）：
    成功 → 保持原样（裸 dict），**不包壳** —— 包了会砸掉 195 个现有调用点
    错误 → {"code": int, "data": Any|null, "message": str, "detail": str}

`detail` 键**必须保留**：旧前端读它。去掉的话所有错误提示会退化成
「请求失败」—— 那是拿「统一」换「能用」。

这个测试守住两件事：
  1. 错误响应**必须**同时有 code / message / detail
  2. `detail` 必须还在（兼容旧前端）—— 哪天有人图省事删了它，测试立刻红
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
os.environ.setdefault("ORYX_HOME", r"D:\OryxData\data")

from fastapi.testclient import TestClient  # noqa: E402

from oryx.config import load_config  # noqa: E402
from oryx.domains import register_all  # noqa: E402
from oryx.server import app  # noqa: E402


def _client() -> TestClient:
    cfg = load_config()

    class _Store:
        def __init__(self):
            self.conv_path = Path(tempfile.gettempdir()) / "oryx-error-contract-test" / "oryx.db"

        def __getattr__(self, name):
            raise RuntimeError("stub")

    class _Agent:
        async def call_async(self, *a, **k):
            raise RuntimeError("stub")

    class _Bus:
        def publish(self, *a, **k):
            pass

    register_all(app, store=_Store(), config=cfg, agent=_Agent(), bus=_Bus(), api_token="")
    return TestClient(app, raise_server_exceptions=False)


def _assert_unified(payload: dict, status: int, label: str) -> None:
    assert isinstance(payload, dict), f"{label}: 错误体不是对象"
    for key in ("code", "message", "detail"):
        assert key in payload, f"{label}: 错误体缺 `{key}` 键 —— {payload}"
    assert payload["code"] == status, f"{label}: code 与 HTTP 状态码不一致 —— {payload}"
    assert payload["message"] == payload["detail"], (
        f"{label}: message 与 detail 不一致 —— 旧前端读 detail，新代码读 message，"
        f"两者必须同文：{payload}"
    )


def test_error_bodies_are_unified():
    """各类错误（401/403/404/422）的响应体形状一致。"""
    client = _client()
    token = app_token()

    # 1) 401 —— 中间件自己返回的（不走异常处理器，所以最容易漏）
    r = client.get("/api/state")
    assert r.status_code == 401, f"应 401，实际 {r.status_code}"
    _assert_unified(r.json(), 401, "401 无令牌")

    # 2) 403 —— 跨源 cookie
    #    注意：**只有 cookie 鉴权才做同源检查**。
    #    走 `X-Oryx-Token` 头 / `?t=` 的不受限 —— 知道令牌就等于有凭据，
    #    那种调用本来就是给命令行与 relay 用的（server.py 的注释写明了这个设计）。
    r = client.get(
        "/api/state",
        cookies={"oryx_token": token},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403, f"应 403，实际 {r.status_code}"
    _assert_unified(r.json(), 403, "403 跨源")

    # 3) 404 —— 领域内抛的 HTTPException
    r = client.get("/api/sessions/不存在的ID", headers={"X-Oryx-Token": token})
    assert r.status_code == 404, f"应 404，实际 {r.status_code}"
    _assert_unified(r.json(), 404, "404 不存在")

    # 4) 422 —— 参数校验失败（压成一句话，原数组留在 data）
    r = client.post(
        "/api/interaction",
        json={},
        headers={"X-Oryx-Token": token, "X-Oryx-Request": "1"},
    )
    assert r.status_code == 422, f"应 422，实际 {r.status_code}"
    body = r.json()
    _assert_unified(body, 422, "422 校验失败")
    assert isinstance(body.get("data"), list), f"422 应保留原始校验数组在 data —— {body}"


def test_success_responses_are_not_wrapped():
    """成功响应**保持原样** —— 不能被统一响应体包壳。

    包壳会砸掉 195 个现有前端调用点（它们直接读 `d.sessions` 这种顶层键）。
    统一是渐进的：新接口用 `ok()`，旧接口在逐域迁移时才换。
    """
    client = _client()
    token = app_token()
    r = client.get("/api/state", headers={"X-Oryx-Token": token})
    assert r.status_code == 200, f"应 200，实际 {r.status_code}"
    body = r.json()
    # 顶层就是领域数据，没有 code/data 包壳
    assert "mode" in body, f"成功响应被包壳了 —— {list(body)[:5]}"
    assert "code" not in body or "data" not in body, (
        f"成功响应被统一响应体包住了 —— 这会破坏现有前端 —— {list(body)[:5]}"
    )


def app_token() -> str:
    from oryx.server import API_TOKEN

    return API_TOKEN
