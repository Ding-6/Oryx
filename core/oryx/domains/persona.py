"""人设域：专家（人设）与子 Agent 的增删改查，以及「当前用的是哪个」。

对外：
    GET    /api/agents/catalog      子 Agent 的候选目录（工具 / 技能 / 模型服务 / 枚举）
    GET    /api/persona             当前人设：选中的专家 + 它的性格/说话风格/适用场景
    POST   /api/expert              切换当前专家
    GET    /api/experts/{id}        专家详情
    POST   /api/experts             新建专家
    PUT    /api/experts/{id}        改专家
    DELETE /api/experts/{id}        删专家
    GET    /api/agents/{id}         子 Agent 详情
    POST   /api/agents              新建子 Agent
    PUT    /api/agents/{id}         改子 Agent
    DELETE /api/agents/{id}         删子 Agent

**为什么写盘在控制面**
------------------
人设与子 Agent 的增删改查是**纯文件操作** —— 专家是 `experts/<id>/EXPERT.md`，
子 Agent 是 `agents/<id>/AGENT.md`。内核只负责读取与重载，
所以写完要调 `catalog.reload` 让它重新扫描。
"""
from __future__ import annotations

import json as jsonlib
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..api.response import ok
from ..ipc import IpcError
from ._common import is_kernel_unready, rpc_failure

router = APIRouter(prefix="/api", tags=["人设"])


# ── 候选目录的枚举常量 ──────────────────────────────────────────────
# 前端不再硬编码这两组选项：GET /api/agents/catalog 把它们一起发过去，
# Pydantic validator 也从同一份常量取值，两边不会各改各的。
PERMISSION_MODES = ("inherit", "ask", "plan", "craft", "bypass", "dontAsk", "auto", "delegate")
AGENT_KINDS = ("agent", "butler")


class ExpertRequest(BaseModel):
    expert: str = ""


class _AssetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    body: str | None = Field(default=None, max_length=100_000)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_\-一-鿿 ]{1,64}", value.strip()):
            raise ValueError("id 只能含中英文、数字、-、_、空格，长度不超过 64")
        return value.strip() if value is not None else None


class ExpertPayload(_AssetPayload):
    scenarios: list[str] | None = None
    tags: list[str] | None = None
    personality: str | None = Field(default=None, max_length=2000)
    speaking_style: str | None = Field(default=None, max_length=2000)
    use_cases: str | None = Field(default=None, max_length=4000)
    self_intro: str | None = Field(default=None, max_length=1000)


class AgentPayload(_AssetPayload):
    tools: list[str] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=100)
    model: str | None = Field(default=None, max_length=120)
    permission_mode: str | None = Field(default=None, max_length=40)
    skills: list[str] | None = None
    disallowed_tools: list[str] | None = None
    memory: str | None = Field(default=None, max_length=120)
    initial_prompt: str | None = Field(default=None, max_length=8000)
    icon: str | None = Field(default=None, max_length=32)
    subtitle: str | None = Field(default=None, max_length=120)
    kind: str | None = Field(default=None, max_length=32)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in AGENT_KINDS:
            raise ValueError("kind 只能是 agent 或 butler")
        return value

    @field_validator("permission_mode")
    @classmethod
    def valid_permission_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in PERMISSION_MODES:
            raise ValueError("权限模式不受支持")
        return value


def _yaml(value: Any) -> str:
    """JSON scalars and arrays are valid YAML and safely preserve punctuation."""
    return jsonlib.dumps(value, ensure_ascii=False)


def _payload_data(req: BaseModel, path_id: str | None = None) -> dict[str, Any]:
    payload = req.model_dump(exclude_unset=True)
    if any(value is None for value in payload.values()):
        raise HTTPException(400, "字段不能为 null；清空文本请传空字符串，清空列表请传空数组")
    if path_id is not None:
        given_id = payload.pop("id", path_id)
        if given_id != path_id:
            raise HTTPException(400, "资源 ID 创建后不可更改")
    return payload


def _creator_metadata(store, kind: str, asset_id: str, actor_id: str | None) -> dict[str, Any]:
    item = store.shared_asset(kind, asset_id)
    creator_id = item.get("creator_id") if item else None
    return {
        "creator_id": creator_id,
        "can_edit": bool(item is not None and creator_id == actor_id),
        "shared": True,
    }


