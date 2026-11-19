"""多 Agent 编排：草案、状态、执行。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any

from ..policy import Risk
from ..tools import ToolSpec


class WorkflowToolsMixin:
    def _register_workflow_tools(self) -> None:
        """工作流编排工具。

        与 team_* 的分工：team_* 是"领导代理逐轮决策"，编排者是主模型；
        工作流是"脚本决策"，编排者是引擎。主模型只写一次编排，
        之后每一步谁做什么由引擎决定，中间结果不回灌上下文。
        """

        def _run(plan: dict[str, Any], args: dict[str, Any] | None = None) -> dict[str, Any]:
            return self.run_workflow(plan, args or {})

        def _status(run_id: str = "") -> dict[str, Any]:
            return self.workflow_status(run_id)

        def _cancel(run_id: str = "") -> dict[str, Any]:
            return self.workflow_cancel(run_id)

        def _draft(goal: str, name: str = "") -> dict[str, Any]:
            """把一句自然语言翻成编排草案 —— 只返回草案，不执行。"""
            from ..planning import draft_workflow

            ids: list[str] = []
            try:
                for item in self.agents.catalog():
                    ids.append(str(item.get("id") if isinstance(item, dict) else item))
            except Exception:
                ids = []
            out = draft_workflow(goal, [i for i in ids if i], name)
            if out.get("ok"):
                return {"ok": True, "content": out}
            return {"ok": False, "content": str(out.get("error") or "草案生成失败")}

        # 意图 → 编排的中间层：模型不用自己从零拼 stages/steps JSON。
        # 草案会用 WorkflowPlan.parse 自检，确认后原样交给 workflow_run。
        self.registry["workflow_draft"] = ToolSpec(
            name="workflow_draft",
            description=(
                "把一句自然语言需求翻译成一份**可直接交给 workflow_run** 的编排草案。"
                "会识别并列的多个子目标并排成并行阶段，否则给出「调研→核对→交付」流水线；"
                "子 Agent 只从真实已定义的 id 里选。只生成草案、不执行，"
                "返回里的 notes 写明哪些是推断的，确认分工后再跑。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "用户的原始需求，保持原话"},
                    "name": {"type": "string", "description": "编排名称，可选"},
                },
                "required": ["goal"],
            },
            risk=Risk.L0_READ,
            embed_text="把需求拆成多 Agent 编排计划，生成工作流草案，并行任务拆解",
            fn=_draft,
        )
        self.registry["workflow_run"] = ToolSpec(
            name="workflow_run",
            description=(
                "按一份编排计划跑多个子 Agent，产出汇总报告。\n"
                "plan = {name, goal, stages: [{name, steps: [{id, name, agent, goal, needs, parallel}]}]}。\n"
                "stage 之间顺序执行；stage 内 needs 相同的 step 并行跑（并发上限 16）。\n"
                "每个 step 派一个子 Agent（agent 必须是已定义的子 Agent id）。\n"
                "中间结果只落库不回灌上下文，你只会拿到最终汇总 —— "
                "适合任务大到逐轮对话装不下的场景。\n"
                "会阻塞到编排结束（通常几分钟），期间用户看不到你的中间回复。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "object",
                        "description": "编排计划，结构见描述",
                        "properties": {
                            "name": {"type": "string"},
                            "goal": {"type": "string", "description": "这份编排要达成什么"},
                            "stages": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "steps": {"type": "array", "items": {"type": "object"}},
                                    },
                                    "required": ["steps"],
                                },
                            },
                        },
                        "required": ["goal", "stages"],
                    },
                    "args": {
                        "type": "object",
                        "description": "传给编排的参数，会写进 run 记录",
                    },
                },
                "required": ["plan"],
            },
            risk=Risk.L0_READ,
            embed_text="多 Agent 并行编排，批量分派子 Agent，大规模审计迁移，跨仓库调研",
            fn=_run,
        )
        self.registry["workflow_status"] = ToolSpec(
            name="workflow_status",
            description="查看某次工作流运行的状态与每个 step 的产出（不给 run_id 就看最近一次）。",
            parameters={
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_status,
        )
        self.registry["workflow_cancel"] = ToolSpec(
            name="workflow_cancel",
            description="中止正在跑的工作流。不给 run_id 就中止最近一次。",
            parameters={
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_cancel,
        )
