"""MCP 域：列出与启停 MCP 服务器。

对外两件事：
    GET  /api/mcp           服务器清单
    POST /api/mcp/toggle   启用/停用一个（启用后立即重载，不用重启内核）

以前 MCP 在界面上**完全看不见**：配置在 `.mcp.json` 里，
但没有任何页面能列出它、启停它、看它提供了哪些工具。
用户装了 MCP server 之后只能去翻配置文件。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api/mcp", tags=["MCP"])


class McpToggleRequest(BaseModel):
    """启停一个 MCP 服务器。name 为空仍由业务层给中文 400。"""
    name: str = ""
    enabled: bool = False


async def list_servers(agent) -> dict:
    """MCP 服务器清单。"""
    try:
        return await agent.call_async("mcp.status")
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def toggle(agent, name: str, enabled: bool) -> dict:
    """启用/停用一个 MCP 服务器。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "name 不能为空")
    try:
        return await agent.call_async("mcp.set_enabled", name=name, enabled=enabled)
    except IpcError as exc:
        raise rpc_failure(exc) from None


def register(app, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _list():
        return json(await list_servers(agent))

    @router.post("/toggle")
    async def _toggle(req: McpToggleRequest):
        return json(await toggle(agent, req.name, req.enabled))

    app.include_router(router)
