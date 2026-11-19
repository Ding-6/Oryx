"""会话域：会话列表、详情、删除。

对齐 Marvis 的 modules/message/ + modules/session：
    actions/list.py    会话列表
    actions/read.py    会话详情（含消息、步骤、待办、用量）
    actions/delete.py  删除会话（跨会话域 + 统计域）

路由前缀 /api/sessions，由 domains/__init__.py 统一装配。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..store import Store

router = APIRouter(prefix="/api/sessions", tags=["会话"])


def list_sessions(store: Store, limit: int = 50) -> dict:
    return {"sessions": store.list_sessions(limit)}


def read_session(store: Store, task_id: str) -> dict:
    detail = store.session_detail(task_id)
    if detail is None:
        raise HTTPException(404, "没有这个会话")
    return detail


def delete_session(store: Store, task_id: str) -> dict:
    if not store.delete_session(task_id):
        raise HTTPException(404, "没有这个会话")
    return {"deleted": task_id}


def session_steps(store: Store, task_id: str) -> dict:
    """某个会话的执行步骤 —— 右栏「执行」标签用。

    单独开一个接口，是为了让右栏**刷新时不必拉整个会话详情**
    （详情里带消息、审计、待办，右栏只要步骤）。
    """
    return {"task_id": task_id, "steps": store.steps_for_task(task_id)}


def session_todos(store: Store, task_id: str) -> dict:
    """某个会话的待办 —— 右栏「任务」标签用。"""
    todos = store.list_todos(task_id)
    return {"task_id": task_id, "todos": todos, "count": len(todos)}


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
    if status not in ("pending", "in_progress", "done"):
        raise HTTPException(400, "status 只能是 pending / in_progress / done")
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


def register(app, store: Store) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _list(limit: int = 50):
        return _json(list_sessions(store, limit))

    @router.get("/{task_id}")
    async def _detail(task_id: str):
        return _json(read_session(store, task_id))

    @router.delete("/{task_id}")
    async def _delete(task_id: str):
        return _json(delete_session(store, task_id))

    @router.put("/{task_id}")
    async def _rename(task_id: str, req: dict):
        return _json(rename_session(store, task_id, str((req or {}).get("title") or "")))

    @router.put("/{task_id}/todos/{todo_id}")
    async def _update_todo(task_id: str, todo_id: str, req: dict):
        return _json(update_todo_status(
            store, task_id, todo_id, str((req or {}).get("status") or "")))

    @router.get("/{task_id}/steps")
    async def _steps(task_id: str):
        return _json(session_steps(store, task_id))

    @router.get("/{task_id}/todos")
    async def _todos(task_id: str):
        return _json(session_todos(store, task_id))

    @router.get("/{task_id}/usage")
    async def _usage(task_id: str):
        return _json(session_usage(store, task_id))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)


def _json(payload: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(payload)