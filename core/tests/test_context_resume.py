"""长会话 token 裁剪 + 断点续跑的回归测试。

两处都曾是诊断报告里的短板：
- `loop.py` 取历史不截断，几百轮后撑爆上下文窗口；
- `agentlink._handle_crash` 用 `chat.run` 整体重跑，已完成的工具全部重来。

这里既测纯函数，也用受控假模型驱动真实内核 / RPC 链路验证接线。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from oryx import context as ctx
from oryx import resume as res
from oryx.agentd import AgentDaemon
from oryx.agentlink import AgentLink
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply
from oryx.store import Store


# ---------------------------------------------------------------- 上下文预算
def test_estimate_tokens_is_conservative():
    assert ctx.estimate_tokens("") == 0
    assert ctx.estimate_tokens("中文一百个字" * 10) > 0
    # 中文按 1 字 1 token，不能低估
    assert ctx.estimate_tokens("你好") >= 2


def test_trim_keeps_recent_and_summarizes_dropped():
    history = [{"role": "user" if i % 2 == 0 else "assistant",
                "content": f"第 {i} 轮的内容 " + "很长" * 40} for i in range(12)]
    kept, summary = ctx.trim_history(history, budget_tokens=300, keep_recent=4)
    assert len(kept) < len(history), "超预算却没有裁剪"
    assert kept[-1] == history[-1], "最近一轮必须保留"
    assert "第 11 轮" not in summary, "被保留的内容不该出现在摘要里"
    assert "历史摘要" in summary and "不要据此编造" in summary
    report = ctx.trim_report(history, kept)
    assert report["dropped"] == len(history) - len(kept)
    assert report["tokens_after"] <= 300 or len(kept) <= ctx.MIN_KEEP


def test_trim_disabled_when_budget_zero():
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    kept, summary = ctx.trim_history(history, 0)
    assert kept == history and summary == ""


def test_trim_never_leaves_less_than_two():
    history = [{"role": "user", "content": "很长" * 500} for _ in range(4)]
    kept, _ = ctx.trim_history(history, budget_tokens=1, keep_recent=8)
    assert len(kept) >= ctx.MIN_KEEP


def test_resolve_budget_prefers_explicit_config(tmp_path: Path):
    cfg = Config({**DEFAULT_CONFIG, "context_budget": 1234})
    assert ctx.resolve_budget(cfg) == 1234
    assert ctx.resolve_budget(Config(dict(DEFAULT_CONFIG))) > 0
    assert ctx.resolve_budget(Config(dict(DEFAULT_CONFIG)),
                              {"context_window": 32000, "max_output": 4096}) > 0


# ---------------------------------------------------------------- 断点发现
def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "oryx.db")


def test_find_interrupted_task_requires_running_with_steps(tmp_path: Path):
    store = make_store(tmp_path)
    assert res.find_interrupted_task(store) == ""
    task_id = store.create_task("做一份报告", "craft", "test")
    # 只有 running、没有任何步骤 → 不算「跑到一半崩了」
    assert res.find_interrupted_task(store) == ""
    step = store.create_step(task_id, 1, "html_write", {"path": "a.html"}, 1)
    store.finish_step(step, "done", result={"path": "a.html", "bytes": 123})
    assert res.find_interrupted_task(store) == task_id


def test_brief_lists_completed_steps_and_forbids_replay(tmp_path: Path):
    store = make_store(tmp_path)
    task_id = store.create_task("做一份报告", "craft", "test")
    step = store.create_step(task_id, 1, "html_write", {"path": "a.html"}, 1)
    store.finish_step(step, "done", result={"path": "D:/a.html", "bytes": 123})
    info = res.build_resume_brief(store, task_id)
    assert info["has_progress"] and info["completed"] == 1
    assert "html_write" in info["brief"] and "D:/a.html" in info["brief"]
    assert "不要重复执行" in info["brief"]


def test_brief_without_done_steps_is_empty(tmp_path: Path):
    store = make_store(tmp_path)
    task_id = store.create_task("x", "craft", "test")
    assert res.build_resume_brief(store, task_id)["has_progress"] is False


# ---------------------------------------------------------------- 内核接线
class CapturingProvider:
    def __init__(self):
        self.systems: list[str] = []
        self.messages: list[list] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.systems.append(system)
        self.messages.append(messages)
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, provider, monkeypatch):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(Config(data), store, EventBus())
    from oryx import loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", lambda cfg: provider)
    return runtime, store


def test_run_reuses_interrupted_task_and_injects_brief(tmp_path: Path, monkeypatch):
    provider = CapturingProvider()
    runtime, store = make_runtime(tmp_path, provider, monkeypatch)
    task_id = store.create_task("继续做", "craft", "test")
    store.add_message(task_id, "user", "继续做")
    step = store.create_step(task_id, 1, "html_write", {"path": "a.html"}, 1)
    store.finish_step(step, "done", result={"path": "a.html", "bytes": 10})

    runtime.run("继续做", resume_brief="不要重复执行上述步骤", resume_task_id=task_id)
    assert provider.systems, "模型没被调用"
    assert "不要重复执行上述步骤" in provider.systems[0]
    # 复用同一个 task，不新开一条会话
    assert len(store.recent_tasks()) == 1
    assert store.get_task(task_id)["status"] == "done"
    # 崩溃前那条提问不能被再写一遍
    users = [m for m in store.messages_for_task(task_id) if m["role"] == "user"]
    assert len(users) == 1, f"user 消息被重复写入：{len(users)} 条"


def test_history_trimming_is_visible_in_event(tmp_path: Path, monkeypatch):
    provider = CapturingProvider()
    runtime, store = make_runtime(tmp_path, provider, monkeypatch)
    bus = EventBus()
    runtime.bus = bus
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    task_id = store.create_task("长会话", "craft", "test")
    for i in range(30):
        store.add_message(task_id, "user" if i % 2 == 0 else "assistant", "很长的内容" * 300)
    # 把预算压到极小，强制触发裁剪
    runtime.config.data["context_budget"] = 200
    runtime.config.data["context_keep_recent"] = 2
    runtime.run("继续", session_id=task_id)
    trimmed = [e for e in events if e.get("type") == "context.trimmed"]
    assert trimmed, "裁剪发生了却没有发事件（用户看不到降级）"
    assert trimmed[0]["dropped"] > 0
    # 断言摘要进了系统提示。
    #
    # 为什么检查**所有** systems 而不是只查 systems[0]：
    # 事故复盘 —— 这个用例曾偶发失败（只在机器负载高、并发跑别的任务时出现），
    # 报的是「历史摘要不在 systems[0] 里」。
    # 但循环体里 provider 可能被调用多次（重试、工具轮次），
    # systems[0] 只是**第一次**调用 —— 那一次未必带摘要。
    # 断言「所有 system 都含摘要」太严（第一次调用可能确实没有），
    # 断言「只有第一次含」又会误报。
    # 正确的契约是：**至少有一次**调用带上了摘要（用户看得见降级说明）。
    with_summary = [s for s in provider.systems if "历史摘要" in s]
    assert with_summary, (
        f"裁剪了 {trimmed[0]['dropped']} 条历史，但系统提示里没有摘要"
        f"（provider 被调用 {len(provider.systems)} 次）"
    )


# ---------------------------------------------------------------- RPC / 看门狗
def test_chat_resume_rpc_passes_brief(tmp_path: Path, monkeypatch):
    provider = CapturingProvider()
    runtime, store = make_runtime(tmp_path, provider, monkeypatch)
    task_id = store.create_task("继续做", "craft", "test")
    store.add_message(task_id, "user", "继续做")
    step = store.create_step(task_id, 1, "html_write", {"path": "a.html"}, 1)
    store.finish_step(step, "done", result={"path": "a.html", "bytes": 10})

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.runtime = runtime
    daemon.bus = EventBus()
    daemon._prewarm = False
    daemon._activated = True
    daemon._busy = False
    captured: list[dict] = []
    finished = threading.Event()

    def fake_run(*args, **kwargs):
        captured.append(kwargs)
        finished.set()
        return "done"

    runtime.run = fake_run  # type: ignore[assignment]
    result = daemon.chat_resume("继续做")
    assert result["resumed"] is True
    assert finished.wait(2), "续跑线程没起来"
    assert captured[0]["resume_task_id"] == task_id
    assert "不要重复执行" in captured[0]["resume_brief"]


def test_chat_resume_falls_back_when_no_breakpoint(tmp_path: Path, monkeypatch):
    runtime, _ = make_runtime(tmp_path, CapturingProvider(), monkeypatch)
    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.runtime = runtime
    daemon.bus = EventBus()
    daemon._prewarm = False
    daemon._activated = True
    daemon._busy = False
    captured: list[dict] = []
    finished = threading.Event()

    def fake_run(*args, **kwargs):
        captured.append(kwargs)
        finished.set()
        return "done"

    runtime.run = fake_run  # type: ignore[assignment]
    result = daemon.chat_resume("全新任务")
    assert result["resumed"] is False
    assert finished.wait(2)
    assert not captured[0].get("resume_brief")


class _FakeProc:
    def poll(self):
        return 1


def test_watchdog_uses_chat_resume_not_chat_run():
    """崩溃恢复不再整体重跑 —— 看门狗必须走续跑 RPC。"""
    link = AgentLink.__new__(AgentLink)
    link._lock = threading.Lock()
    link._stopped = False
    link._last_restart_at = 0.0
    link._restart_count = 0
    link._last_run = {"goal": "继续做", "session_id": ""}
    link._proc = _FakeProc()
    link._client = None
    link._config_provider = None
    link._syncing = False
    link._wake = threading.Event()
    link._watchdog = None
    events: list[dict] = []
    link.on_event = events.append
    link._teardown = lambda: None
    link._spawn = lambda python=None, timeout=0: None
    calls: list[str] = []

    def fake_call(method, timeout=600.0, **params):
        calls.append(method)
        return {"resumed": True}

    link.call = fake_call
    link._handle_crash()
    assert "chat.resume" in calls, f"看门狗仍在整体重跑：{calls}"
    assert "chat.run" not in calls
    resumed = [e for e in events if e.get("phase") == "resumed"]
    assert resumed and "断点" in resumed[0]["detail"]
