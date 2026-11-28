from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx.agents import AgentLibrary
from oryx.config import DEFAULT_CONFIG, Config
from oryx.domains import persona
from oryx.domains.account import SESSION_COOKIE, token_digest
from oryx.domains.chat import ChatRequest, _resolve_run_snapshot
from oryx.experts import ExpertLibrary
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store


class _Catalog:
    def __init__(self, agents: Path, experts: Path):
        self.agent_library = AgentLibrary(agents)
        self.expert_library = ExpertLibrary(experts)

    async def call_async(self, action: str, **params):
        if action == "catalog.reload":
            self.agent_library.reload()
            self.expert_library.reload()
            return {"ok": True}
        if action == "catalog.tools":
            return {"groups": []}
        if action == "catalog.agents":
            return self.agent_library.catalog()
        if action == "catalog.agent":
            item = self.agent_library.get(params["agent_id"])
            return item.to_dict() if item else None
        if action == "catalog.experts":
            return self.expert_library.catalog()
        if action == "catalog.expert":
            item = self.expert_library.get(params["expert_id"])
            return item.to_dict() if item else None
        raise AssertionError(action)


def _app(store: Store, catalog: _Catalog, monkeypatch) -> FastAPI:
    monkeypatch.setattr(persona, "agents_root", lambda: catalog.agent_library.root)
    monkeypatch.setattr(persona, "experts_root", lambda: catalog.expert_library.root)
    app = FastAPI()

    @app.middleware("http")
    async def bind_user(request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE, "")
        user = store.user_for_login_session(token_digest(token)) if token else None
        request.state.user_id = user["id"] if user else None
        return await call_next(request)

    prior = persona.router
    persona.router = APIRouter(prefix="/api", tags=["人设"])
    try:
        persona.register(app, SimpleNamespace(data={"expert": ""}, save=lambda: None), catalog, store)
    finally:
        persona.router = prior
    return app


def _login(store: Store, client: TestClient, user_id: str, token: str) -> None:
    store.create_login_session(token_digest(token), user_id, 4_000_000_000)
    client.cookies.set(SESSION_COOKIE, token)


def _data(response):
    body = response.json()
    assert body["code"] == 0
    return body["data"]


def test_shared_agent_creation_is_creator_editable_and_other_accounts_read_only(tmp_path, monkeypatch):
    store = Store(tmp_path / "oryx.db")
    alice = store.create_user("alice", "hash")
    bob = store.create_user("bobby", "hash")
    catalog = _Catalog(tmp_path / "agents", tmp_path / "experts")
    app = _app(store, catalog, monkeypatch)
    alice_client, bob_client = TestClient(app), TestClient(app)
    _login(store, alice_client, alice["id"], "alice-session")
    _login(store, bob_client, bob["id"], "bob-session")

    created = alice_client.post("/api/agents", json={
        "id": "qa-helper", "name": "质量助手", "description": "检查质量",
        "tools": [], "max_turns": 5, "model": "inherit",
        "permission_mode": "ask", "skills": [], "body": "先列出问题。",
    })
    assert created.status_code == 200
    assert created.json()["code"] == 0

    visible = _data(bob_client.get("/api/agents"))
    assert len(visible) == 1
    assert visible[0]["name"] == "质量助手"
    assert visible[0]["can_edit"] is False
    assert bob_client.put("/api/agents/qa-helper", json={"name": "被改名"}).status_code == 403

    editable = _data(alice_client.get("/api/agents/qa-helper"))
    assert editable["can_edit"] is True
    assert editable["body"] == "先列出问题。"
    updated = alice_client.put("/api/agents/qa-helper", json={"name": "质量审校"})
    assert updated.status_code == 200
    assert _data(alice_client.get("/api/agents/qa-helper"))["name"] == "质量审校"
    assert bob_client.delete("/api/agents/qa-helper").status_code == 403
    assert alice_client.delete("/api/agents/qa-helper").status_code == 200

    expert_created = alice_client.post("/api/experts", json={
        "id": "clear-writer", "name": "清晰写作者", "description": "输出清晰文案",
        "scenarios": ["说明文"], "tags": ["写作"], "personality": "耐心",
        "speaking_style": "简洁", "body": "先明确读者和目标。",
    })
    assert expert_created.status_code == 200
    assert len(_data(bob_client.get("/api/experts"))) == 1
    assert bob_client.put("/api/experts/clear-writer", json={"name": "抢改"}).status_code == 403
    assert _data(alice_client.get("/api/experts/clear-writer"))["can_edit"] is True


