"""多 Agent 团队：编队、投递/执行分离、成员间传话、白名单不被放宽。"""

from __future__ import annotations

from pathlib import Path

import pytest

from oryx import loop as loop_mod
from oryx.agents import AgentLibrary
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store
from oryx.team import TeamRegistry, parse_members

AGENTS_ROOT = Path(__file__).resolve().parents[2] / "agents"


class ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.index = 0
        self.systems: list[str] = []
        self.goals: list[str] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.systems.append(system)
        # capture the first user message so we can assert what the member was told
        for m in messages:
            if m.get("role") == "user":
                blocks = m.get("content") or []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "text":
                        self.goals.append(b["text"])
                break
        if self.index < len(self.script):
            name, args = self.script[self.index]
            self.index += 1
            if name is None:
                return ProviderReply(text="已完成", tool_calls=[], stop_reason="end_turn")
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id=f"c{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="已完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script=None):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    data["interaction"] = "craft"
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = ScriptedProvider(script or [(None, {})])
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    runtime._active_task_id = store.create_task("x", "efficiency", "test")
    runtime._active_registry = dict(runtime.registry)
    return runtime, store, provider, bus


# ------------------------------------------------------------------ members parsing


class TestParseMembers:
    def test_plain_list(self):
        assert parse_members(["a", "b"]) == [("a", "成员"), ("b", "成员")]

    def test_comma_string(self):
        assert parse_members("a, b") == [("a", "成员"), ("b", "成员")]

    def test_role_syntax(self):
        assert parse_members(["a:调研", "b：成稿"]) == [("a", "调研"), ("b", "成稿")]

    def test_dict_form(self):
        assert parse_members([{"agent_id": "a", "role": "审校"}]) == [("a", "审校")]

    def test_garbage_returns_empty(self):
        assert parse_members(123) == []


# ------------------------------------------------------------------ registry


class TestTeamRegistry:
    def test_create_and_list(self):
        reg = TeamRegistry()
        team, err = reg.create("delivery", ["a", "b"], goal="发货", known_agents={"a", "b"})
        assert team is not None and err == ""
        assert team.roles() == "a（成员）、b（成员）"
        assert len(reg.list()) == 1

    def test_reject_unknown_agent(self):
        reg = TeamRegistry()
        team, err = reg.create("x", ["ghost"], known_agents={"a"})
        assert team is None
        assert "不存在" in err

    def test_reject_duplicate_name(self):
        reg = TeamRegistry()
        reg.create("dup", ["a"], known_agents={"a"})
        team, err = reg.create("dup", ["a"], known_agents={"a"})
        assert team is None and "已经有" in err

    def test_delete(self):
        reg = TeamRegistry()
        reg.create("gone", ["a"], known_agents={"a"})
        assert reg.delete("gone") is True
        assert reg.delete("gone") is False

    def test_broadcast_fans_out(self):
        reg = TeamRegistry()
        reg.create("t", ["a", "b"], known_agents={"a", "b"})
        msg, err = reg.post("t", "main", "broadcast", "开工")
        assert msg is not None and err == ""
        assert len(reg.inbox("t", "a", mark_read=False)) == 1
        assert len(reg.inbox("t", "b", mark_read=False)) == 1

    def test_inbox_marks_read(self):
        reg = TeamRegistry()
        reg.create("t", ["a"], known_agents={"a"})
        reg.post("t", "main", "a", "干活")
        assert len(reg.inbox("t", "a")) == 1
        assert len(reg.inbox("t", "a")) == 0, "读过就不该再出现"

    def test_send_to_non_member_rejected(self):
        reg = TeamRegistry()
        reg.create("t", ["a"], known_agents={"a", "b"})
        msg, err = reg.post("t", "main", "b", "你没进队")
        assert msg is None and "不在" in err

    def test_message_trimmed_to_capacity(self):
        reg = TeamRegistry()
        reg.create("t", ["a"], known_agents={"a"})
        for i in range(260):
            reg.post("t", "main", "a", f"m{i}")
        assert len(reg.thread("t", 999)) <= 200


# ------------------------------------------------------------------ tools


