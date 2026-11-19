"""生命周期 hook：7 个切点的注册、分发与否决。"""

from __future__ import annotations

import time

from oryx.hooks import (
    HOOK_NAMES,
    ON_END,
    ON_HANDOFF,
    ON_LLM_END,
    ON_LLM_START,
    ON_START,
    ON_TOOL_END,
    ON_TOOL_START,
    VETOABLE,
    HookBus,
    builtin_hooks,
)


def test_hook_names_match_marvis_seven():
    assert HOOK_NAMES == (
        "on_start", "on_end", "on_handoff",
        "on_tool_start", "on_tool_end",
        "on_llm_start", "on_llm_end",
    ), "hook 集必须与 Marvis 的 7 个切点一一对应"
    assert VETOABLE == frozenset({ON_TOOL_START, ON_LLM_START})


def test_register_and_emit_in_order():
    bus = HookBus()
    seen: list[str] = []
    bus.on(ON_START, lambda **kw: seen.append("a"), owner="t")
    bus.on(ON_START, lambda **kw: seen.append("b"), owner="t")

    bus.start(task_id="x")

    assert seen == ["a", "b"], "同一 hook 的多个回调按注册顺序跑"
    assert bus.count(ON_START) == 2


def test_unknown_hook_name_rejected():
    bus = HookBus()
    try:
        bus.on("on_nope", lambda **kw: None)
    except ValueError as exc:
        assert "on_nope" in str(exc)
    else:
        raise AssertionError("未知 hook 名必须报错")


def test_hook_exception_is_swallowed_and_recorded():
    bus = HookBus()
    calls: list[int] = []

    def boom(**kw):
        raise RuntimeError("hook 坏了")

    bus.on(ON_END, boom, owner="bad")
    bus.on(ON_END, lambda **kw: calls.append(1), owner="good")

    out = bus.end(task_id="t")

    assert calls == [1], "hook 抛异常不能阻断后续回调"
    assert out["errors"] and "bad" in out["errors"][0]


def test_veto_hook_blocks_and_carries_reason():
    bus = HookBus()
    bus.on(ON_TOOL_START, lambda **kw: False, owner="deny")

    out = bus.tool_start(task_id="t", step_id="s1", tool="shell_sandbox", reason="命中限流")

    assert out["veto"] is True
    assert out["reason"] == "命中限流"


def test_non_veto_hook_ignores_false_return():
    bus = HookBus()
    bus.on(ON_TOOL_END, lambda **kw: False, owner="noop")

    assert bus.tool_end(tool="fs_read")["veto"] is False, "on_tool_end 不参与否决"


def test_off_removes_only_that_owner():
    bus = HookBus()
    hits: list[str] = []
    bus.on(ON_START, lambda **kw: hits.append("a"), owner="A")
    bus.on(ON_START, lambda **kw: hits.append("b"), owner="B")

    assert bus.off(ON_START, "A") is True
    bus.start(task_id="t")

    assert hits == ["b"], "off 只摘自己那个 owner 的回调"
    assert bus.off(ON_START, "A") is False


def test_builtin_hooks_report_three_dimensions():
    bus = HookBus()
    metrics: list[dict] = []
    builtin_hooks(bus, on_metrics=metrics.append)

    bus.start(task_id="t1")
    time.sleep(0.01)
    bus.llm_start(task_id="t1", turn=0, model="m1")
    time.sleep(0.01)
    bus.llm_end(task_id="t1", turn=0, model="m1", input_tokens=10, output_tokens=4)
    bus.tool_start(task_id="t1", step_id="s1", tool="fs_read")
    bus.tool_end(task_id="t1", step_id="s1", tool="fs_read", ok=True)
    bus.end(task_id="t1", status="done")

    kinds = {m["kind"] for m in metrics}
    assert kinds == {"run", "llm", "tool"}, "三个维度都要出指标"
    # start/end 是两个不同的 dict，配对必须靠业务标识而不是对象地址
    llm = next(m for m in metrics if m["kind"] == "llm")
    assert llm["elapsed_ms"] > 0, "llm 耗时要真配对到，不能恒为 0"
    assert llm["input_tokens"] == 10 and llm["output_tokens"] == 4
    run = next(m for m in metrics if m["kind"] == "run")
    assert run["elapsed_ms"] > 0 and run["status"] == "done"
    tool_metric = next(m for m in metrics if m["kind"] == "tool")
    assert tool_metric["name"] == "fs_read" and tool_metric["ok"] is True


def test_builtin_hooks_do_not_veto():
    bus = HookBus()
    builtin_hooks(bus)
    # 内置回调都返回 None，不该拦下任何调用
    assert bus.tool_start(tool="x", step_id="s")["veto"] is False
    assert bus.llm_start(model="m")["veto"] is False


def test_veto_accepts_tuple_with_reason():
    bus = HookBus()
    bus.on(ON_LLM_START, lambda **kw: (False, "本轮预算超了"), owner="budget")

    out = bus.llm_start(model="m")

    assert out["veto"] is True
    assert out["reason"] == "本轮预算超了"


def test_builtin_and_user_hooks_coexist():
    """内置指标 hook 与业务 hook 互不干扰：用户能否决，内置只记录。"""
    bus = HookBus()
    metrics: list[dict] = []
    builtin_hooks(bus, on_metrics=metrics.append)
    bus.on(ON_TOOL_START, lambda **kw: (False, "禁止"), owner="deny")

    out = bus.tool_start(task_id="t", step_id="s", tool="x")

    assert out["veto"] is True and out["reason"] == "禁止"
    assert bus.count(ON_TOOL_START) == 2, "两个 owner 都注册上了"
    bus.tool_end(task_id="t", step_id="s", tool="x", ok=False)
    assert metrics and metrics[-1]["elapsed_ms"] >= 0


def test_handoff_passes_target_and_kind():
    bus = HookBus()
    got: list[dict] = []
    bus.on(ON_HANDOFF, lambda **kw: got.append(kw), owner="spy")

    bus.handoff(kind="team", target="researcher", team="delivery", goal="调研")

    assert got[0]["kind"] == "team" and got[0]["target"] == "researcher"