"""工具检索域：语义索引状态与按需取 schema。

对外两件事：
    GET  /api/tools/index   语义索引状态：是否就绪、索引了多少个、降级原因
    POST /api/tools/search  工具检索 —— 对应 WorkBuddy 的 ToolSearch，
                            让模型按需拿 schema（几百个工具全塞进上下文会炸）
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api/tools", tags=["工具检索"])


class ToolSearchRequest(BaseModel):
    query: str = ""
    limit: int = 12


async def index_status(agent) -> dict:
    """语义索引状态。"""
    try:
        return await agent.call_async("catalog.tool_index", timeout=120.0)
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def search(agent, query: str, limit: int = 12) -> dict:
    """工具检索。"""
    try:
        payload = await agent.call_async(
            "catalog.tools_search", query=query, limit=int(limit)
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"query": query, **payload}


def register(app, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/index")
    async def _index():
        return json(await index_status(agent))

    @router.post("/search")
    async def _search(req: ToolSearchRequest):
        return json(await search(agent, req.query, req.limit))

    app.include_router(router)
