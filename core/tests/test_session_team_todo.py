"""会话挂团队 + 用户添加待办。

这两件事以前**只有 agent 能做**：
  · 待办只有 `task_create` 工具能建，用户想自己列个清单都做不到；
  · 团队是全局的，跟会话没有任何关联 —— 一个团队开了十几个对话都用它，
    成员分不清自己这一轮属于谁。

这里把两条链路钉死：写进去 → 读回来 → 改 → 再读。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture(scope="module")
def client():
    import os

    os.environ.setdefault("ORYX_HOME", str(Path(__file__).resolve().parent / "_tmp_home"))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


def _data(resp):
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def test_user_can_add_todo(client):
    """用户手动加一条待办 —— 以前只有 agent 的 task_create 工具能建。"""
    import oryx.server as sm

    task_id = sm.store.create_task("待办闭环", "craft", "minimax")
    try:
        r = client.post(f"/api/sessions/{task_id}/todos", json={"title": "读一遍 main.py"})
        assert r.status_code == 200
        created = _data(r)
        assert created["title"] == "读一遍 main.py"
        assert created["status"] == "pending"

        # 读回来
        listed = _data(client.get(f"/api/sessions/{task_id}/todos"))
        assert any(t["title"] == "读一遍 main.py" for t in listed)

        # 会话详情里也带得到（对话里的任务条靠它）
        detail = _data(client.get(f"/api/sessions/{task_id}"))
        assert any(t["title"] == "读一遍 main.py" for t in detail["todos"])
    finally:
        sm.store.delete_session(task_id)


def test_add_todo_validates(client):
    """少字段要明确报错，别静默存个半成品。"""
    import oryx.server as sm

    task_id = sm.store.create_task("待办校验", "craft", "minimax")
    try:
        assert client.post(f"/api/sessions/{task_id}/todos", json={"title": "  "}).status_code == 400
        assert client.post(f"/api/sessions/{task_id}/todos", json={"title": "x", "status": "?"}).status_code == 400
        assert client.post("/api/sessions/nope/todos", json={"title": "x"}).status_code == 404
    finally:
        sm.store.delete_session(task_id)


def test_session_can_have_its_own_team(client):
    """每个会话挂一个自己的团队。"""
    import oryx.server as sm

    task_id = sm.store.create_task("挂团队", "craft", "minimax")
    try:
        r = client.put(f"/api/sessions/{task_id}", json={"team": "我的小组"})
        assert r.status_code == 200
        assert _data(r)["team"] == "我的小组"
        assert sm.store.session_team(task_id) == "我的小组"

        # 会话详情里也带得到（前端打开会话时读它）
        detail = _data(client.get(f"/api/sessions/{task_id}"))
        assert detail["team"] == "我的小组"

        # 只改名 —— 团队**不能**被改掉
        r2 = client.put(f"/api/sessions/{task_id}", json={"title": "新名字"})
        assert r2.status_code == 200
        data = _data(r2)
        assert data["title"] == "新名字"
        assert data["team"] == "我的小组", "只改名把团队弄丢了"
        assert sm.store.session_team(task_id) == "我的小组"

        # 摘掉
        client.put(f"/api/sessions/{task_id}", json={"team": ""})
        assert sm.store.session_team(task_id) == ""
    finally:
        sm.store.delete_session(task_id)


def test_two_sessions_can_have_different_teams(client):
    """团队是全局的、会话是私人的 —— 两个会话挂不同的团队互不影响。"""
    import oryx.server as sm

    a = sm.store.create_task("会话 A", "craft", "minimax")
    b = sm.store.create_task("会话 B", "craft", "minimax")
    try:
        client.put(f"/api/sessions/{a}", json={"team": "甲组"})
        client.put(f"/api/sessions/{b}", json={"team": "乙组"})
        assert sm.store.session_team(a) == "甲组"
        assert sm.store.session_team(b) == "乙组"
    finally:
        sm.store.delete_session(a)
        sm.store.delete_session(b)
