"""内嵌终端：执行、中断、状态。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class TerminalToolsMixin:
    def _register_terminal_tools(self) -> None:
        from ..terminal import TerminalService, notify

        self.terminal = TerminalService(
            on_event=lambda event: self.bus.publish("term.event", **event)
        )

        def _run(command: str, cwd: str = "") -> dict[str, Any]:
            session = self.terminal.run(command, cwd)
            return {
                "ok": True,
                "content": {
                    "id": session["id"],
                    "status": "已启动，输出会实时推送到界面",
                    "command": command,
                    "cwd": session["cwd"],
                },
            }

        def _status(id: str = "") -> dict[str, Any]:
            if id:
                session = self.terminal.get(id)
                if not session:
                    return {"ok": False, "content": f"没有这个会话：{id}"}
                return {"ok": True, "content": session}
            return {"ok": True, "content": self.terminal.list()}

        def _interrupt(id: str) -> dict[str, Any]:
            killed = self.terminal.interrupt(id)
            return {"ok": killed, "content": "已中断" if killed else "该会话没在运行"}

        def _notify(title: str, message: str, method: str = "toast", webhook: str = "") -> dict[str, Any]:
            result = notify(title, message, method, webhook)
            return {"ok": result["ok"], "content": result["content"]}

        self.registry["term_run"] = ToolSpec(
            name="term_run",
            description=(
                "在终端里执行命令，输出实时推送到界面的终端面板。"
                "适合长时间任务（跑测试、装依赖），用户能看着进度。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "cwd": {"type": "string", "description": "工作目录，默认当前工作区"},
                },
                "required": ["command"],
            },
            risk=Risk.L2_DESTRUCTIVE,  # 执行命令必须确认
            fn=_run,
        )
        self.registry["term_status"] = ToolSpec(
            name="term_status",
            description="查看终端会话状态与输出。",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "会话 id，留空则列出全部"}},
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_status,
        )
        self.registry["term_interrupt"] = ToolSpec(
            name="term_interrupt",
            description="中断正在运行的终端会话。",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            risk=Risk.L1_WRITE,
            fn=_interrupt,
        )
        self.registry["notify"] = ToolSpec(
            name="notify",
            description=(
                "发通知。method 用 toast（Windows 系统通知）或 webhook（POST 到指定地址）。"
                "任务跑完需要提醒用户时用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "method": {"type": "string", "enum": ["toast", "webhook"]},
                    "webhook": {"type": "string", "description": "webhook 地址"},
                },
                "required": ["title", "message"],
            },
            risk=Risk.L1_WRITE,
            fn=_notify,
        )
