"""模块 7：团队整体保存、后台派发与精确状态码的契约测试。

钉住五件容易回退的事：

1. 整体保存必须**先全部校验再一次落地** —— 校验失败时内存与 SQLite
   都不能动（原来前端拆成 5+ 次串行请求，中途失败就半保存）。
2. 单成员团队换人必须成功 —— 旧的「先删后加」会撞上「至少要留一个成员」。
3. `PUT /api/teams/{name}` 带 `members` 时只发**一次** `team.save` IPC。
4. 状态码不能靠「error 里有没有『创建者』」猜：
   「至少要留一个成员」是 400、重名是 409、找不到是 404、越权是 403。
5. 后台派发 POST 立刻返回、不等待子 Agent 跑完；重复提交只派一次活；
   别人的作业查不到。老前端用的同步 `/dispatch` 路由保持可用。
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from oryx.domains import team as domain
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store
from oryx.team import TeamRegistry


# ────────────────────────────────────────────────────────── 内核层：整体保存

class _Runtime:
    """只实现 AgentRuntime.team_save 用到的三个属性。"""

    def __init__(self, store: Store, agents=("a", "b", "c")):
        self.teams = TeamRegistry(store)
        self.agents = SimpleNamespace(agents={a: object() for a in agents})
        self.events: list[tuple] = []
        self.bus = SimpleNamespace(
            publish=lambda *args, **kwargs: self.events.append((args, kwargs))
        )

    def save(self, *args, **kwargs):
        return AgentRuntime.team_save(self, *args, **kwargs)

    def create(self, name, members, goal="", owner_id="u1"):
        return self.teams.create(name, members, goal,
                                 known_agents=set(self.agents.agents), owner_id=owner_id)


def test_save_replaces_everything_in_one_shot(tmp_path: Path):
    rt = _Runtime(Store(tmp_path / "oryx.db"))
    team, err = rt.create("旧队", ["a", "b"], "旧目标")
    assert team is not None, err
    rt.teams.post("旧队", "main", "a", "一条协作记录", owner_id="u1")

    out = rt.save("旧队", new_name="新队", goal="新目标",
                  members=[{"agent_id": "c", "role": "审校"}], owner_id="u1")
    assert out["ok"], out
    assert out["name"] == "新队"

    obj = rt.teams.get("新队")
    assert obj is not None and rt.teams.get("旧队") is None
    assert obj.goal == "新目标"
    assert obj.members == {"c": "审校"}
    # 信箱按 team_id 关联，换成员/改名都不该丢
    assert [m["content"] for m in rt.teams.thread("新队", owner_id="u1")] == ["一条协作记录"]
    # SQLite 侧也同步了
    row = Store(tmp_path / "oryx.db").get_team("新队")
    assert row is not None and row["goal"] == "新目标"
    assert Store(tmp_path / "oryx.db").get_team("旧队") is None


def test_single_member_replacement_succeeds(tmp_path: Path):
    """旧流程先删后加：删最后一人必失败，所以换人根本做不到。"""
    rt = _Runtime(Store(tmp_path / "oryx.db"))
    rt.create("小队", ["a"], "")
    out = rt.save("小队", members=[{"agent_id": "b", "role": "调研"}], owner_id="u1")
    assert out["ok"], out
    assert rt.teams.get("小队") is not None
    assert rt.teams.get("小队").members == {"b": "调研"}


def test_invalid_save_changes_nothing(tmp_path: Path):
    """校验失败时一个字节都不能写 —— 这是「整体保存」最核心的保证。"""
    db = tmp_path / "oryx.db"
    rt = _Runtime(Store(db))
    rt.create("小队", ["a", "b"], "原目标", )
    before = rt.teams.get("小队").members.copy()

    cases = [
        dict(new_name="小队2", members=[{"agent_id": "ghost", "role": "x"}]),
        dict(new_name="小队2", members=[]),
        dict(new_name="小队2", members=[{"agent_id": "a", "role": "1"},
                                        {"agent_id": "a", "role": "2"}]),
        dict(new_name="坏/名字", members=[{"agent_id": "a", "role": ""}]),
    ]
    for payload in cases:
        out = rt.save("小队", **payload, owner_id="u1")
        assert out["ok"] is False, payload
        obj = rt.teams.get("小队")
        assert obj.name == "小队" and obj.goal == "原目标" and obj.members == before
        assert Store(db).get_team("小队2") is None

    # 同名冲突也必须在落地之前拦住
    rt.create("占名", ["c"], "")
    out = rt.save("小队", new_name="占名", members=[{"agent_id": "a", "role": ""}],
                  owner_id="u1")
    assert out["ok"] is False and out["reason"] == "conflict"
    assert rt.teams.get("小队").name == "小队"


def test_save_keeps_stale_member_but_validates_new_ones(tmp_path: Path):
    """已保存但目录里查不到的成员保留（模块 6 的原则），只校验新加入的。"""
    rt = _Runtime(Store(tmp_path / "oryx.db"))
    team, _ = rt.create("小队", ["a"], "")
    team.members["已下架"] = "旧角色"

    out = rt.save("小队", goal="只改目标",
                  members=[{"agent_id": "已下架", "role": "旧角色"},
                           {"agent_id": "b", "role": "新"}], owner_id="u1")
    assert out["ok"], out
    assert "已下架" in rt.teams.get("小队").members

    out2 = rt.save("小队", members=[{"agent_id": "ghost", "role": "x"}], owner_id="u1")
    assert out2["ok"] is False and "不存在" in out2["error"]


def test_save_requires_owner(tmp_path: Path):
    rt = _Runtime(Store(tmp_path / "oryx.db"))
    rt.create("小队", ["a"], owner_id="u1")
    out = rt.save("小队", new_name="别的名字", members=[{"agent_id": "a", "role": ""}],
                  owner_id="u2")
    assert out["ok"] is False and out["reason"] == "forbidden"
    assert rt.teams.get("小队") is not None and rt.teams.get("别的名字") is None


def test_save_publishes_one_update_event(tmp_path: Path):
    rt = _Runtime(Store(tmp_path / "oryx.db"))
    rt.create("小队", ["a"], "")
    rt.save("小队", goal="新目标", members=[{"agent_id": "b", "role": ""}], owner_id="u1")
    assert len(rt.events) == 1
    assert rt.events[0][0][0] == "team.updated"


# ────────────────────────────────────────────────────────── 控制面：HTTP

class _Agent:
    """假的 IPC 内核代理。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.dispatch_delay = 0.15
        self.dispatch_result = {"ok": True, "content": "成员已交回结论"}
        self.dispatch_calls = 0
        self.save_result: dict | None = None
        self.remove_result: dict | None = None

    async def call_async(self, action: str, **params):
        self.calls.append((action, params))
        if action == "team.save":
            return dict(self.save_result or {
                "ok": True,
                "team": {"id": "t1", "name": params.get("new_name") or params["team"],
                         "goal": "新目标", "members": params["members"]},
            })
        if action == "team.update":
            return {"ok": True, "team": {"id": "t1", "name": params["team"]}}
        if action == "team.remove_member":
            return dict(self.remove_result or {"ok": True, "removed": params["agent"]})
        raise AssertionError(f"意外的 IPC：{action}")

    def call(self, action: str, **params):
        """后台派发与老同步路由都走这条（IPC 同步入口）。"""
        self.calls.append((action, params))
        if action == "team.dispatch":
            self.dispatch_calls += 1
            time.sleep(self.dispatch_delay)
            return dict(self.dispatch_result)
        raise AssertionError(f"意外的同步 IPC：{action}")


