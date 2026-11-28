"""领域层：路由装配与用例行为。

对齐 Marvis 的 modules/ + actions/ 结构 —— 每个域自成一个 router，
用例是纯函数，路由层只做参数绑定与依赖注入。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oryx.domains import DOMAINS, model_config, schedule, session, skill, workflow
from oryx.ipc import IpcError


# ---------- 会话域 ----------
def test_session_actions_are_pure_functions(tmp_path):
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task_id = store.create_task("目标", "craft", "p")
    store.add_message(task_id, "user", "你好")

    # v2：列表的领域对象**就是数组**，不再多包一层 {"sessions": [...]}
    listed = session.list_sessions(store)
    assert isinstance(listed, list)
    assert len(listed) == 1

    detail = session.read_session(store, task_id)
    assert detail["task"]["id"] == task_id
    assert detail["messages"][0]["content"] == "你好"

    assert session.delete_session(store, task_id) == {"deleted": task_id}
    assert session.list_sessions(store) == []


def test_session_read_missing_raises_404(tmp_path):
    from fastapi import HTTPException

    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    with pytest.raises(HTTPException) as exc:
        session.read_session(store, "nope")
    assert exc.value.status_code == 404


# ---------- 模型配置域 ----------
def test_provider_list_masks_keys(tmp_path):
    from oryx.config import Config

    config = Config({"providers": {"p1": {"type": "openai", "base_url": "u", "model": "m",
                                          "api_key": "sk-secret-value"}}})
    result = model_config.list_providers(config)
    item = result["items"][0]
    assert "sk-secret-value" not in json.dumps(result), "密钥不能出现在返回里"
    assert item["capabilities"], "要带上能力描述"


def test_provider_create_and_delete(tmp_path):
    from oryx.config import Config

    config = Config({"providers": {}})
    req = model_config.ProviderRequest(name="p1", type="openai", base_url="https://x", model="m",
                                        api_key="k")
    assert model_config.create_provider(config, req)["name"] == "p1"
    assert len(model_config.list_providers(config)["items"]) == 1
    assert model_config.delete_provider(config, "p1") == {"deleted": "p1"}
    with pytest.raises(Exception):
        model_config.delete_provider(config, "p1")


# ---------- 公共 ----------
class FakeAgent:
    """假 AgentLink。域层只做参数校验与 RPC 转发，这里记录调用即可。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.timeouts: dict[str, float] = {}

    def call(self, method: str, timeout: float = 60.0, **params):
        self.calls.append((method, params))
        self.timeouts[method] = timeout
        if method == "automation.list":
            return [{"id": "a1", "name": "定时"}]
        if method == "automation.add":
            return {"id": "a1", "name": params.get("name", "")}
        if method == "automation.remove":
            return True
        if method == "terminal.run":
            return {"id": "t1", "status": "已启动，输出会实时推送到界面",
                    "command": params.get("command", ""), "cwd": "C:/tmp"}
        if method == "terminal.list":
            return []
        if method == "terminal.interrupt":
            return True
        if method == "catalog.reload":
            return {"skills": 0}
        if method == "catalog.skills_detail":
            # 内核那份权威的技能清单 —— 域层不再自己建 SkillLibrary，
            # 所以假 Agent 要能给出清单（否则域层会退回本地读盘）。
            root = skill.skills_root()
            out = []
            for d in sorted(root.glob("*/SKILL.md")):
                out.append({
                    "name": d.parent.name,
                    "description": "",
                    "path": str(d),
                })
            return {"skills": out}
        if method == "workflow.list":
            return {"runs": [{"id": "wfr_1", "status": "done"}]}
        if method == "workflow.run":
            return {"ok": True, "content": "# 汇总", "run_id": "wfr_1", "status": "done"}
        if method == "workflow.status":
            return {"ok": True, "content": {"id": params.get("run_id", ""), "steps": []}}
        if method == "workflow.cancel":
            return {"ok": True, "content": "已请求中止"}
        if method == "workflow.delete":
            return {"ok": True, "deleted": params.get("run_id", "")}
        raise AssertionError(f"未预期的 RPC：{method}")


# ---------- 技能域 ----------
@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    """把技能根指到临时目录，避免测试写进仓库。"""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(skill, "skills_root", lambda: root)
    return root


