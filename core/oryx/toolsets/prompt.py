"""提示词查看与优化。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class PromptToolsMixin:
    def _register_prompt_tools(self) -> None:
        from ..promptopt import optimize

        def _optimize(
            text: str, skills: str = "", expert: str = "", style: str = ""
        ) -> dict[str, Any]:
            chosen = [s.strip() for s in skills.split(",") if s.strip()]
            result = optimize(
                text,
                skills=self.skills.skills,
                bound=chosen,
                expert=expert or self.config.data.get("expert", ""),
                style=style or self.config.data.get("style", ""),
                workspace=self.config.data.get("workspace") or "",
            )
            return {"ok": result["ok"], "content": result["content"]}

        self.registry["prompt_optimize"] = ToolSpec(
            name="prompt_optimize",
            description=(
                "优化用户的请求：补上动作、范围、验收标准，并可绑定技能。"
                "拿不准用户到底想要什么时，先优化再执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "用户原始输入"},
                    "skills": {"type": "string", "description": "要绑定的技能名，逗号分隔"},
                    "expert": {"type": "string", "description": "指定的专家角色"},
                    "style": {"type": "string", "description": "指定的回答风格"},
                },
                "required": ["text"],
            },
            risk=Risk.L0_READ,
            fn=_optimize,
        )
