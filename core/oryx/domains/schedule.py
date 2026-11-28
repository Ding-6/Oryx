"""自动任务和终端接口。自动任务返回统一响应，并按账号隔离。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..agentlink import AgentLink
from ..api.response import ok
from ..automation import parse_cron
from ..ipc import IpcError
from ._common import map_ipc_error

router = APIRouter(tags=["自动任务"])


class AutomationRequest(BaseModel):
    name: str
    cron: str
    goal: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 80:
            raise ValueError("任务名称需为 1–80 个字符")
        return value

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        value = value.strip()
        parse_cron(value)
        return value

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 8000:
            raise ValueError("执行目标需为 1–8000 个字符")
        return value


class AutomationUpdateRequest(BaseModel):
    name: str = ""
    cron: str = ""
    goal: str = ""

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        value = value.strip()
        if value:
            parse_cron(value)
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if value and len(value) > 80:
            raise ValueError("任务名称不能超过 80 个字符")
        return value

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        value = value.strip()
        if value and len(value) > 8000:
            raise ValueError("执行目标不能超过 8000 个字符")
        return value


class AutomationToggleRequest(BaseModel):
    enabled: bool


class TerminalRunRequest(BaseModel):
    """终端运行请求 —— 以前是裸 dict 请求体，字段零校验。

    body 传成数组 / 字符串时，原来的 `req.get` 会抛 AttributeError → 500；
    现在契约层直接 422。`command` 空串仍由业务层给中文 400，
    不改成 422 —— 老前端的错误提示路径要保持不变。
    """
    command: str = ""
    cwd: str = ""


class TerminalInterruptRequest(BaseModel):
    id: str = ""


def list_automations(agent: AgentLink, owner_id: str | None = None) -> dict:
    params = {"owner_id": owner_id} if owner_id is not None else {}
    return {"automations": _call(agent, "automation.list", **params)}


def create_automation(
    agent: AgentLink, req: AutomationRequest, owner_id: str | None = None
) -> dict:
    params = {"name": req.name, "cron": req.cron, "goal": req.goal}
    if owner_id is not None:
        params["owner_id"] = owner_id
    return _call(agent, "automation.add", **params)


def update_automation(
    agent: AgentLink, item_id: str, req: AutomationUpdateRequest, owner_id: str | None = None
) -> dict:
    params = {"id": item_id, "name": req.name, "cron": req.cron, "goal": req.goal}
    if owner_id is not None:
        params["owner_id"] = owner_id
    item = _call(agent, "automation.update", **params)
    if not item:
        raise HTTPException(404, "没有这个任务")
    return {"updated": item}


def delete_automation(agent: AgentLink, item_id: str, owner_id: str | None = None) -> dict:
    params = {"id": item_id}
    if owner_id is not None:
        params["owner_id"] = owner_id
    removed = _call(agent, "automation.remove", **params)
    if not removed:
        raise HTTPException(404, "没有这个任务")
    return {"deleted": item_id}


def toggle_automation(
    agent: AgentLink, item_id: str, enabled: bool, owner_id: str | None = None
) -> dict:
    params = {"id": item_id, "enabled": enabled}
    if owner_id is not None:
        params["owner_id"] = owner_id
    item = _call(agent, "automation.toggle", **params)
    if not item:
        raise HTTPException(404, "没有这个任务")
    return item


def list_automation_runs(
    agent: AgentLink, item_id: str, limit: int = 50, owner_id: str | None = None
) -> dict:
    params = {"id": item_id, "limit": limit}
    if owner_id is not None:
        params["owner_id"] = owner_id
    return _call(agent, "automation.runs", **params)


def retry_automation_run(
    agent: AgentLink, run_id: str, owner_id: str | None = None
) -> dict:
    params = {"run_id": run_id}
    if owner_id is not None:
        params["owner_id"] = owner_id
    result = _call(agent, "automation.retry_run", **params)
    if not result:
        raise HTTPException(404, "没有这条失败记录，或你无权访问")
    return result


def run_terminal(agent: AgentLink, req: TerminalRunRequest) -> dict:
    command = req.command.strip()
    if not command:
        raise HTTPException(400, "command 不能为空")
    return _call(agent, "terminal.run", command=command, cwd=req.cwd)


def list_terminal(agent: AgentLink) -> dict:
    return {"sessions": _call(agent, "terminal.list")}


def interrupt_terminal(agent: AgentLink, req: TerminalInterruptRequest) -> dict:
    result = _call(agent, "terminal.interrupt", id=req.id)
    return {"ok": result, "detail": "已中断" if result else "没有这个终端会话"}


def _call(agent: AgentLink, method: str, **params):
    try:
        return agent.call(method, **params)
    except IpcError as exc:
        raise _map(exc) from None


def _map(exc: IpcError) -> HTTPException:
    """状态码规则统一走 `_common.map_ipc_error`，本域不再自己维护一份。"""
    return map_ipc_error(exc)


def register(app, agent: AgentLink) -> None:
    @router.get("/api/automations")
    async def _auto_list(request: Request):
        return ok(list_automations(agent, getattr(request.state, "user_id", None)))

    @router.post("/api/automations")
    async def _auto_add(req: AutomationRequest, request: Request):
        return ok(create_automation(agent, req, getattr(request.state, "user_id", None)))

    @router.put("/api/automations/{item_id}")
    async def _auto_update(item_id: str, req: AutomationUpdateRequest, request: Request):
        return ok(update_automation(agent, item_id, req, getattr(request.state, "user_id", None)))

    @router.delete("/api/automations/{item_id}")
    async def _auto_del(item_id: str, request: Request):
        return ok(delete_automation(agent, item_id, getattr(request.state, "user_id", None)))

    @router.post("/api/automations/{item_id}/toggle")
    async def _auto_toggle(item_id: str, req: AutomationToggleRequest, request: Request):
        return ok(toggle_automation(agent, item_id, req.enabled, getattr(request.state, "user_id", None)))

    @router.get("/api/automations/{item_id}/runs")
    async def _auto_runs(item_id: str, request: Request, limit: int = 50):
        return ok(list_automation_runs(
            agent, item_id, max(1, min(limit, 200)), getattr(request.state, "user_id", None)
        ))

    @router.post("/api/automation-runs/{run_id}/retry")
    async def _auto_retry(run_id: str, request: Request):
        return ok(retry_automation_run(agent, run_id, getattr(request.state, "user_id", None)))

    @router.post("/api/terminal/run")
    async def _term_run(req: TerminalRunRequest):
        return ok(run_terminal(agent, req))

    @router.get("/api/terminal")
    async def _term_list():
        return ok(list_terminal(agent))

    @router.post("/api/terminal/interrupt")
    async def _term_kill(req: TerminalInterruptRequest):
        return ok(interrupt_terminal(agent, req))

    app.include_router(router)
