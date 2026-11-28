"""模块 9：设置剩余项与「日志监控」的 HTTP 契约。

取证结论：这批接口后端一直都在，缺的是新前端入口。本文件钉住两件
容易被「接个界面」掩盖的事：

  · `POST /api/features/toggle` 原来是 `req: dict` + `bool(req.get("value"))` ——
    传 `{"value": "false"}` 会得到 **True**（想关结果开了），而且 `key`
    不校验，随便一个名字都能写进开关文件，成为目录里看不见的幽灵键。
  · `GET /api/usage` 的 `hours` 原来没有边界：负数把 since 推到未来（恒为 0），
    超大值等于全表扫描。clamp 后的值必须回显在 `window_hours` 里，
    界面才不会把「近 1 小时」标成「近 30 天」。

另外把审计导出、自检、外观、记忆的**数据形状**锁住 —— 前端生成下载文件
和渲染卡片都依赖这些键，后端改字段名会让界面静默显示空白。

这些域返回的都是**裸对象**（老前端还在用），本轮刻意不改成 v2；
前端 `api.get` 本来就兼容两种形状。
"""
from __future__ import annotations

import json as jsonlib
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CORE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    sys.path.insert(0, str(CORE))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


@pytest.fixture()
def features_path(tmp_path, monkeypatch):
    """把开关文件指到用例自己的临时文件，不碰全局测试配置。

    `feature.py` 的 `FEATURES_PATH` 是模块级常量；改模块属性后
    `read_features` / `toggle` / `read_profile` 都会跟着走。
    """
    from oryx.domains import feature as domain

    path = tmp_path / "features.yaml"
    monkeypatch.setattr(domain, "FEATURES_PATH", path)
    return path


def _err(resp, status: int, needle: str = ""):
    """兼容生产 app 的统一错误体与 FastAPI 默认的 {detail}。"""
    assert resp.status_code == status, resp.text
    body = resp.json()
    msg = body.get("message") or body.get("detail") or ""
    if needle:
        assert needle in str(msg), body
    return body


# ── 特性开关 ────────────────────────────────────────────────────────


def test_feature_catalog_shape(client):
    body = client.get("/api/features").json()
    assert isinstance(body.get("profile"), str)
    assert isinstance(body.get("raw"), dict)
    flags = body.get("flags")
    assert isinstance(flags, list) and len(flags) > 10
    for card in flags:
        assert {"key", "description", "enabled", "value", "implemented"} <= set(card)
        assert isinstance(card["enabled"], bool)
        assert isinstance(card["implemented"], bool)


def test_toggle_string_false_is_false(client, features_path):
    """回归：`bool("false")` 是 True —— 字符串 'false' 必须真的关掉。"""
    res = client.post("/api/features/toggle", json={"key": "EnableOffice", "value": "false"})
    assert res.status_code == 200, res.text
    assert res.json()["value"] is False
    assert res.json()["key"] == "EnableOffice"

    flags = client.get("/api/features").json()["flags"]
    card = next(x for x in flags if x["key"] == "EnableOffice")
    assert card["value"] is False
    assert card["enabled"] is False
    # 落盘内容也得是布尔假，不是字符串
    saved = jsonlib.loads(features_path.read_text("utf-8"))
    assert saved["flags"]["EnableOffice"] is False


def test_toggle_bool_true_roundtrip(client, features_path):
    res = client.post("/api/features/toggle", json={"key": "EnableOffice", "value": True})
    assert res.status_code == 200, res.text
    assert res.json()["value"] is True
    flags = client.get("/api/features").json()["flags"]
    assert next(x for x in flags if x["key"] == "EnableOffice")["value"] is True


def test_toggle_rejects_unknown_key(client, features_path):
    _err(
        client.post("/api/features/toggle", json={"key": "MadeUpFlag", "value": True}),
        400, "没有这个特性开关",
    )
    assert not features_path.exists(), "非法 key 不能写盘"


def test_toggle_rejects_empty_key(client, features_path):
    _err(
        client.post("/api/features/toggle", json={"key": "   ", "value": True}),
        400, "key 不能为空",
    )


def test_toggle_rejects_extra_field_and_missing_value(client, features_path):
    _err(client.post("/api/features/toggle", json={"key": "EnableOffice", "value": True, "oops": 1}), 422)
    _err(client.post("/api/features/toggle", json={"key": "EnableOffice"}), 422)


