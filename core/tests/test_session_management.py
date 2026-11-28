"""会话列表与详情（模块 5）的域契约。

前端要接的四个动作，后端都得有而且语义正确：

  · 新建空会话：status=idle（不是 running），config_snapshot 留空，
    否则界面会误以为「正在运行」而禁止发送第一句话；
  · 分页 + 服务端搜索：`q` 过滤**全部**会话，不是让前端在已加载的一页里筛；
    用户搜 `%` / `_` 是字面量，不是通配符；
  · 删除会话：连检查点一起清干净 —— 模块 4 的回退不能对着已删会话生效；
  · 手动加待办：空标题 / 非法状态给中文 400，不是 422 的机器话。

另一个坑在前端：`updated_at` 是**秒**，`new Date(ts)` 会当成毫秒，
分组会全落到「更早」，由 `apps/web/scripts/check-sessions.cjs` 守着。
"""
from __future__ import annotations

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
def store():
    import oryx.server as server_mod

    return server_mod.store


def _data(resp):
    body = resp.json()
    assert body["code"] == 0, body
    assert body["message"] == ""
    return body["data"]


def _err(resp, status: int, needle: str = ""):
    body = resp.json()
    assert resp.status_code == status, body
    assert body["code"] == status, body
    assert body["data"] is None
    assert body["message"], body
    if needle:
        assert needle in body["message"], body
    return body


def _tag() -> str:
    return "M5-" + uuid.uuid4().hex[:8]


# ── 新建空会话 ──────────────────────────────────────────────────────


def test_create_empty_session_without_body(client, store):
    """「+ 新会话」不该被要求先填名字 —— body 整个省掉也要能建。"""
    data = _data(client.post("/api/sessions"))
    task_id = data["task_id"]
    try:
        assert data["status"] == "idle", "空会话不能是 running，否则界面禁止发送"
        assert data["title"] == "新会话"
        detail = _data(client.get(f"/api/sessions/{task_id}"))
        assert detail["messages"] == []
        assert detail["todos"] == []
        assert detail["steps"] == []
        listed = _data(client.get(f"/api/sessions?q=&limit=200"))
        hit = next((s for s in listed if s["id"] == task_id), None)
        assert hit is not None
        assert hit["title"] == "新会话", "列表里不能是一条空白标题"
        assert hit["renamed"] is False
    finally:
        store.delete_session(task_id)


def test_create_session_with_title_marks_renamed(client, store):
    tag = _tag()
    data = _data(client.post("/api/sessions", json={"title": tag}))
    task_id = data["task_id"]
    try:
        assert data["title"] == tag
        hit = next(s for s in _data(client.get(f"/api/sessions?q={tag}"))
                   if s["id"] == task_id)
        assert hit["title"] == tag
        assert hit["renamed"] is True
    finally:
        store.delete_session(task_id)


def test_create_session_rejects_long_title(client):
    _err(client.post("/api/sessions", json={"title": "名" * 61}), 400, "太长")


def test_empty_session_has_no_frozen_snapshot(client, store):
    """配置快照要等第一句话才固化 —— 空会话提前锁死一份没选过的配置是假的。"""
    task_id = _data(client.post("/api/sessions"))["task_id"]
    try:
        task = store.get_task(task_id)
        assert task is not None, "内核续聊靠 get_task(session_id) 复用，必须查得到"
        assert task["status"] == "idle"
        assert not task.get("config_snapshot"), "空会话不该预先固化运行配置"
    finally:
        store.delete_session(task_id)


# ── 分页与服务端搜索 ────────────────────────────────────────────────


def test_offset_pages_are_disjoint(client, store):
    tag = _tag()
    ids = [store.create_task(f"{tag}-{i}", "craft", "minimax") for i in range(5)]
    try:
        first = _data(client.get(f"/api/sessions?q={tag}&limit=2&offset=0"))
        second = _data(client.get(f"/api/sessions?q={tag}&limit=2&offset=2"))
        third = _data(client.get(f"/api/sessions?q={tag}&limit=2&offset=4"))
        assert len(first) == 2 and len(second) == 2 and len(third) == 1
        seen = [s["id"] for s in first + second + third]
        assert len(set(seen)) == 5, "分页把同一条会话重复发了"
        assert set(seen) == set(ids)
    finally:
        for task_id in ids:
            store.delete_session(task_id)


