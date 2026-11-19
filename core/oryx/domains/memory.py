"""长期记忆域：记、查、忘。

对外三件事：
    GET    /api/memory           列出记忆
    POST   /api/memory           记一条
    DELETE /api/memory/{id}     忘掉一条
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..store import Store
from ._common import json

router = APIRouter(prefix="/api/memory", tags=["长期记忆"])


class MemoryRequest(BaseModel):
    """记忆写入的请求契约。

    **为什么要有这个契约层**
    ----------------------
    以前这里是 `req: dict` + `str(req.get("text", ""))`。
    前端表单没填时传 `{"text": null}`，而 `str(None)` 得到的是字符串
    `"None"` —— **真的被写进了库里**。实测：

        POST /api/memory {"text": null}
        → 200 {"id":"eae86c0823","text":"None","deduped":false}

        GET /api/memory
        → {"memories":[{"id":"eae86c0823","text":"None",...}]}

    `tags` 同样中招（`{"tags": null}` → 库里 tags 写成 "None"）。

    修法：把 null 在**契约层**归一成空串，
    再交给业务校验（text 为空 → 400「text 不能为空」）。
    顺带把类型也管起来 —— `{"text": 123}` 现在是 422，
    而不是静默存成字符串 "123"。
    """

    text: str | None = None
    tags: str | None = None

    @field_validator("text", "tags", mode="before")
    @classmethod
    def _null_to_empty(cls, value):
        """null → 空串。

        `mode="before"` 是关键 —— 校验器要看到**原始值**，
        否则 None 已经被 pydantic 拒掉了。
        """
        return "" if value is None else value


def list_memories(store: Store, limit: int = 50) -> dict:
    return {"memories": store.search_memories("", limit)}


def add_memory(store: Store, text: str, tags: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    # 返回值原样透出 —— `save_memory` 给的是 {"id": ..., "text": ...}，
    # 前端拿 id 去显示「刚记住的那条」。换掉会让前端拿不到 id。
    return store.save_memory(text, (tags or "").strip())


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
    async def _add(req: MemoryRequest):
        return json(add_memory(store, req.text, req.tags))

    @router.delete("/{memory_id}")
    async def _delete(memory_id: str):
        return json(delete_memory(store, memory_id))

    app.include_router(router)