def test_skill_crud(skill_root):
    agent = FakeAgent()
    assert skill.create_skill(agent, skill.SkillCreateRequest(
        name="demo", description="说明", body="内容")) == {"created": "demo"}
    assert any(s["name"] == "demo" for s in skill.list_skills(agent)["skills"])

    detail = skill.read_skill(agent, "demo")
    assert detail["description"] == "说明"

    # 只传 description：body 必须沿用旧值（exclude_unset 语义）
    assert skill.update_skill(agent, "demo", skill.SkillUpdateRequest(
        description="新说明"))["updated"] == "demo"
    assert skill.read_skill(agent, "demo")["description"] == "新说明"
    assert skill.read_skill(agent, "demo")["body"].strip() == "内容"

    assert skill.delete_skill(agent, "demo") == {"deleted": "demo"}
    assert not any(s["name"] == "demo" for s in skill.list_skills(agent)["skills"])


def test_skill_write_triggers_kernel_reload(skill_root):
    """写完技能要通知内核重扫，否则新技能不会立刻出现在对话里。"""
    agent = FakeAgent()
    skill.create_skill(agent, skill.SkillCreateRequest(name="demo", body="x"))
    assert any(m == "catalog.reload" for m, _ in agent.calls)


def test_skill_name_rejects_path_traversal(skill_root):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        skill.create_skill(FakeAgent(), skill.SkillCreateRequest(name="../escape", body="x"))
    assert exc.value.status_code == 400


def test_skill_create_rejects_duplicate(skill_root):
    from fastapi import HTTPException

    agent = FakeAgent()
    skill.create_skill(agent, skill.SkillCreateRequest(name="demo", body="x"))
    with pytest.raises(HTTPException) as exc:
        skill.create_skill(agent, skill.SkillCreateRequest(name="demo", body="x"))
    assert exc.value.status_code == 409


# ---------- 自动任务域 ----------
def test_schedule_actions_rpc():
    agent = FakeAgent()
    created = schedule.create_automation(agent, schedule.AutomationRequest(
        name="定时", cron="0 9 * * *", goal="做事"))
    assert created["name"] == "定时"
    assert agent.calls[0][0] == "automation.add"

    listed = schedule.list_automations(agent)
    assert listed["automations"][0]["id"] == "a1"

    assert schedule.delete_automation(agent, "a1") == {"deleted": "a1"}


def test_schedule_run_terminal_forwards_to_kernel():
    # 模块 10：`/api/terminal/run` 换成 Pydantic 契约后，域函数也收模型，
    # 与 workflow.run_workflow(..., WorkflowRunRequest) 的惯例一致。
    agent = FakeAgent()
    out = schedule.run_terminal(agent, schedule.TerminalRunRequest(command="echo hi"))
    assert out["id"] == "t1"
    assert "已启动" in out["status"]
    assert ("terminal.run", {"command": "echo hi", "cwd": ""}) in agent.calls


def test_schedule_rejects_empty_command():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        schedule.run_terminal(FakeAgent(), schedule.TerminalRunRequest(command="  "))
    assert exc.value.status_code == 400


# ---------- 工作流域 ----------
def test_workflow_forwards_plan_to_kernel():
    agent = FakeAgent()
    plan = {"goal": "查全部接口", "stages": [{"name": "S1", "steps": [
        {"id": "a", "agent": "doc-manager", "goal": "看看需求文档"}
    ]}]}
    out = workflow.run_workflow(agent, workflow.WorkflowRunRequest(plan=plan))
    assert out["run_id"] == "wfr_1"
    method, params = agent.calls[-1]
    assert method == "workflow.run"
    assert params["plan"] == plan
    assert params["args"] == {}


def test_workflow_run_uses_long_timeout():
    """一次编排可能跑几分钟，不能用默认 60s 超时。"""
    agent = FakeAgent()
    workflow.run_workflow(agent, workflow.WorkflowRunRequest(plan={"goal": "g"}))
    assert agent.timeouts["workflow.run"] == workflow.RUN_TIMEOUT
    assert workflow.RUN_TIMEOUT > 600


