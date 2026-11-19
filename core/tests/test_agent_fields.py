"""子 Agent 的扩展字段必须**真的生效**。

agents.py 声称「frontmatter 支持 WorkBuddy 子代理定义的 14 个字段」，
但其中 8 个（model / permissionMode / skills / effort / background /
memory / initialPrompt / mcpServers）**解析了却从不被使用** ——
声明了「这个 Agent 会某项技能 / 用某个模型」，实际那个 Agent 根本不知道。

这组测试锁住三个已实现的字段：model / permissionMode / skills。
剩下的字段要么实现，要么从文档里删掉承诺 —— 不能留着空头支票。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store


class _CapturingProvider:
    """记录被调用的 provider 配置与提示词。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.systems: list[str] = []
        self.calls = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        from oryx.providers import ProviderReply

        self.systems.append(system)
        self.calls += 1
        return ProviderReply(text="做完了", tool_calls=[], stop_reason="end_turn")


def _runtime(tmp_path: Path, agent_md: str, providers: dict | None = None) -> tuple:
    """造一个 runtime，agents 目录里放一个自定义 Agent。

    注意 AgentRuntime 的 skill_root / agents 目录是从**仓库根**推出来的
    （root.parent / "agents"），所以这里造完 runtime 后要把 AgentLibrary
    换成临时的那个 —— 否则用的是仓库里真实的 8 个 Agent，测试里的
    "custom" 根本不存在。
    """
    root = tmp_path / "agents" / "custom"
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENT.md").write_text(agent_md, encoding="utf-8")

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["providers"] = providers or {
        "main": {"type": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "big-model"},
        "cheap": {"type": "openai", "base_url": "http://127.0.0.1:2/v1", "model": "small-model"},
    }
    data["active_provider"] = "main"
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(config, store, EventBus())

    from oryx.agents import AgentLibrary

    runtime.agents = AgentLibrary(tmp_path / "agents")
    return runtime, store


# ------------------------------------------------------------------ model


def test_agent_can_pick_its_own_model(tmp_path: Path, monkeypatch):
    """子 Agent 可以指定用哪个模型服务。

    为什么要这个：审校、跑批这类活用便宜的小模型就够，
    全部用同一个模型就是浪费。
    """
    runtime, _ = _runtime(tmp_path, """---
name: 便宜跑批
description: 跑批用
tools: fs_read
model: cheap
max_turns: 2
---
你是跑批的。
""")
    captured: list[_CapturingProvider] = []

    def _build(cfg):
        p = _CapturingProvider(cfg)
        captured.append(p)
        return p

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", _build)
    out = runtime.run_subagent("custom", "干活")

    assert out.get("ok") is True, out
    assert captured, "provider 没被调用"
    assert captured[0].cfg.get("name") == "cheap", (
        f"应该用 cheap，实际用了 {captured[0].cfg.get('name')}"
    )


def test_agent_with_unknown_model_fails_loudly(tmp_path: Path, monkeypatch):
    """指定的模型不存在时**要报错**，不能静默退回默认模型。

    静默退回的话，用户以为子 Agent 在用 A，实际跑的是 B ——
    那种「看起来能跑但结果不对」最难查。
    """
    runtime, _ = _runtime(tmp_path, """---
name: 找不到模型
description: x
tools: fs_read
model: 这个模型不存在
max_turns: 2
---
正文
""")
    out = runtime.run_subagent("custom", "干活")
    assert out.get("ok") is False
    assert "这个模型不存在" in str(out.get("content", ""))


def test_agent_model_by_model_name(tmp_path: Path, monkeypatch):
    """也可以直接写模型名（而不只是 provider 名）。"""
    runtime, _ = _runtime(tmp_path, """---
name: 按模型名
description: x
tools: fs_read
model: small-model
max_turns: 2
---
正文
""")
    captured: list[_CapturingProvider] = []

    def _build(cfg):
        p = _CapturingProvider(cfg)
        captured.append(p)
        return p

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", _build)
    out = runtime.run_subagent("custom", "干活")
    assert out.get("ok") is True, out
    assert captured[0].cfg.get("name") == "cheap"


