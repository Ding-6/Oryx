"""跨端远控域：启动/停止远控、看状态。

对外三件事：
    POST /api/remote/start   起远控会话（手机接续 / 远程看屏）
    POST /api/remote/stop    停掉
    GET  /api/remote/status  当前状态

这三个都是**转发到内核**的 RPC —— 控制面不碰实现细节。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api/remote", tags=["跨端远控"])


class RemoteStartRequest(BaseModel):
    relay_url: str = ""
    code: str = ""
    policy: int = 0  # 1=只读 2=可控 3=完整；0=用配置默认


async def start(agent, req: RemoteStartRequest) -> dict:
    try:
        result = await agent.call_async(
            "remote.start",
            relay_url=req.relay_url,
            code=req.code,
            policy=int(req.policy),
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise HTTPException(400, result.get("content", "启动失败"))
    return result["content"]


async def stop(agent) -> dict:
    try:
        result = await agent.call_async("remote.stop")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": bool(result.get("ok")), "detail": result.get("content")}


async def status(agent) -> dict:
    try:
        content = await agent.call_async("remote.status")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return content


def register(app, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.post("/start")
    async def _start(req: RemoteStartRequest):
        return json(await start(agent, req))

    @router.post("/stop")
    async def _stop():
        return json(await stop(agent))

    @router.get("/status")
    async def _status():
        return json(await status(agent))

    app.include_router(router)
