"""自动任务域：定时任务的增删查与终端命令。

对齐 Marvis 的 modules/schedule/actions/{create,read,update,delete,exec}
与 modules/terminal。

调度器与终端都在 Agent 内核进程里（它们持有子进程句柄和线程），
所以这一域只做参数校验 + RPC 转发，真正的执行路径仍走内核已注册的工具，
风险评级与审计都留在工具层。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agentlink import AgentLink
from ..ipc import IpcError
from ._common import is_kernel_unready

router = APIRouter(tags=["自动任务"])


class AutomationRequest(BaseModel):
    name: str
    cron: str
    goal: str


class AutomationUpdateRequest(BaseModel):
    name: str = ""
    cron: str = ""
    goal: str = ""


# ---------- actions ----------
def list_automations(agent: AgentLink) -> dict:
    return {"automations": _call(agent, "automation.list")}


def create_automation(agent: AgentLink, req: AutomationRequest) -> dict:
    return _call(agent, "automation.add", name=req.name, cron=req.cron, goal=req.goal)


def update_automation(agent: AgentLink, item_id: str, req: AutomationUpdateRequest) -> dict:
    """改定时任务。cron 语法错会从内核抛回来，转成 400。"""
    try:
        item = _call(
            agent, "automation.update", id=item_id,
            name=req.name, cron=req.cron, goal=req.goal,
        )
    except IpcError as exc:
        raise _map(exc) from None
    if not item:
        raise HTTPException(404, "没有这个任务")
    return {"updated": item}


def delete_automation(agent: AgentLink, item_id: str) -> dict:
    removed = _call(agent, "automation.remove", id=item_id)
    if not removed:
        raise HTTPException(404, "没有这个任务")
    return {"deleted": item_id}


def run_terminal(agent: AgentLink, req: dict) -> dict:
    command = str(req.get("command", "")).strip()
    if not command:
        raise HTTPException(400, "command 不能为空")
    # 输出靠 term.event 实时推送，这里只回报会话信息
    return _call(agent, "terminal.run", command=command, cwd=str(req.get("cwd", "")))


def list_terminal(agent: AgentLink) -> dict:
    return {"sessions": _call(agent, "terminal.list")}


def interrupt_terminal(agent: AgentLink, req: dict) -> dict:
    ok = _call(agent, "terminal.interrupt", id=str(req.get("id", "")))
    return {"ok": ok, "detail": "已中断" if ok else "没有这个终端会话"}


def _call(agent: AgentLink, method: str, **params):
    try:
        return agent.call(method, **params)
    except IpcError as exc:
        raise _map(exc) from None


def _map(exc: IpcError) -> HTTPException:
    text = str(exc)
    # 「内核未就绪」是 503（服务端依赖没就绪），规则与 _common 一致。
    if is_kernel_unready(text):
        return HTTPException(503, text)
    if "没有这个" in text:
        return HTTPException(404, text)
    return HTTPException(400, text)


# ---------- routes ----------
def register(app, agent: AgentLink) -> None:
    from fastapi.responses import JSONResponse

    @router.get("/api/automations")
    async def _auto_list():
        return JSONResponse(list_automations(agent))

    @router.post("/api/automations")
    async def _auto_add(req: AutomationRequest):
        return JSONResponse(create_automation(agent, req))

    @router.put("/api/automations/{item_id}")
    async def _auto_update(item_id: str, req: AutomationUpdateRequest):
        return JSONResponse(update_automation(agent, item_id, req))

    @router.delete("/api/automations/{item_id}")
    async def _auto_del(item_id: str):
        return JSONResponse(delete_automation(agent, item_id))

    @router.post("/api/terminal/run")
    async def _term_run(req: dict):
        return JSONResponse(run_terminal(agent, req))

    @router.get("/api/terminal")
    async def _term_list():
        return JSONResponse(list_terminal(agent))

    @router.post("/api/terminal/interrupt")
    async def _term_kill(req: dict):
        return JSONResponse(interrupt_terminal(agent, req))

    app.include_router(router)