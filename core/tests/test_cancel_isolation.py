"""模块 11：并发运行的停止隔离。

取证结论（为什么这一轮必须做）
------------------------------
阶段一体检把「并发运行的停止隔离」列为待确认 #10。现在能确认它是
**真缺陷**，而且实测复现了：

    A、B 两轮并发，各记下自己的中止代号 0
    用户停止 A → 全局代号 +1 → 变成 1
    B 查自己的代号（0）发现「变了」→ **B 也被当成被停止**

原因：中止信号是**全局共享**的（一个 `threading.Event` + 一个全局
代号计数器），不是「每个运行一份」。

这个文件钉住修好之后的不变量：

  1. 停止 A，A 停、B 不停；
  2. 停止 A 不会把 B 的审批等待一起打断；
  3. `cancel(task_id=…)` 能精确定位到目标运行（不再被单槽顶掉）；
  4. `is_task_running` 在并发下对每个会话都准（检查点回退的保护）；
  5. 两轮各自的中止信号互不污染（代号不串）。

同时也钉住**不能被改坏的老语义**：
  · 停止先于发起到达，仍然算数；
  · 在旧任务上误点的停止，不影响下一轮；
  · 审批等待仍能被立刻打断。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from test_cancel import SlowProvider, make_runtime  # noqa: E402


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """等条件成立。返回是否在超时前成立。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestConcurrentCancelIsolation:
    def test_stopping_a_does_not_stop_b(self, tmp_path):
        """**核心不变量**：停止 A，B 不受影响。

        修之前的症状：全局代号 +1 让 B 也判定成「被停止」，
        B 的任务被标成 cancelled，用户会发现自己的另一个会话莫名停了。
        """
        runtime, store, provider, bus = make_runtime(tmp_path)
        task_a = store.create_task("会话 A", "efficiency", "test")
        task_b = store.create_task("会话 B", "efficiency", "test")

        # 两轮**各用各的 provider** —— 共用一个的话 `calls` 计数会互相干扰，
        # 测出来的失败原因不是「被误停」，而是测试自己的噪声。
        provider_a = SlowProvider(rounds=30, delay=0.02, path=str(tmp_path))
        provider_b = SlowProvider(rounds=30, delay=0.02, path=str(tmp_path))
        results: dict[str, str] = {}

        def run_a():
            import oryx.loop as loop_mod
            loop_mod.build_provider = lambda cfg: provider_a  # type: ignore[assignment]
            results["a"] = runtime.run("会话 A", session_id=task_a)

        def run_b():
            import oryx.loop as loop_mod
            loop_mod.build_provider = lambda cfg: provider_b  # type: ignore[assignment]
            results["b"] = runtime.run("会话 B", session_id=task_b)

        ta = threading.Thread(target=run_a, daemon=True)
        tb = threading.Thread(target=run_b, daemon=True)
        ta.start()
        tb.start()

        # 两轮都在跑
        assert _wait_until(lambda: len(runtime._runs) >= 2), "两轮并发没起来"
        assert _wait_until(lambda: runtime.is_task_running(task_a))
        assert _wait_until(lambda: runtime.is_task_running(task_b))

        # 停止 A（此时 B 也正在跑 —— 这就是串扰的窗口）
        out = runtime.cancel(task_id=task_a, owner_id=None)
        assert out["ok"] is True

        ta.join(timeout=20)
        tb.join(timeout=20)
        assert not ta.is_alive(), "A 没停下来"
        assert not tb.is_alive(), "B 没停下来"

        status_a = store.get_task(task_a)["status"]
        status_b = store.get_task(task_b)["status"]
        assert status_a == "cancelled", f"A 的状态是 {status_a}"
        assert status_b == "done", (
            f"B 被误停了 —— 状态是 {status_b}（这正是模块 11 要修的串扰）"
        )
        # B 必须**跑完自己的轮数**才结束 —— 被提前打断的话状态会是 cancelled，
        # 上面那条断言已经覆盖；这里再确认它确实跑到了收尾。
        assert provider_b.calls >= 3, (
            f"B 只调了 {provider_b.calls} 次模型 —— 它没跑完就被打断了"
        )

    def test_cancel_generations_do_not_cross(self, tmp_path):
        """两轮各持一份中止信号：代号互不影响。

        修之前是**一个全局计数器** —— 停止 A 会让 B 的代号也变。
        """
        runtime, store, *_ = make_runtime(tmp_path)
        runtime.mark_run_pending(task_id="task-a", owner_id="alice")
        runtime.mark_run_pending(task_id="task-b", owner_id="bob")

        aborts = {slot.task_id: slot.abort for slot in runtime._runs.values()}
        assert set(aborts) == {"task-a", "task-b"}

        # 停止 A
        assert runtime.cancel(task_id="task-a", owner_id="alice")["ok"] is True

        assert aborts["task-a"].generation == 1, "A 的代号应该 +1"
        assert aborts["task-b"].generation == 0, "B 的代号不该被动到"

        # 停止 B
        assert runtime.cancel(task_id="task-b", owner_id="bob")["ok"] is True
        assert aborts["task-a"].generation == 1, "A 的代号不该再变"
        assert aborts["task-b"].generation == 1, "B 的代号应该 +1"

    def test_cancel_targets_exact_run(self, tmp_path):
        """`cancel(task_id=…)` 精确定位目标运行。

        修之前靠单槽 `_active_task_id`：两个会话并发时，
        后启动的那个把槽位顶掉，于是「停 A」找不到 A（停不掉）。
        """
        runtime, store, *_ = make_runtime(tmp_path)
        runtime.mark_run_pending(task_id="task-a", owner_id="alice")
        runtime.mark_run_pending(task_id="task-b", owner_id="alice")

        assert runtime.cancel(task_id="task-a", owner_id="alice")["ok"] is True
        aborts = {slot.task_id: slot.abort for slot in runtime._runs.values()}
        assert aborts["task-a"].generation == 1
        assert aborts["task-b"].generation == 0

    def test_cancel_does_not_touch_other_account(self, tmp_path):
        """账号不对就停不了 —— 别人的会话不能被停。"""
        runtime, store, *_ = make_runtime(tmp_path)
        runtime.mark_run_pending(task_id="task-a", owner_id="alice")

        assert runtime.cancel(task_id="task-a", owner_id="bob")["ok"] is False
        aborts = {slot.task_id: slot.abort for slot in runtime._runs.values()}
        assert aborts["task-a"].generation == 0, "账号不匹配时不该置位"

    def test_stopping_a_does_not_wake_b_approval(self, tmp_path):
        """停止 A 不该打断 B 的审批等待。

        审批等待是最长的一处阻塞（5 分钟）——
        串扰会让用户的另一个会话莫名跳出「未确认」。
        """
        runtime, store, *_ = make_runtime(tmp_path)
        results: dict[str, bool] = {}

        def wait(step, task, owner):
            results[step] = runtime.wait_approval(step, timeout=30, task_id=task, owner_id=owner)

        threads = {}
        for step, task, owner in (("step_a", "task-a", "alice"), ("step_b", "task-b", "bob")):
            threads[step] = threading.Thread(target=wait, args=(step, task, owner), daemon=True)
            threads[step].start()
        assert _wait_until(lambda: len(runtime._approvals) == 2), "两个审批等待没登记上"

        # A 有运行实例（让 cancel 走「停运行」分支而不是「唤醒审批」分支）
        runtime.mark_run_pending(task_id="task-a", owner_id="alice")
        assert runtime.cancel(task_id="task-a", owner_id="alice")["ok"] is True

        time.sleep(0.5)
        assert not threads["step_a"].is_alive(), "A 的审批等待该被打断"
        assert threads["step_b"].is_alive(), "B 的审批等待被误打断了"

        # 收尾：把 B 的等待停掉，别留后台线程
        runtime.resolve_approval("step_b", True)

    def test_is_task_running_is_per_session(self, tmp_path):
        """并发时 `is_task_running` 对每个会话都要准。

        这个判定是**检查点回退的保护闸门** ——
        判错的后果是「在工具正写文件时回退」，文件会被写花。
        """
        runtime, store, *_ = make_runtime(tmp_path)
        task_a = store.create_task("会话 A", "efficiency", "test")
        task_b = store.create_task("会话 B", "efficiency", "test")

        assert runtime.is_task_running(task_a) is False
        runtime.mark_run_pending(task_id=task_a, owner_id=None)
        runtime.mark_run_pending(task_id=task_b, owner_id=None)

        # 两轮都在跑 —— 单槽实现下这里会只有一个为 true
        assert runtime.is_task_running(task_a) is True
        assert runtime.is_task_running(task_b) is True
        assert runtime.is_task_running("task-c") is False

    def test_run_instance_is_released_after_run(self, tmp_path):
        """运行结束后实例要从表里注销 —— 否则停止会打到已结束的会话。"""
        provider = SlowProvider(rounds=2, delay=0.01, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑一轮", "efficiency", "test")

        runtime.run("跑一轮", session_id=task_id)
        assert store.get_task(task_id)["status"] == "done"
        assert not runtime._runs, "跑完的实例没注销"
        assert runtime.is_task_running(task_id) is False

        # 已结束的会话不该还能被停止
        assert runtime.cancel(task_id=task_id)["ok"] is False

    def test_approval_wait_still_interrupted_by_cancel(self, tmp_path):
        """老语义不能被改坏：审批等待仍能被停止立刻打断。"""
        runtime, store, *_ = make_runtime(tmp_path)
        runtime.mark_run_pending(task_id="task-a", owner_id="alice")
        result: dict[str, bool] = {}

        def waiter():
            result["approved"] = runtime.wait_approval(
                "step_x", timeout=30, task_id="task-a", owner_id="alice"
            )

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        assert _wait_until(lambda: len(runtime._approvals) == 1), "审批等待没登记上"

        assert runtime.cancel(task_id="task-a", owner_id="alice")["ok"] is True
        t.join(timeout=5)
        assert not t.is_alive(), "审批等待没被停止打断"
        assert result["approved"] is False

    def test_stale_cancel_does_not_stop_next_run(self, tmp_path):
        """老语义不能被改坏：旧任务上的误停不影响下一轮。"""
        provider = SlowProvider(rounds=2, delay=0.01, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)

        task1 = store.create_task("第一轮", "efficiency", "test")
        runtime.run("第一轮", session_id=task1)
        assert store.get_task(task1)["status"] == "done"

        # 用户在已经结束的任务上误点停止
        assert runtime.cancel(task_id=task1)["ok"] is False

        task2 = store.create_task("第二轮", "efficiency", "test")
        runtime.run("第二轮", session_id=task2)
        assert store.get_task(task2)["status"] == "done", "上一轮的误停把这一轮也停掉了"

    def test_pending_window_cancel_still_counts(self, tmp_path):
        """老语义不能被改坏：停止先于发起到达，仍然算数。

        用户点「发送」后立刻点「停止」——两条请求并发到达，
        停止请求可能先到内核。那一下必须算数。
        """
        provider = SlowProvider(rounds=20, delay=0.02, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑个长任务", "efficiency", "test")

        # 内核收到 chat.run → 登记「正要开始跑」
        runtime.mark_run_pending(task_id=task_id, owner_id=None)
        # 停止请求紧接着到达（还没进 run()）
        assert runtime.cancel(task_id=task_id)["ok"] is True

        runtime.run("跑个长任务", session_id=task_id, pending_slot=True)

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_CANCELLED" in kinds, kinds
        assert provider.calls < 20, f"跑了 {provider.calls} 轮，没停下来"
        assert store.get_task(task_id)["status"] == "cancelled"


class TestCancelHttpContract:
    """HTTP 层的停止契约 —— 域与内核之间那一段。"""

    def test_cancel_endpoint_requires_task_id_for_precision(self):
        """会话页的停止必须带 task_id（前端断言在 check-sessions.cjs）。

        不带 task_id 时，域层会退回「按 seq 猜当前那一个」——
        并发下会停错会话。新前端一律带 task_id。
        """
        from oryx.domains.chat import CancelRequest

        assert CancelRequest(task_id="t1").task_id == "t1"
        assert CancelRequest().task_id is None
        # seq 仍然保留 —— 老前端靠它对齐「提前到达」的中止请求
        assert CancelRequest(seq=7).seq == 7

    def test_cancel_run_payload_shape(self):
        """内核的中止结果要能回「停的是哪一个会话」。"""
        from oryx.domains.chat import cancel_run_payload

        out = cancel_run_payload({"ok": True, "task_id": "t1"})
        assert out == {"ok": True, "task_id": "t1"}
        assert cancel_run_payload({"ok": False}) == {"ok": False, "task_id": ""}

    def test_agentd_cancel_forwards_task_and_owner(self):
        """IPC 侧的 chat.cancel 要把 task_id 与 owner_id 原样送进去。

        丢一个字段就退化成「停当前那一个」——
        这正是模块 11 修掉的串扰路径。
        """
        import inspect

        from oryx.agentd import AgentDaemon

        sig = inspect.signature(AgentDaemon.chat_cancel)
        assert "task_id" in sig.parameters
        assert "owner_id" in sig.parameters
