"""团队域：多 Agent 编队。

对外九件事：
    GET    /api/teams                        当前所有团队 + 每个团队的最近消息
    GET    /api/teams/{name}                 团队详情
    POST   /api/teams                        建团队
    DELETE /api/teams/{name}                 解散团队
    PUT    /api/teams/{name}                 改名字 / 目标 / 成员分工
    POST   /api/teams/{name}/members         加成员
    DELETE /api/teams/{name}/members/{id}    移出成员
    POST   /api/teams/{name}/message         投递消息到成员信箱（不触发执行）
    POST   /api/teams/{name}/dispatch        让某个成员真正开工

团队是**纯内存态**（这一轮的临时编队），所以整套都在 Agent 内核里，
控制面只做转发。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..events import EventBus
from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api/teams", tags=["团队"])


async def list_teams(agent) -> dict:
    """当前所有团队 + 每个团队的最近消息，供右栏「团队」标签渲染。"""
    try:
        teams = await agent.call_async("team.list")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"teams": teams}


async def get_team(agent, name: str) -> dict:
    try:
        team = await agent.call_async("team.get", name=name)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not team:
        raise HTTPException(404, "没有这个团队")
    return team


async def create_team(agent, req: dict) -> dict:
    try:
        result = await agent.call_async(
            "team.create", name=str(req.get("name", "")),
            members=req.get("members") or [], goal=str(req.get("goal", "")),
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return result["team"]


async def delete_team(agent, bus: EventBus, name: str) -> dict:
    try:
        await agent.call_async("team.delete", name=name)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    bus.publish("team.deleted", name=name)
    return {"deleted": name}


async def add_member(agent, name: str, req: dict) -> dict:
    try:
        result = await agent.call_async(
            "team.add_member", team=name,
            agent=str(req.get("agent", "")), role=str(req.get("role", "")),
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": True, "content": result.get("message")}


async def remove_member(agent, name: str, agent_id: str) -> dict:
    """把一个成员移出团队。踢人之后队伍还剩几个人要看得见。"""
    try:
        result = await agent.call_async("team.remove_member", team=name, agent=agent_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise HTTPException(404, str(result.get("error") or "没有这个成员"))
    return {"ok": True, "removed": agent_id}


async def update_team(agent, name: str, req: dict) -> dict:
    """改团队的名字、目标或成员分工。

    成员分工单独改（role 只传被改的那个 agent_id），
    不传 members 就保持原样 —— 前端改一个人的分工不该把整队名单洗掉。
    """
    try:
        result = await agent.call_async(
            "team.update", team=name,
            new_name=str(req.get("name", "") or ""),
            goal=req.get("goal"),
            member=str(req.get("member", "") or ""),
            role=req.get("role"),
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise HTTPException(400, str(result.get("error") or "改不动这个团队"))
    return {"ok": True, "team": result.get("team")}


async def send_message(agent, name: str, req: dict) -> dict:
    """投递消息到成员信箱（不触发执行）。"""
    try:
        result = await agent.call_async(
            "team.send_message", team=name, to=str(req.get("to", "")),
            content=str(req.get("content", "")),
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": True, "content": result.get("message")}


def dispatch(agent, name: str, req: dict) -> dict:
    """让某个成员真正开工。

    **同步 def** —— FastAPI 会把它放进线程池，不堵事件循环。
    """
    try:
        result = agent.call("team.dispatch", team=name, agent=str(req.get("agent", "")))
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": True, "content": result.get("content")}


def register(app, agent, bus: EventBus) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _list():
        return json(await list_teams(agent))

    @router.get("/{name}")
    async def _detail(name: str):
        return json(await get_team(agent, name))

    @router.post("")
    async def _create(req: dict):
        return json(await create_team(agent, req))

    @router.delete("/{name}")
    async def _delete(name: str):
        return json(await delete_team(agent, bus, name))

    @router.put("/{name}")
    async def _update(name: str, req: dict):
        return json(await update_team(agent, name, req))

    @router.post("/{name}/members")
    async def _add_member(name: str, req: dict):
        return json(await add_member(agent, name, req))

    @router.delete("/{name}/members/{agent_id}")
    async def _remove_member(name: str, agent_id: str):
        return json(await remove_member(agent, name, agent_id))

    @router.post("/{name}/message")
    async def _message(name: str, req: dict):
        return json(await send_message(agent, name, req))

    @router.post("/{name}/dispatch")
    def _dispatch(name: str, req: dict):
        return json(dispatch(agent, name, req))

    app.include_router(router)
