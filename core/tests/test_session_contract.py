"""session 域的 v2 契约。

这是**样板域**的契约测试 —— 后面 21 个域照这个改，
所以这里定下的形状就是全项目 v2 的样子：

    成功 → `{code: 0, data: <领域对象>, message: ""}`
    失败 → `{code: <HTTP 状态码>, data: null, message: "...", detail: "..."}`

`data` 的规则（spec §1.1）
-------------------------
`data` 恒为该接口的**领域对象**：

    · 领域对象本身就是一个列表 → `data` 是**数组**
    · 否则 → `data` 是**对象**

这条规则最容易写错的地方是「多包一层」：
`{"sessions": [...]}` 这种包法没有信息量，前端却要为此多写一次 `.sessions`。
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 临时目录建在**测试专用的根目录**下（conftest 已把它指到 D 盘），
# 而且用完删掉 —— 以前直接 mkdtemp 到 %TEMP%（C 盘），
# 建完从不管，每次跑测试都在 C 盘留一个孤儿目录。
# 临时目录建在**测试专用的根目录**下（conftest 已把它指到 D 盘），
# 而且用完删掉 —— 以前直接 mkdtemp 到 %TEMP%（C 盘），
# 建完从不管，每次跑测试都在 C 盘留一个孤儿目录。
_TMP_HOME = Path(tempfile.mkdtemp(prefix="oryx-session-"))
os.environ.setdefault("ORYX_HOME", str(_TMP_HOME))
atexit.register(lambda: shutil.rmtree(_TMP_HOME, ignore_errors=True))
atexit.register(lambda: shutil.rmtree(_TMP_HOME, ignore_errors=True))


@pytest.fixture(scope="module")
def client():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


@pytest.fixture()
def store():
    import oryx.server as server_mod

    return server_mod.store


def _data(resp):
    body = resp.json()
    assert body["code"] == 0, body
    assert body["message"] == ""
    return body["data"]


def _err(resp):
    body = resp.json()
    assert body["code"] == resp.status_code, body
    assert body["data"] is None
    assert body["message"], body
    # detail 是给旧前端留的 —— 它到处写 `x.detail || r.status`
    assert body["detail"] == body["message"]
    return body


# ── 列表 ────────────────────────────────────────────────────────────


def test_sessions_list_data_is_array(client, store):
    """列表的 data 是**数组**，不是 {"sessions": [...]}。"""
    task_id = store.create_task("契约测试会话", "craft", "minimax")
    store.add_message(task_id, "user", "你好")
    try:
        data = _data(client.get("/api/sessions"))
        assert isinstance(data, list)
        assert any(s["id"] == task_id for s in data)
        assert "sessions" not in data
    finally:
        store.delete_session(task_id)


def test_sessions_list_limit(client, store):
    ids = [store.create_task(f"限流 {i}", "craft", "minimax") for i in range(4)]
    try:
        data = _data(client.get("/api/sessions?limit=2"))
        assert len(data) == 2
    finally:
        for task_id in ids:
            store.delete_session(task_id)


# ── 详情 ────────────────────────────────────────────────────────────


def test_session_detail_shape(client, store):
    task_id = store.create_task("详情契约", "craft", "minimax")
    store.add_message(task_id, "user", "在吗")
    try:
        data = _data(client.get(f"/api/sessions/{task_id}"))
        assert isinstance(data, dict)
        for key in ("task", "title", "messages", "steps", "todos", "usage"):
            assert key in data, f"详情少了 {key}"
        assert data["messages"][0]["content"] == "在吗"
    finally:
        store.delete_session(task_id)


def test_session_approvals_is_array(client, store):
    """审批兜底接口：data 是数组，内核没等确认时是空数组。"""
    task_id = store.create_task("审批兜底契约", "craft", "minimax")
    try:
        data = _data(client.get(f"/api/sessions/{task_id}/approvals"))
        assert isinstance(data, list)
    finally:
        store.delete_session(task_id)


def test_session_approvals_404(client):
    _err(client.get("/api/sessions/no-such-task/approvals"))


def test_session_detail_404(client):
    resp = client.get("/api/sessions/does-not-exist")
    assert resp.status_code == 404
    _err(resp)


# ── 改名 ────────────────────────────────────────────────────────────


def test_rename_session(client, store):
    task_id = store.create_task("原来的名字", "craft", "minimax")
    try:
        data = _data(client.put(f"/api/sessions/{task_id}", json={"title": "新名字"}))
        assert data["renamed"] == task_id
        assert data["title"] == "新名字"
    finally:
        store.delete_session(task_id)


def test_rename_with_null_title_clears_name(client, store):
    """`{"title": null}` = 撤掉自定义名字。

    以前 `str(req.get("title"))` 会把字面量 "None" 存成会话名。
    """
    task_id = store.create_task("原始目标", "craft", "minimax")
    try:
        client.put(f"/api/sessions/{task_id}", json={"title": "临时名"})
        data = _data(client.put(f"/api/sessions/{task_id}", json={"title": None}))
        assert data["title"] == ""
        listed = _data(client.get("/api/sessions"))
        mine = next(s for s in listed if s["id"] == task_id)
        assert mine["renamed"] is False
        assert "None" not in mine["title"]
    finally:
        store.delete_session(task_id)


def test_rename_404(client):
    resp = client.put("/api/sessions/nope", json={"title": "x"})
    assert resp.status_code == 404
    _err(resp)


def test_rename_missing_title_clears_name(client, store):
    """缺 title = 撤掉名字 —— 与「传空串」同义。

    所以这里不是 422：PUT 的语义是「把标题换成这个」，
    没给就是换成空。给默认值而不是必填，是刻意的。
    """
    task_id = store.create_task("字段校验", "craft", "minimax")
    try:
        client.put(f"/api/sessions/{task_id}", json={"title": "临时名"})
        data = _data(client.put(f"/api/sessions/{task_id}", json={}))
        assert data["title"] == ""
    finally:
        store.delete_session(task_id)


def test_rename_wrong_type_is_422(client, store):
    """契约层：title 必须是字符串。"""
    task_id = store.create_task("类型校验", "craft", "minimax")
    try:
        resp = client.put(f"/api/sessions/{task_id}", json={"title": 123})
        assert resp.status_code == 422
    finally:
        store.delete_session(task_id)


# ── 待办 ────────────────────────────────────────────────────────────


def test_todo_status_flow(client, store):
    task_id = store.create_task("待办契约", "craft", "minimax")
    todo = store.add_todo(task_id, "写代码")
    try:
        data = _data(
            client.put(
                f"/api/sessions/{task_id}/todos/{todo['id']}",
                json={"status": "done"},
            )
        )
        assert data["updated"] == todo["id"]
        assert data["todo"]["status"] == "done"
    finally:
        store.delete_session(task_id)


def test_todo_bad_status_is_400(client, store):
    task_id = store.create_task("待办校验", "craft", "minimax")
    todo = store.add_todo(task_id, "写代码")
    try:
        resp = client.put(
            f"/api/sessions/{task_id}/todos/{todo['id']}", json={"status": "瞎写"}
        )
        assert resp.status_code == 400
        assert "pending / in_progress / done" in resp.json()["message"]
    finally:
        store.delete_session(task_id)


def test_todo_missing_status_is_422(client, store):
    task_id = store.create_task("待办缺字段", "craft", "minimax")
    todo = store.add_todo(task_id, "写代码")
    try:
        resp = client.put(f"/api/sessions/{task_id}/todos/{todo['id']}", json={})
        assert resp.status_code == 422
    finally:
        store.delete_session(task_id)


def test_todos_list_data_is_array(client, store):
    task_id = store.create_task("待办列表", "craft", "minimax")
    store.add_todo(task_id, "第一件事")
    try:
        data = _data(client.get(f"/api/sessions/{task_id}/todos"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "第一件事"
    finally:
        store.delete_session(task_id)


# ── 步骤与用量 ──────────────────────────────────────────────────────


def test_steps_list_data_is_array(client, store):
    task_id = store.create_task("步骤列表", "craft", "minimax")
    step_id = store.create_step(task_id, 1, "fs_read", {"path": "x.txt"}, 0)
    store.finish_step(step_id, "done", {"ok": True})
    try:
        data = _data(client.get(f"/api/sessions/{task_id}/steps"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["tool"] == "fs_read"
    finally:
        store.delete_session(task_id)


def test_usage_shape(client, store):
    task_id = store.create_task("用量", "craft", "minimax")
    store.add_message(task_id, "user", "在吗")
    try:
        data = _data(client.get(f"/api/sessions/{task_id}/usage"))
        assert isinstance(data, dict)
        assert data["task_id"] == task_id
        assert data["messages"] == 1
        assert "usage" in data
    finally:
        store.delete_session(task_id)


def test_usage_404(client):
    resp = client.get("/api/sessions/nope/usage")
    assert resp.status_code == 404
    _err(resp)


# ── 删除 ────────────────────────────────────────────────────────────


def test_delete_session(client, store):
    task_id = store.create_task("待删", "craft", "minimax")
    data = _data(client.delete(f"/api/sessions/{task_id}"))
    assert data["deleted"] == task_id
    assert client.get(f"/api/sessions/{task_id}").status_code == 404


def test_delete_missing_session_404(client):
    resp = client.delete("/api/sessions/nope")
    assert resp.status_code == 404
    _err(resp)