def test_agent_model_inherit_uses_default(tmp_path: Path, monkeypatch):
    """inherit = 跟随主 Agent 的模型。"""
    runtime, _ = _runtime(tmp_path, """---
name: 跟随
description: x
tools: fs_read
model: inherit
max_turns: 2
---
正文
""")
    captured: list[_CapturingProvider] = []

    def _build(cfg):
        p = _CapturingProvider(cfg)
        captured.append(p)
        return p

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", _build)
    runtime.run_subagent("custom", "干活")
    assert captured[0].cfg.get("name") == "main"


# ------------------------------------------------------------------ skills


def test_agent_skills_are_injected(tmp_path: Path, monkeypatch):
    """skills 字段要把技能正文真的喂进提示词。

    以前 skills 解析了但从不用 —— Agent 声称「掌握某技能」，
    实际提示词里一个字都没有。
    """
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 一个技能\n---\n第一步：先做这个。\n",
        encoding="utf-8",
    )

    runtime, _ = _runtime(tmp_path, """---
name: 会技能
description: x
tools: fs_read
skills: my-skill
max_turns: 2
---
正文
""")
    # 把技能目录挂到这个 runtime 上
    from oryx.skills import SkillLibrary

    runtime.skills = SkillLibrary(tmp_path / "skills")

    captured: list[_CapturingProvider] = []

    def _build(cfg):
        p = _CapturingProvider(cfg)
        captured.append(p)
        return p

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", _build)
    out = runtime.run_subagent("custom", "干活")
    assert out.get("ok") is True, out
    system = captured[0].systems[0]
    assert "my-skill" in system
    assert "第一步：先做这个" in system, "技能的正文没有注入提示词"


def test_agent_missing_skill_is_noted(tmp_path: Path, monkeypatch):
    """技能找不到时要说明，不能当作有。"""
    runtime, _ = _runtime(tmp_path, """---
name: 缺技能
description: x
tools: fs_read
skills: 不存在的技能
max_turns: 2
---
正文
""")
    captured: list[_CapturingProvider] = []

    def _build(cfg):
        p = _CapturingProvider(cfg)
        captured.append(p)
        return p

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", _build)
    runtime.run_subagent("custom", "干活")
    system = captured[0].systems[0]
    assert "未找到" in system


# ------------------------------------------------------------------ permissionMode


def test_permission_mode_ask_blocks_destructive(tmp_path: Path, monkeypatch):
    """permissionMode=ask 的子 Agent 不能自己做 L2 操作。

    这是**收紧**闸门：主 Agent 在 craft 模式时，子 Agent 声明 ask 仍然生效。
    """
    target = tmp_path / "x.txt"
    target.write_text("x", encoding="utf-8")

    runtime, _ = _runtime(tmp_path, """---
name: 谨慎的
description: x
tools: fs_read, fs_delete
permissionMode: ask
max_turns: 3
---
正文
""")
    calls: list[tuple[str, dict]] = []

    class _ToolProvider:
        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            from oryx.providers import ProviderReply, ToolCall

            if self.done:
                return ProviderReply(text="好了", tool_calls=[], stop_reason="end_turn")
            self.done = True
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id="c1", name="fs_delete",
                                    arguments={"path": str(target)})],
                stop_reason="tool_use",
            )

        done = False

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", lambda cfg: _ToolProvider())
    out = runtime.run_subagent("custom", "删掉那个文件")
    # 关键：文件**没被删** —— 权限基线挡住了
    assert target.exists(), "permissionMode=ask 的子 Agent 不该自己做删除"


def test_permission_mode_ask_allows_read(tmp_path: Path, monkeypatch):
    """permissionMode=ask 不影响只读操作。"""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    runtime, _ = _runtime(tmp_path, """---
name: 谨慎的
description: x
tools: fs_read
permissionMode: ask
max_turns: 3
---
正文
""")
    class _ReadProvider:
        done = False

        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            from oryx.providers import ProviderReply, ToolCall

            if self.done:
                return ProviderReply(text="读完了", tool_calls=[], stop_reason="end_turn")
            self.done = True
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id="c1", name="fs_read",
                                    arguments={"path": str(tmp_path / "a.txt")})],
                stop_reason="tool_use",
            )

    import oryx.loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", lambda cfg: _ReadProvider())
    out = runtime.run_subagent("custom", "读一下")
    assert out.get("ok") is True
