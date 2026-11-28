"""反向验证脚本：把「旧的全局中止机制」与「新的每运行一份」放一起对比。

为什么需要它
------------
`git stash` 式反向验证在这个仓库里**不可靠**：
模块 4–10 的改动从未提交，HEAD 里的 loop.py 缺 `mark_run_pending(task_id=…)`
等接口，回退后测试报的是「AttributeError / TypeError」——
那是「接口不存在」，不是「行为不对」，证明不了任何事。

所以这里直接用**旧机制的真实语义**（全局 Event + 全局代号计数器）
搭一个最小复现，与新机制对照：

    旧：停止 A → 全局代号 +1 → B 判定「代号变了」→ B 也停
    新：停止 A → 只动 A 自己的那份 → B 不受影响

跑法：
    python core/tests/probe_cancel_isolation.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from oryx.loop import _RunAbort  # noqa: E402


class OldStyleAbort:
    """旧机制：全局共享的一个 Event + 一个全局代号计数器。

    这是模块 11 之前 `AgentRuntime` 里那套的真实语义，
    照抄出来做对照 —— 不是「假想的老实现」。
    """

    def __init__(self) -> None:
        self.event = threading.Event()
        self.generation = 0
        self.consumed = 0

    def cancel(self) -> None:
        self.generation += 1
        self.event.set()

    def is_cancelled(self, generation: int) -> bool:
        if self.generation != generation:
            self.consumed = max(self.consumed, self.generation)
            return True
        return self.event.is_set()


def check_old_style() -> bool:
    """旧机制：停止 A 会不会把 B 一起停掉。"""
    abort = OldStyleAbort()
    gen_a = abort.consumed
    gen_b = abort.consumed
    abort.cancel()  # 停止 A
    return abort.is_cancelled(gen_b)  # B 的判定


def check_new_style() -> bool:
    """新机制：每个运行一份自己的信号。"""
    abort_a = _RunAbort()
    abort_b = _RunAbort()
    gen_a = abort_a.consumed
    gen_b = abort_b.consumed
    abort_a.cancel()  # 停止 A
    return abort_b.is_cancelled(gen_b)  # B 的判定


def main() -> None:
    old_cross_talk = check_old_style()
    new_cross_talk = check_new_style()

    print("旧机制（全局 Event + 全局代号）：")
    print(f"  停止 A 后，B 是否被判为取消 = {old_cross_talk}")
    print("新机制（每个运行一份 _RunAbort）：")
    print(f"  停止 A 后，B 是否被判为取消 = {new_cross_talk}")
    print()

    assert old_cross_talk is True, (
        "旧机制没有复现串扰 —— 对照组失效，后面的结论不成立"
    )
    assert new_cross_talk is False, (
        "新机制仍然串扰 —— 模块 11 没修好"
    )
    print("[OK] 对照组成立：旧机制串扰、新机制隔离。")


if __name__ == "__main__":
    main()
