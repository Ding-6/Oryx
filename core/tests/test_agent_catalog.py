"""模块 6：子 Agent 候选目录与写盘前字段校验的契约测试。

钉住四件容易回退的事：

1. `GET /api/agents/catalog` 必须注册在 `/api/agents/{agent_id}` 之前 ——
   否则 "catalog" 会被当成一个 Agent id 去查详情，返回 404。
2. 内核没起来时目录**降级**（tools/skills 空 + available=False），
   不能整个 503 白屏；模型服务来自 config，永远可用。
3. tools / disallowed_tools / skills / model 四类候选都要在写盘前校验，
   不能让填错的配置进目录等运行时才炸。
4. 空 tools 的定义在内核没起来时也能创建 —— 以前即使 tools 为空
   也会调一次 `catalog.tools`，IPC 一失败就 503。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx.agents import AgentLibrary
from oryx.domains import persona
from oryx.domains.account import SESSION_COOKIE, token_digest
from oryx.experts import ExpertLibrary
from oryx.ipc import IpcError
from oryx.store import Store


class _Catalog:
    """只实现 persona 域会用到的 IPC 方法。"""

    def __init__(self, agents: Path, experts: Path, *, tools=None, skills=None,
                 fail_tools=False, fail_skills=False):
        self.agent_library = AgentLibrary(agents)
        self.expert_library = ExpertLibrary(experts)
        self.tools = tools if tools is not None else ["fs_read", "fs_write", "index_search"]
        self.skills = skills if skills is not None else ["writing", "review"]
        self.fail_tools = fail_tools
        self.fail_skills = fail_skills

    async def call_async(self, action: str, **params):
        if action == "catalog.reload":
            self.agent_library.reload()
            self.expert_library.reload()
            return {"ok": True}
        if action == "catalog.tools":
            if self.fail_tools:
                raise IpcError("内核未就绪")
            return {"groups": [{"group": "fs", "tools": [
                {"name": n, "description": f"{n} 的说明", "risk_label": "低"}
                for n in self.tools
            ]}]}
        if action == "catalog.skills_detail":
            if self.fail_skills:
                raise IpcError("内核未就绪")
            return {"skills": [
                {"name": n, "description": f"{n} 说明", "path": f"/skills/{n}"}
                for n in self.skills
            ]}
        if action == "catalog.skills":
            if self.fail_skills:
                raise IpcError("内核未就绪")
            return list(self.skills)
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


def _app(store: Store, catalog: _Catalog, monkeypatch, providers=None):
    monkeypatch.setattr(persona, "agents_root", lambda: catalog.agent_library.root)
    monkeypatch.setattr(persona, "experts_root", lambda: catalog.expert_library.root)
    app = FastAPI()

    @app.middleware("http")
    async def bind_user(request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE, "")
        user = store.user_for_login_session(token_digest(token)) if token else None
        request.state.user_id = user["id"] if user else None
        return await call_next(request)

    config = SimpleNamespace(
        data={
            "expert": "",
            "providers": providers if providers is not None else {
                "main": {"type": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "m1"},
            },
        },
        save=lambda: None,
    )
    prior = persona.router
    persona.router = APIRouter(prefix="/api", tags=["人设"])
    try:
        persona.register(app, config, catalog, store)
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


def _error_message(response) -> str:
    """测试 app 没挂全局异常处理器（只有 detail），生产 app 有 message。"""
    body = response.json()
    return str(body.get("message") or body.get("detail") or "")


def _fixture(tmp_path, monkeypatch, **catalog_kw):
    store = Store(tmp_path / "oryx.db")
    user = store.create_user("alice", "hash")
    catalog = _Catalog(tmp_path / "agents", tmp_path / "experts", **catalog_kw)
    app = _app(store, catalog, monkeypatch)
    client = TestClient(app)
    _login(store, client, user["id"], "alice-session")
    return store, catalog, client


def _base_payload(**overrides):
    payload = {
        "id": "qa-helper", "name": "质量助手", "description": "检查质量",
        "tools": [], "max_turns": 5, "model": "inherit",
        "permission_mode": "ask", "skills": [], "disallowed_tools": [],
        "kind": "agent", "body": "先列出问题。",
    }
    payload.update(overrides)
    return payload


# ── 候选目录 ──────────────────────────────────────────────────────────

def test_catalog_returns_all_candidates_and_enums(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    data = _data(client.get("/api/agents/catalog"))
    assert [t["name"] for t in data["tools"]] == ["fs_read", "fs_write", "index_search"]
    assert data["tools"][0]["group"] == "fs"
    assert data["tools"][0]["description"] == "fs_read 的说明"
    assert [s["name"] for s in data["skills"]] == ["writing", "review"]
    assert [m["name"] for m in data["models"]] == ["main"]
    assert data["models"][0]["model"] == "m1"
    assert data["permission_modes"] == list(persona.PERMISSION_MODES)
    assert data["kinds"] == list(persona.AGENT_KINDS)
    assert data["available"] == {"tools": True, "skills": True, "models": True}


def test_catalog_route_wins_over_agent_detail(tmp_path, monkeypatch):
    """路由顺序回归：catalog 不能被 /agents/{agent_id} 吃掉。"""
    _, _, client = _fixture(tmp_path, monkeypatch)
    response = client.get("/api/agents/catalog")
    assert response.status_code == 200
    assert "models" in _data(response)


def test_catalog_degrades_instead_of_503_when_kernel_unready(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch, fail_tools=True, fail_skills=True)
    response = client.get("/api/agents/catalog")
    assert response.status_code == 200
    data = _data(response)
    assert data["tools"] == [] and data["skills"] == []
    assert data["available"] == {"tools": False, "skills": False, "models": True}
    assert [m["name"] for m in data["models"]] == ["main"]


def test_catalog_skills_accepts_plain_name_list(tmp_path, monkeypatch):
    """catalog.skills 历史上返回过 list[str]，两种形状都要吃得下。"""
    from oryx.domains.persona import _skill_names

    assert _skill_names(["a", {"name": "b"}, {"name": ""}, 3]) == {"a", "b"}


# ── 写盘前校验 ────────────────────────────────────────────────────────

def test_create_rejects_unknown_tool(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    response = client.post("/api/agents", json=_base_payload(tools=["fs_read", "不存在的工具"]))
    assert response.status_code == 400
    assert "这些工具不存在" in _error_message(response)
    assert not (tmp_path / "agents" / "qa-helper").exists(), "校验失败不能留下半个目录"


def test_create_rejects_unknown_disallowed_tool(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    response = client.post("/api/agents", json=_base_payload(
        tools=["fs_read"], disallowed_tools=["乱写的"]))
    assert response.status_code == 400
    assert "额外禁用工具里有不存在的" in _error_message(response)


def test_create_rejects_unknown_skill(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    response = client.post("/api/agents", json=_base_payload(skills=["writing", "没有这个技能"]))
    assert response.status_code == 400
    assert "这些技能不存在" in _error_message(response)


def test_create_rejects_unknown_model_even_when_kernel_unready(tmp_path, monkeypatch):
    """模型校验读 config，不依赖内核 —— 内核没起来也照拦。"""
    _, _, client = _fixture(tmp_path, monkeypatch, fail_tools=True, fail_skills=True)
    response = client.post("/api/agents", json=_base_payload(model="不存在的服务"))
    assert response.status_code == 400
    assert "模型服务「不存在的服务」不存在" in _error_message(response)


def test_create_accepts_all_known_values(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    response = client.post("/api/agents", json=_base_payload(
        tools=["fs_read"], disallowed_tools=["fs_write"],
        skills=["writing"], model="main"))
    assert response.status_code == 200
    assert _data(response)["created"] is True
    detail = _data(client.get("/api/agents/qa-helper"))
    assert detail["tools"] == ["fs_read"]
    assert detail["disallowed_tools"] == ["fs_write"]
    assert detail["skills"] == ["writing"]
    assert detail["model"] == "main"


def test_create_without_tools_works_when_kernel_unready(tmp_path, monkeypatch):
    """既存 bug 回归：以前 tools 为空也会调 catalog.tools，内核没起来就 503。"""
    _, _, client = _fixture(tmp_path, monkeypatch, fail_tools=True, fail_skills=True)
    response = client.post("/api/agents", json=_base_payload())
    assert response.status_code == 200, response.text


def test_kernel_unready_skips_name_checks_but_keeps_model_check(tmp_path, monkeypatch):
    """拿不到清单时不该把合法编辑挡在门外，但 config 能判的仍然要判。"""
    _, _, client = _fixture(tmp_path, monkeypatch, fail_tools=True, fail_skills=True)
    ok_response = client.post("/api/agents", json=_base_payload(tools=["随便什么"], skills=["也随便"]))
    assert ok_response.status_code == 200
    bad = client.post("/api/agents", json=_base_payload(id="another", model="乱写"))
    assert bad.status_code == 400


def test_update_validates_new_fields(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    assert client.post("/api/agents", json=_base_payload()).status_code == 200
    bad = client.put("/api/agents/qa-helper", json=_base_payload(skills=["没有这个技能"]))
    assert bad.status_code == 400
    assert "这些技能不存在" in _error_message(bad)
    good = client.put("/api/agents/qa-helper", json=_base_payload(
        name="质量审校", tools=["fs_read"], skills=["review"], model="main"))
    assert good.status_code == 200
    assert _data(client.get("/api/agents/qa-helper"))["name"] == "质量审校"


def test_pydantic_still_rejects_unsupported_enums(tmp_path, monkeypatch):
    _, _, client = _fixture(tmp_path, monkeypatch)
    assert client.post("/api/agents", json=_base_payload(kind="robot")).status_code == 422
    assert client.post("/api/agents", json=_base_payload(permission_mode="god")).status_code == 422


# ── 403 文案 ──────────────────────────────────────────────────────────

def test_builtin_definition_403_says_builtin_not_other_creator(tmp_path, monkeypatch):
    """内置定义没有创建者记录 —— 不能骗用户说「由其他创建者维护」。"""
    store, catalog, client = _fixture(tmp_path, monkeypatch)
    builtin = catalog.agent_library.root / "builtin"
    builtin.mkdir(parents=True)
    (builtin / "AGENT.md").write_text(
        "---\nname: 内置助手\ndescription: 预装\ntools: []\n---\n正文。\n", encoding="utf-8",
    )
    catalog.agent_library.reload()
    response = client.put("/api/agents/builtin", json={"name": "想改名"})
    assert response.status_code == 403
    message = _error_message(response)
    assert "内置定义" in message and "没有创建者记录" in message
    assert "其他创建者" not in message


def test_other_creator_403_says_other_creator(tmp_path, monkeypatch):
    store, _, alice_client = _fixture(tmp_path, monkeypatch)
    assert alice_client.post("/api/agents", json=_base_payload()).status_code == 200
    bob = store.create_user("bobby", "hash")
    bob_client = TestClient(alice_client.app)
    _login(store, bob_client, bob["id"], "bob-session")
    response = bob_client.put("/api/agents/qa-helper", json={"name": "抢改"})
    assert response.status_code == 403
    assert "其他创建者" in _error_message(response)
