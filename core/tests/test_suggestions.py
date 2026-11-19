"""运行中主动建议与进度快照的回归测试。

锁住的是「主动性真的接进内核链路」，而不只是有个能调用的函数：
用受控假模型驱动真实 AgentRuntime.run()，检查 SSE 事件里确实出现了
run.suggestion / run.progress，且建议内容指向真实落盘的文件。
"""
from __future__ import annotations

from pathlib import Path

from oryx import suggestions
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store


class ScriptedProvider:
    """按脚本依次返回工具调用，最后收尾。"""

    def __init__(self, script):
        self.script = list(script)
        self.index = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        if self.index < len(self.script):
            name, args = self.script[self.index]
            self.index += 1
            if name is None:
                return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id=f"c{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script, monkeypatch):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    from oryx import loop as loop_mod

    monkeypatch.setattr(loop_mod, "build_provider", lambda cfg: ScriptedProvider(script))
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    return runtime, events


# ---------------------------------------------------------------- 规则本身
def test_suggestions_are_concrete_and_safe_for_xlsx():
    out = suggestions.suggest(
        tool="xlsx_write", args={"path": "D:/data/周报.xlsx"},
        result={"ok": True, "content": {"path": "D:/data/周报.xlsx", "bytes": 100}},
    )
    assert out and len(out) <= suggestions.MAX_SUGGESTIONS
    assert all(s.get("label") and (s.get("prompt") or s.get("action")) for s in out)
    assert any("周报.xlsx" in s.get("prompt", "") for s in out)


def test_failure_suggestion_does_not_retry_same_args():
    out = suggestions.suggest(tool="shell", args={}, result={"ok": False, "content": "超时"}, ok=False)
    assert len(out) == 1 and out[0]["kind"] == "recover"
    assert "不要重复同样的参数" in out[0]["prompt"]


def test_research_without_artifact_suggests_writing_report():
    out = suggestions.suggest(tool="index_search", args={"query": "TODO"},
                              result={"ok": True, "content": "..."})
    assert any(s["kind"] == "html" for s in out)


def test_progress_snapshot_is_structured():
    snap = suggestions.progress_snapshot(turn=2, completed=["a", "b", "c"],
                                         artifacts=["x.html"], current="c")
    assert snap["steps"] == 3 and snap["artifacts"] == 1 and snap["turn"] == 2
    assert "已完成 3 步" in snap["label"]


# ---------------------------------------------------------------- 内核链路
def test_run_emits_suggestion_and_progress(tmp_path: Path, monkeypatch):
    path = str(tmp_path / "board.html")
    runtime, events = make_runtime(
        tmp_path,
        [("html_write", {"path": path, "title": "看板", "sections": [
            {"heading": "概览", "paragraphs": ["一行说明"]}]}), (None, {})],
        monkeypatch,
    )
    runtime.run("做一份看板")
    kinds = [e.get("type") for e in events]
    assert "run.progress" in kinds, "主循环没有发结构化进度"
    sug = [e for e in events if e.get("type") == "run.suggestion"]
    assert sug, "没有发主动建议"
    assert any(e.get("phase") == "final" for e in sug), "收尾没有给最终建议"
    labels = [s["label"] for e in sug for s in e.get("suggestions", [])]
    assert "在界面里预览" in labels, f"建议没接上真实产物：{labels}"
    assert Path(path).exists()
    progress = [e for e in events if e.get("type") == "run.progress"][0]
    assert progress["steps"] >= 1 and progress["artifacts"] >= 1


def test_step_suggestions_are_deduplicated(tmp_path: Path, monkeypatch):
    """运行中同一阶段不要反复刷同一张卡；收尾那组是替换上一组，允许重叠。"""
    path = str(tmp_path / "a.html")
    runtime, events = make_runtime(
        tmp_path,
        [("html_write", {"path": path, "title": "A"}), (None, {})],
        monkeypatch,
    )
    runtime.run("做看板")
    step_labels = [s["label"] for e in events
                   if e.get("type") == "run.suggestion" and e.get("phase") == "step"
                   for s in e.get("suggestions", [])]
    assert len(step_labels) == len(set(step_labels)), f"运行中建议重复了：{step_labels}"
    assert any(e.get("phase") == "final" for e in events
               if e.get("type") == "run.suggestion")