def _require_editor(store, kind: str, asset_id: str, actor_id: str | None) -> None:
    metadata = store.shared_asset(kind, asset_id)
    if metadata is None:
        # 早于账号系统的内置定义没有创建者记录 —— 不是「别人的」，
        # 以前那句含糊的「只能查看」让前端误写成「由其他创建者维护」。
        raise HTTPException(403, "这是内置定义，没有创建者记录，只能查看；如需修改请新建一个自己的定义")
    if metadata.get("creator_id") != actor_id:
        raise HTTPException(403, "这条定义由其他创建者维护，只有创建者可以修改或删除")


# ── 目录与 id 校验 ────────────────────────────────────────────────────

def experts_root() -> Path:
    """仓库里的 experts/ 目录。

    `parents[3]` —— 本文件在 `oryx/domains/` 下，比 `oryx/server.py`
    **多一层**。用原来的索引会指到 `core/` 而不是仓库根 ⚠️
    """
    return Path(__file__).resolve().parents[3] / "experts"


def agents_root() -> Path:
    """仓库里的 agents/ 目录（同样注意层级）。"""
    return Path(__file__).resolve().parents[3] / "agents"


def expert_dir(expert_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-一-鿿 ]{1,64}", expert_id or ""):
        raise HTTPException(400, "人设 id 只能含中英文、数字、-、_、空格")
    return experts_root() / expert_id


def agent_dir(agent_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-一-鿿 ]{1,64}", agent_id or ""):
        raise HTTPException(400, "Agent id 只能含中英文、数字、-、_、空格")
    return agents_root() / agent_id


# ── 写盘 ──────────────────────────────────────────────────────────────

