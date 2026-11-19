"""项目文档：需求、决策、变更。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class DocToolsMixin:
    def _register_doc_tools(self) -> None:
        doc = self.project_doc

        def _read(limit: int = 20000) -> dict[str, Any]:
            return {"ok": True, "content": doc.read(int(limit))}

        def _require(title: str, detail: str = "") -> dict[str, Any]:
            entry_id = doc.add_requirement(title, detail)
            self.bus.publish("doc.updated", entry=entry_id, stats=doc.stats())
            return {"ok": True, "content": {"id": entry_id, "path": str(doc.path)}}

        def _decision(title: str, detail: str = "") -> dict[str, Any]:
            entry_id = doc.add_decision(title, detail)
            self.bus.publish("doc.updated", entry=entry_id, stats=doc.stats())
            return {"ok": True, "content": {"id": entry_id, "path": str(doc.path)}}

        def _change(summary: str, files: list[str] | None = None, rationale: str = "") -> dict[str, Any]:
            entry_id = doc.log_change(summary, files or [], rationale)
            self.bus.publish("doc.updated", entry=entry_id, stats=doc.stats())
            return {"ok": True, "content": {"id": entry_id, "path": str(doc.path)}}

        self.registry["doc_read"] = ToolSpec(
            name="doc_read",
            description="读取项目文档（需求/决策/变更记录都在同一个文件里）。",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "最多读取字符数"}},
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_read,
        )
        self.registry["doc_requirement_add"] = ToolSpec(
            name="doc_requirement_add",
            description="把一条需求记进项目文档，返回编号（REQ-001 形式）。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "一句话需求"},
                    "detail": {"type": "string", "description": "验收标准、边界、不做什么"},
                },
                "required": ["title"],
            },
            risk=Risk.L1_WRITE,
            fn=_require,
        )
        self.registry["doc_decision_add"] = ToolSpec(
            name="doc_decision_add",
            description="把一条技术决策记进项目文档，说明选了什么、为什么、代价。",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
                "required": ["title"],
            },
            risk=Risk.L1_WRITE,
            fn=_decision,
        )
        self.registry["doc_change_log"] = ToolSpec(
            name="doc_change_log",
            description="记录一次改动：改了什么、动了哪些文件、为什么。每次改动都要记。",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "一句话说明改了什么"},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "改动文件列表"},
                    "rationale": {"type": "string", "description": "为什么改"},
                },
                "required": ["summary"],
            },
            risk=Risk.L1_WRITE,
            fn=_change,
        )
