"""延迟执行的工具 —— 大结果先落库、回头再取。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..providers import ProviderError, ToolCall, build_provider
from ..tools import ToolSpec, build_registry, tool_result_text
import json


class DeferToolsMixin:
    def _register_defer_tools(self) -> None:
        """两段式工具加载的第二段：defer_execute。

        对齐 WorkBuddy 的 DeferExecuteTool —— 延迟的工具不进模型上下文，
        模型用 tool_search 找到工具名后，通过这个统一入口调用。
        延迟只影响"看得见"，不影响"能调用"：风险分级、权限闸门照常走。
        """
        def _execute(tool: str, arguments: str = "{}") -> dict[str, Any]:
            name = (tool or "").strip()
            if name not in self.registry:
                available = "、".join(sorted(self.registry)[:20])
                return {
                    "ok": False,
                    "content": f"没有这个工具：{name}。用 tool_search 查一下。可用示例：{available}",
                }
            try:
                args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else {}
            except json.JSONDecodeError as exc:
                return {"ok": False, "content": f"arguments 不是合法 JSON：{exc}"}
            if not isinstance(args, dict):
                return {"ok": False, "content": "arguments 必须是 JSON 对象"}

            # 找到即展开 —— 后续轮次它就直接在上下文里了
            self.defer.materialize(name)
            spec = self.registry.get(name)
            if spec is not None:
                # 同时放进活动注册表，_handle_tool_call 才认得它
                self._active_registry[name] = spec
            self.bus.publish("tool.materialized", tool=name)

            # 走完整的工具调用链路：风险分级、权限闸门、hook、审计一样不少。
            # 延迟只影响"模型看得见"，不影响"这些工具受不受管"。
            self._seq += 1
            call = ToolCall(id=f"defer_{self._seq}", name=name, arguments=args)
            result = self._handle_tool_call(self._active_task_id or "", self._seq, call)
            return {"ok": not result.get("is_error"), "content": result.get("content")}

        self.registry["defer_execute"] = ToolSpec(
            name="defer_execute",
            description=(
                "调用一个延迟加载的工具。上下文里只放常用工具，"
                "其余工具先用 tool_search 按名字或用途查出来，再用这里调用。"
                "arguments 是 JSON 字符串，键名就是该工具的参数名。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "工具名，来自 tool_search 的结果"},
                    "arguments": {
                        "type": "string",
                        "description": "参数字典的 JSON 字符串，如 {\"path\":\"a.txt\"}",
                    },
                },
                "required": ["tool"],
            },
            risk=Risk.L1_WRITE,
            fn=_execute,
        )
