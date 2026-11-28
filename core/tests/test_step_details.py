"""执行步骤详情的落库契约。

前端「执行步骤」面板要展示参数、结果、报错、失败分类、只读自证、产物路径。
这些字段以前**只在** `step.done` / `step.failed` 的 SSE 事件里，
页面一刷新就没了：失败分类退回笼统的「失败」、产物路径消失。

本文件把「事件里有的，落库里也该有」钉住，并顺带验证老调用点兼容、
老库能幂等补列。对应改动：`store.py` 的 steps 表与 `finish_step()`、
`loop.py:3094` 的收尾调用。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


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
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def test_successful_step_exposes_all_detail_fields(client, store):
    """成功步骤：args / result / 耗时 / 分类 / 产物 / 只读自证都能查到。"""
    task_id = store.create_task("步骤详情-成功", "craft", "minimax")
    step_id = store.create_step(
        task_id, 1, "fs_write", {"path": "a.txt", "content": "hi"}, 1
    )
    store.finish_step(
        step_id, "done", result="已写入 a.txt",
        artifact_path="D:/work/a.txt", mutated=True,
    )
    try:
        step = _data(client.get(f"/api/sessions/{task_id}"))["steps"][0]
        assert step["id"] == step_id
        assert step["tool"] == "fs_write"
        assert step["state"] == "done"
        assert step["risk"] == 1
        assert json.loads(step["args"]) == {"path": "a.txt", "content": "hi"}
        assert json.loads(step["result"]) == "已写入 a.txt"
        assert step["error"] is None
        assert step["error_class"] is None
        assert step["artifact_path"] == "D:/work/a.txt"
        assert step["mutated"] == 1
        assert step["started_at"] is not None
        assert step["ended_at"] is not None
        assert step["ended_at"] >= step["started_at"]
    finally:
        store.delete_session(task_id)


def test_failed_step_keeps_error_and_class(client, store):
    """失败步骤：报错与失败分类刷新后还在，结果为空。"""
    task_id = store.create_task("步骤详情-失败", "craft", "minimax")
    step_id = store.create_step(task_id, 1, "shell", {"cmd": "rm -rf /"}, 2)
    store.finish_step(
        step_id, "failed", error="权限不足：E2", error_class="E2", mutated=False,
    )
    try:
        step = _data(client.get(f"/api/sessions/{task_id}"))["steps"][0]
        assert step["state"] == "failed"
        assert step["error"] == "权限不足：E2"
        assert step["error_class"] == "E2"
        assert step["result"] is None
        assert step["artifact_path"] is None
        assert step["mutated"] == 0
    finally:
        store.delete_session(task_id)


def test_denied_step_uses_legacy_signature(client, store):
    """旧调用点（只传 state + error）仍然可用，且不会凭空显示「未改动」。"""
    task_id = store.create_task("步骤详情-拒绝", "craft", "minimax")
    step_id = store.create_step(task_id, 1, "shell", {"cmd": "rm"}, 3)
    store.finish_step(step_id, "denied", error="命中禁止规则")
    try:
        step = _data(client.get(f"/api/sessions/{task_id}"))["steps"][0]
        assert step["state"] == "denied"
        assert step["error"] == "命中禁止规则"
        assert step["error_class"] is None
        assert step["artifact_path"] is None
        assert step["mutated"] is None
    finally:
        store.delete_session(task_id)


def test_steps_subresource_matches_detail(client, store):
    """`/steps` 与详情里的 steps 是同一份数据，不能一个有一个没有。"""
    task_id = store.create_task("步骤详情-子资源", "craft", "minimax")
    step_id = store.create_step(task_id, 1, "fs_read", {"path": "x.txt"}, 0)
    store.finish_step(step_id, "done", result="内容", mutated=False)
    try:
        detail_step = _data(client.get(f"/api/sessions/{task_id}"))["steps"][0]
        listed = _data(client.get(f"/api/sessions/{task_id}/steps"))
        assert len(listed) == 1
        assert listed[0] == detail_step
        assert listed[0]["error_class"] is None
        assert listed[0]["mutated"] == 0
    finally:
        store.delete_session(task_id)


def test_legacy_steps_table_gets_new_columns(tmp_path):
    """老数据文件的 steps 表没有新列 —— 启动时幂等补齐，跑两次也不炸。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.store import Store

    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE steps ("
            " id TEXT PRIMARY KEY, task_id TEXT NOT NULL, seq INTEGER NOT NULL,"
            " agent TEXT, tool TEXT, args TEXT, state TEXT, risk INTEGER,"
            " result TEXT, error TEXT, started_at REAL, ended_at REAL)"
        )
        conn.commit()

    columns = {"error_class": "TEXT", "artifact_path": "TEXT", "mutated": "INTEGER"}
    Store._ensure_columns(db, "steps", columns)
    Store._ensure_columns(db, "steps", columns)  # 幂等

    with sqlite3.connect(db) as conn:
        present = {row[1] for row in conn.execute("PRAGMA table_info(steps)")}
    assert {"error_class", "artifact_path", "mutated"} <= present
