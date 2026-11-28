"""中止正在跑的任务。

用户点「停止」后：
  · 任务状态变 cancelled
  · 已经产出的内容保留（半个答案比没答案有用）
  · 主循环不再往下跑
  · 审批等待能被立刻打断（不然点了没反应 —— 任务还杵在等确认上）
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store


class SlowProvider:
    """每轮都要等一下 —— 给 cancel 留出打断的窗口。"""

    def __init__(self, rounds: int = 5, delay: float = 0.02, path: str = "."):
        self.rounds = rounds
        self.delay = delay
        self.calls = 0
        # 工具的路径参数必须在授权目录内。
        # 传 "." 会解析到**进程 cwd**（不在 allowed_roots 里）→ 升到 L2 →
        # 触发审批等待（默认 300 秒），整个测试会卡十分钟。
        self.path = path

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.calls += 1
        time.sleep(self.delay)
        if self.calls >= self.rounds:
            return ProviderReply(text="做完了", tool_calls=[], stop_reason="end_turn")
        # 每轮都发一个工具调用，逼着主循环继续下一轮
        return ProviderReply(
            text="",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="fs_list", arguments={"path": self.path})],
            stop_reason="tool_use",
        )


class OneShotProvider:
    """一轮就返回（有正文、没工具调用）。

    用来验「模型秒回」场景下的取消 —— 这条路径会直接 break 出去，
    绕过了「每轮开头」的取消检查。

    delay 要**足够长**（比取消时刻晚不少）——
    取消必须发生在**模型调用期间**，才测得到「轮末」那次检查。
    取消发生在模型调用之前的话，轮首那次检查就命中了，压根走不到轮末。
    """

    def __init__(self, delay: float = 0.3, path: str = "."):
        self.delay = delay
        self.calls = 0
        self.path = path

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.calls += 1
        time.sleep(self.delay)
        return ProviderReply(text="故事讲完了", tool_calls=[], stop_reason="end_turn")


class EmptyReplyProvider:
    """返回**空**回复（一轮就结束，且没有任何内容块）。

    覆盖的是另一条收尾路径：模型既没说话也没调工具 → assistant_blocks 为空
    → 走 `if not assistant_blocks: break` 那一支。
    两条路径都得查停止标志，漏一条就有一种「按了停止没反应」的场景。

    delay 要**足够长**（比取消时刻晚不少）——
    取消必须发生在**模型调用期间**，才测得到「轮末」那次检查。
    取消发生在模型调用之前的话，轮首那次检查就命中了，压根走不到轮末。
    """

    def __init__(self, delay: float = 0.3):
        self.delay = delay
        self.calls = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.calls += 1
        time.sleep(self.delay)
        return ProviderReply(text="", tool_calls=[], stop_reason="end_turn")


class EndlessStreamProvider:
    """**永远吐不完**的模型 —— 模拟 Base 模型不会终止生成。

    每一段之间睡 0.05 秒，一共 200 段（够跑 10 秒）。
    中止标志如果在流式回调里查，几十毫秒就能打断；
    以前只在轮首/轮末查的话，这一整轮要等 10 秒才跑完 ——
    症状是「点了停止，界面一直转圈，任务状态永远停在 running」。
    """

    def __init__(self, segments: int = 200, delay: float = 0.05):
        self.segments = segments
        self.delay = delay
        self.calls = 0
        self.sent = 0
        # 「已经吐过至少一个 chunk」的信号。
        # 测试用它做**确定性**的取消时机：等模型真的开始吐了再点停止，
        # 而不是靠固定 sleep 猜时间（见 test_cancel_keeps_partial_stream_text）。
        self.first_chunk = threading.Event()

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.calls += 1
        for i in range(self.segments):
            if on_text:
                on_text(f"第{i}段 ")
                self.first_chunk.set()
            self.sent += 1
            time.sleep(self.delay)
        return ProviderReply(text="", tool_calls=[], stop_reason="end_turn")


class ReasoningOnlyProvider:
    """只吐思考过程、不吐正文。

    覆盖「思考通道里点停止」这一路 ——
    本机挂的 qwen3 系列先吐一大段 reasoning，那段期间点停止也要能打断。
    """

    def __init__(self, segments: int = 200, delay: float = 0.05):
        self.segments = segments
        self.delay = delay
        self.calls = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096,
             on_reasoning=None):
        self.calls += 1
        for i in range(self.segments):
            if on_reasoning:
                on_reasoning(f"想{i} ")
            time.sleep(self.delay)
        return ProviderReply(text="", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, provider=None):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    data["interaction"] = "craft"
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    prov = provider or SlowProvider(path=str(tmp_path))
    loop_mod.build_provider = lambda cfg: prov  # type: ignore[assignment]
    return runtime, store, prov, bus


class TestCancel:
    def test_cancel_without_task_fails(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        out = runtime.cancel()
        assert out["ok"] is False
        assert "没有正在跑" in out["error"]

    def test_cancel_sets_flag(self, tmp_path):
        runtime, *_ = make_runtime(tmp_path)
        # 「有任务在跑」用 _run_pending / _run_in_flight 判定，
        # 不用 _active_task_id —— 后者是「当前/最近一个任务」的句柄，
        # 跑完从不清，拿它当忙碌标志会让定时任务永远排不上队。
        runtime.mark_run_pending()
        out = runtime.cancel()
        assert out["ok"] is True
        # 模块 11：中止信号改成**每个运行实例一份**，不再是全局 Event。
        # 断言的是「目标实例的那一份被置位」。
        assert runtime._only_run_abort().event.is_set()

    def test_cancel_targets_task_and_owner(self, tmp_path):
        """会话页停止按钮只能停止匹配当前会话和账号的运行。"""
        runtime, store, *_ = make_runtime(tmp_path)
        task_id = store.create_task(
            "只停止自己的任务", "efficiency", "test", owner_id="alice"
        )
        runtime.mark_run_pending(task_id=task_id, owner_id="alice")

        assert runtime.cancel(task_id="another-task", owner_id="alice")["ok"] is False
        assert runtime.cancel(task_id=task_id, owner_id="bob")["ok"] is False
        assert not runtime._only_run_abort().event.is_set(), (
            "不匹配的停止请求不能触碰运行标志"
        )

        assert runtime.cancel(task_id=task_id, owner_id="alice")["ok"] is True
        runtime.run(
            "只停止自己的任务", session_id=task_id,
            pending_slot=True, owner_id="alice",
        )
        assert store.get_task(task_id, "alice")["status"] == "cancelled"

    def test_run_stops_after_cancel(self, tmp_path):
        """点了停止，主循环就该在下一轮收手。"""
        provider = SlowProvider(rounds=20, delay=0.02, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑个长任务", "efficiency", "test")

        # 跑起来之后立刻叫停
        def stop_soon():
            time.sleep(0.08)
            runtime.cancel()

        threading.Thread(target=stop_soon, daemon=True).start()
        runtime.run("跑个长任务", session_id=task_id)

        # 不该跑满 20 轮
        assert provider.calls < 20, f"跑了 {provider.calls} 轮，没停下来"
        assert store.get_task(task_id)["status"] == "cancelled"

    def test_cancel_keeps_partial_output(self, tmp_path):
        """已经产出的内容要保留 —— 半个答案比没答案有用。"""
        provider = SlowProvider(rounds=20, delay=0.02, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑个长任务", "efficiency", "test")

        def stop_soon():
            time.sleep(0.08)
            runtime.cancel()

        threading.Thread(target=stop_soon, daemon=True).start()
        runtime.run("跑个长任务", session_id=task_id)

        msgs = store.messages_for_task(task_id)
        # 至少有一条用户消息（提问本身留着了）
        assert any(m["role"] == "user" for m in msgs)

    def test_cancel_publishes_event(self, tmp_path):
        """前端靠这个事件把「停止」按钮变回发送键。"""
        provider = SlowProvider(rounds=20, delay=0.02, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑个长任务", "efficiency", "test")

        def stop_soon():
            time.sleep(0.08)
            runtime.cancel()

        threading.Thread(target=stop_soon, daemon=True).start()
        runtime.run("跑个长任务", session_id=task_id)

        events = list(bus._buffer)
        kinds = [e.get("type") for e in events]
        # 必须是 AG-UI 家族的大写名 —— 前端 case 'RUN_CANCELLED' 精确匹配，
        # 写成小写就等于没接这个事件
        assert "RUN_CANCELLED" in kinds, kinds

    def test_new_run_clears_cancel_flag(self, tmp_path):
        """上一轮留下的中止标志不能影响下一轮 —— 否则第二次发消息就秒停。"""
        runtime, store, provider, bus = make_runtime(tmp_path)
        # 模块 11：中止信号是**每个运行实例一份**的，
        # 上一轮的信号随实例注销，不会漏到下一轮。
        # 这里模拟「上一轮留下了一个被置位的信号」。
        runtime.mark_run_pending()
        runtime._only_run_abort().event.set()
        task_id = store.create_task("再跑一次", "efficiency", "test")
        runtime.run("再跑一次", session_id=task_id)
        assert store.get_task(task_id)["status"] == "done"

    def test_approval_wait_interrupted_by_cancel(self, tmp_path):
        """审批是最长的等待 —— 点了停止要能立刻打断。"""
        runtime, *_ = make_runtime(tmp_path)
        result = {}

        def waiter():
            result["approved"] = runtime.wait_approval("step_x", timeout=30)

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.2)
        runtime.cancel()
        t.join(timeout=5)
        assert not t.is_alive(), "审批等待没被停止打断"
        assert result["approved"] is False

    def test_cancel_only_wakes_matching_approval(self, tmp_path):
        """停止只认「同一个任务、同一个账号」的审批等待，别人的不能碰。"""
        runtime, *_ = make_runtime(tmp_path)
        results = {}

        def wait(step, task, owner):
            results[step] = runtime.wait_approval(step, timeout=30, task_id=task, owner_id=owner)

        threads = {}
        for step, task, owner in (("step_a", "task-a", "alice"), ("step_b", "task-b", "bob")):
            threads[step] = threading.Thread(target=wait, args=(step, task, owner), daemon=True)
            threads[step].start()
        deadline = time.time() + 5
        while len(runtime._approvals) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(runtime._approvals) == 2, "两个审批等待都要先登记上"

        # 账号对不上、任务对不上 —— 都不算数，也不能碰中止标志
        assert runtime.cancel(task_id="task-a", owner_id="bob")["ok"] is False
        assert runtime.cancel(task_id="task-x", owner_id="alice")["ok"] is False
        assert all(t.is_alive() for t in threads.values())

        # 匹配的那一笔：立刻唤醒，结果按「未确认」算
        assert runtime.cancel(task_id="task-a", owner_id="alice")["ok"] is True
        threads["step_a"].join(timeout=5)
        assert not threads["step_a"].is_alive(), "匹配的审批等待要被立刻打断"
        assert results["step_a"] is False
        # 没有运行可消费这笔中止 —— 任何运行实例的代号都不能动，
        # 否则下一条消息会被秒停
        assert not runtime._runs, "这两个审批等待不隶属于任何运行实例"

    def test_cancel_during_startup_is_not_swallowed(self, tmp_path):
        """任务刚起步时点的停止，必须真的停住。

        回归用例：以前 run() 是在**搭环境的中段**才清中止标志，
        于是「用户点发送后立刻点停止」会被随后那次 clear() 抹掉 ——
        这一轮照跑到底，界面上按了停止却什么都没发生。

        实测延迟 0/1/5/20ms 的停止全部丢失，只有 80ms 之后才生效。
        现在标志在 run() 入口就清掉，之后到达的 cancel() 一定看得见。
        """
        # 多个延迟都试：竞态窗口有多宽，要靠时间量出来，不能靠猜
        for delay in (0.0, 0.001, 0.01):
            provider = SlowProvider(rounds=20, delay=0.02, path=str(tmp_path))
            runtime, store, provider, bus = make_runtime(tmp_path, provider)
            task_id = store.create_task("跑个长任务", "efficiency", "test")
            # 真实链路：内核收到 chat.run 就登记「正要开始跑」，
            # 那一段窗口里点的停止也要算数（见 mark_run_pending）。
            runtime.mark_run_pending()

            def stop_soon(d=delay):
                time.sleep(d)
                runtime.cancel()

            threading.Thread(target=stop_soon, daemon=True).start()
            runtime.run("跑个长任务", session_id=task_id, pending_slot=True)

            kinds = [e.get("type") for e in bus._buffer]
            assert "RUN_CANCELLED" in kinds, f"延迟 {delay}s 的停止被吞掉了：{kinds}"
            assert provider.calls < 20, f"延迟 {delay}s：跑了 {provider.calls} 轮，没停下来"
            assert store.get_task(task_id)["status"] == "cancelled"

    def test_cancel_before_run_is_not_swallowed(self, tmp_path):
        """停止请求**比任务起步还先到**内核时，也必须停住。

        真实链路：前端点「发送」和点「停止」是两条独立的 HTTP 请求，
        可以并发 —— 停止请求完全可能先到内核。那��内核还没登记任务，
        cancel() 认为「没有正在跑的任务」，这一下就被丢掉了。

        内核侧的防线：agentd 收到 chat.run 就登记「正要开始跑」，
        那一段窗口里点的停止也算数。
        """
        provider = SlowProvider(rounds=20, delay=0.02, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑个长任务", "efficiency", "test")

        # 内核收到 chat.run → 登记「正要开始跑」
        runtime.mark_run_pending()
        # 停止请求紧接着到达（还没进 run()）
        out = runtime.cancel()
        assert out["ok"] is True
        runtime.run("跑个长任务", session_id=task_id, pending_slot=True)

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_CANCELLED" in kinds, kinds
        assert provider.calls < 20, f"跑了 {provider.calls} 轮，没停下来"
        assert store.get_task(task_id)["status"] == "cancelled"

    def test_stale_cancel_does_not_stop_next_run(self, tmp_path):
        """在旧任务上误点的停止，不该把下一条消息也停掉。

        回归用例：以前「有没有任务在跑」看的是 _active_task_id ——
        那个字段跑完**从不清**，所以：
          · is_busy 恒为真 → 定时任务永远排不上队
          · 用户在旧任务上点停止，那一下会一直挂着 → 下一条消息秒停
        """
        provider = SlowProvider(rounds=3, delay=0.01, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task1 = store.create_task("第一轮", "efficiency", "test")
        runtime.run("第一轮", session_id=task1)
        assert store.get_task(task1)["status"] == "done"

        # 用户在已经结束的任务上误点停止
        out = runtime.cancel()
        assert out["ok"] is False, "没有任务在跑时不该接受中止"

        time.sleep(0.05)
        provider.calls = 0
        task2 = store.create_task("第二轮", "efficiency", "test")
        runtime.run("第二轮", session_id=task2)
        assert store.get_task(task2)["status"] == "done", "上一轮的误停把这一轮也停掉了"

    def test_busy_flag_is_cleared_after_run(self, tmp_path):
        """跑完之后不能再「忙」—— 不然定时任务永远排不上队。

        守门用例：忙碌计数一旦泄漏，症状是「自动任务再也不触发」，
        而且没有任何报错 —— 极难定位。
        """
        provider = SlowProvider(rounds=3, delay=0.01, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("跑个长任务", "efficiency", "test")
        runtime.mark_run_pending()
        runtime.run("跑个长任务", session_id=task_id, pending_slot=True)
        assert not runtime.is_busy, "跑完之后仍然是忙的"

    def test_busy_flag_survives_setup_failure(self, tmp_path):
        """搭环境阶段炸了，忙碌标志也要还回去。

        回归用例：计数写在 run() 的中段时，setup 段抛异常就漏掉收尾 ——
        标志永远挂着，之后所有自动任务都不再触发。
        """
        runtime, store, provider, bus = make_runtime(tmp_path)

        def boom(*a, **kw):
            raise RuntimeError("provider 起不来")

        runtime.config.provider_config = boom  # type: ignore[assignment]
        with pytest.raises(RuntimeError):
            runtime.run("跑个长任务", session_id="missing-task")
        assert not runtime.is_busy, "setup 炸了之后仍然是忙的"

    def test_cancel_in_last_turn_still_cancels(self, tmp_path):
        """模型**一轮就返回**（没工具调用）时点的停止，也要停住。

        回归用例：取消检查原来只在「每轮开头」做，
        而「模型一轮说完就 break 出去」这条路径**绕过了检查** ——
        用户在那一轮里点的停止被吞掉，任务照跑完（状态是 done 不是 cancelled）。

        界面验收抓到过这个：点发送 → 立刻点停止 →
        界面没有「（已停止）」，库里状态是 done。
        """
        provider = OneShotProvider(delay=0.3, path=str(tmp_path))
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("讲个故事", "efficiency", "test")
        runtime.mark_run_pending()

        def stop_during_turn():
            # 取消必须落在**模型调用期间**（provider sleep 0.3 秒）——
            # 落在调用之前的话轮首那次检查就命中了，压根走不到轮末。
            time.sleep(0.1)
            runtime.cancel()

        threading.Thread(target=stop_during_turn, daemon=True).start()
        runtime.run("讲个故事", session_id=task_id, pending_slot=True)

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_CANCELLED" in kinds, kinds
        assert store.get_task(task_id)["status"] == "cancelled", (
            f"状态是 {store.get_task(task_id)['status']}，不是 cancelled"
        )

    def test_cancel_in_empty_reply_turn_still_cancels(self, tmp_path):
        """模型返回**空**回复时点的停止，也要停住。

        这是另一条收尾路径（`not assistant_blocks` 那支）——
        两条路径都漏检查就会有一种「按了停止没反应」的场景。
        """
        provider = EmptyReplyProvider(delay=0.3)
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("讲个故事", "efficiency", "test")
        runtime.mark_run_pending()

        def stop_during_turn():
            # 取消必须落在**模型调用期间**（provider sleep 0.3 秒）
            time.sleep(0.1)
            runtime.cancel()

        threading.Thread(target=stop_during_turn, daemon=True).start()
        runtime.run("讲个故事", session_id=task_id, pending_slot=True)

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_CANCELLED" in kinds, kinds
        assert store.get_task(task_id)["status"] == "cancelled", (
            f"状态是 {store.get_task(task_id)['status']}，不是 cancelled"
        )

    def test_cancel_interrupts_endless_model_stream(self, tmp_path):
        """模型**永远吐不完**时点的停止，必须立刻打断。

        回归用例（真实服务上抓到的）：本机挂的 qwen3.5:9b 是 Base 模型，
        不会终止生成 —— 一次 chat() 能连吐好几分钟。
        以前只在主循环的轮首/轮末查中止标志，
        中间那次模型调用要等它**整个跑完**才有机会收手：
          · 任务状态永远停在 running（不会被标成 cancelled）
          · 界面一直转圈
        修法是在**流式回调里**查标志 —— 每个 chunk 到达时查一次，命中就打断。
        """
        provider = EndlessStreamProvider(segments=200, delay=0.05)
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("讲个没完的故事", "efficiency", "test")
        runtime.mark_run_pending()

        def stop_during_stream():
            time.sleep(0.15)
            runtime.cancel()

        threading.Thread(target=stop_during_stream, daemon=True).start()
        started = time.time()
        runtime.run("讲个没完的故事", session_id=task_id, pending_slot=True)
        elapsed = time.time() - started

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_CANCELLED" in kinds, kinds
        assert store.get_task(task_id)["status"] == "cancelled", (
            f"状态是 {store.get_task(task_id)['status']}，不是 cancelled"
        )
        # 关键：不能等模型自己跑完（200 段 × 0.05 秒 = 10 秒）
        assert elapsed < 3.0, f"中止花了 {elapsed:.1f} 秒才生效 —— 模型调用没被打断"
        assert provider.sent < 200, f"吐了 {provider.sent} 段，流式读取没被打断"

    def test_cancel_interrupts_reasoning_stream(self, tmp_path):
        """**思考通道**里点的停止，也要能打断。

        本机挂的 qwen3 系列先吐一大段 reasoning ——
        只在正文回调里查标志的话，那段思考期间点停止照样停不下来。
        """
        provider = ReasoningOnlyProvider(segments=200, delay=0.05)
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("想一想", "efficiency", "test")
        runtime.mark_run_pending()

        def stop_during_stream():
            time.sleep(0.15)
            runtime.cancel()

        threading.Thread(target=stop_during_stream, daemon=True).start()
        started = time.time()
        runtime.run("想一想", session_id=task_id, pending_slot=True)
        elapsed = time.time() - started

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_CANCELLED" in kinds, kinds
        assert store.get_task(task_id)["status"] == "cancelled", (
            f"状态是 {store.get_task(task_id)['status']}，不是 cancelled"
        )
        assert elapsed < 3.0, f"中止花了 {elapsed:.1f} 秒才生效"

    def test_cancel_keeps_partial_stream_text(self, tmp_path):
        """中止时已经吐出来的正文要留住 ——「半个答案比没答案有用」。

        取消时机必须是**确定的**：等模型真的吐过至少一个 chunk 再点停止。
        以前这里是固定 sleep(0.15)，隐含假设「搭环境 < 150ms」——
        机器负载一高（跑整文件时前面有二十来个用例），取消就落在
        「模型还没被调用」之前。那条路径本身是合法的
        （取消先于任务启动 → 直接收手，本就没有正文可留），
        但它测的不是这条用例想测的东西，于是断言莫名其妙地失败。

        等 first_chunk 信号才是真的在测「流式输出中途被中止」。
        """
        provider = EndlessStreamProvider(segments=200, delay=0.05)
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("讲个没完的故事", "efficiency", "test")
        runtime.mark_run_pending()

        def stop_during_stream():
            provider.first_chunk.wait(timeout=10)
            time.sleep(0.1)  # 让它多吐几段，确保有内容可留
            runtime.cancel()

        threading.Thread(target=stop_during_stream, daemon=True).start()
        runtime.run("讲个没完的故事", session_id=task_id, pending_slot=True)

        msgs = store.messages_for_task(task_id)
        assistant = [m for m in msgs if m["role"] == "assistant"]
        assert provider.sent > 0, "模型一次都没被调用，取消时机不对"
        assert assistant, "中止后已产出的正文丢了"
        assert assistant[0]["content"].strip(), "中止后存的正文是空的"

    def test_stream_cancel_is_not_reported_as_failure(self, tmp_path):
        """中止**不能**被报成「任务失败」。

        _StreamCancelled 是 RuntimeError 的子类 ——
        如果没在 except Exception 之前接住它，
        状态会变成 failed，界面上就显示「任务失败」而不是「已停止」。
        """
        provider = EndlessStreamProvider(segments=200, delay=0.05)
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("讲个没完的故事", "efficiency", "test")
        runtime.mark_run_pending()

        def stop_during_stream():
            time.sleep(0.15)
            runtime.cancel()

        threading.Thread(target=stop_during_stream, daemon=True).start()
        runtime.run("讲个没完的故事", session_id=task_id, pending_slot=True)

        kinds = [e.get("type") for e in bus._buffer]
        assert "RUN_ERROR" not in kinds, "中止被报成了错误"
        assert "RUN_CANCELLED" in kinds, kinds

    def test_cancel_stops_subagent_loop(self, tmp_path):
        """点了停止，子 Agent 也不能接着跑。

        回归用例：子 Agent 调的是同一个 provider.chat()，
        但它那一圈**完全没有中止检查** ——
        用户点了停止，主 Agent 收手了，子 Agent 还在后台接着跑，
        甚至还会继续发工具调用（界面看着已经停了，实际还在动）。
        """
        provider = EndlessStreamProvider(segments=200, delay=0.05)
        runtime, store, provider, bus = make_runtime(tmp_path, provider)
        task_id = store.create_task("派个子 Agent", "efficiency", "test")
        runtime._active_task_id = task_id
        runtime._active_registry = dict(runtime.registry)
        # 必须登记「有任务在跑」——
        # cancel() 只在真有任务在跑时才 +1 中止代号，
        # 没有它的话那一下停止根本不算数（被判成「误点在旧任务上」）。
        runtime.mark_run_pending()

        def stop_soon():
            time.sleep(0.15)
            runtime.cancel()

        threading.Thread(target=stop_soon, daemon=True).start()
        started = time.time()
        runtime.run_subagent("doc-manager", "记录这条需求")
        elapsed = time.time() - started

        # 子 Agent 第一轮就会调 provider.chat()（EndlessStreamProvider 会一直吐）
        # 中止后不能再有第二轮
        assert elapsed < 3.0, f"子 Agent 跑了 {elapsed:.1f} 秒才收手 —— 中止没生效"
        assert provider.calls <= 1, f"子 Agent 调了 {provider.calls} 次模型，中止后还在跑"