def _app(agent: _Agent, monkeypatch):
    app = FastAPI()

    @app.middleware("http")
    async def bind_user(request: Request, call_next):
        # 生产里这个来自会话 cookie；测试用请求头切账号更方便。
        request.state.user_id = request.headers.get("X-Test-User") or None
        return await call_next(request)

    prior = domain.router
    domain.router = APIRouter(prefix="/api/teams", tags=["团队"])
    try:
        domain.register(app, agent, EventBus())
    finally:
        domain.router = prior
    return app


def _client(agent: _Agent, monkeypatch, user="u1"):
    client = TestClient(_app(agent, monkeypatch))
    if user:
        client.headers.update({"X-Test-User": user})
    return client


def _data(response):
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


def _message(response) -> str:
    body = response.json()
    return str(body.get("message") or body.get("detail") or "")


def _members_payload(*pairs):
    return {"members": [{"agent_id": a, "role": r} for a, r in pairs]}


def test_put_with_members_sends_exactly_one_ipc(tmp_path, monkeypatch):
    agent = _Agent()
    client = _client(agent, monkeypatch)
    res = client.put("/api/teams/小队", json={
        "name": "新队", "goal": "新目标", **_members_payload(("a", "调研"), ("b", "审校")),
    })
    assert res.status_code == 200
    assert _data(res)["team"]["name"] == "新队"
    assert [a for a, _ in agent.calls] == ["team.save"], "整体保存只能发一次 IPC"
    assert agent.calls[0][1]["members"] == [
        {"agent_id": "a", "role": "调研"}, {"agent_id": "b", "role": "审校"}
    ]


