"""共享团队定义与账号私有的团队对话。"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..api.response import ok
from ..events import EventBus
from ..ipc import IpcError
from ._common import rpc_failure

router = APIRouter(prefix="/api/teams", tags=["团队"])


class DispatchJobRegistry:
    """后台派发作业表：提交即返回 job_id，按账号隔离查询。

    内核的 `team.dispatch` 会等子 Agent 整轮跑完（几分钟）。控制面原来把
    它做成同步 `def` 端点，HTTP 线程被占满，前端只能干转圈。这里改成
    「提交 → 立刻回执 → 后台线程跑 IPC → 前端轮询状态」。

    为什么是线程不是 `asyncio.create_task`：挂在当前请求事件循环上的任务，
    请求一结束就没有可依赖的生命周期（有些 ASGI 宿主会直接回收 loop）。
    IPC 客户端本身按请求 id 支持并发，内核侧也是每请求一线程，
    后台线程里跑同步 `agent.call` 不会挡住其它接口。

    作业表住在控制面内存里：服务重启会丢，但派发结束后内核会把结论
    `post` 回团队信箱（那是持久化的），所以刷新页面仍看得到结果。
    `_inflight` 还顺带做了防重复：同一个 (团队, 成员, 账号) 已有在途作业时
    再点一次，直接回原作业，不会真的派两遍活。
    """

    MAX_JOBS = 200

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._inflight: dict[tuple[str, str, str | None], str] = {}
        self._lock = threading.Lock()

    def snapshot(self, job_id: str, owner_id: str | None) -> dict[str, Any] | None:
        """取快照；不是本人创建的作业一律当不存在。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["owner_id"] != owner_id:
                return None
            return dict(job)

    def submit(self, agent, team: str, agent_id: str, owner_id: str | None):
        """登记一个作业并起后台线程。返回 (快照, 是否新创建)。"""
        key = (team, agent_id, owner_id)
        with self._lock:
            existing = self._inflight.get(key)
            if existing:
                job = self._jobs.get(existing)
                if job and job["status"] == "running":
                    return dict(job), False
            job_id = uuid.uuid4().hex[:16]
            job: dict[str, Any] = {
                "job_id": job_id,
                "team": team,
                "agent": agent_id,
                "owner_id": owner_id,
                "status": "running",
                "content": "",
                "error": "",
                "started_at": time.time(),
                "finished_at": None,
            }
            self._jobs[job_id] = job
            self._inflight[key] = job_id
            if len(self._jobs) > self.MAX_JOBS:
                ordered = sorted(self._jobs, key=lambda k: self._jobs[k]["started_at"])
                for stale in ordered[: len(self._jobs) - self.MAX_JOBS]:
                    self._jobs.pop(stale, None)
        thread = threading.Thread(
            target=self._run,
            args=(agent, job_id, team, agent_id, owner_id),
            name=f"oryx-dispatch-{job_id}",
            daemon=True,
        )
        self._threads[job_id] = thread
        thread.start()
        return dict(job), True

    def _finish(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(changes)
                job["finished_at"] = time.time()
                key = (job["team"], job["agent"], job["owner_id"])
                if self._inflight.get(key) == job_id:
                    self._inflight.pop(key, None)
        self._threads.pop(job_id, None)

    def _run(self, agent, job_id: str, team: str, agent_id: str,
             owner_id: str | None) -> None:
        try:
            result = agent.call(
                "team.dispatch", team=team, agent=agent_id, owner_id=owner_id
            )
            if not result.get("ok"):
                self._finish(job_id, status="failed",
                             error=str(result.get("content") or "派发失败"))
            else:
                self._finish(job_id, status="done",
                             content=str(result.get("content") or ""))
        except Exception as exc:  # noqa: BLE001 — 后台线程不能把异常抛出去
            self._finish(job_id, status="failed", error=str(exc))


def team_error(result: dict[str, Any]) -> HTTPException:
    """把内核的 `ok=False` 精确映射成 HTTP 状态码。

    以前只靠「error 里有没有『创建者』」二分，结果「至少要留一个成员」
    被压成 404、「已经有同名团队」被压成 400。内核现在带 `reason`，
    老内核没带时再按文案兜底，不靠猜一个关键词就定生死。
    """
    message = str(result.get("error") or result.get("content") or "操作失败")
    by_reason = {"not_found": 404, "forbidden": 403, "conflict": 409, "invalid": 400}
    status = by_reason.get(str(result.get("reason") or ""))
    if status is None:
        if "没有叫" in message or "不在团队里" in message or "没有这个团队" in message:
            status = 404
        elif "创建者" in message:
            status = 403
        elif "已经有" in message:
            status = 409
        else:
            status = 400
    return HTTPException(status, message)


class TeamCreateRequest(BaseModel):
    name: str
    goal: str = ""
    members: list[Any] = Field(min_length=1, max_length=8)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 80 or "/" in value or "\\" in value:
            raise ValueError("团队名称需为 1–80 个字符，且不能包含斜杠")
        return value

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        if len(value) > 4000:
            raise ValueError("团队目标不能超过 4000 个字符")
        return value.strip()


class TeamMemberInput(BaseModel):
    agent_id: str
    role: str = ""

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("成员 agent_id 不能为空或超过 100 个字符")
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 80:
            raise ValueError("成员职责不能超过 80 个字符")
        return value


class TeamUpdateRequest(BaseModel):
    name: str = ""
    goal: str | None = None
    member: str = ""
    role: str | None = None
    # 给了就是「整体保存」：名字 / 目标 / 成员名单一次替换。
    # 不给则保持旧的「改单个成员分工」语义，老调用方不受影响。
    members: list[TeamMemberInput] | None = None

    @field_validator("members")
    @classmethod
    def validate_members(cls, value: list[TeamMemberInput] | None):
        if value is not None and not (1 <= len(value) <= 8):
            raise ValueError("成员需为 1–8 位")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if value and (len(value) > 80 or "/" in value or "\\" in value):
            raise ValueError("团队名称不能超过 80 个字符，也不能包含斜杠")
        return value

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4000:
            raise ValueError("团队目标不能超过 4000 个字符")
        return value.strip() if value is not None else None

    @field_validator("member")
    @classmethod
    def validate_member(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 100:
            raise ValueError("agent 不能超过 100 个字符")
        return value

    @field_validator("role")
    @classmethod
    def validate_update_role(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 80:
            raise ValueError("成员职责不能超过 80 个字符")
        return value.strip() if value is not None else None


class TeamMemberRequest(BaseModel):
    agent: str
    role: str = ""

    @field_validator("agent")
    @classmethod
    def validate_agent(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("agent 不能为空或超过 100 个字符")
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if len(value) > 80:
            raise ValueError("成员职责不能超过 80 个字符")
        return value.strip()


class TeamMessageRequest(BaseModel):
    to: str
    content: str

    @field_validator("to")
    @classmethod
    def validate_to(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("消息接收成员不能为空或超过 100 个字符")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 4000:
            raise ValueError("消息内容需为 1–4000 个字符")
        return value


class TeamDispatchRequest(BaseModel):
    agent: str

    @field_validator("agent")
    @classmethod
    def validate_agent(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("agent 不能为空")
        return value


def _actor(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


async def list_teams(agent, owner_id: str | None) -> dict:
    try:
        teams = await agent.call_async("team.list", owner_id=owner_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"teams": teams}


async def get_team(agent, name: str, owner_id: str | None) -> dict:
    try:
        team = await agent.call_async("team.get", name=name, owner_id=owner_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not team:
        raise HTTPException(404, "没有这个团队")
    return team


async def create_team(agent, req: TeamCreateRequest, owner_id: str | None) -> dict:
    try:
        result = await agent.call_async(
            "team.create", name=req.name, members=req.members, goal=req.goal, owner_id=owner_id,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return result["team"]


async def delete_team(agent, bus: EventBus, name: str, owner_id: str | None) -> dict:
    try:
        deleted = await agent.call_async("team.delete", name=name, owner_id=owner_id)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not deleted:
        try:
            team = await agent.call_async("team.get", name=name, owner_id=owner_id)
        except IpcError as exc:
            raise rpc_failure(exc) from None
        if not team:
            raise HTTPException(404, "没有这个团队")
        raise HTTPException(403, "只有创建者可以删除这个共享团队")
    bus.publish("team.deleted", name=name)
    return {"deleted": name}


async def add_member(agent, name: str, req: TeamMemberRequest, owner_id: str | None) -> dict:
    try:
        result = await agent.call_async(
            "team.add_member", team=name, agent=req.agent, role=req.role, owner_id=owner_id,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": True, "content": result.get("message")}


async def remove_member(agent, name: str, agent_id: str, owner_id: str | None) -> dict:
    try:
        result = await agent.call_async(
            "team.remove_member", team=name, agent=agent_id, owner_id=owner_id,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise team_error(result)
    return {"ok": True, "removed": agent_id}


async def update_team(agent, name: str, req: TeamUpdateRequest, owner_id: str | None) -> dict:
    """两种语义共用这个 PUT：

    - 带 `members` → 整体保存，走 `team.save` 一次 IPC（先全校验再一次落地）；
    - 不带 `members` → 保持旧的「改名 / 改目标 / 改某一个成员分工」。
    """
    try:
        if req.members is not None:
            result = await agent.call_async(
                "team.save", team=name, new_name=req.name, goal=req.goal,
                members=[m.model_dump() for m in req.members], owner_id=owner_id,
            )
        else:
            result = await agent.call_async(
                "team.update", team=name, new_name=req.name, goal=req.goal,
                member=req.member, role=req.role, owner_id=owner_id,
            )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise team_error(result)
    return {"ok": True, "team": result.get("team")}


async def send_message(agent, name: str, req: TeamMessageRequest, owner_id: str | None) -> dict:
    try:
        result = await agent.call_async(
            "team.send_message", team=name, to=req.to, content=req.content, owner_id=owner_id,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": True, "content": result.get("message")}


def dispatch(agent, name: str, req: TeamDispatchRequest, owner_id: str | None) -> dict:
    """同步派发 —— 保留给老前端 `core/web/modules/models.js`。

    它会占住一条 HTTP 线程直到子 Agent 跑完。新前端请用下面的
    `POST /{name}/dispatch/jobs`（提交即返回 + 轮询），不要把老路由改语义。
    """
    try:
        result = agent.call(
            "team.dispatch", team=name, agent=req.agent, owner_id=owner_id,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"ok": True, "content": result.get("content")}


def register(app, agent, bus: EventBus) -> None:
    jobs = DispatchJobRegistry()
    @router.get("")
    async def _list(request: Request):
        return ok(await list_teams(agent, _actor(request)))

    @router.get("/{name}")
    async def _detail(name: str, request: Request):
        return ok(await get_team(agent, name, _actor(request)))

    @router.post("")
    async def _create(req: TeamCreateRequest, request: Request):
        return ok(await create_team(agent, req, _actor(request)))

    @router.delete("/{name}")
    async def _delete(name: str, request: Request):
        return ok(await delete_team(agent, bus, name, _actor(request)))

    @router.put("/{name}")
    async def _update(name: str, req: TeamUpdateRequest, request: Request):
        return ok(await update_team(agent, name, req, _actor(request)))

    @router.post("/{name}/members")
    async def _add_member(name: str, req: TeamMemberRequest, request: Request):
        return ok(await add_member(agent, name, req, _actor(request)))

    @router.delete("/{name}/members/{agent_id}")
    async def _remove_member(name: str, agent_id: str, request: Request):
        return ok(await remove_member(agent, name, agent_id, _actor(request)))

    @router.post("/{name}/message")
    async def _message(name: str, req: TeamMessageRequest, request: Request):
        return ok(await send_message(agent, name, req, _actor(request)))

    @router.post("/{name}/dispatch/jobs")
    async def _dispatch_job_start(name: str, req: TeamDispatchRequest, request: Request):
        """提交后台派发：立刻回 job_id，不等待子 Agent 跑完。"""
        job, _created = jobs.submit(agent, name, req.agent, _actor(request))
        return ok({k: v for k, v in job.items() if k != "owner_id"})

    @router.get("/{name}/dispatch/jobs/{job_id}")
    async def _dispatch_job_status(name: str, job_id: str, request: Request):
        """轮询作业状态。别人的作业、别的团队的作业一律 404。"""
        job = jobs.snapshot(job_id, _actor(request))
        if job is None or job["team"] != name:
            raise HTTPException(404, "没有这个派发作业")
        return ok({k: v for k, v in job.items() if k != "owner_id"})

    @router.post("/{name}/dispatch")
    def _dispatch(name: str, req: TeamDispatchRequest, request: Request):
        return ok(dispatch(agent, name, req, _actor(request)))

    app.include_router(router)