class TestTeamTools:
    def test_tools_registered(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        for name in (
            "team_create",
            "team_delete",
            "team_list",
            "team_add_member",
            "team_send_message",
            "team_dispatch",
            "team_inbox",
        ):
            assert name in runtime.registry, f"{name} 没注册"

    def test_create_list_delete_cycle(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        created = runtime.registry["team_create"].fn(
            name="delivery", members=["doc-manager:记录"], goal="写文档"
        )
        assert created["ok"]
        assert created["meta"]["team"]["name"] == "delivery"

        listed = runtime.registry["team_list"].fn()
        assert listed["content"][0]["name"] == "delivery"

        assert runtime.registry["team_delete"].fn(name="delivery")["ok"]
        assert runtime.registry["team_list"].fn()["content"] == []

    def test_send_does_not_execute(self, tmp_path):
        """投递与执行分离：send 只入信箱，不调模型。"""
        runtime, store, provider, bus = make_runtime(tmp_path)
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        before = len(provider.systems)

        sent = runtime.registry["team_send_message"].fn(team="t", to="doc-manager", content="记一条")
        assert sent["ok"]
        assert len(provider.systems) == before, "team_send_message 不该触发模型"

        inbox = runtime.registry["team_inbox"].fn(team="t", agent="doc-manager")
        assert len(inbox["content"]) == 1

    def test_dispatch_runs_member_and_returns_result(self, tmp_path):
        runtime, store, provider, bus = make_runtime(
            tmp_path,
            [
                ("doc_requirement_add", {"title": "支持团队", "detail": "能组队"}),
                (None, {}),
            ],
        )
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        runtime.registry["team_send_message"].fn(team="t", to="doc-manager", content="记一条需求")

        out = runtime.registry["team_dispatch"].fn(team="t", agent="doc-manager")
        assert out["ok"], out
        # 成员真的写了项目文档
        assert "支持团队" in runtime.project_doc.read()
        # 结论回投给主 Agent
        main_box = runtime.teams.inbox("t", "main", mark_read=False)
        assert len(main_box) == 1

    def test_dispatch_without_message_fails(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        out = runtime.registry["team_dispatch"].fn(team="t", agent="doc-manager")
        assert out["ok"] is False
        assert "信箱是空的" in out["content"]

    def test_dispatch_rejects_non_member(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        out = runtime.registry["team_dispatch"].fn(team="t", agent="researcher")
        assert out["ok"] is False
        assert "不在" in out["content"]

    def test_team_does_not_widen_tool_whitelist(self, tmp_path):
        """团队不会给成员放宽任何权限。"""
        victim = tmp_path / "keep.txt"
        victim.write_text("别删我", encoding="utf-8")
        runtime, *_ = make_runtime(tmp_path, [("fs_delete", {"path": str(victim)}), (None, {})])
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        runtime.registry["team_send_message"].fn(team="t", to="doc-manager", content="删掉那个文件")
        runtime.registry["team_dispatch"].fn(team="t", agent="doc-manager")
        assert victim.exists()

    def test_add_member_validates_agent(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        bad = runtime.registry["team_add_member"].fn(team="t", agent="ghost", role="x")
        assert bad["ok"] is False and "没有这个子 Agent" in bad["content"]
        good = runtime.registry["team_add_member"].fn(team="t", agent="reviewer", role="审校")
        assert good["ok"] is True

    def test_dispatch_emits_member_events(self, tmp_path):
        """派活与完成都要发事件，而且要带成员的名字与图标。

        前端靠这两个事件把「谁被叫走了、说了什么」显示出来 ——
        少了 agent_name 就只能显示英文 id。
        """
        runtime, store, provider, bus = make_runtime(tmp_path, [(None, {})])
        runtime.registry["team_create"].fn(name="t", members=["doc-manager"], goal="")
        runtime.registry["team_send_message"].fn(team="t", to="doc-manager", content="记一条")
        runtime.registry["team_dispatch"].fn(team="t", agent="doc-manager")

        events = list(bus._buffer)
        started = [e for e in events if e.get("type") == "team.member.started"]
        assert started, "派活要发 team.member.started"
        assert started[0]["agent"] == "doc-manager"
        assert started[0]["agent_name"] == "文档管理"

        done = [e for e in events if e.get("type") == "team.member.done"]
        assert done, "完成要发 team.member.done"
        assert done[0]["agent"] == "doc-manager"
        assert done[0]["ok"] is True
        # 结论要带出来 —— 前端就是靠它把结论显示到对话流里
        assert isinstance(done[0]["text"], str) and done[0]["text"]


class TestTeamPersistence:
    """团队落库：重启后还在，而且顺序不能反。"""

    def test_messages_survive_restart_in_order(self, tmp_path):
        """恢复后的信箱顺序要跟原来一致。

        存储层按 ts DESC 返回（最新的在前），恢复时直接 append
        会让整个信箱顺序反过来 —— 用户看到的对话是倒着放的。
        """
        from oryx.store import Store
        from oryx.team import TeamRegistry

        store = Store(tmp_path / "oryx.db")
        reg = TeamRegistry(store)
        team, err = reg.create("t", ["doc-manager"], goal="")
        assert team is not None, err
        reg.post("t", "main", "doc-manager", "第一条")
        reg.post("t", "doc-manager", "main", "第二条")
        reg.post("t", "main", "doc-manager", "第三条")

        # 重新建一个 registry —— 相当于重启
        again = TeamRegistry(store)
        restored = again.get("t")
        assert restored is not None
        contents = [m.content for m in restored.messages]
        assert contents == ["第一条", "第二条", "第三条"], contents

    def test_delete_team_clears_its_messages(self, tmp_path):
        """删团队要把信箱一起清掉。

        原来的实现先删 teams 行、再用子查询找 id 删消息 ——
        行已经没了，子查询查不到，消息全成孤儿留在库里。
        """
        from oryx.store import Store

        store = Store(tmp_path / "oryx.db")
        store.save_team({"id": "tid1", "name": "t", "goal": "", "members": {"a": "成员"}})
        store.save_team_message({
            "id": "m1", "team_id": "tid1", "sender": "main",
            "recipient": "a", "content": "hello", "read": False, "ts": 1.0,
        })
        assert store.team_messages("tid1")

        assert store.delete_team("t") is True
        assert store.team_messages("tid1") == [], "消息应该跟着团队一起删掉"

    def test_rename_team_keeps_messages(self, tmp_path):
        """改名不能丢协作记录 —— 消息是按 team_id 关联的，id 不变就还在。"""
        from oryx.store import Store

        store = Store(tmp_path / "oryx.db")
        store.save_team({"id": "tid1", "name": "old", "goal": "", "members": {"a": "成员"}})
        store.save_team_message({
            "id": "m1", "team_id": "tid1", "sender": "main",
            "recipient": "a", "content": "hello", "read": False, "ts": 1.0,
        })
        assert store.rename_team("old", "new") is True
        assert store.get_team("new") is not None
        assert store.get_team("old") is None
        assert len(store.team_messages("tid1")) == 1, "改名不该丢消息"

    def test_rename_team_rejects_duplicate(self, tmp_path):
        from oryx.store import Store

        store = Store(tmp_path / "oryx.db")
        store.save_team({"id": "t1", "name": "a", "goal": "", "members": {}})
        store.save_team({"id": "t2", "name": "b", "goal": "", "members": {}})
        assert store.rename_team("a", "b") is False
        assert store.get_team("a") is not None


class TestTeamEditing:
    """团队编辑：改名、改目标、改分工、移出成员。"""

    def test_remove_member(self, tmp_path):
        from oryx.store import Store
        from oryx.team import TeamRegistry

        reg = TeamRegistry(Store(tmp_path / "oryx.db"))
        reg.create("t", ["doc-manager", "reviewer"], goal="")
        ok, err = reg.remove_member("t", "reviewer")
        assert ok and err == ""
        assert "reviewer" not in reg.get("t").members

    def test_remove_unknown_member(self, tmp_path):
        from oryx.team import TeamRegistry

        reg = TeamRegistry()
        reg.create("t", ["doc-manager"], goal="")
        ok, err = reg.remove_member("t", "ghost")
        assert ok is False and "不在" in err


class TestTeamDaemonEditing:
    """内核侧的 team.update / team.remove_member。"""

    def test_update_changes_goal_and_role(self, tmp_path):
        runtime, store, provider, bus = make_runtime(tmp_path)
        runtime.teams.create("t", ["doc-manager", "reviewer"], goal="旧目标")
        out = runtime.team_update("t", "", "新目标", "reviewer", "审校")
        assert out["ok"], out
        team = runtime.teams.get("t")
        assert team.goal == "新目标"
        assert team.members["reviewer"] == "审校"

    def test_update_renames_team(self, tmp_path):
        runtime, store, provider, bus = make_runtime(tmp_path)
        runtime.teams.create("t", ["doc-manager"], goal="")
        out = runtime.team_update("t", "新名字", "", "", None)
        assert out["ok"], out
        assert runtime.teams.get("新名字") is not None
        assert runtime.teams.get("t") is None

    def test_update_rejects_duplicate_name(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        runtime.teams.create("a", ["doc-manager"], goal="")
        runtime.teams.create("b", ["reviewer"], goal="")
        out = runtime.team_update("a", "b", "", "", None)
        assert out["ok"] is False and "已经有" in out["error"]

    def test_update_unknown_team(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        out = runtime.team_update("ghost", "x", "", "", None)
        assert out["ok"] is False and "没有叫" in out["error"]

    def test_remove_member_rejects_last_one(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        runtime.teams.create("t", ["doc-manager"], goal="")
        out = runtime.team_remove_member("t", "doc-manager")
        assert out["ok"] is False and "至少要留一个" in out["error"]

    def test_remove_member_works(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        runtime.teams.create("t", ["doc-manager", "reviewer"], goal="")
        out = runtime.team_remove_member("t", "reviewer")
        assert out["ok"], out
        assert "reviewer" not in runtime.teams.get("t").members



# ------------------------------------------------------------------ seed agents


class TestSeedAgents:
    def test_researcher_and_reviewer_exist(self):
        library = AgentLibrary(AGENTS_ROOT)
        assert "researcher" in library.agents
        assert "reviewer" in library.agents

    def test_researcher_has_no_write_tools(self):
        library = AgentLibrary(AGENTS_ROOT)
        tools = library.agents["researcher"].tools
        assert "fs_write" not in tools
        assert "fs_delete" not in tools

    def test_reviewer_is_read_only(self):
        library = AgentLibrary(AGENTS_ROOT)
        tools = set(library.agents["reviewer"].tools)
        assert not (tools & {"fs_write", "fs_delete", "shell", "doc_change_log"})
