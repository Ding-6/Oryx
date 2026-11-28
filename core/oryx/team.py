"""多 Agent 团队：建队、拆队、角色分配与成员间传话。

设计取向（对齐 WorkBuddy 的 TeamCreate / TeamDelete / SendMessage）：

- 团队是**这一轮任务里的临时编队**，不是组织架构。
- 每个成员就是一个 `agents/<id>/AGENT.md` 子 Agent，工具白名单仍是硬边界，
  团队不会给成员放宽任何权限。
- **投递与执行分离**。这是刻意的：
    - `team_send_message` 只往信箱里投，不触发模型；
    - `team_dispatch`      投递完立刻等它跑完，把结论带回来。
  这样主 Agent 可以先把活派布完再统一开工，
  不会因为一句「收到」就把一堆子 Agent 全拉起来烧 token。

持久化（第二批新增）：给了 store 之后，团队与信箱会落 SQLite，
重启后还在。不给 store 就是纯内存（测试与临时编排用）。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

MAX_TEAMS = 12
MAX_MEMBERS = 8
MAX_MESSAGES_PER_TEAM = 200
MAX_MESSAGE_CHARS = 4000
MAX_MESSAGE_PREVIEW = 160


@dataclass
class TeamMessage:
    id: str
    team_id: str
    sender: str
    recipient: str
    content: str
    ts: float
    read: bool = False
    owner_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "team_id": self.team_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "content": self.content,
            "ts": self.ts,
            "read": self.read,
        }

    def preview(self) -> str:
        flat = " ".join(self.content.split())
        return flat[:MAX_MESSAGE_PREVIEW] + ("…" if len(flat) > MAX_MESSAGE_PREVIEW else "")


@dataclass
class Team:
    id: str
    name: str
    goal: str
    members: dict[str, str] = field(default_factory=dict)  # agent_id -> role
    messages: list[TeamMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    owner_id: str | None = None

    def roles(self) -> str:
        return "、".join(f"{aid}（{role}）" for aid, role in self.members.items())

    def to_dict(self, actor_id: str | None = None) -> dict[str, Any]:
        visible_messages = [m for m in self.messages if m.owner_id == actor_id]
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "members": [{"agent_id": aid, "role": role} for aid, role in self.members.items()],
            "message_count": len(visible_messages),
            "unread": sum(1 for m in visible_messages if not m.read),
            "created_at": self.created_at,
            "creator_id": self.owner_id,
            "shared": self.owner_id != actor_id,
            "can_edit": self.owner_id == actor_id,
        }


def parse_members(raw: Any) -> list[tuple[str, str]]:
    """接受三种写法，方便模型随手写：

        ["researcher", "writer"]
        "researcher, writer"
        ["researcher:调研", "writer:成稿"]
    """
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, str):
        items = [seg for seg in raw.replace("，", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []

    out: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            agent_id = str(item.get("agent_id") or item.get("id") or "").strip()
            role = str(item.get("role") or "").strip()
        else:
            text = str(item).strip()
            if not text:
                continue
            if ":" in text or "：" in text:
                agent_id, _, role = text.replace("：", ":").partition(":")
                agent_id, role = agent_id.strip(), role.strip()
            else:
                agent_id, role = text, ""
        if agent_id:
            out.append((agent_id, role or "成员"))
    return out


class TeamRegistry:
    def __init__(self, store: Any = None):
        self._teams: dict[str, Team] = {}
        self._lock = threading.Lock()
        self.store = store
        if store is not None:
            self._restore()

    # ---------------- 持久化 ----------------
    def _restore(self) -> None:
        """从存储恢复团队与信箱。"""
        try:
            rows = self.store.list_teams()
        except Exception:
            return
        for row in rows:
            try:
                team = Team(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    goal=str(row.get("goal") or ""),
                    members={str(k): str(v) for k, v in (row.get("members") or {}).items()},
                    created_at=float(row.get("created_at") or time.time()),
                    owner_id=str(row["owner_id"]) if row.get("owner_id") else None,
                )
            except Exception:
                continue
            self._teams[team.name] = team
            # 恢复信箱。存储层按 ts DESC 返回（最新的在前），
            # 这里要**倒着填** —— 直接 append 会让信箱顺序整个反过来。
            try:
                if hasattr(self.store, "team_message_owners") and hasattr(self.store, "team_messages_for_owner"):
                    messages = [
                        msg
                        for owner_id in self.store.team_message_owners(team.id)
                        for msg in self.store.team_messages_for_owner(
                            team.id, owner_id, limit=MAX_MESSAGES_PER_TEAM
                        )
                    ]
                    messages.sort(key=lambda msg: (float(msg.get("ts") or 0), str(msg.get("id") or "")))
                else:
                    messages = list(reversed(self.store.team_messages(team.id, limit=MAX_MESSAGES_PER_TEAM)))
                for msg in messages:
                    team.messages.append(
                        TeamMessage(
                            id=str(msg["id"]),
                            team_id=team.id,
                            sender=str(msg["sender"]),
                            recipient=str(msg["recipient"]),
                            content=str(msg["content"]),
                            ts=float(msg.get("ts") or time.time()),
                            read=bool(msg.get("read")),
                            owner_id=str(msg["owner_id"]) if msg.get("owner_id") else None,
                        )
                    )
            except Exception:
                pass

    def _persist_team(self, team: Team) -> None:
        if self.store is None:
            return
        try:
            self.store.save_team({
                "id": team.id,
                "name": team.name,
                "goal": team.goal,
                "members": dict(team.members),
                "created_at": team.created_at,
                "owner_id": team.owner_id,
            })
        except Exception:
            pass

    def _persist_message(self, team: Team, message: TeamMessage) -> None:
        if self.store is None:
            return
        try:
            self.store.save_team_message({**message.to_dict(), "owner_id": message.owner_id})
        except Exception:
            pass

    # ---------------- 建队 / 拆队 ----------------
    def create(
        self,
        name: str,
        members: Any,
        goal: str = "",
        known_agents: set[str] | None = None,
        owner_id: str | None = None,
    ) -> tuple[Team | None, str]:
        name = (name or "").strip()
        if not name:
            return None, "团队需要一个名字"
        if len(name) > 80 or "/" in name or "\\" in name:
            return None, "团队名称不能超过 80 个字符，也不能包含斜杠"

        parsed = parse_members(members)
        if not parsed:
            return None, "团队至少要有一个成员，例如 members=[\"researcher\", \"writer\"]"
        if len(parsed) > MAX_MEMBERS:
            return None, f"成员最多 {MAX_MEMBERS} 个，当前 {len(parsed)} 个"

        unknown = [aid for aid, _ in parsed if known_agents is not None and aid not in known_agents]
        if unknown:
            return None, f"这些子 Agent 不存在：{'、'.join(unknown)}。用 list_agents 看可用的"

        with self._lock:
            if name in self._teams:
                return None, f"已经有叫「{name}」的团队了，换个名字或者先 team_delete"
            if len(self._teams) >= MAX_TEAMS:
                return None, f"同时最多 {MAX_TEAMS} 个团队，先拆掉一个"

            team = Team(
                id=uuid.uuid4().hex[:16],
                name=name,
                goal=(goal or "").strip(),
                members=dict(parsed),
                owner_id=owner_id,
            )
            self._teams[name] = team
            self._persist_team(team)
            return team, ""

    def can_edit(self, name: str, owner_id: str | None = None) -> bool:
        team = self.get(name)
        return team is not None and team.owner_id == owner_id

    def delete(self, name: str, owner_id: str | None = None) -> bool:
        with self._lock:
            key = (name or "").strip()
            team = self._teams.get(key)
            if team is None or team.owner_id != owner_id:
                return False
            if self.store is not None:
                try:
                    if not self.store.delete_team(key, owner_id):
                        return False
                except Exception:
                    return False
            self._teams.pop(key, None)
            return True

    def get(self, name: str) -> Team | None:
        return self._teams.get((name or "").strip())

    def list(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [t.to_dict(owner_id) for t in sorted(self._teams.values(), key=lambda x: x.created_at)]

    # ---------------- 成员 ----------------
    def add_member(
        self, team_name: str, agent_id: str, role: str = "", owner_id: str | None = None
    ) -> tuple[bool, str]:
        team = self.get(team_name)
        if team is None:
            return False, f"没有叫「{team_name}」的团队"
        if team.owner_id != owner_id:
            return False, "只有创建者可以修改这个共享团队"
        agent_id = (agent_id or "").strip()
        if not agent_id:
            return False, "agent_id 不能为空"
        if agent_id in team.members:
            return False, f"{agent_id} 已经在团队里了"
        if len(team.members) >= MAX_MEMBERS:
            return False, f"成员最多 {MAX_MEMBERS} 个"
        with self._lock:
            team.members[agent_id] = (role or "").strip() or "成员"
        self._persist_team(team)
        return True, ""

    def remove_member(
        self, team_name: str, agent_id: str, owner_id: str | None = None
    ) -> tuple[bool, str]:
        team = self.get(team_name)
        if team is None:
            return False, f"没有叫「{team_name}」的团队"
        if team.owner_id != owner_id:
            return False, "只有创建者可以修改这个共享团队"
        with self._lock:
            if team.members.pop((agent_id or "").strip(), None) is None:
                return False, f"{agent_id} 不在团队里"
        self._persist_team(team)
        return True, ""

    # ---------------- 信箱 ----------------
    def post(
        self,
        team_name: str,
        sender: str,
        recipient: str,
        content: str,
        owner_id: str | None = None,
    ) -> tuple[TeamMessage | None, str]:
        team = self.get(team_name)
        if team is None:
            return None, f"没有叫「{team_name}」的团队"
        content = (content or "").strip()
        if not content:
            return None, "消息内容不能为空"
        if len(content) > MAX_MESSAGE_CHARS:
            return None, f"消息太长（{len(content)} 字），控制在 {MAX_MESSAGE_CHARS} 字以内"

        recipient = (recipient or "").strip()
        if not recipient:
            return None, "recipient 不能为空"
        # "main" 是编排者本人，成员回报时统一寄回这里，不算团队成员
        allowed_recipients = set(team.members) | {"broadcast", "all", "main"}
        if recipient not in allowed_recipients:
            return None, (
                f"{recipient} 不在「{team_name}」里。成员：{'、'.join(team.members) or '无'}。"
                f"要发给所有人用 recipient=\"broadcast\""
            )

        with self._lock:
            msg = TeamMessage(
                id=uuid.uuid4().hex,
                team_id=team.id,
                sender=(sender or "main").strip() or "main",
                recipient=recipient,
                content=content,
                ts=time.time(),
                owner_id=owner_id,
            )
            appended: list[TeamMessage] = []
            if recipient in ("broadcast", "all"):
                # 广播拆成每人一封，收件人自己看自己的
                for aid in team.members:
                    one = TeamMessage(
                        id=f"{msg.id}-{aid}",
                        team_id=team.id,
                        sender=msg.sender,
                        recipient=aid,
                        content=content,
                        ts=msg.ts,
                        owner_id=owner_id,
                    )
                    team.messages.append(one)
                    appended.append(one)
            else:
                team.messages.append(msg)
                appended.append(msg)
            owner_messages = [m for m in team.messages if m.owner_id == owner_id]
            if len(owner_messages) > MAX_MESSAGES_PER_TEAM:
                retained = {id(m) for m in owner_messages[-MAX_MESSAGES_PER_TEAM:]}
                team.messages = [
                    m for m in team.messages if m.owner_id != owner_id or id(m) in retained
                ]
        # 广播会拆成好几封，每一封都要落库
        for stored in appended:
            self._persist_message(team, stored)
        if self.store is not None and hasattr(self.store, "prune_team_messages"):
            try:
                self.store.prune_team_messages(team.id, owner_id, MAX_MESSAGES_PER_TEAM)
            except Exception:
                pass
        return msg, ""

    def inbox(
        self, team_name: str, recipient: str, mark_read: bool = True,
        owner_id: str | None = None,
    ) -> list[TeamMessage]:
        team = self.get(team_name)
        if team is None:
            return []
        recipient = (recipient or "").strip()
        with self._lock:
            hits = [m for m in team.messages if m.owner_id == owner_id and m.recipient == recipient and not m.read]
            if mark_read:
                for m in hits:
                    m.read = True
        if mark_read and hits and self.store is not None:
            try:
                self.store.mark_team_messages_read(team.id, recipient, owner_id)
            except Exception:
                pass
        return hits

    def thread(
        self, team_name: str, limit: int = 20, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        team = self.get(team_name)
        if team is None:
            return []
        with self._lock:
            messages = [m for m in team.messages if m.owner_id == owner_id]
            return [m.to_dict() for m in messages[-max(1, limit) :]]

    def clear(self) -> None:
        with self._lock:
            self._teams.clear()
