"""长期记忆域：记、查、忘。

对外三件事：
    GET    /api/memory           列出记忆
    POST   /api/memory           记一条
    DELETE /api/memory/{id}     忘掉一条
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..store import Store
from ._common import json

router = APIRouter(prefix="/api/memory", tags=["长期记忆"])


def list_memories(store: Store, limit: int = 50) -> dict:
    return {"memories": store.search_memories("", limit)}


def add_memory(store: Store, text: str, tags: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    # 返回值原样透出 —— `save_memory` 给的是 {"id": ..., "text": ...}，
    # 前端拿 id 去显示「刚记住的那条」。换掉会让前端拿不到 id。
    return store.save_memory(text, tags)


def delete_memory(store: Store, memory_id: str) -> dict:
    if not store.forget_memory(memory_id):
        raise HTTPException(404, "没有这条记忆")
    return {"deleted": memory_id}


def register(app, store: Store) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _list(limit: int = 50):
        return json(list_memories(store, limit))

    @router.post("")
    async def _add(req: dict):
        return json(add_memory(
            store, str(req.get("text", "")), str(req.get("tags", ""))))

    @router.delete("/{memory_id}")
    async def _delete(memory_id: str):
        return json(delete_memory(store, memory_id))

    app.include_router(router)