def write_expert_md(path: Path, d: dict[str, Any]) -> None:
    lines = ["---", f"name: {_yaml(d.get('name', ''))}", f"description: {_yaml(d.get('description', ''))}"]
    for key in ("scenarios", "tags"):
        v = d.get(key) or []
        lines.append(f"{key}: {_yaml(v if isinstance(v, list) else [str(v)])}")
    for key in ("personality", "speaking_style", "use_cases", "self_intro"):
        lines.append(f"{key}: {_yaml(d.get(key, ''))}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + str(d.get("body", "")).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def write_agent_md(path: Path, d: dict[str, Any]) -> None:
    fields = {
        "name": d.get("name", ""),
        "description": d.get("description", ""),
        "tools": d.get("tools") or [],
        "max_turns": int(d.get("max_turns", 6) or 6),
        "model": d.get("model", "inherit"),
        "permissionMode": d.get("permission_mode", d.get("permissionMode", "inherit")),
        "skills": d.get("skills") or [],
        "disallowedTools": d.get("disallowed_tools", d.get("disallowedTools")) or [],
        "memory": d.get("memory", ""),
        "initialPrompt": d.get("initial_prompt", d.get("initialPrompt", "")),
        "icon": d.get("icon", ""),
        "subtitle": d.get("subtitle", ""),
        "kind": d.get("kind", "agent"),
    }
    lines = ["---", *(f"{key}: {_yaml(value)}" for key, value in fields.items()), "---"]
    text = "\n".join(lines) + "\n" + str(d.get("body", "")).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


async def reload_catalog(agent) -> None:
    """写完盘让内核重新扫描。

    内核没起来也不该阻断写文件 —— 它下次启动自己会扫到。
    """
    try:
        await agent.call_async("catalog.reload")
    except IpcError:
        pass


async def known_tools(agent) -> set[str]:
    """当前工具名集合，用来校验子 Agent 的白名单。"""
    try:
        payload = await agent.call_async("catalog.tools")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {t["name"] for g in payload.get("groups", []) for t in g["tools"]}


# ── 候选目录与字段校验 ────────────────────────────────────────────────
# 以前工具 / 技能 / 模型都是手敲文本框，后端只校验 tools —— 填错要等
# 派活时才炸。这里把三份权威清单聚合成一个接口，写盘前统一校验。

def _skill_names(raw: Any) -> set[str]:
    """catalog.skills 历史上返回过 list[str] 和 list[dict] 两种形状。"""
    out: set[str] = set()
    for item in raw or []:
        if isinstance(item, str):
            if item.strip():
                out.add(item)
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                out.add(name)
    return out


def _provider_specs(config) -> dict[str, Any]:
    """模型服务名 → 配置。config 在测试里可能是 SimpleNamespace。"""
    data = getattr(config, "data", None) or {}
    return data.get("providers") or {}


async def asset_catalog(agent, config) -> dict[str, Any]:
    """子 Agent 配置页的候选目录。

    三份清单**各自降级**：内核没起来时工具 / 技能给空数组并把
    available 标 False，界面照常能开（只是提示「启动后才有候选」），
    模型服务直接读 config，永远可用。绝不能因为一个 IPC 失败，
    让整个配置页 503 白屏。
    """
    tools: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    tools_ready = False
    skills_ready = False

    try:
        payload = await agent.call_async("catalog.tools")
        for group in payload.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            label = str(group.get("group") or "")
            for tool in group.get("tools", []) or []:
                if isinstance(tool, dict) and tool.get("name"):
                    tools.append({
                        "name": str(tool["name"]),
                        "group": label,
                        "description": str(tool.get("description") or ""),
                        "risk_label": str(tool.get("risk_label") or ""),
                    })
        tools_ready = True
    except IpcError:
        pass

    try:
        raw = await agent.call_async("catalog.skills_detail")
        items = raw.get("skills") if isinstance(raw, dict) else raw
        for item in items or []:
            if isinstance(item, dict) and str(item.get("name") or "").strip():
                skills.append({
                    "name": str(item["name"]),
                    "description": str(item.get("description") or ""),
                })
            elif isinstance(item, str) and item.strip():
                skills.append({"name": item, "description": ""})
        skills_ready = True
    except IpcError:
        pass

    models = [
        {
            "name": str(name),
            "model": str((spec or {}).get("model") or ""),
            "type": str((spec or {}).get("type") or "openai"),
        }
        for name, spec in sorted(_provider_specs(config).items())
    ]
    return {
        "tools": tools,
        "skills": skills,
        "models": models,
        "permission_modes": list(PERMISSION_MODES),
        "kinds": list(AGENT_KINDS),
        "available": {"tools": tools_ready, "skills": skills_ready, "models": True},
    }


async def validate_agent_assets(agent, config, data: dict[str, Any]) -> None:
    """写盘前校验 tools / disallowed_tools / skills / model。

    **内核没起来时只跳过那一项校验，不阻断保存**：拿不到工具清单，
    就没法判断名字对不对，这时候把用户的合法编辑挡在门外是没道理的。
    模型服务来自 config，与内核无关，所以这项永远校验。
    """
    model = str(data.get("model") or "").strip()
    if model and model != "inherit" and model not in _provider_specs(config):
        raise HTTPException(400, f"模型服务「{model}」不存在，请先到设置里添加")

    tool_values = list(data.get("tools") or []) + list(data.get("disallowed_tools") or [])

    async def check_tools() -> set[str] | None:
        if not tool_values:
            return None
        try:
            return await known_tools(agent)
        except HTTPException as exc:
            if exc.status_code == 503:
                return None
            raise

    known = await check_tools()
    if known is not None:
        bad = sorted({str(v) for v in (data.get("tools") or []) if str(v) not in known})
        if bad:
            raise HTTPException(400, f"这些工具不存在：{'、'.join(bad)}")
        bad = sorted({str(v) for v in (data.get("disallowed_tools") or []) if str(v) not in known})
        if bad:
            raise HTTPException(400, f"额外禁用工具里有不存在的：{'、'.join(bad)}")

    skill_values = list(data.get("skills") or [])
    if skill_values:
        try:
            raw = await agent.call_async("catalog.skills")
        except IpcError as exc:
            if not is_kernel_unready(str(exc)):
                raise rpc_failure(exc) from None
            raw = None
        if raw is not None:
            known_skills = _skill_names(raw)
            bad = sorted({str(v) for v in skill_values if str(v) not in known_skills})
            if bad:
                raise HTTPException(400, f"这些技能不存在：{'、'.join(bad)}")


# ── 专家 ──────────────────────────────────────────────────────────────

def _body_from_file(path: Path) -> str:
    if not path.is_file():
        return ""
    from ..frontmatter import FRONTMATTER

    matched = FRONTMATTER.match(path.read_text("utf-8-sig"))
    return matched.group(2).strip() if matched else ""


async def list_assets(agent, store, kind: str, actor_id: str | None) -> list[dict[str, Any]]:
    try:
        items = await agent.call_async("catalog.agents" if kind == "agent" else "catalog.experts")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    root = agents_root() if kind == "agent" else experts_root()
    filename = "AGENT.md" if kind == "agent" else "EXPERT.md"
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        asset_id = str(item["id"])
        out.append({
            **item,
            "body": _body_from_file(root / asset_id / filename),
            **(_creator_metadata(store, kind, asset_id, actor_id) if store else {}),
        })
    return out


async def expert_detail(agent, expert_id: str, store=None, actor_id: str | None = None) -> dict:
    try:
        e = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not e:
        raise HTTPException(404, "没有这个人设")
    body = _body_from_file(expert_dir(expert_id) / "EXPERT.md")
    metadata = _creator_metadata(store, "expert", expert_id, actor_id) if store else {}
    return {**e, "body": body, **metadata}


async def expert_create(agent, data: dict[str, Any], store=None, actor_id: str | None = None) -> dict:
    expert_id = str(data.get("id", "")).strip()
    if not expert_id:
        raise HTTPException(400, "id 不能为空")
    if not str(data.get("name") or "").strip():
        raise HTTPException(400, "名称不能为空")
    try:
        exists = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    target = expert_dir(expert_id)
    if exists or target.exists() or (store and store.shared_asset("expert", expert_id)):
        raise HTTPException(409, "已存在同名人设")
    normalized = {"scenarios": [], "tags": [], **data}
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, "已存在同名人设") from None
    try:
        write_expert_md(target / "EXPERT.md", normalized)
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(500, f"保存人设失败：{exc}") from None
    if store:
        store.claim_shared_asset("expert", expert_id, actor_id)
    await reload_catalog(agent)
    return {"id": expert_id, "created": True}


