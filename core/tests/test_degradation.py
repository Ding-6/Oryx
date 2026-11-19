"""安全口径 + 静默降级可见化 + 定时任务失败通知的回归测试。

三处历史短板：
1. ``mcp.py`` 无注解工具返回 L1，与 README/tools.py「一律按 L2」矛盾；
2. 向量检索退回关键词、OCR 引擎缺失都是静默的，用户看不出能力缩水；
3. 定时任务兜底失败路径只发事件，用户不打开界面就完全收不到。
"""
from __future__ import annotations

from pathlib import Path

from oryx import automation, ocr, terminal
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.mcp import MCPTool
from oryx.store import Store


def make_runtime(tmp_path: Path):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    bus = EventBus()
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), bus)
    return runtime, events


# ---------------------------------------------------------------- MCP 风险
def test_undeclared_mcp_tool_is_L2():
    def tool(annotations):
        return MCPTool(server="s", name="n", description="", input_schema={},
                       annotations=annotations)

    assert tool({}).risk_level() == 2, "无注解必须按 L2 走确认"
    assert tool({"readOnlyHint": True}).risk_level() == 0
    assert tool({"destructiveHint": True}).risk_level() == 2
    # readOnly 优先于 destructive：声明只读就别再升级
    assert tool({"readOnlyHint": True, "destructiveHint": True}).risk_level() == 0


# ---------------------------------------------------------------- 降级可见化
def test_index_search_announces_missing_vector_model(tmp_path: Path, monkeypatch):
    runtime, events = make_runtime(tmp_path)
    monkeypatch.setattr(runtime.index, "stats", lambda: {
        "files": 10, "known_files": 10, "chunks": 20, "vectors": 0,
        "embedding_ready": False, "db_mb": 0.1, "scanning": False, "last_scan": None,
    })
    monkeypatch.setattr(runtime.index, "search", lambda *a, **kw: [])
    runtime.registry["index_search"].fn(query="随便", mode="hybrid")
    degraded = [e for e in events if e.get("type") == "capability.degraded"]
    assert len(degraded) == 1
    assert degraded[0]["capability"] == "语义检索"
    assert "关键词" in degraded[0]["reason"]
    assert degraded[0]["action"], "降级要给出恢复办法"


def test_index_search_announces_missing_vectors_even_when_model_present(tmp_path: Path, monkeypatch):
    runtime, events = make_runtime(tmp_path)
    monkeypatch.setattr(runtime.index, "stats", lambda: {
        "files": 10, "chunks": 20, "vectors": 0, "embedding_ready": True,
    })
    monkeypatch.setattr(runtime.index, "search", lambda *a, **kw: [])
    runtime.registry["index_search"].fn(query="随便", mode="semantic")
    degraded = [e for e in events if e.get("type") == "capability.degraded"]
    assert degraded and "没有向量" in degraded[0]["reason"]


def test_ocr_engine_missing_is_announced(tmp_path: Path, monkeypatch):
    runtime, events = make_runtime(tmp_path)
    monkeypatch.setattr(ocr, "available", lambda: False)
    runtime.registry["ocr_image"].fn(path=str(tmp_path / "no-such.png"))
    degraded = [e for e in events if e.get("type") == "capability.degraded"]
    assert degraded and degraded[0]["capability"] == "图片 OCR"
    assert "rapidocr" in degraded[0]["action"]


def test_degradation_is_deduplicated(tmp_path: Path, monkeypatch):
    runtime, events = make_runtime(tmp_path)
    monkeypatch.setattr(runtime.index, "stats", lambda: {
        "files": 10, "chunks": 20, "vectors": 0, "embedding_ready": False,
    })
    monkeypatch.setattr(runtime.index, "search", lambda *a, **kw: [])
    runtime.registry["index_search"].fn(query="a", mode="hybrid")
    runtime.registry["index_search"].fn(query="b", mode="hybrid")
    assert len([e for e in events if e.get("type") == "capability.degraded"]) == 1


# ---------------------------------------------------------------- 失败通知
class _Item:
    id = "auto-1"
    name = "每日汇总"


def test_failure_notification_accepts_string_and_deduplicates(tmp_path: Path, monkeypatch):
    runtime, _ = make_runtime(tmp_path)
    sent: list[dict] = []

    def fake_notify(title, message, method="toast", webhook=""):
        sent.append({"title": title, "message": message, "method": method})
        return {"ok": True}

    monkeypatch.setattr(terminal, "notify", fake_notify)
    runtime._notify_automation_failure(_Item(), "调度器异常")
    runtime._notify_automation_failure(_Item(), "调度器异常")
    assert len(sent) == 1, "同一任务短时间内不该重复轰炸通知栏"
    assert "调度器异常" in sent[0]["message"]
    assert "toast" == sent[0]["method"]


def test_scheduler_on_error_uses_system_notification(tmp_path: Path, monkeypatch):
    """兜底路径也要走系统级通知，不能只发一个没人看的事件。"""
    captured: dict = {}

    class FakeScheduler:
        def __init__(self, on_fire, store=None, is_busy=None, on_error=None):
            captured["on_error"] = on_error
            self.items = {}

        def start(self):
            pass

    monkeypatch.setattr(automation, "AutomationScheduler", FakeScheduler)
    sent: list[dict] = []
    monkeypatch.setattr(
        terminal, "notify",
        lambda title, message, method="toast", webhook="": (
            sent.append({"message": message}) or {"ok": True}
        ),
    )
    runtime, events = make_runtime(tmp_path)
    callback = captured.get("on_error")
    assert callable(callback)
    callback(_Item(), "boom")
    assert any(e.get("type") == "automation.failed" for e in events)
    assert sent, "调度器兜底失败只发了事件，没有走系统通知"
