"""大屏域：/api/overview/{teams,runs,suggestions}。

这三个接口的定位
----------------
大屏此前**一个接口都不调**，纯渲染 SSE 攒出来的内存状态 ——
刷新一下页面总览就空了。这三个接口是服务端真相的取口。

所以这里测的重点不是「返回了什么」，而是：
  · 内核**没起来**时会不会把整屏拖成 500（不该）
  · 计数对不对（total/active/today 必须是库里的聚合，不是列表里数出来的）
  · 建议是不是确定性规则给的（不是模型编的）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP_HOME = Path(tempfile.mkdtemp(prefix="oryx-overview-"))
import os

os.environ.setdefault("ORYX_HOME", str(_TMP_HOME))


@pytest.fixture(scope="module")
def client():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


def _data(resp):
    """剥掉统一响应壳，拿到领域数据。"""
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


# ── teams ───────────────────────────────────────────────────────────


def test_overview_teams_degrades_without_kernel(client):
    """内核没起来时必须 200 + 空列表，不能 500。

    大屏是锦上添花的视图，它挂了不该拖垮别的接口，
    更不该让整屏变成一个红色错误框。
    """
    resp = client.get("/api/overview/teams")
    assert resp.status_code == 200
    data = _data(resp)
    assert data["teams"] == []
    assert data["team_count"] == 0
    assert data["member_count"] == 0
    # 降级原因要如实告诉前端
    assert "teams" in data["degraded"]


# ── runs ────────────────────────────────────────────────────────────


def test_overview_runs_empty(client):
    resp = client.get("/api/overview/runs")
    assert resp.status_code == 200
    data = _data(resp)
    assert data["runs"] == []
    assert data["total"] == 0
    assert data["active"] == 0
    assert data["today"] == 0
    assert data["window"] == 20


def test_overview_runs_counts_are_aggregate_not_window(client):
    """total 必须是**全量**，不能是「返回列表的长度」。

    这条是刻意的：列表限量 20，数出来的是「最近 20 个里有几个」，
    跟「一共跑了多少」不是一回事 —— 显示错了用户会以为数据丢了。
    """
    import oryx.server as server_mod

    store = server_mod.store
    ids = [store.create_task(f"计数测试 {i}", "craft", "minimax") for i in range(3)]
    try:
        data = _data(client.get("/api/overview/runs?limit=2"))
        # 列表按 limit 截断，但计数是全量的
        assert len(data["runs"]) == 2
        assert data["total"] >= 3
        assert data["window"] == 2
    finally:
        for task_id in ids:
            store.delete_session(task_id)


def test_session_stats_counts_every_row():
    """session_stats 走的是库里的聚合查询，不受列表限量影响。"""
    from oryx.store import Store

    store = Store(Path(_TMP_HOME) / "count-check.db")
    ids = [store.create_task(f"第 {i} 轮任务", "craft", "minimax") for i in range(25)]
    try:
        stats = store.session_stats()
        assert stats["total"] == 25
        # 25 个都是 running（没调 set_task_status）
        assert stats["active"] == 25
        assert stats["today"] == 25
    finally:
        for task_id in ids:
            store.delete_session(task_id)


def test_overview_runs_limit_is_capped(client):
    resp = client.get("/api/overview/runs?limit=9999")
    assert resp.status_code == 200
    assert _data(resp)["window"] == 100


def test_overview_runs_reflects_real_session(client):
    """真造一条会话，接口要能看到它。"""
    from oryx.store import Store
    import oryx.server as server_mod

    store = server_mod.store
    task_id = store.create_task("大屏测试任务", "craft", "minimax")
    store.add_message(task_id, "user", "你好")
    try:
        data = _data(client.get("/api/overview/runs"))
        assert data["total"] >= 1
        assert any(r["id"] == task_id for r in data["runs"])
        mine = next(r for r in data["runs"] if r["id"] == task_id)
        assert mine["title"]
        assert mine["msg_count"] >= 1
    finally:
        store.delete_session(task_id)


# ── suggestions ─────────────────────────────────────────────────────


def test_overview_suggestions_empty_when_no_session(client):
    resp = client.get("/api/overview/suggestions")
    assert resp.status_code == 200
    data = _data(resp)
    assert data["suggestions"] == []
    assert data["count"] == 0
    assert data["source"] == {}


def test_overview_suggestions_from_xlsx_artifact(client):
    """产出过一个 .xlsx 之后，应该给出「画成图表」这类建议。

    走的是确定性规则（按后缀查表），不是模型现场编的 ——
    所以这里断言的是**具体的标签**，模型编不出来这个。
    """
    from oryx.store import Store
    import oryx.server as server_mod

    store = server_mod.store
    task_id = store.create_task("做一张表", "craft", "minimax")
    step_id = store.create_step(
        task_id, 1, "xlsx_write", {"path": "D:/tmp/report.xlsx"}, 0
    )
    store.finish_step(step_id, "done", {"ok": True, "path": "D:/tmp/report.xlsx"})
    try:
        data = _data(client.get("/api/overview/suggestions"))
        assert data["count"] >= 1
        labels = [s["label"] for s in data["suggestions"]]
        assert "画成图表" in labels
        assert data["source"]["task_id"] == task_id
        assert "D:/tmp/report.xlsx" in data["source"]["artifacts"]
        # 每条建议都要有可执行内容（prompt 或 action）
        for item in data["suggestions"]:
            assert item.get("prompt") or item.get("action")
    finally:
        store.delete_session(task_id)


def test_overview_suggestions_scoped_to_task(client):
    """指定 task_id 就只看那一次运行的产物。"""
    from oryx.store import Store
    import oryx.server as server_mod

    store = server_mod.store
    old_id = store.create_task("老任务", "craft", "minimax")
    new_id = store.create_task("新任务", "craft", "minimax")
    step_id = store.create_step(new_id, 1, "xlsx_write", {"path": "D:/tmp/new.xlsx"}, 0)
    store.finish_step(step_id, "done", {"path": "D:/tmp/new.xlsx"})
    try:
        data = _data(client.get(f"/api/overview/suggestions?task_id={old_id}"))
        assert data["source"]["task_id"] == old_id
        assert data["source"]["artifact_count"] == 0
    finally:
        store.delete_session(old_id)
        store.delete_session(new_id)


def test_overview_suggestions_ignores_sentences_with_filenames(client):
    """「请看 report.xlsx」是一句话，不是一个产物路径。

    认错了会给用户一张指向不存在文件的建议卡。
    """
    from oryx.store import Store
    import oryx.server as server_mod

    store = server_mod.store
    task_id = store.create_task("只是提到了文件", "craft", "minimax")
    step_id = store.create_step(
        task_id, 1, "fs_read", {"path": "请看 report.xlsx 这个文件"}, 0
    )
    store.finish_step(step_id, "done", {"content": "内容是 请看 report.xlsx 这个文件"})
    try:
        data = _data(client.get("/api/overview/suggestions"))
        assert data["source"]["artifact_count"] == 0
    finally:
        store.delete_session(task_id)