async def expert_update(agent, expert_id: str, data: dict[str, Any], store=None, actor_id: str | None = None) -> dict:
    if store:
        _require_editor(store, "expert", expert_id, actor_id)
    try:
        e = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not e:
        raise HTTPException(404, "没有这个人设")
    merged = {**e, **{k: v for k, v in data.items() if k in (
        "name", "description", "scenarios", "tags", "personality",
        "speaking_style", "use_cases", "self_intro", "body")}}
    write_expert_md(expert_dir(expert_id) / "EXPERT.md", merged)
    if store:
        store.touch_shared_asset("expert", expert_id)
    await reload_catalog(agent)
    return {"id": expert_id, "updated": True}


async def expert_delete(agent, config, expert_id: str, store=None, actor_id: str | None = None) -> dict:
    if store:
        _require_editor(store, "expert", expert_id, actor_id)
    try:
        e = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not e:
        raise HTTPException(404, "没有这个人设")
    try:
        shutil.rmtree(expert_dir(expert_id))
    except OSError as exc:
        raise HTTPException(500, f"删除人设失败：{exc}") from None
    if store:
        store.delete_shared_asset("expert", expert_id)
    await reload_catalog(agent)
    if config.data.get("expert") == expert_id:
        config.data["expert"] = ""
        config.save()
    return {"id": expert_id, "deleted": True}


# ── 子 Agent ──────────────────────────────────────────────────────────

async def agent_detail(agent, agent_id: str, store=None, actor_id: str | None = None) -> dict:
    try:
        a = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not a:
        raise HTTPException(404, "没有这个子 Agent")
    body = _body_from_file(agent_dir(agent_id) / "AGENT.md")
    metadata = _creator_metadata(store, "agent", agent_id, actor_id) if store else {}
    return {**a, "body": body, **metadata}


async def agent_create(agent, data: dict[str, Any], store=None, actor_id: str | None = None, config=None) -> dict:
    agent_id = str(data.get("id", "")).strip()
    if not agent_id:
        raise HTTPException(400, "id 不能为空")
    if not str(data.get("name") or "").strip():
        raise HTTPException(400, "名称不能为空")
    try:
        exists = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    target = agent_dir(agent_id)
    if exists or target.exists() or (store and store.shared_asset("agent", agent_id)):
        raise HTTPException(409, "已存在同名 Agent")
    # 工具 / 技能 / 模型服务的白名单是硬约束，写盘前一起校验。
    # 以前只有 tools 会查，而且**即使 tools 为空也会调 IPC** ——
    # 内核没起来时连一个空工具定义的 Agent 都建不了（503）。
    await validate_agent_assets(agent, config, data)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, "已存在同名 Agent") from None
    try:
        write_agent_md(target / "AGENT.md", {"max_turns": 6, "kind": "agent", **data})
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(500, f"保存 Agent 失败：{exc}") from None
    if store:
        store.claim_shared_asset("agent", agent_id, actor_id)
    await reload_catalog(agent)
    return {"id": agent_id, "created": True}


async def agent_update(agent, agent_id: str, data: dict[str, Any], store=None, actor_id: str | None = None, config=None) -> dict:
    if store:
        _require_editor(store, "agent", agent_id, actor_id)
    try:
        a = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not a:
        raise HTTPException(404, "没有这个子 Agent")
    await validate_agent_assets(agent, config, data)
    merged = {**a, **{k: v for k, v in data.items() if k in (
        "name", "description", "tools", "max_turns", "body", "model",
        "permission_mode", "skills", "disallowed_tools", "memory", "initial_prompt",
        "icon", "subtitle", "kind")}}
    write_agent_md(agent_dir(agent_id) / "AGENT.md", merged)
    if store:
        store.touch_shared_asset("agent", agent_id)
    await reload_catalog(agent)
    return {"id": agent_id, "updated": True}