def test_toggle_blocked_when_self_switch_disabled(client, features_path):
    """`EnableFeatureFlags` 关掉后就不能再从界面改开关（自锁语义要如实提示）。"""
    features_path.write_text(
        jsonlib.dumps({"profile": "personal", "flags": {"EnableFeatureFlags": False}}),
        encoding="utf-8",
    )
    _err(
        client.post("/api/features/toggle", json={"key": "EnableOffice", "value": False}),
        403, "EnableFeatureFlags 已关闭",
    )


# ── 用量汇总 ────────────────────────────────────────────────────────


def test_usage_default_shape(client):
    body = client.get("/api/usage").json()
    assert body["window_hours"] == 720
    for key in ("total", "today"):
        assert {"calls", "input_tokens", "output_tokens"} <= set(body[key])
    assert isinstance(body["by_model"], list)


def test_usage_hours_clamped_low(client):
    body = client.get("/api/usage?hours=-5").json()
    assert body["window_hours"] == 1


def test_usage_hours_clamped_high(client):
    body = client.get("/api/usage?hours=99999").json()
    assert body["window_hours"] == 8760


# ── 审计导出的数据源 ────────────────────────────────────────────────


def test_audit_missing_session_is_404(client):
    _err(client.get(f"/api/audit/{uuid.uuid4().hex}"), 404, "没有这个会话")


def test_audit_rows_are_exportable(client):
    """导出是前端本地生成文件 —— 后端只要保证行齐全且能 JSON 序列化。"""
    import oryx.server as server_mod

    created = client.post("/api/sessions", json={})
    assert created.status_code == 200, created.text
    task_id = created.json()["data"]["task_id"]
    try:
        server_mod.store.audit(
            task_id, "step-1", "write_file", {"path": "a.txt"}, 2, "approved", "ok",
        )
        body = client.get(f"/api/audit/{task_id}").json()
        assert body["task_id"] == task_id
        assert len(body["audit"]) == 1
        row = body["audit"][0]
        assert {"id", "ts", "task_id", "step_id", "tool", "args", "risk", "decision", "outcome"} <= set(row)
        assert row["tool"] == "write_file"
        assert jsonlib.dumps(body["audit"], ensure_ascii=False)  # 可序列化
    finally:
        client.delete(f"/api/sessions/{task_id}")


# ── 环境自检 ────────────────────────────────────────────────────────


def test_doctor_shape(client):
    body = client.get("/api/doctor").json()
    assert {"version", "python", "platform", "network_note", "providers",
            "active_provider", "dependencies", "checks", "hints"} <= set(body)
    assert isinstance(body["checks"], list)
    for check in body["checks"]:
        assert {"name", "ok", "detail"} <= set(check)
    assert isinstance(body["hints"], list)


# ── 外观 ────────────────────────────────────────────────────────────


def test_appearance_shape(client):
    body = client.get("/api/appearance").json()
    assert {"theme", "background", "background_exists", "background_fit"} <= set(body)
    assert body["theme"] in ("light", "dark")
    assert isinstance(body["background_exists"], bool)


def test_appearance_rejects_invalid_values(client):
    _err(client.post("/api/appearance/theme", json={"theme": "neon"}), 400, "theme 只能是 light 或 dark")
    _err(client.post("/api/appearance/background-fit", json={"fit": "stretch"}), 400, "fit 只能是")
    _err(client.post("/api/appearance/background", json={"data_url": "not-a-data-url"}), 400, "只支持")


# ── 长期记忆 ────────────────────────────────────────────────────────


def test_memory_crud_and_null_guard(client):
    _err(client.post("/api/memory", json={"text": None}), 400, "text 不能为空")
    _err(client.post("/api/memory", json={"text": "   "}), 400, "text 不能为空")

    text = f"模块9回归记忆-{uuid.uuid4().hex[:8]}"
    created_id = None
    try:
        body = client.post("/api/memory", json={"text": text, "tags": "回归"}).json()
        assert body["text"] == text
        assert body["deduped"] is False
        created_id = body["id"]
        assert any(m["id"] == created_id for m in client.get("/api/memory").json()["memories"])

        again = client.post("/api/memory", json={"text": text}).json()
        assert again["deduped"] is True
        assert again["id"] == created_id

        deleted = client.delete(f"/api/memory/{created_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] == created_id
        created_id = None
        _err(client.delete(f"/api/memory/{body['id']}"), 404, "没有这条记忆")
    finally:
        if created_id:
            client.delete(f"/api/memory/{created_id}")
