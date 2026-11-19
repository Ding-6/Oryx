"""失败分类（E1-E5）的回归测试。

为什么单独测这一层：
提示词里写了「先分类再决策，禁止笼统重试」，但模型收到的
失败信息如果只是「失败了」，它就得自己猜 —— 猜成 E1（重连）
还是 E5（补依赖），决定了下一步是重试还是换路。

所以分类必须是**确定性**的：同一个错误文本，两次调用要给出同一个分类。
"""

from __future__ import annotations

import json

import pytest

from oryx.failure import (
    ADVICE,
    E0_UNKNOWN,
    E1_UNREACHABLE,
    E2_PERMISSION,
    E3_UNSUPPORTED,
    E4_MISMATCH,
    E5_MISSING,
    annotate,
    classify,
)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("连接失败：目标主机不可达", E1_UNREACHABLE),
        ("命令超时（>60s）：xxx", E1_UNREACHABLE),
        ("Connection refused", E1_UNREACHABLE),
        ("没有权限读取：D:\\x", E2_PERMISSION),
        ("Access is denied", E2_PERMISSION),
        ("未知工具：foo", E3_UNSUPPORTED),
        ("参数不合法：missing required argument", E3_UNSUPPORTED),
        ("正则不合法：unbalanced parenthesis", E3_UNSUPPORTED),
        ("文件不存在：D:\\x\\a.txt", E5_MISSING),
        ("目标不存在：D:\\x", E5_MISSING),
        ("No module named 'yaml'", E5_MISSING),
        ("结果与预期不符", E4_MISMATCH),
    ],
)
def test_classification(message, expected):
    assert classify(message) == expected


def test_unknown_failure_gets_e0():
    """认不出来就给 E0 —— 不能硬塞进某一类，那会让模型按错的策略走。"""
    assert classify("阿巴阿巴") == E0_UNKNOWN
    assert classify("") == E0_UNKNOWN


def test_permission_is_checked_before_missing():
    """「拒绝访问」要判成 E2 而不是 E5。

    两者措辞可能同时出现（「权限不足：文件不存在」）——
    按顺序匹配时权限必须在前，否则模型会以为补个文件就行。
    """
    assert classify("权限不足：文件不存在") == E2_PERMISSION


def test_classification_is_deterministic():
    """同一个错误文本，两次分类结果必须一样。

    这是这一层存在的理由 —— 让模型自己判断的话，
    同个错误两次调用可能给出两个分类，策略就乱了。
    """
    message = "没有权限读取：D:\\secret\\.env"
    assert classify(message) == classify(message) == E2_PERMISSION


def test_annotate_adds_class_and_advice():
    """失败结果要带上分类代码与对应动作。"""
    result = {"ok": False, "content": "文件不存在：D:\\x\\a.txt"}
    out = annotate(result, "fs_read")
    assert out["ok"] is False
    assert out["error_class"] == E5_MISSING
    assert "E5" in out["content"]
    assert "fs_read" in out["content"]
    assert ADVICE[E5_MISSING] in out["content"]


def test_annotate_leaves_success_alone():
    """成功的结果不加工 —— 加分类只是白占上下文。"""
    result = {"ok": True, "content": "读到了"}
    assert annotate(result, "fs_read") is result


def test_annotate_does_not_mutate_input():
    """不能改原对象 —— 调用方还要用原值落库与推事件。"""
    result = {"ok": False, "content": "不存在"}
    annotate(result, "fs_read")
    assert result["content"] == "不存在"
    assert "error_class" not in result


def test_annotate_skips_empty_error():
    """错误消息本身是空的就不加标签 —— 加了也是噪声。"""
    result = {"ok": False, "content": ""}
    out = annotate(result, "fs_read")
    assert "error_class" not in out


def test_every_class_has_advice():
    """每一类都要有对应的动作 —— 分类出来没有建议等于没分类。"""
    for code in (E1_UNREACHABLE, E2_PERMISSION, E3_UNSUPPORTED, E4_MISMATCH, E5_MISSING):
        assert ADVICE[code], f"{code} 没有对应的建议动作"
        assert len(ADVICE[code]) > 10, f"{code} 的建议太短，说不清该做什么"


def test_failure_class_reaches_the_model_context():
    """分类要真的进到模型看到的那段文本里 —— 不能只挂在返回体上。

    这条走真实的 `_handle_tool_call`：单测 annotate 只能证明
    「函数会加标签」，证不了「出口那里真的加上了」。
    """
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp)]
    store = Store(tmp / "oryx.db")
    runtime = AgentRuntime(Config(data), store, EventBus())

    captured: list[dict] = []
    runtime.bus.add_sync_sink(captured.append)

    missing = tmp / "not-there.txt"
    out = runtime._handle_tool_call(
        "", 1, ToolCall(id="c1", name="fs_read", arguments={"path": str(missing)})
    )
    assert out["is_error"] is True
    assert "E5" in out["content"], "失败分类没进到模型看到的内容里"

    # 界面上也要能看到 —— 事件里带 error_class
    done = [e for e in captured if e["type"] == "step.failed"]
    assert done, "没有发出 step.failed"
    assert done[0].get("error_class") == E5_MISSING, "事件里没有错误分类"
