"""长期记忆：记、查、改、忘。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class MemoryToolsMixin:
    def _register_memory_tools(self) -> None:
        def _save(text: str, tags: str = "") -> dict[str, Any]:
            owner_id = getattr(self, "_active_owner_id", None)
            saved = self.store.save_memory(text, tags, owner_id)
            self.bus.publish("memory.updated", memory=saved, _owner_id=owner_id)
            return {"ok": True, "content": saved}

        def _search(query: str = "", limit: int = 20) -> dict[str, Any]:
            owner_id = getattr(self, "_active_owner_id", None)
            return {
                "ok": True,
                "content": self.store.search_memories(query, int(limit), owner_id),
            }

        def _forget(id: str) -> dict[str, Any]:
            owner_id = getattr(self, "_active_owner_id", None)
            removed = self.store.forget_memory(id, owner_id)
            if removed:
                self.bus.publish(
                    "memory.updated", memory={"id": id, "forgotten": True},
                    _owner_id=owner_id,
                )
            return {"ok": removed, "content": "已删除" if removed else f"没有这条记忆：{id}"}

        self.registry["memory_save"] = ToolSpec(
            name="memory_save",
            description=(
                "记住一条跨会话的事实：用户偏好、项目约定、踩过的坑。"
                "只在信息会长期有用时才记，不要记一次性的对话内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "一句话事实，自包含"},
                    "tags": {"type": "string", "description": "可选标签，逗号分隔"},
                },
                "required": ["text"],
            },
            risk=Risk.L1_WRITE,
            fn=_save,
        )
        self.registry["memory_search"] = ToolSpec(
            name="memory_search",
            description="检索长期记忆。回答涉及用户偏好或历史约定时先查一下。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词，留空则列出最近的"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_search,
        )
        self.registry["memory_forget"] = ToolSpec(
            name="memory_forget",
            description="删除一条不再准确的记忆。",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            risk=Risk.L1_WRITE,
            fn=_forget,
        )