def test_workflow_list_and_detail():
    agent = FakeAgent()
    assert workflow.list_workflows(agent)["runs"][0]["id"] == "wfr_1"
    assert workflow.workflow_detail(agent, "wfr_9")["content"]["id"] == "wfr_9"
    assert workflow.cancel_workflow(agent, "wfr_9")["ok"] is True


def test_workflow_missing_run_raises_404():
    """查不到 run 要回 404：前端判 content 空，会把 200 + 文本渲染成空弹层。"""
    from fastapi import HTTPException

    class NotFoundAgent(FakeAgent):
        def call(self, method: str, timeout: float = 60.0, **params):
            if method == "workflow.status":
                return {"ok": False, "content": "没有这次运行：wfr_x"}
            raise IpcError("没有这次运行：wfr_x")

    with pytest.raises(HTTPException) as exc:
        workflow.workflow_detail(NotFoundAgent(), "wfr_x")
    assert exc.value.status_code == 404
    assert "没有这次运行" in str(exc.value.detail)


def test_workflow_rpc_error_maps_to_404():
    """内核直接抛 IpcError 时也要 404。"""
    from fastapi import HTTPException

    class NotFoundAgent(FakeAgent):
        def call(self, method: str, timeout: float = 60.0, **params):
            raise IpcError("没有这次运行：wfr_x")

    with pytest.raises(HTTPException) as exc:
        workflow.workflow_detail(NotFoundAgent(), "wfr_x")
    assert exc.value.status_code == 404


def test_workflow_delete():
    agent = FakeAgent()
    assert workflow.delete_workflow(agent, "wfr_7") == {"deleted": "wfr_7"}
    assert ("workflow.delete", {"run_id": "wfr_7"}) in agent.calls


def test_workflow_delete_missing_raises_404():
    class GoneAgent(FakeAgent):
        def call(self, method: str, timeout: float = 60.0, **params):
            if method == "workflow.delete":
                return {"ok": False, "content": ""}
            return super().call(method, timeout=timeout, **params)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        workflow.delete_workflow(GoneAgent(), "wfr_x")
    assert exc.value.status_code == 404


# ---------- 装配 ----------
# 领域清单 —— 与 `oryx/domains/__init__.py` 的 DOMAINS 一一对应。
#
# 这份清单是**手写的**，所以改domains 时要同步改这里 ——
# 忘了的话测试会红（这是有意的：宁可测试红，也不要悄悄少注册一个域）。
EXPECTED_DOMAINS = [
    "session", "model_config", "skill", "schedule", "checkpoint", "workflow",
    "appearance", "feature", "memory", "remote", "ui", "extension",
    "tools", "toolpack", "mcp", "file", "persona", "team", "index", "knowledge",
    "preference", "chat", "app", "system",
    "overview",
]
EXPECTED_PREFIXES = {
    "/api/sessions", "/api/providers", "/api/skills", "",
    "/api/checkpoints", "/api/workflows",
    "/api/appearance", "/api/features", "/api/memory", "/api/remote",
    "/api/ui", "/api/extensions", "/api/ui-extensions",
    "/api/tools", "/api/mcp", "/api/files", "/api/workspace", "/api/doc",
    "/api/teams", "/api/overview", "/api/tool-packs", "/api/knowledge",
    # persona 域管**多个**前缀（/api/persona、/api/expert、/api/experts、
    # /api/agents），index 域也是（/api/index、/api/drives、/api/images）——
    # 所以它们的 router 用 `/api` 前缀 + tag 区分。
    "/api",
}


def test_all_domains_are_listed():
    names = [m.__name__.rsplit(".", 1)[-1] for m, _ in DOMAINS]
    assert names == EXPECTED_DOMAINS


def test_domain_routers_use_isolated_prefixes():
    """每个域的 router 前缀都要登记在册。

    **一个域可能有多个 router**（扩展域有 `/api/extensions` 和
    `/api/ui-extensions` 两个）—— 所以要遍历所有 router，
    只看 `m.router` 会漏掉第二个。
    """
    prefixes = set()
    for m, _ in DOMAINS:
        routers = getattr(m, "routers", None)
        if routers:
            prefixes.update(r.prefix for r in routers)
        else:
            prefixes.add(m.router.prefix)
    # 兜底：模块里若还有别的 *router 变量，也要算进来
    for m, _ in DOMAINS:
        for name in dir(m):
            if name.endswith("_router"):
                r = getattr(m, name)
                if hasattr(r, "prefix"):
                    prefixes.add(r.prefix)
    assert prefixes == EXPECTED_PREFIXES


