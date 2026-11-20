"""接口闭环自检：每一条「写进去 → 读出来 → 改掉 → 删掉」都跑通。

为什么要有这个
--------------
「接口存在」不等于「功能能用」。体检时发现两类假绿：

  · 域里的 action 写了，但**从没被路由调用**（改了半天没接上）
  · 路由能通，但**读回来是空的**（写进去了，读的是另一个地方）

所以这里不看「有没有这条路由」，而是**真的走一遍**：
创建 → 读回 → 修改 → 读回 → 删除 → 确认没了。

覆盖的是**用户真会用的那些流程**，不是每个接口都测一遍 ——
但凡进了这个清单的，都是「坏了用户立刻会骂」的。

跑法
----
    python -m pytest core/tests/test_api_closed_loop.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP_HOME = Path(tempfile.mkdtemp(prefix="oryx-loop-"))
os.environ.setdefault("ORYX_HOME", str(_TMP_HOME))


@pytest.fixture(scope="module")
def client():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


def _data(resp):
    """拿到领域数据。

    v2 的响应是 `{code, data, message}`；**还没改造的域返回裸对象**。
    两种都吃 —— 跟前端 `lib/api.ts` 的 `unwrap` 同一套规则
    （认「code 是数字 + message 是字符串 + data 键存在」这三个特征）。
    """
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


# ── 会话：创建 → 读 → 改 → 删 ──────────────────────────────────────


def test_session_closed_loop(client):
    """会话是应用的主线：建 → 查 → 改名 → 改待办 → 删。"""
    import oryx.server as sm

    store = sm.store
    task_id = store.create_task("闭环测试会话", "craft", "minimax")
    store.add_message(task_id, "user", "在吗")
    try:
        # ① 列表里能看到
        listed = _data(client.get("/api/sessions"))
        assert any(s["id"] == task_id for s in listed)

        # ② 详情能拿到消息
        detail = _data(client.get(f"/api/sessions/{task_id}"))
        assert detail["messages"][0]["content"] == "在吗"

        # ③ 改名 → 列表里的标题跟着变
        _data(client.put(f"/api/sessions/{task_id}", json={"title": "改过的名字"}))
        again = _data(client.get("/api/sessions"))
        mine = next(s for s in again if s["id"] == task_id)
        assert mine["title"] == "改过的名字"
        assert mine["renamed"] is True

        # ④ 待办：建 → 改状态 → 读回
        todo = store.add_todo(task_id, "闭环待办")
        updated = _data(
            client.put(
                f"/api/sessions/{task_id}/todos/{todo['id']}",
                json={"status": "done"},
            )
        )
        assert updated["todo"]["status"] == "done"
        todos = _data(client.get(f"/api/sessions/{task_id}/todos"))
        assert any(t["status"] == "done" for t in todos)

        # ⑤ 步骤与用量
        step_id = store.create_step(task_id, 1, "fs_read", {"path": "x"}, 0)
        store.finish_step(step_id, "done", {"ok": True})
        steps = _data(client.get(f"/api/sessions/{task_id}/steps"))
        assert len(steps) == 1
        usage = _data(client.get(f"/api/sessions/{task_id}/usage"))
        assert usage["task_id"] == task_id
        assert usage["steps"] == 1

        # ⑥ 删除 → 读不到
        _data(client.delete(f"/api/sessions/{task_id}"))
        assert client.get(f"/api/sessions/{task_id}").status_code == 404
    finally:
        store.delete_session(task_id)


# ── 长期记忆：记 → 查 → 忘 ─────────────────────────────────────────


def test_memory_closed_loop(client):
    """记忆是用户手动攒的，写进去要能查出来、删得掉。"""
    added = _data(client.post("/api/memory", json={"text": "闭环测试记忆", "tags": "t"}))
    mem_id = added["id"]
    try:
        listed = _data(client.get("/api/memory?limit=50"))["memories"]
        assert any(m["id"] == mem_id for m in listed)

        _data(client.delete(f"/api/memory/{mem_id}"))
        after = _data(client.get("/api/memory?limit=50"))["memories"]
        assert not any(m["id"] == mem_id for m in after)
    finally:
        client.delete(f"/api/memory/{mem_id}")


# ── 模型服务：列 → 改 → 激活 ───────────────────────────────────────


def test_provider_closed_loop(client):
    """模型服务：改完要能读回来（这条闭环以前是断的 —— 见 test_config_sync）。

    这里只验「改 → 读」，不真的调模型。
    """
    created = _data(
        client.post(
            "/api/providers",
            json={
                "name": "闭环测试服务",
                "type": "anthropic",
                "base_url": "https://example.invalid/anthropic",
                "model": "probe-model",
                "api_key": "sk-loop-probe",
            },
        )
    )
    name = created.get("name") or "闭环测试服务"
    try:
        listed = _data(client.get("/api/providers"))
        # 形状是 {active, local, items[]} —— items 才是 provider 列表
        assert any(p.get("name") == name for p in listed["items"])

        _data(
            client.put(
                f"/api/providers/{name}",
                json={"model": "probe-model-2", "api_key": "sk-loop-probe-2"},
            )
        )
        again = _data(client.get("/api/providers"))
        mine = next(p for p in again["items"] if p.get("name") == name)
        assert mine.get("model") == "probe-model-2"
    finally:
        client.delete(f"/api/providers/{name}")


# ── 大屏三接口：能取到、形状对 ──────────────────────────────────────


def test_overview_closed_loop(client):
    """大屏三个接口要能一起取到，且形状对得上页面。"""
    teams = _data(client.get("/api/overview/teams"))
    assert isinstance(teams["teams"], list)
    assert isinstance(teams["agents"], list)
    assert teams["team_count"] == len(teams["teams"])
    assert teams["member_count"] == sum(t["member_count"] for t in teams["teams"])

    runs = _data(client.get("/api/overview/runs?limit=5"))
    assert isinstance(runs["runs"], list)
    assert runs["window"] == 5
    assert runs["total"] >= len(runs["runs"])

    sugs = _data(client.get("/api/overview/suggestions"))
    assert isinstance(sugs["suggestions"], list)
    assert sugs["count"] == len(sugs["suggestions"])


# ── 外观：主题切换 → 读回 ──────────────────────────────────────────


def test_appearance_closed_loop(client):
    """主题切了要能读回来（前端靠它决定明暗）。"""
    _data(client.post("/api/appearance/theme", json={"theme": "dark"}))
    assert _data(client.get("/api/appearance"))["theme"] == "dark"
    _data(client.post("/api/appearance/theme", json={"theme": "light"}))
    assert _data(client.get("/api/appearance"))["theme"] == "light"


# ── 对话参数：交互/风格/模式 切了要生效 ────────────────────────────


def test_preference_closed_loop(client):
    """对话参数是「切了立刻生效」的一类 —— 改完要在 /api/state 里看得见。"""
    _data(client.post("/api/interaction", json={"interaction": "plan"}))
    assert _data(client.get("/api/state"))["interaction"] == "plan"

    _data(client.post("/api/interaction", json={"interaction": "craft"}))
    assert _data(client.get("/api/state"))["interaction"] == "craft"


# ── 工作流模板：列 → 取 → 删 ──────────────────────────────────────


def test_workflow_templates_closed_loop(client):
    """工作流模板：列表能取到，且每条都有名字（前端要渲染成入口）。"""
    templates = _data(client.get("/api/workflows/templates"))
    items = templates if isinstance(templates, list) else templates.get("templates", [])
    assert isinstance(items, list)
    for t in items:
        assert t.get("name"), f"模板没有名字，前端渲染不出来：{t}"


# ── 界面扩展：列 → 取 ──────────────────────────────────────────────


def test_ui_modules_closed_loop(client):
    """界面扩展的模块清单要能取到（前端启动时拉一次）。

    形状是 {extensions, skipped, disabled, mount_points, cap_rules, cap_notes}。
    前端启动时拉一次，按 extensions 里的 url 动态 import。
    """
    payload = _data(client.get("/api/ui/modules"))
    for key in ("extensions", "skipped", "disabled", "mount_points"):
        assert isinstance(payload[key], list), f"{key} 不是列表：{payload[key]}"
    assert isinstance(payload["cap_rules"], dict)
    # 每个已加载的扩展都要有 id 和 url —— 前端靠它们动态 import
    for m in payload["extensions"]:
        assert m.get("id"), f"扩展没有 id：{m}"
        assert m.get("url"), f"扩展没有 url：{m}"
