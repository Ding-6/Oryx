"""技能调用。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..skills import MAX_SKILL_CHARS
from ..tools import ToolSpec, build_registry, tool_result_text


class UseSkillToolsMixin:
    def _register_use_skill(self) -> None:
        def _use_skill(name: str) -> dict[str, Any]:
            skill = self.skills.get(name)
            if not skill:
                available = "、".join(self.skills.skills) or "无"
                return {"ok": False, "content": f"没有名为 {name} 的技能。可用：{available}"}
            # 依赖不满足的技能读出来也没用 —— 但要**说清楚为什么**，
            # 而不是让它按流程走一半然后卡住。
            if skill.blocked_reason:
                return {
                    "ok": False,
                    "content": (
                        f"技能 {name} 当前不可用：{skill.blocked_reason}。"
                        f"请先补上缺的依赖，或在设置里停用/修复对应扩展。"
                    ),
                }
            body = skill.body[:MAX_SKILL_CHARS]
            return {"ok": True, "content": body, "meta": {"skill": name}}

        self.registry["use_skill"] = ToolSpec(
            name="use_skill",
            description="读取某个技能的完整操作步骤。当任务与技能目录中的某项相关时调用。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名"}},
                "required": ["name"],
            },
            risk=Risk.L0_READ,
            fn=_use_skill,
        )
