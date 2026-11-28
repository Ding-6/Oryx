"""偏好目录接口契约 —— 前端下拉的唯一数据源。

为什么单独测它
--------------
新前端的「交互模式 / 回答风格 / 计算模式」三个下拉全部由
`GET /api/preferences/catalog` 渲染，前端不硬编码选项。于是这条链路
最危险的失败不是「接口 500」，而是**目录悄悄跟实现漂移**：

  · 后端加了模式，目录忘了列 → 界面上永远选不到
  · 目录列了名字，但 POST /api/interaction 不认 → 用户点了就 400
  · label 与 InteractionMode.label 各写一份 → 中文名慢慢对不上
  · 把 ModePolicy.prompt（系统提示词）整段发下去 → 前端拿到不该拿的东西

所以这里把目录钉在 `MODE_POLICIES` / `STYLES` 上，并逐个把目录里的值
POST 回偏好接口，确认「列出来的就能选」。

跑法
----
    python -m pytest core/tests/test_preference_catalog.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP_HOME = Path(tempfile.mkdtemp(prefix="oryx-pref-catalog-"))
os.environ.setdefault("ORYX_HOME", str(_TMP_HOME))


@pytest.fixture(scope="module")
def client():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


def _data(resp):
    """跟前端 lib/api.ts 的 unwrap 同一套规则。"""
    body = resp.json()
    if (
        isinstance(body, dict)
        and isinstance(body.get("code"), int)
        and isinstance(body.get("message"), str)
        and "data" in body
    ):
        assert body["code"] == 0, f"{resp.request.url} → {body}"
        return body["data"]
    return body


def _catalog(client):
    resp = client.get("/api/preferences/catalog")
    assert resp.status_code == 200, resp.text
    # 新接口必须是 v2 统一响应，不能退回裸对象
    body = resp.json()
    assert isinstance(body.get("code"), int) and body["code"] == 0
    assert isinstance(body.get("message"), str) and "data" in body
    return body["data"]


def test_catalog_returns_all_three_lists(client):
    data = _catalog(client)
    assert set(data) == {"interactions", "styles", "compute_modes"}
    assert isinstance(data["interactions"], list) and data["interactions"]
    assert isinstance(data["styles"], list) and data["styles"]
    assert isinstance(data["compute_modes"], list) and data["compute_modes"]


def test_interactions_match_backend_policies(client):
    from oryx.modes import MODE_POLICIES, InteractionMode

    items = _catalog(client)["interactions"]
    assert len(items) == len(MODE_POLICIES) == 7
    assert {i["name"] for i in items} == set(MODE_POLICIES)
    # dontAsk  historically 被文档漏掉 —— 必须还在
    assert "dontAsk" in {i["name"] for i in items}

    keys = {
        "name", "label", "hint", "read_only",
        "skip_approval", "unattended", "coordination_only",
    }
    for item in items:
        assert set(item) == keys, f"{item['name']} 字段不齐：{set(item) ^ keys}"
        policy = MODE_POLICIES[item["name"]]
        assert item["label"] == InteractionMode(item["name"]).label
        assert item["hint"], "每个模式都要有一句给界面看的说明"
        assert item["read_only"] is (not policy.allow_write)
        assert item["skip_approval"] is policy.skip_approval
        assert item["unattended"] is policy.deny_on_ask
        assert item["coordination_only"] is policy.coordination_only
        # 系统提示词不能下发给前端
        assert "当前是【" not in item["hint"]
        assert len(item["hint"]) < 80


def test_styles_match_backend_styles(client):
    from oryx.styles import STYLES

    items = _catalog(client)["styles"]
    assert len(items) == len(STYLES) == 6
    assert {i["name"] for i in items} == set(STYLES)
    for item in items:
        assert set(item) == {"name", "label", "hint"}
        assert item["label"] == STYLES[item["name"]].label
        assert item["hint"], "每个风格都要有一句给界面看的说明"
        assert "表达风格：" not in item["hint"], "不要把系统提示词发给前端"


def test_compute_modes_are_exactly_the_two_supported_values(client):
    items = _catalog(client)["compute_modes"]
    assert {i["name"] for i in items} == {"efficiency", "local"}
    for item in items:
        assert set(item) == {"name", "label", "hint"}
        assert item["label"] and item["hint"]


def test_every_catalog_interaction_is_selectable(client):
    """列出来的每个交互模式都必须能真的切过去（含 dontAsk / delegate）。"""
    from oryx.domains.preference import preference_catalog  # noqa: F401

    names = [i["name"] for i in _catalog(client)["interactions"]]
    original = client.get("/api/state").json().get("interaction", "craft")
    try:
        for name in names:
            resp = client.post("/api/interaction", json={"interaction": name})
            assert resp.status_code == 200, f"{name}: {resp.text}"
            assert _data(resp)["interaction"] == name
            assert client.get("/api/state").json()["interaction"] == name
    finally:
        client.post("/api/interaction", json={"interaction": original})


def test_every_catalog_style_is_selectable(client):
    names = [i["name"] for i in _catalog(client)["styles"]]
    original = client.get("/api/state").json().get("style", "professional")
    try:
        for name in names:
            resp = client.post("/api/style", json={"style": name})
            assert resp.status_code == 200, f"{name}: {resp.text}"
            assert _data(resp)["style"] == name
            assert client.get("/api/state").json()["style"] == name
    finally:
        client.post("/api/style", json={"style": original})


def test_invalid_preferences_are_rejected(client):
    assert client.post("/api/interaction", json={"interaction": "nope"}).status_code == 400
    assert client.post("/api/style", json={"style": "nope"}).status_code == 400
    assert client.post("/api/mode", json={"mode": "nope"}).status_code == 400
    # 错误文案要把合法值列全，前端能直接展示给用户
    text = client.post("/api/interaction", json={"interaction": "nope"}).json()["detail"]
    for name in ("ask", "plan", "craft", "bypass", "dontAsk", "auto", "delegate"):
        assert name in text, f"错误文案漏了 {name}：{text}"


def test_catalog_function_matches_http_payload():
    """纯函数版本与 HTTP 响应一致 —— 便于别处复用，不绕一圈网络。"""
    from oryx.domains.preference import preference_catalog

    data = preference_catalog()
    assert set(data) == {"interactions", "styles", "compute_modes"}
    assert len(data["interactions"]) == 7
    assert len(data["styles"]) == 6
    assert {i["name"] for i in data["compute_modes"]} == {"efficiency", "local"}


def test_mode_switch_survives_missing_provider(client):
    """没配模型服务时切计算模式：已经存盘了，就不能报 500 说「切换失败」。"""
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.domains.preference import set_mode

    cfg = Config({
        **DEFAULT_CONFIG,
        "providers": {}, "active_provider": "",
        "local_provider": "", "cloud_provider": "",
    })
    result = set_mode(cfg, "local")
    assert result["mode"] == "local"
    assert result["provider"] in (None, "")
    assert result["model"] in (None, "")
    assert cfg.data["mode"] == "local", "模式必须真的落盘，不是只回个假成功"

    # HTTP 层同样不能 500（改完要能读回）
    original = client.get("/api/state").json().get("mode", "efficiency")
    try:
        resp = client.post("/api/mode", json={"mode": "local"})
        assert resp.status_code == 200, resp.text
        assert _data(resp)["mode"] == "local"
        assert client.get("/api/state").json()["mode"] == "local"
    finally:
        client.post("/api/mode", json={"mode": original})