def test_put_without_members_keeps_legacy_update(tmp_path, monkeypatch):
    agent = _Agent()
    client = _client(agent, monkeypatch)
    res = client.put("/api/teams/小队", json={"goal": "只改目标", "member": "a", "role": "审校"})
    assert res.status_code == 200
    assert [a for a, _ in agent.calls] == ["team.update"]


def test_members_length_is_validated(tmp_path, monkeypatch):
    agent = _Agent()
    client = _client(agent, monkeypatch)
    bad = client.put("/api/teams/小队", json={"members": []})
    assert bad.status_code == 422
    assert agent.calls == [], "校验没过就不该发 IPC"
    too_many = client.put("/api/teams/小队", json=_members_payload(
        *[(f"a{i}", "") for i in range(9)]))
    assert too_many.status_code == 422


@pytest.mark.parametrize("reason,status,text", [
    ("not_found", 404, "没有叫「x」的团队"),
    ("forbidden", 403, "只有创建者可以修改这个共享团队"),
    ("conflict", 409, "已经有叫「x」的团队了"),
    ("invalid", 400, "团队至少要有一个成员"),
])
def test_save_error_maps_to_precise_status(tmp_path, monkeypatch, reason, status, text):
    agent = _Agent()
    agent.save_result = {"ok": False, "error": text, "reason": reason}
    client = _client(agent, monkeypatch)
    res = client.put("/api/teams/小队", json=_members_payload(("a", "")))
    assert res.status_code == status
    assert text in _message(res)


def test_last_member_error_is_400_not_404(tmp_path, monkeypatch):
    """文档里点名的那条：「至少要留一个成员」被粗暴映射成 404。"""
    agent = _Agent()
    agent.remove_result = {"ok": False, "error": "团队至少要留一个成员", "reason": "invalid"}
    client = _client(agent, monkeypatch)
    res = client.delete("/api/teams/小队/members/a")
    assert res.status_code == 400
    assert "至少要留一个" in _message(res)


