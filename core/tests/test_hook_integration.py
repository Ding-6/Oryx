"""Hook 在真实 run 里的触发：7 个切点全覆盖 + veto 能拦住执行。"""

from __future__ import annotations

from pathlib import Path

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.hooks import (
    ON_END,
    ON_HANDOFF,
    ON_LLM_END,
    ON_LLM_START,
    ON_START,
    ON_TOOL_END,
    ON_TOOL_START,
)
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store


class ScriptedProvider:
    """按脚本回放：先要一次 fs_list，再给最终文本。"""

    def __init__(self, script):
        self.script = list(script)
        self.index = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
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


def make_runtime(tmp_path: Path, script):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    data["interaction"] = "craft"
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = ScriptedProvider(script)
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    return runtime, store, bus


def test_all_hooks_fire_during_a_real_run(tmp_path):
    runtime, store, bus = make_runtime(tmp_path, [("fs_list", {"path": str(tmp_path)}), (None, {})])

    fired: dict[str, list[dict]] = {}
    for name in (ON_START, ON_END, ON_HANDOFF, ON_TOOL_START, ON_TOOL_END, ON_LLM_START, ON_LLM_END):
        fired[name] = []
        runtime.hooks.on(name, (lambda n: lambda **kw: fired[n].append(kw))(name), owner="spy")

    events: list[dict] = []
    bus.add_sync_sink(events.append)

    runtime.run("看看目录里有什么")

    for name in (ON_START, ON_END, ON_TOOL_START, ON_TOOL_END, ON_LLM_START, ON_LLM_END):
        assert fired[name], f"{name} 在真实 run 里必须触发"

    assert fired[ON_START][0]["task_id"] == store.recent_tasks()[0]["id"]
    assert fired[ON_END][0]["status"] == "done"
    assert fired[ON_TOOL_START][0]["tool"] == "fs_list"
    assert fired[ON_TOOL_END][0]["ok"] is True
    assert fired[ON_LLM_START][0]["turn"] == 0 and fired[ON_LLM_END][0]["turn"] == 0
    # 内置耗时统计把结果发成 hook.metrics 事件
    assert any(e["type"] == "hook.metrics" for e in events), "内置 hook 必须外发指标事件"


def test_handoff_fires_when_spawning_subagent(tmp_path):
    runtime, _store, _bus = make_runtime(tmp_path, [(None, {})])

    seen: list[dict] = []
    runtime.hooks.on(ON_HANDOFF, lambda **kw: seen.append(kw), owner="spy")

    runtime.run_subagent("doc-manager", "记一条需求")

    assert seen and seen[0]["kind"] == "subagent"
    assert seen[0]["target"] == "doc-manager"


def test_tool_hook_can_veto_execution(tmp_path):
    runtime, store, bus = make_runtime(tmp_path, [("fs_list", {"path": str(tmp_path)}), (None, {})])
    # 工具一旦跑过就会建出标记文件，用它证明工具确实没被调用
    marker = tmp_path / "fs_list_ran.txt"

    runtime.hooks.on(
        ON_TOOL_START,
        lambda **kw: (False, "本轮禁止访问文件系统") if kw.get("tool") == "fs_list" else True,
        owner="deny",
    )

    events: list[dict] = []
    bus.add_sync_sink(events.append)
    runtime.run("看看目录里有什么")

    denied = [e for e in events if e["type"] == "step.denied"]
    assert denied, "被 hook 否决的工具必须发 step.denied"
    assert denied[0]["tool"] == "fs_list"
    assert not marker.exists()
    audit = store.audit_for_task(store.recent_tasks()[0]["id"])
    assert any(a["decision"] == "hook_denied" for a in audit), "否决要留审计"


def test_veto_reason_surfaces_to_the_model(tmp_path):
    runtime, _store, _bus = make_runtime(tmp_path, [(None, {})])

    runtime.hooks.on(
        ON_TOOL_START,
        lambda **kw: (False, "本轮禁止访问文件系统") if kw.get("tool") == "fs_list" else True,
        owner="deny",
    )
    runtime._active_task_id = "task-x"
    runtime._active_interaction = "craft"

    out = runtime._handle_tool_call(
        "task-x", 1, ToolCall(id="c1", name="fs_list", arguments={"path": str(tmp_path)})
    )

    assert out["is_error"] is True
    assert "本轮禁止访问文件系统" in out["content"]
    assert "不要重试" in out["content"], "要明确告诉模型别重试，否则会死循环"


def test_bare_false_falls_back_to_caller_reason(tmp_path):
    """回调只想否决、不想解释时，用调用方给的 reason 兜底。"""
    runtime, _store, _bus = make_runtime(tmp_path, [(None, {})])
    runtime.hooks.on(ON_TOOL_START, lambda **kw: False, owner="deny")
    runtime._active_task_id = "task-x"
    runtime._active_interaction = "craft"

    out = runtime._handle_tool_call(
        "task-x", 1, ToolCall(id="c1", name="fs_list", arguments={"path": str(tmp_path)})
    )

    assert out["is_error"] is True
    assert "hook 拒绝" in out["content"]