async def agent_delete(agent, agent_id: str, store=None, actor_id: str | None = None) -> dict:
    if store:
        _require_editor(store, "agent", agent_id, actor_id)
    try:
        a = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not a:
        raise HTTPException(404, "没有这个子 Agent")
    try:
        shutil.rmtree(agent_dir(agent_id))
    except OSError as exc:
        raise HTTPException(500, f"删除 Agent 失败：{exc}") from None
    if store:
        store.delete_shared_asset("agent", agent_id)
    await reload_catalog(agent)
    return {"id": agent_id, "deleted": True}


# ── 当前人设 ──────────────────────────────────────────────────────────

async def set_expert(agent, config, expert: str) -> dict:
    if expert:
        try:
            exists = await agent.call_async("catalog.expert", expert_id=expert)
        except IpcError as exc:
            raise rpc_failure(exc) from None
        if not exists:
            raise HTTPException(404, "没有这个专家")
    config.data["expert"] = expert
    config.save()
    return {"expert": expert}


async def current_persona(agent, config) -> dict:
    """当前人设：选中的专家 + 它的性格、说话风格、适用场景。"""
    expert_id = config.data.get("expert", "")
    expert = None
    available: list[dict[str, Any]] = []
    try:
        expert = await agent.call_async("catalog.expert", expert_id=expert_id)
        available = await agent.call_async("catalog.experts")
    except IpcError:
        pass
    return {
        "expert": expert_id,
        "persona": expert,
        "available": available,
        "style": config.data.get("style", "professional"),
    }


def register(app, config, agent, store=None) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/persona")
    async def _persona():
        return ok(await current_persona(agent, config))

    @router.post("/expert")
    async def _set_expert(req: ExpertRequest):
        return ok(await set_expert(agent, config, req.expert))

    @router.get("/experts")
    async def _experts(request: Request):
        return ok(await list_assets(agent, store, "expert", getattr(request.state, "user_id", None)))

    @router.get("/experts/{expert_id}")
    async def _expert_detail(expert_id: str, request: Request):
        return ok(await expert_detail(
            agent, expert_id, store, getattr(request.state, "user_id", None)
        ))

    @router.post("/experts")
    async def _expert_create(req: ExpertPayload, request: Request):
        payload = _payload_data(req)
        return ok(await expert_create(
            agent, payload, store, getattr(request.state, "user_id", None)
        ))

    @router.put("/experts/{expert_id}")
    async def _expert_update(expert_id: str, req: ExpertPayload, request: Request):
        payload = _payload_data(req, expert_id)
        return ok(await expert_update(
            agent, expert_id, payload, store, getattr(request.state, "user_id", None)
        ))

    @router.delete("/experts/{expert_id}")
    async def _expert_delete(expert_id: str, request: Request):
        return ok(await expert_delete(
            agent, config, expert_id, store, getattr(request.state, "user_id", None)
        ))

    @router.get("/agents")
    async def _agents(request: Request):
        return ok(await list_assets(agent, store, "agent", getattr(request.state, "user_id", None)))

    # ⚠️ 必须注册在 /agents/{agent_id} 之前 —— 否则 "catalog" 会被当成
    # 一个 Agent id 去查详情，返回 404。「catalog」视为保留字。
    @router.get("/agents/catalog")
    async def _agent_catalog():
        return ok(await asset_catalog(agent, config))

    @router.get("/agents/{agent_id}")
    async def _agent_detail(agent_id: str, request: Request):
        return ok(await agent_detail(
            agent, agent_id, store, getattr(request.state, "user_id", None)
        ))

    @router.post("/agents")
    async def _agent_create(req: AgentPayload, request: Request):
        payload = _payload_data(req)
        return ok(await agent_create(
            agent, payload, store, getattr(request.state, "user_id", None), config=config
        ))

    @router.put("/agents/{agent_id}")
    async def _agent_update(agent_id: str, req: AgentPayload, request: Request):
        payload = _payload_data(req, agent_id)
        return ok(await agent_update(
            agent, agent_id, payload, store, getattr(request.state, "user_id", None), config=config
        ))

    @router.delete("/agents/{agent_id}")
    async def _agent_delete(agent_id: str, request: Request):
        return ok(await agent_delete(
            agent, agent_id, store, getattr(request.state, "user_id", None)
        ))

    app.include_router(router)
