"""`/api/ping` 的身份标识契约。

桌面壳（``apps/desktop/dist/index.html``）与 Rust 侧 ``probe_port()`` 都靠
``service: "oryx"`` 判断「这个端口上跑的是不是 Oryx 内核」。2026-11-26 实测：
只探 TCP 端口时，「地导目标分析」后端占了 8765，桌面端直接跳到人家的
JSON 页面上。所以这个字段不能删、不能改名。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


@pytest.fixture(scope="module")
def client():
    from oryx.server import API_TOKEN, app

    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


def test_ping_carries_oryx_identity(client):
    r = client.get("/api/ping")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "oryx"


def test_ping_response_is_stable_json(client):
    """响应体是紧凑 JSON —— Rust 侧按子串 `"service":"oryx"` 校验。"""
    r = client.get("/api/ping")
    raw = r.content.decode("utf-8")
    assert '"service":"oryx"' in raw
    assert set(r.json()) == {"ok", "service"}


def test_ping_is_side_effect_free(client):
    """连续两次结果一致 —— 探活不写任何状态。"""
    a = client.get("/api/ping")
    b = client.get("/api/ping")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_ping_is_public_without_token():
    """桌面壳 / Rust 侧不带令牌探活 —— /api/ping 必须在 _PUBLIC_PATHS 里。"""
    from oryx.server import app

    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/ping").status_code == 200
