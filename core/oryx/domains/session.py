"""会话域：会话列表、详情、改名、删除、待办、用量。

这是 v2 重写的**样板域** —— 第一个按新契约改的域。

    契约层      请求体用 pydantic 模型，不再是裸 `req: dict`
    统一响应    成功 `{code, data, message}`；错误 `{code, data, message, detail}`
    前端接入    老前端（core/web）同步改，新前端（apps/web）直接用

其余 21 个域照这个路子铺开。

路由：
    GET    /api/sessions                        列表
    GET    /api/sessions/{task_id}              详情（消息 / 步骤 / 待办 / 用量）
    DELETE /api/sessions/{task_id}              删除
    PUT    /api/sessions/{task_id}              改名
    PUT    /api/sessions/{task_id}/todos/{tid}  改待办状态
    GET    /api/sessions/{task_id}/steps        执行步骤
    GET    /api/sessions/{task_id}/todos        待办
    GET    /api/sessions/{task_id}/usage        用量摘要

**data 的形状规则**（v2 全局规则，spec §1.1）
--------------------------------------------
`data` 恒为该接口的**领域对象**：

    · 领域对象本身就是一个列表 → `data` 是**数组**
      （`/api/sessions`、`/api/sessions/{id}/steps`、`/api/sessions/{id}/todos`）
    · 否则 → `data` 是**对象**
      （详情、用量摘要、大屏那三个聚合视图）

所以 `/api/sessions` 返回的是**数组**，不是 `{"sessions": [...]}` ——
多包一层没有任何信息量，前端却要为此多写一次 `.sessions`。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..api.response import ok
from ..store import Store

router = APIRouter(prefix="/api/sessions", tags=["会话"])

# 待办的三种状态。
TODO_STATUSES = ("pending", "in_progress", "done")


class RenameRequest(BaseModel):
    """改名请求。

    `title` 传空串 = 撤掉自定义名字，退回用首条提问当标题。
    传 null 也当空串 —— 前端表单清空时给的就是 null，
    以前 `str(None)` 会把字面量 "None" 存成会话名。
    """

    title: str = ""

    @field_validator("title", mode="before")
    @classmethod
    def _null_to_empty(cls, value):
        return "" if value is None else value


class TodoStatusRequest(BaseModel):
    """改待办状态。

    状态取值在**业务层**校验（400 + 中文提示），不在这里用 Literal ——
    Literal 不匹配会变成 422，错误体里只有字段路径和英文校验消息，
    用户看到的是「body.status：Input should be 'pending'」这种机器话。
    """

    status: str


# ── 纯函数（不依赖 FastAPI，便于单测）────────────────────────────────


def list_sessions(store: Store, limit: int = 50) -> list[dict]:
    """会话列表。domain object 是列表 → data 是数组。"""
    limit = max(1, min(int(limit), 200))
    return store.list_sessions(limit)


def read_session(store: Store, task_id: str) -> dict:
    """会话详情。domain object 是对象 → data 是对象。"""
    detail = store.session_detail(task_id)
    if detail is None:
        raise HTTPException(404, "没有这个会话")
    return detail


def delete_session(store: Store, task_id: str) -> dict:
    if not store.delete_session(task_id):
        raise HTTPException(404, "没有这个会话")
    return {"deleted": task_id}


def session_steps(store: Store, task_id: str) -> list[dict]:
    """某个会话的执行步骤 —— 右栏「执行」标签用。

    单独开一个接口，是为了让右栏**刷新时不必拉整个会话详情**
    （详情里带消息、审计、待办，右栏只要步骤）。

    domain object 是列表 → data 是数组。
    """
    return store.steps_for_task(task_id)


def session_todos(store: Store, task_id: str) -> list[dict]:
    """某个会话的待办 —— 右栏「任务」标签用。domain object 是列表 → 数组。"""
    return store.list_todos(task_id)


def session_usage(store: Store, task_id: str) -> dict:
    """某个会话的用量与审计摘要 —— 右栏「详情」标签用。"""
    detail = store.session_detail(task_id)
    if detail is None:
        raise HTTPException(404, "没有这个会话")
    return {
        "task_id": task_id,
        "usage": detail.get("usage") or {},
        "steps": len(detail.get("steps") or []),
        "todos": len(detail.get("todos") or []),
        "messages": len(detail.get("messages") or []),
    }


def update_todo_status(store: Store, task_id: str, todo_id: str, status: str) -> dict:
    """改待办的状态 —— 用户在右栏「任务」标签里直接勾。

    状态：`pending` / `in_progress` / `done`。
    """
    status = (status or "").strip()
    if status not in TODO_STATUSES:
        raise HTTPException(
            400, "status 只能是 pending / in_progress / done"
        )
    todo = store.update_todo(task_id, todo_id, status)
    if todo is None:
        raise HTTPException(404, "没有这个待办")
    return {"updated": todo_id, "todo": todo}


def rename_session(store: Store, task_id: str, title: str) -> dict:
    """给会话改个名字。

    名字存 tasks.title —— 会话 id 不变，消息、步骤、检查点都不受影响。
    传空串 = 撤掉自定义名字，退回用首条提问当标题。
    """
    if not store.rename_session(task_id, title):
        raise HTTPException(404, "没有这个会话")
    return {"renamed": task_id, "title": (title or "").strip()}


# ── 装配 ────────────────────────────────────────────────────────────


def register(app, store: Store) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _list(limit: int = 50):
        return ok(list_sessions(store, limit))

    @router.get("/{task_id}")
    async def _detail(task_id: str):
        return ok(read_session(store, task_id))

    @router.delete("/{task_id}")
    async def _delete(task_id: str):
        return ok(delete_session(store, task_id))

    @router.put("/{task_id}")
    async def _rename(task_id: str, req: RenameRequest):
        return ok(rename_session(store, task_id, req.title))

    @router.put("/{task_id}/todos/{todo_id}")
    async def _update_todo(task_id: str, todo_id: str, req: TodoStatusRequest):
        return ok(update_todo_status(store, task_id, todo_id, req.status))

    @router.get("/{task_id}/steps")
    async def _steps(task_id: str):
        return ok(session_steps(store, task_id))

    @router.get("/{task_id}/todos")
    async def _todos(task_id: str):
        return ok(session_todos(store, task_id))

    @router.get("/{task_id}/usage")
    async def _usage(task_id: str):
        return ok(session_usage(store, task_id))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
