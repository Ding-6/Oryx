"""子 Agent 名册、派发、协作。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any

from ..policy import Risk
from ..tools import ToolSpec


class AgentToolsMixin:
    def _register_agent_tools(self) -> None:
        def _spawn(agent: str, goal: str, acceptance: str = "") -> dict[str, Any]:
            return self.run_subagent(agent, goal, acceptance=acceptance)

        def _list() -> dict[str, Any]:
            return {"ok": True, "content": self.agents.catalog()}

        self.registry["spawn_agent"] = ToolSpec(
            name="spawn_agent",
            description=(
                "把一件专门的活交给专职子 Agent（L1），它在自己的领域里闭环，"
                "只拥有被授权的工具，做完回一段结论。"
                "可用 Agent 与各自职责见系统提示词里的「可用的专职 Agent」一段；"
                "不确定就调 list_agents 看全。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "子 Agent id"},
                    "goal": {
                        "type": "string",
                        "description": (
                            "交给它做什么。写完整目标 + 必要的上下文，"
                            "**只写目标与验收判据，不要替它写步骤** —— "
                            "步骤是它自己的活，你写了反而把它降级成 L3 工具的执行器。"
                        ),
                    },
                    "acceptance": {
                        "type": "string",
                        "description": (
                            "可验证的完成判据，例如「3 个文件均已落盘且返回绝对路径」。"
                            "给了判据，子 Agent 才知道什么时候算做完。"
                        ),
                    },
                },
                "required": ["agent", "goal"],
            },
            risk=Risk.L1_WRITE,
            fn=_spawn,
        )
        self.registry["list_agents"] = ToolSpec(
            name="list_agents",
            description="列出可用的子 Agent 及其工具白名单。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_list,
        )

        # ---------------------------------------------------------------- 团队
        def _known_agent_ids() -> set[str]:
            return set(self.agents.agents)

        def _team_create(name: str, members: Any, goal: str = "") -> dict[str, Any]:
            team, err = self.teams.create(
                name, members, goal, known_agents=_known_agent_ids(), owner_id=self._active_owner_id
            )
            if team is None:
                return {"ok": False, "content": err}
            self.bus.publish("team.created", team=team.to_dict(self._active_owner_id), _owner_id=self._active_owner_id)
            return {
                "ok": True,
                "content": f"已建团队「{team.name}」，成员：{team.roles()}",
                "meta": {"team": team.to_dict()},
            }

        def _team_delete(name: str) -> dict[str, Any]:
            if not self.teams.delete(name, self._active_owner_id):
                return {"ok": False, "content": f"没有这个团队，或你没有删除权限：{name}"}
            self.bus.publish("team.deleted", name=name, _owner_id=self._active_owner_id)
            return {"ok": True, "content": f"已解散团队「{name}」"}

        def _team_list() -> dict[str, Any]:
            return {"ok": True, "content": self.teams.list(self._active_owner_id)}

        def _team_add(team: str, agent: str, role: str = "") -> dict[str, Any]:
            if agent not in _known_agent_ids():
                return {"ok": False, "content": f"没有这个子 Agent：{agent}"}
            ok, err = self.teams.add_member(team, agent, role, self._active_owner_id)
            if not ok:
                return {"ok": False, "content": err}
            obj = self.teams.get(team)
            self.bus.publish("team.updated", team=obj.to_dict(self._active_owner_id) if obj else {}, _owner_id=self._active_owner_id)
            return {"ok": True, "content": f"{agent} 已加入「{team}」，成员：{obj.roles() if obj else ''}"}

        def _team_send(team: str, to: str, content: str) -> dict[str, Any]:
            msg, err = self.teams.post(team, "main", to, content, self._active_owner_id)
            if msg is None:
                return {"ok": False, "content": err}
            self.bus.publish("team.message", team=team, message=msg.to_dict(), _owner_id=self._active_owner_id)
            if to in ("broadcast", "all"):
                return {"ok": True, "content": f"已广播给「{team}」全体成员"}
            return {"ok": True, "content": f"已投递消息给 {to}（内容未执行，用 team_dispatch 才会真的开工）"}

        def _team_dispatch(team: str, agent: str, owner_id: str | None = None) -> dict[str, Any]:
            owner_id = self._active_owner_id if owner_id is None else owner_id
            obj = self.teams.get(team)
            if obj is None:
                return {"ok": False, "content": f"没有叫「{team}」的团队"}
            agent = (agent or "").strip()
            if agent not in obj.members:
                return {"ok": False, "content": f"{agent} 不在「{team}」里，成员：{'、'.join(obj.members)}"}
            inbox = self.teams.inbox(team, agent, owner_id=owner_id)
            if not inbox:
                return {"ok": False, "content": f"{agent} 信箱是空的，先用 team_send 派活"}

            briefing = "\n\n".join(
                [f"[来自 {m.sender}] {m.content}" for m in inbox]
            )
            self.hooks.handoff(
                kind="team", target=agent, task_id=self._active_task_id or "",
                team=team, goal=briefing, depth=1,
            )
            spec = self.agents.get(agent)
            # 派活这件事本身要让用户看见 —— 否则界面上只是"卡住了"，
            # 用户不知道是谁被叫走了、在做什么。
            self.bus.publish(
                "team.member.started",
                team=team,
                agent=agent,
                agent_name=spec.name if spec else agent,
                agent_icon=spec.icon if spec else "",
                goal=briefing[:400],
                _owner_id=owner_id,
            )
            result = self.run_subagent(agent, briefing)
            text = result.get("content", "") if isinstance(result.get("content"), str) else str(result.get("content"))
            self.teams.post(team, agent, "main", text, owner_id)
            self.bus.publish(
                "team.member.done",
                team=team,
                agent=agent,
                agent_name=spec.name if spec else agent,
                agent_icon=spec.icon if spec else "",
                text=text,
                # 空产出要标出来 —— 「没产出」和「一条正常回复」长得一样的话，
                # 用户会以为它说完了但其实什么都没说。
                empty=not text.strip() or text.strip() == "（子 Agent 没有产出文本）",
                ok=bool(result.get("ok")),
                _owner_id=owner_id,
            )
            if not result.get("ok"):
                return {"ok": False, "content": f"{agent} 执行失败：{text}"}
            return {"ok": True, "content": text}

        def _team_inbox(team: str, agent: str) -> dict[str, Any]:
            return {"ok": True, "content": [
                m.to_dict() for m in self.teams.inbox(
                    team, agent, mark_read=False, owner_id=self._active_owner_id
                )
            ]}

        self.registry["team_create"] = ToolSpec(
            name="team_create",
            description="组建一个多 Agent 团队。members 是子 Agent id 列表，可以写成 \"id:角色\" 指定分工。团队只是这一轮任务的临时编队。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "团队名，如 delivery"},
                    "members": {
                        "anyOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "string"},
                        ],
                        "description": "成员子 Agent id，可写成 [\"researcher:调研\", \"writer:成稿\"]",
                    },
                    "goal": {"type": "string", "description": "团队要达成的目标，写清楚验收标准"},
                },
                "required": ["name", "members"],
            },
            risk=Risk.L0_READ,
            fn=_team_create,
        )
        self.registry["team_delete"] = ToolSpec(
            name="team_delete",
            description="解散一个团队。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk=Risk.L0_READ,
            fn=_team_delete,
        )
        self.registry["team_list"] = ToolSpec(
            name="team_list",
            description="列出当前所有团队及其成员。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_team_list,
        )
        self.registry["team_add_member"] = ToolSpec(
            name="team_add_member",
            description="往已建团队里加一个成员并指定角色。",
            parameters={
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "agent": {"type": "string", "description": "子 Agent id"},
                    "role": {"type": "string", "description": "分工，如「调研」「审校」"},
                },
                "required": ["team", "agent"],
            },
            risk=Risk.L0_READ,
            fn=_team_add,
        )
        self.registry["team_send_message"] = ToolSpec(
            name="team_send_message",
            description=(
                "给某个成员投一条消息（只投递不执行）。recipient 填 broadcast 表示群发。"
                "想让它真的开工，改用 team_dispatch。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "to": {"type": "string", "description": "成员 agent id，或 broadcast"},
                    "content": {"type": "string", "description": "要交代的任务，写清楚上下文和验收标准"},
                },
                "required": ["team", "to", "content"],
            },
            risk=Risk.L0_READ,
            fn=_team_send,
        )
        self.registry["team_dispatch"] = ToolSpec(
            name="team_dispatch",
            description=(
                "让某个成员读它信箱里的消息并真正执行，把结论带回来。"
                "会等待它跑完，所以一次只派一个。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "agent": {"type": "string", "description": "成员 agent id"},
                },
                "required": ["team", "agent"],
            },
            risk=Risk.L1_WRITE,
            fn=_team_dispatch,
        )
        self.registry["team_inbox"] = ToolSpec(
            name="team_inbox",
            description="查看某个成员信箱里还没读的消息（不标记已读）。",
            parameters={
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "agent": {"type": "string"},
                },
                "required": ["team", "agent"],
            },
            risk=Risk.L0_READ,
            fn=_team_inbox,
        )
