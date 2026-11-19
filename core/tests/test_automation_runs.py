"""定时任务的执行记录与失败通知。

以前 automations 表只记 last_run_at（最后一次什么时候跑的），
「那次到底生成了什么、成没成」完全查不到；失败还被
`except Exception: pass` 静默吞掉 —— 用户不打开界面就完全错过。

这组测试锁住三件事：
1. 每次执行都留一条独立记录（含产出与失败原因）；
2. 失败**不再被吞掉**；
3. 失败会走通知渠道。
"""

from __future__ import annotations

import time
from pathlib import Path

from oryx.automation import Automation, AutomationScheduler
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store


# ------------------------------------------------------------------ 存储层


def test_run_record_is_written(tmp_path: Path):
    """开始与结束各写一次，构成一条完整记录。"""
    store = Store(tmp_path / "oryx.db")
    store.start_automation_run("r1", "a1", "每日检查", "看看有什么新消息", time.time())
    time.sleep(0.01)
    store.finish_automation_run("r1", "done", output="检查完毕，没有新消息", task_id="t1")

    rows = store.automation_runs("a1")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "done"
    assert "没有新消息" in row["output"]
    assert row["task_id"] == "t1"
    assert row["duration_ms"] >= 0


def test_failed_run_records_reason(tmp_path: Path):
    """失败的执行要记下失败原因。"""
    store = Store(tmp_path / "oryx.db")
    store.start_automation_run("r2", "a2", "会失败的任务", "跑一下", time.time())
    store.finish_automation_run("r2", "failed", error="ValueError: 模型没配好")

    row = store.automation_run_detail("r2")
    assert row is not None
    assert row["status"] == "failed"
    assert "模型没配好" in row["error"]


def test_running_state_is_visible(tmp_path: Path):
    """执行中的记录要能看见 —— 任务卡死时用户才知道少了什么。

    先写 running 再结束：中间那一段就是「任务卡住」的现场。
    """
    store = Store(tmp_path / "oryx.db")
    store.start_automation_run("r3", "a3", "卡住的任务", "跑一下", time.time())

    rows = store.automation_runs("a3")
    assert len(rows) == 1
    assert rows[0]["status"] == "running"


def test_runs_are_ordered_newest_first(tmp_path: Path):
    """执行记录按时间倒序 —— 最近的在最前面。"""
    store = Store(tmp_path / "oryx.db")
    for i in range(3):
        store.start_automation_run(f"r{i}", "a", "任务", "目标", time.time() + i)
    rows = store.automation_runs("a")
    assert len(rows) == 3
    assert rows[0]["id"] == "r2"


def test_runs_filtered_by_automation(tmp_path: Path):
    """按任务 id 过滤 —— 不同定时任务的记录不能混在一起。"""
    store = Store(tmp_path / "oryx.db")
    store.start_automation_run("x1", "aa", "A", "目标", time.time())
    store.start_automation_run("x2", "bb", "B", "目标", time.time())
    assert len(store.automation_runs("aa")) == 1
    assert len(store.automation_runs("bb")) == 1
    assert len(store.automation_runs()) == 2


# ------------------------------------------------------------------ 调度器


def test_scheduler_does_not_swallow_failure():
    """触发失败**不能被静默吞掉** —— 要留下痕迹并通知出去。

    以前这里是 `except Exception: pass`：定时任务挂了没人知道，
    用户不打开界面就完全错过。

    这里直接验 _tick 里那条路径：让 on_fire 抛异常，
    断言 last_error 有内容、on_error 被调用。
    """
    errors: list[tuple[str, str]] = []

    def boom(item):
        raise RuntimeError("炸了")

    scheduler = AutomationScheduler(
        boom,
        on_error=lambda item, err: errors.append((item.id, err)),
    )
    scheduler.add("会炸的", "* * * * *", "跑一下")

    # _tick 有两个跳过条件：同一分钟不重复触发、秒数 >5 不补跑。
    # 前者靠 _last_tick 绕，后者是「现在」的秒数 —— 直接调触发逻辑更直接。
    item = next(iter(scheduler.items.values()))
    scheduler._run_item(item)

    assert scheduler.last_error, "失败被吞掉了，没有留下任何痕迹"
    assert "炸了" in scheduler.last_error
    assert errors, "on_error 没有被调用"
    assert errors[0][1] == scheduler.last_error


# ------------------------------------------------------------------ 接线


def _make_runtime(tmp_path: Path):
    """造一个 runtime，并把 provider 钉死。

    为什么必须显式钉：`loop_mod.build_provider` 是**模块级全局**，
    别的测试（test_modes / test_autoclass 等）会直接替换它且从不还原。
    不钉的话，本组用例会继承上一个测试留下的假 provider ——
    那个 provider 会去调 L2 工具，于是卡在 300 秒的审批等待上
    （APPROVAL_TIMEOUT），单测从 1 秒变成 5 分钟。

    这里用「一句话收尾、不调任何工具」的 provider：
    定时任务要验的是执行记录与失败通知，不需要模型真的干活。
    """
    from oryx import loop as loop_mod
    from oryx.providers import ProviderReply

    class _QuietProvider:
        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            return ProviderReply(text="已完成", tool_calls=[], stop_reason="end_turn")

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    loop_mod.build_provider = lambda cfg: _QuietProvider()  # type: ignore[assignment]
    return runtime, store, bus


def test_fire_writes_execution_record(tmp_path: Path):
    """跑一次定时任务，执行记录要落库。"""
    runtime, store, bus = _make_runtime(tmp_path)
    item = runtime.scheduler.add("每日检查", "0 9 * * *", "看看有什么新消息")

    runtime.scheduler.on_fire(item)

    rows = store.automation_runs(item.id)
    assert rows, "没有留下执行记录"
    assert rows[0]["status"] in ("done", "failed")
    assert rows[0]["name"] == "每日检查"


def test_fire_failure_is_recorded_and_notified(tmp_path: Path, monkeypatch):
    """失败要落库**并且**发通知。"""
    runtime, store, bus = _make_runtime(tmp_path)

    sent: list[tuple[str, str, str]] = []

    def fake_notify(title, message, method="toast", webhook=""):
        sent.append((title, message, method))
        return {"ok": True, "content": "已发送"}

    import oryx.terminal as terminal_mod

    monkeypatch.setattr(terminal_mod, "notify", fake_notify)

    item = runtime.scheduler.add("会失败的", "0 9 * * *", "跑一下")

    def boom(goal, **kwargs):
        raise RuntimeError("模型没配好")

    monkeypatch.setattr(runtime, "run", boom)
    runtime.scheduler.on_fire(item)

    rows = store.automation_runs(item.id)
    assert rows and rows[0]["status"] == "failed"
    assert "模型没配好" in rows[0]["error"]
    assert sent, "失败没有发通知"
    assert "定时任务" in sent[0][1]


def test_automation_runs_tool_is_registered(tmp_path: Path):
    """查询工具要注册进注册表 —— 模型才能自己查。"""
    runtime, store, bus = _make_runtime(tmp_path)
    assert "automation_runs" in runtime.registry
    assert runtime.registry["automation_runs"].risk.label == "只读"


def test_automation_runs_tool_returns_records(tmp_path: Path):
    """工具能查出记录。"""
    runtime, store, bus = _make_runtime(tmp_path)
    item = runtime.scheduler.add("每日检查", "0 9 * * *", "看看有什么新消息")
    runtime.scheduler.on_fire(item)

    result = runtime.registry["automation_runs"].fn()
    assert result["ok"] is True
    assert result["meta"]["count"] >= 1
