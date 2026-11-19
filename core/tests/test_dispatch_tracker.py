"""控制面的「发起在途」跟踪。

背景：前端的「发送」和「停止」是**两条独立的 HTTP 请求**，可以并发。
用户点「发送」后立刻点「停止」，停止请求完全可能先到 —— 先到内核会被丢掉
（内核还没登记任务），先到控制面则控制面还不知道有发起在路上。

所以控制面按**序号**（前端给的 seq）跟踪每一轮发起的状态，
中止请求按序号对齐：
  · inflight（发起在途）→ 等它落地再把中止转给内核
  · unknown（发起还没到）→ 先记下，等 chat 到了补一次
  · landed（已经在跑了）→ 直接转发给内核
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def tracker():
    """拿到控制面的 _DispatchTracker 类。

    **不要**在这里删 sys.modules 里的 oryx 模块 ——
    conftest 在导入期把 oryx.config / oryx.store / oryx.embeddings 里的
    路径常量重定向到了测试目录（模型目录尤其重要）。
    把模块删掉再重新导入，那些常量就按默认值重新算 ——
    向量模型目录指回真实路径，语义检索的测试全变空。
    （这属于「测试污染全局状态」：一个文件坏了，整片测试都红，
    报错还完全指不到真凶。）
    """
    # `_DispatchTracker` 已经搬进 `oryx/domains/chat.py`
    # —— 对话域管「发送 / 停止」两条并发请求的序号追踪。
    # 它原来住在 server.py，现在从新家取。
    module = importlib.import_module("oryx.domains.chat")
    yield module.DispatchTracker


class TestDispatchTracker:
    def test_fresh_seq_is_unknown(self, tracker):
        t = tracker()
        assert t.state(1) == "unknown"

    def test_zero_seq_is_unknown(self, tracker):
        """前端没带序号时不能瞎猜是哪一轮 —— 当成 unknown。"""
        t = tracker()
        assert t.state(0) == "unknown"

    def test_inflight_while_begin(self, tracker):
        t = tracker()
        t.begin(7)
        assert t.state(7) == "inflight"

    def test_landed_after_end(self, tracker):
        t = tracker()
        t.begin(7)
        t.end(7)
        assert t.state(7) == "landed"

    def test_cancel_ahead_is_remembered(self, tracker):
        """中止先到：这笔要记下来，等 chat 到了补上。"""
        t = tracker()
        assert t.note_cancel(5) is True
        assert t.take_cancel(5) is True
        # 取过一次就没了 —— 不能重复补
        assert t.take_cancel(5) is False

    def test_cancel_of_older_seq_is_ignored(self, tracker):
        """上一轮的迟到中止不该被记下来。

        记下来的话，下一次发消息会被莫名停掉（症状是「第二条消息秒停」）。
        """
        t = tracker()
        t.begin(9)
        t.end(9)              # 已见过序号 9
        assert t.note_cancel(3) is False, "旧序号的迟到中止被记下了"
        assert t.take_cancel(3) is False

    def test_cancel_of_newer_seq_is_remembered(self, tracker):
        """序号比已知最大值还大 = 这一轮的 chat 可能还在路上 —— 要记。"""
        t = tracker()
        t.begin(9)
        assert t.note_cancel(10) is True
        assert t.take_cancel(10) is True

    def test_take_cancel_without_note_is_false(self, tracker):
        t = tracker()
        assert t.take_cancel(3) is False

    def test_landed_set_is_bounded(self, tracker):
        """落地序号只留最近 32 个 —— 免得这个集合无限涨。"""
        t = tracker()
        for i in range(1, 200):
            t.begin(i)
            t.end(i)
        assert len(t._landed) <= 32