def test_remove_error_without_reason_falls_back_by_text(tmp_path, monkeypatch):
    """老内核不带 reason 时也不能把业务规则压成 404。"""
    agent = _Agent()
    agent.remove_result = {"ok": False, "error": "团队至少要留一个成员"}
    client = _client(agent, monkeypatch)
    assert client.delete("/api/teams/小队/members/a").status_code == 400

    agent.remove_result = {"ok": False, "error": "没有叫「x」的团队"}
    assert client.delete("/api/teams/小队/members/a").status_code == 404

    agent.remove_result = {"ok": False, "error": "只有创建者可以修改这个共享团队"}
    assert client.delete("/api/teams/小队/members/a").status_code == 403

    agent.remove_result = {"ok": False, "error": "已经有叫「y」的团队了"}
    assert client.delete("/api/teams/小队/members/a").status_code == 409


def _wait_job(client, job_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/api/teams/小队/dispatch/jobs/{job_id}")
        assert res.status_code == 200
        data = _data(res)
        if data["status"] != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("作业没有在超时内结束")


def test_dispatch_job_returns_immediately_and_reports_result(tmp_path, monkeypatch):
    agent = _Agent()
    agent.dispatch_delay = 0.3
    client = _client(agent, monkeypatch)

    before = time.time()
    started = _data(client.post("/api/teams/小队/dispatch/jobs", json={"agent": "a"}))
    elapsed = time.time() - before
    assert elapsed < 0.2, f"提交不该等子 Agent 跑完（实际 {elapsed:.2f}s）"
    assert started["status"] == "running" and started["agent"] == "a"
    job_id = started["job_id"]

    # 立刻查还是 running
    assert _data(client.get(f"/api/teams/小队/dispatch/jobs/{job_id}"))["status"] == "running"
    done = _wait_job(client, job_id)
    assert done["status"] == "done"
    assert done["content"] == "成员已交回结论"
    assert done["finished_at"] is not None
    assert agent.dispatch_calls == 1


def test_duplicate_submit_reuses_inflight_job(tmp_path, monkeypatch):
    """防重复点击：同一 (团队, 成员, 账号) 在途时再点，不派第二次活。"""
    agent = _Agent()
    agent.dispatch_delay = 0.4
    client = _client(agent, monkeypatch)
    a = _data(client.post("/api/teams/小队/dispatch/jobs", json={"agent": "a"}))
    b = _data(client.post("/api/teams/小队/dispatch/jobs", json={"agent": "a"}))
    assert a["job_id"] == b["job_id"]
    _wait_job(client, a["job_id"])
    assert agent.dispatch_calls == 1


def test_job_is_private_and_team_scoped(tmp_path, monkeypatch):
    agent = _Agent()
    agent.dispatch_delay = 0.2
    client = _client(agent, monkeypatch, user="u1")
    job_id = _data(client.post("/api/teams/小队/dispatch/jobs", json={"agent": "a"}))["job_id"]

    other = client.get(f"/api/teams/小队/dispatch/jobs/{job_id}", headers={"X-Test-User": "u2"})
    assert other.status_code == 404
    wrong_team = client.get(f"/api/teams/别的队/dispatch/jobs/{job_id}")
    assert wrong_team.status_code == 404
    assert client.get("/api/teams/小队/dispatch/jobs/nope").status_code == 404
    _wait_job(client, job_id)


def test_failed_dispatch_is_reported(tmp_path, monkeypatch):
    agent = _Agent()
    agent.dispatch_result = {"ok": False, "content": "a 执行失败：模型超时"}
    client = _client(agent, monkeypatch)
    job_id = _data(client.post("/api/teams/小队/dispatch/jobs", json={"agent": "a"}))["job_id"]
    done = _wait_job(client, job_id)
    assert done["status"] == "failed"
    assert "模型超时" in done["error"]


def test_legacy_sync_dispatch_still_works(tmp_path, monkeypatch):
    """老前端 core/web/modules/models.js 还在用这条同步路由。"""
    agent = _Agent()
    client = _client(agent, monkeypatch)
    res = client.post("/api/teams/小队/dispatch", json={"agent": "a"})
    assert res.status_code == 200
    assert _data(res)["content"] == "成员已交回结论"
    assert [a for a, _ in agent.calls] == ["team.dispatch"]