@pytest.mark.asyncio
async def test_selected_agent_and_expert_are_snapshotted_together_and_cannot_change(tmp_path, monkeypatch):
    store = Store(tmp_path / "oryx.db")
    agents_root, experts_root = tmp_path / "agents", tmp_path / "experts"
    agent_dir = agents_root / "researcher"
    expert_dir = experts_root / "analyst"
    agent_dir.mkdir(parents=True)
    expert_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: 调研员\ndescription: 查证\ntools: []\nmax_turns: 4\n---\n只给有依据的结论。\n",
        encoding="utf-8",
    )
    (expert_dir / "EXPERT.md").write_text(
        "---\nname: 分析专家\ndescription: 分析数据\nscenarios: [统计]\n---\n给出方法与结论。\n",
        encoding="utf-8",
    )
    catalog = _Catalog(agents_root, experts_root)
    monkeypatch.setattr(persona, "agent_dir", lambda asset_id: agents_root / asset_id)
    monkeypatch.setattr(persona, "expert_dir", lambda asset_id: experts_root / asset_id)
    config = SimpleNamespace(data={"mode": "efficient", "interaction": "craft", "style": "professional"})

    snapshot = await _resolve_run_snapshot(
        catalog, config, ChatRequest(goal="分析", agent_id="researcher", expert="analyst"),
        store, None, "alice",
    )
    assert snapshot["agent"]["id"] == "researcher"
    assert snapshot["expert"]["id"] == "analyst"
    assert snapshot["agent"]["body"] == "只给有依据的结论。"
    task_id = store.create_task(
        "分析", "efficient", "local", owner_id="alice",
        agent_id="researcher", expert_id="analyst", config_snapshot=snapshot,
    )
    task = store.get_task(task_id, "alice")
    assert task["config_snapshot"]["agent"]["name"] == "调研员"
    assert task["config_snapshot"]["expert"]["name"] == "分析专家"

    same = await _resolve_run_snapshot(
        catalog, config,
        ChatRequest(goal="继续分析", agent_id="researcher", expert="analyst"),
        store, task, "alice",
    )
    assert same == snapshot
    with pytest.raises(Exception) as error:
        await _resolve_run_snapshot(
            catalog, config,
            ChatRequest(goal="继续分析", agent_id="researcher", expert=""),
            store, task, "alice",
        )
    assert getattr(error.value, "status_code", None) == 409


def test_task_config_snapshot_is_write_once(tmp_path):
    store = Store(tmp_path / "oryx.db")
    task_id = store.create_task("goal", "craft", "local", owner_id="u")
    first = {"agent_id": "one", "expert_id": "a"}
    second = {"agent_id": "two", "expert_id": "b"}
    assert store.set_task_config(task_id, "one", "a", first)
    assert not store.set_task_config(task_id, "two", "b", second)
    task = store.get_task(task_id, "u")
    assert task["agent_id"] == "one"
    assert task["expert_id"] == "a"
    assert task["config_snapshot"] == first


def test_runtime_uses_frozen_agent_and_expert_profile(tmp_path, monkeypatch):
    config_data = dict(DEFAULT_CONFIG)
    config_data["allowed_roots"] = [str(tmp_path)]
    config_data["forbidden_paths"] = []
    config_data["providers"] = {
        "main": {"type": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "main-model"}
    }
    config_data["active_provider"] = "main"
    config = Config(config_data)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(config, store, EventBus())
    runtime.agents = AgentLibrary(tmp_path / "agents")
    captured = {}

    class Provider:
        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            from oryx.providers import ProviderReply

            captured["system"] = system
            captured["tools"] = tools
            return ProviderReply(text="done", tool_calls=[], stop_reason="end_turn")

    import oryx.loop as loop_module

    monkeypatch.setattr(loop_module, "build_provider", lambda _cfg: Provider())
    snapshot = {
        "version": 1,
        "agent_id": "frozen-agent",
        "expert_id": "frozen-expert",
        "runtime": {"mode": "efficiency", "interaction": "craft", "style": "professional"},
        "agent": {
            "id": "frozen-agent", "name": "快照调研员", "description": "仅做查证",
            "tools": [], "max_turns": 1, "model": "inherit", "permission_mode": "inherit",
            "skills": [], "disallowed_tools": [], "memory": "", "initial_prompt": "先列证据",
            "body": "本次快照职责正文。", "kind": "agent",
        },
        "expert": {
            "id": "frozen-expert", "name": "快照专家", "description": "分析口径",
            "scenarios": [], "tags": [], "personality": "严谨",
            "speaking_style": "简洁", "use_cases": "", "self_intro": "", "body": "本次快照人设正文。",
        },
    }

    runtime.run(
        "完成检查", mode="efficiency", interaction="craft", style="professional",
        expert="frozen-expert", agent_id="frozen-agent", config_snapshot=snapshot,
        owner_id="alice",
    )

    assert "本次快照职责正文。" in captured["system"]
    assert "本次快照人设正文。" in captured["system"]
    assert "严谨" in captured["system"]
    assert captured["tools"] == []
    task = store.recent_tasks(owner_id="alice")[0]
    assert task["agent_id"] == "frozen-agent"
    assert task["expert_id"] == "frozen-expert"