def test_key_paths_are_reachable():
    """关键路径在**真实 app** 上都得在。

    比「前缀清单」更直接 —— 前缀对但路径拼错，这里就能抓到。
    """
    from oryx.server import app

    paths = set(app.openapi().get("paths") or {})
    for p in (
        "/api/sessions", "/api/providers", "/api/skills",
        "/api/appearance", "/api/features", "/api/memory", "/api/remote/status",
        "/api/ui/modules", "/api/extensions", "/api/tools/index",
        "/api/mcp", "/api/files/list", "/api/workspace", "/api/doc",
        "/api/persona", "/api/experts", "/api/agents",
        "/api/teams", "/api/index/status", "/api/drives", "/api/images",
        "/api/knowledge/collections", "/api/knowledge/search",
        # 最后一批
        "/api/state", "/api/chat", "/api/apps", "/api/prewarm",
        "/api/doctor", "/api/fs/list", "/api/approve", "/api/interaction",
        # 大屏（多 Agent 一屏总览）
        "/api/overview/teams", "/api/overview/runs", "/api/overview/suggestions",
    ):
        assert p in paths, f"关键路径不见了：{p}"


def test_server_exposes_domain_routes():
    """集成：真 app 上 6 个域的路由都要能通。

    用 TestClient 会触发 startup，从而真的把 Agent 内核进程拉起来 ——
    这正是要验证的：HTTP 层能通过 RPC 拿到 Agent 侧的数据。
    """
    from oryx.server import API_TOKEN, app, agent, store

    checkpoint_task_id = store.create_task(
        "checkpoint route smoke test", "craft", "test-provider"
    )

    try:
        with TestClient(app) as client:
            assert agent.alive, "startup 之后 Agent 内核进程应该活着"
            headers = {"X-Oryx-Token": API_TOKEN}
            for path in ("/api/sessions", "/api/providers", "/api/skills",
                         "/api/automations", "/api/terminal", "/api/workflows"):
                assert client.get(path, headers=headers).status_code == 200, path

            checkpoint_path = f"/api/checkpoints/{checkpoint_task_id}"
            checkpoint_response = client.get(checkpoint_path, headers=headers)
            assert checkpoint_response.status_code == 200, checkpoint_path
            assert checkpoint_response.json()["task_id"] == checkpoint_task_id

            # 依赖 Agent 内核的两个域：要能真正取到数据，而不只是 200
            caps = client.get("/api/capabilities", headers=headers)
            assert caps.status_code == 200
            assert caps.json()["total"] > 40

            skills = client.get("/api/skills", headers=headers).json()
            assert isinstance(skills["skills"], list)
    finally:
        store.delete_session(checkpoint_task_id)


def test_server_session_rename_route(tmp_path):
    """改名要走通整条路：HTTP → store → 回列表能看见新名字。"""
    from oryx.server import API_TOKEN, app, store

    with TestClient(app) as client:
        headers = {"X-Oryx-Token": API_TOKEN}
        # 写操作要带来源标记 —— 少了这个头会被中间件挡成 403
        put_headers = {**headers, "X-Oryx-Request": "1"}
        task = store.create_task("原始目标", "craft", "test")
        r = client.put(f"/api/sessions/{task}", json={"title": "改过的名字"},
                       headers=put_headers)
        assert r.status_code == 200, r.text
        # v2 统一响应：领域数据在 data 里，列表本身就是数组
        listed = client.get("/api/sessions", headers=headers).json()["data"]
        item = next(s for s in listed if s["id"] == task)
        assert item["title"] == "改过的名字", str(item)
        assert item["renamed"] is True

        # 清空名字 → 退回用提问当标题
        r = client.put(f"/api/sessions/{task}", json={"title": ""}, headers=put_headers)
        assert r.status_code == 200
        # v2 统一响应：领域数据在 data 里，列表本身就是数组
        listed = client.get("/api/sessions", headers=headers).json()["data"]
        item = next(s for s in listed if s["id"] == task)
        assert item["title"] == "原始目标", str(item)