def test_search_runs_in_sql_not_in_loaded_page(client, store):
    """目标会话排在第一页之外，`q` 也得把它搜出来。

    如果后端先 LIMIT 再让前端过滤，limit=1 时这一条必然是空。
    """
    tag = _tag()
    ids = [store.create_task(f"与本搜索无关的噪音 {i}", "craft", "minimax")
           for i in range(3)]
    target = store.create_task(f"目标 {tag}", "craft", "minimax")
    # 把目标压到「最新的一条」之后：若先 LIMIT 再筛，它根本进不了那一页。
    with store._conn() as conn:
        conn.execute("UPDATE tasks SET updated_at=updated_at-100000 WHERE id=?", (target,))
    try:
        found = _data(client.get("/api/sessions", params={"q": tag, "limit": 1}))
        assert found, "服务端搜索不能在 LIMIT 之后做"
        assert found[0]["id"] == target
    finally:
        for task_id in ids + [target]:
            store.delete_session(task_id)


def test_search_special_chars_are_literals(client, store):
    """`%` / `_` 是用户想搜的字面量，不能被 LIKE 当通配符。"""
    tag = _tag()
    wanted = store.create_task(f"{tag}%_", "craft", "minimax")
    noise = store.create_task(f"{tag}普通会话", "craft", "minimax")
    try:
        found = _data(client.get("/api/sessions", params={"q": tag + "%_", "limit": 50}))
        assert [s["id"] for s in found] == [wanted], "通配符被当成条件了"
    finally:
        for task_id in (wanted, noise):
            store.delete_session(task_id)


def test_search_matches_custom_title(client, store):
    tag = _tag()
    task_id = store.create_task("原始提问完全无关", "craft", "minimax")
    try:
        _data(client.put(f"/api/sessions/{task_id}", json={"title": tag}))
        found = _data(client.get(f"/api/sessions?q={tag}"))
        assert any(s["id"] == task_id for s in found), "改名后要能按新名字搜到"
    finally:
        store.delete_session(task_id)


def test_limit_and_offset_are_clamped(store):
    """超范围的 limit / 负数 offset 不能把 SQL 打炸。"""
    from oryx.domains import session as domain

    task_id = store.create_task("clamp 边界", "craft", "minimax")
    try:
        assert len(domain.list_sessions(store, limit=0)) == 1
        assert len(domain.list_sessions(store, limit=9999, offset=-10)) <= 200
    finally:
        store.delete_session(task_id)


# ── 删除会话要连检查点一起清 ────────────────────────────────────────


def test_delete_session_clears_checkpoints_and_files(client, store):
    """删会话后不能留下孤儿检查点 —— 否则回退还能改文件、裁消息。"""
    task_id = store.create_task("要删掉的会话", "craft", "minimax")
    cp = store.create_checkpoint(task_id, 0, "第一轮")
    store.record_checkpoint_file(cp, "notes.txt", None, "内容", False)
    assert store.get_checkpoint(cp) is not None
    assert store.checkpoint_files(cp), "测试前提：检查点里有文件快照"

    _data(client.delete(f"/api/sessions/{task_id}"))

    assert store.get_checkpoint(cp) is None, "检查点主表没清"
    assert store.list_checkpoints(task_id) == []
    with store._conn() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM checkpoint_files WHERE checkpoint_id=?", (cp,)
        ).fetchone()[0]
    assert left == 0, "checkpoint_files 子表没清（外键悬空）"
    _err(client.post(f"/api/checkpoints/{cp}/rewind", json={"scope": "Code"}),
         404, "没有这个检查点")


# ── 手动加待办 ──────────────────────────────────────────────────────


def test_create_todo_via_http(client, store):
    task_id = store.create_task("加待办", "craft", "minimax")
    try:
        todo = _data(client.post(f"/api/sessions/{task_id}/todos",
                                 json={"title": "买牛奶"}))
        assert todo["title"] == "买牛奶"
        assert todo["status"] == "pending"
        assert todo["id"]
        listed = _data(client.get(f"/api/sessions/{task_id}/todos"))
        assert any(t["id"] == todo["id"] for t in listed)
    finally:
        store.delete_session(task_id)


@pytest.mark.parametrize("payload,needle", [
    ({"title": "   "}, "标题"),
    ({"title": "字" * 201}, "太长"),
    ({"title": "有事", "status": "瞎写"}, "status 只能是"),
])
def test_create_todo_bad_input_is_chinese_400(client, store, payload, needle):
    task_id = store.create_task("待办校验", "craft", "minimax")
    try:
        _err(client.post(f"/api/sessions/{task_id}/todos", json=payload), 400, needle)
    finally:
        store.delete_session(task_id)
