"""人设域：专家（人设）与子 Agent 的增删改查，以及「当前用的是哪个」。

对外：
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

import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api", tags=["人设"])


class ExpertRequest(BaseModel):
    expert: str = ""


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

def write_expert_md(path: Path, d: dict) -> None:
    lines = ["---", f"name: {d.get('name', '')}", f"description: {d.get('description', '')}"]
    for key in ("scenarios", "tags"):
        v = d.get(key) or []
        lines.append(f"{key}: {', '.join(v) if isinstance(v, list) else v}")
    for key in ("personality", "speaking_style", "use_cases", "self_intro"):
        lines.append(f"{key}: {d.get(key, '')}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + str(d.get("body", "")).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def write_agent_md(path: Path, d: dict) -> None:
    tools = d.get("tools") or []
    lines = [
        "---",
        f"name: {d.get('name', '')}",
        f"description: {d.get('description', '')}",
        f"tools: {', '.join(tools) if isinstance(tools, list) else tools}",
        f"max_turns: {int(d.get('max_turns', 6) or 6)}",
        "---",
    ]
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


# ── 专家 ──────────────────────────────────────────────────────────────

async def expert_detail(agent, expert_id: str) -> dict:
    try:
        e = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not e:
        raise HTTPException(404, "没有这个人设")
    # 内核只回传解析后的头部，正文（frontmatter 之后那部分）仍要从文件读
    from ..frontmatter import FRONTMATTER

    body = ""
    body_path = expert_dir(expert_id) / "EXPERT.md"
    if body_path.is_file():
        matched = FRONTMATTER.match(body_path.read_text("utf-8"))
        if matched:
            body = matched.group(2)
    return {**e, "body": body.strip()}


async def expert_create(agent, req: dict) -> dict:
    expert_id = str(req.get("id", "")).strip()
    if not expert_id:
        raise HTTPException(400, "id 不能为空")
    try:
        exists = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if exists:
        raise HTTPException(409, "已存在同名人设")
    write_expert_md(expert_dir(expert_id) / "EXPERT.md", req)
    await reload_catalog(agent)
    return {"created": expert_id}


async def expert_update(agent, expert_id: str, req: dict) -> dict:
    try:
        e = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not e:
        raise HTTPException(404, "没有这个人设")
    merged = {**e, **{k: v for k, v in req.items() if k in (
        "name", "description", "scenarios", "tags", "personality",
        "speaking_style", "use_cases", "self_intro", "body")}}
    write_expert_md(expert_dir(expert_id) / "EXPERT.md", merged)
    await reload_catalog(agent)
    return {"updated": expert_id}


async def expert_delete(agent, config, expert_id: str) -> dict:
    try:
        e = await agent.call_async("catalog.expert", expert_id=expert_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not e:
        raise HTTPException(404, "没有这个人设")
    shutil.rmtree(expert_dir(expert_id), ignore_errors=True)
    await reload_catalog(agent)
    if config.data.get("expert") == expert_id:
        config.data["expert"] = ""
        config.save()
    return {"deleted": expert_id}


# ── 子 Agent ──────────────────────────────────────────────────────────

async def agent_detail(agent, agent_id: str) -> dict:
    try:
        a = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not a:
        raise HTTPException(404, "没有这个子 Agent")
    # 内核只回头部，正文仍从文件读
    from ..frontmatter import FRONTMATTER

    body = ""
    body_path = agent_dir(agent_id) / "AGENT.md"
    if body_path.is_file():
        matched = FRONTMATTER.match(body_path.read_text("utf-8"))
        if matched:
            body = matched.group(2)
    return {**a, "body": body.strip()}


async def agent_create(agent, req: dict) -> dict:
    agent_id = str(req.get("id", "")).strip()
    if not agent_id:
        raise HTTPException(400, "id 不能为空")
    try:
        exists = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if exists:
        raise HTTPException(409, "已存在同名 Agent")
    tools = req.get("tools") or []
    # 工具白名单是硬约束，写盘前先校验，不让错的配置进目录
    unknown = [t for t in tools if t not in await known_tools(agent)]
    if unknown:
        raise HTTPException(400, f"这些工具不存在：{'、'.join(unknown)}")
    write_agent_md(agent_dir(agent_id) / "AGENT.md", req)
    await reload_catalog(agent)
    return {"created": agent_id}


async def agent_update(agent, agent_id: str, req: dict) -> dict:
    try:
        a = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not a:
        raise HTTPException(404, "没有这个子 Agent")
    if req.get("tools") is not None:
        unknown = [t for t in req["tools"] if t not in await known_tools(agent)]
        if unknown:
            raise HTTPException(400, f"这些工具不存在：{'、'.join(unknown)}")
    merged = {**a, **{k: v for k, v in req.items() if k in (
        "name", "description", "tools", "max_turns", "body")}}
    write_agent_md(agent_dir(agent_id) / "AGENT.md", merged)
    await reload_catalog(agent)
    return {"updated": agent_id}


async def agent_delete(agent, agent_id: str) -> dict:
    try:
        a = await agent.call_async("catalog.agent", agent_id=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not a:
        raise HTTPException(404, "没有这个子 Agent")
    shutil.rmtree(agent_dir(agent_id), ignore_errors=True)
    await reload_catalog(agent)
    return {"deleted": agent_id}


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


def register(app, config, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/persona")
    async def _persona():
        return json(await current_persona(agent, config))

    @router.post("/expert")
    async def _set_expert(req: ExpertRequest):
        return json(await set_expert(agent, config, req.expert))

    @router.get("/experts/{expert_id}")
    async def _expert_detail(expert_id: str):
        return json(await expert_detail(agent, expert_id))

    @router.post("/experts")
    async def _expert_create(req: dict):
        return json(await expert_create(agent, req))

    @router.put("/experts/{expert_id}")
    async def _expert_update(expert_id: str, req: dict):
        return json(await expert_update(agent, expert_id, req))

    @router.delete("/experts/{expert_id}")
    async def _expert_delete(expert_id: str):
        return json(await expert_delete(agent, config, expert_id))

    @router.get("/agents/{agent_id}")
    async def _agent_detail(agent_id: str):
        return json(await agent_detail(agent, agent_id))

    @router.post("/agents")
    async def _agent_create(req: dict):
        return json(await agent_create(agent, req))

    @router.put("/agents/{agent_id}")
    async def _agent_update(agent_id: str, req: dict):
        return json(await agent_update(agent, agent_id, req))

    @router.delete("/agents/{agent_id}")
    async def _agent_delete(agent_id: str):
        return json(await agent_delete(agent, agent_id))

    app.include_router(router)
