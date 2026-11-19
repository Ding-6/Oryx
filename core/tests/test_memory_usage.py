"""记忆与用量统计。"""

from __future__ import annotations

from pathlib import Path

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply
from oryx.store import Store


class UsageProvider:
    def __init__(self, usage=None):
        self.usage = usage if usage is not None else {"input_tokens": 120, "output_tokens": 45}
        self.systems: list[str] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.systems.append(system)
        if on_text:
            on_text("好的")
        return ProviderReply(text="好的", tool_calls=[], stop_reason="end_turn", usage=dict(self.usage))


def make_runtime(tmp_path: Path, usage=None):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = UsageProvider(usage)
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    return runtime, store, provider


# ---------------------------------------------------------------- 记忆


def test_memory_save_dedup_and_search(tmp_path):
    runtime, store, provider = make_runtime(tmp_path)
    first = store.save_memory("用户偏好简洁回答", "preference")
    assert first["deduped"] is False

    again = store.save_memory("用户偏好简洁回答", "preference")
    assert again["deduped"] is True
    assert again["id"] == first["id"]

    assert store.search_memories("简洁")
    assert not store.search_memories("不存在的关键词")


def test_memory_forget(tmp_path):
    _, store, _ = make_runtime(tmp_path)
    saved = store.save_memory("临时记忆")
    assert store.forget_memory(saved["id"]) is True
    assert store.forget_memory(saved["id"]) is False
    assert store.search_memories("临时记忆") == []


def test_memory_injected_into_system_prompt(tmp_path):
    runtime, store, provider = make_runtime(tmp_path)
    store.save_memory("这个项目的主力语言是 Python")
    runtime.run("你好", interaction="craft")
    assert "主力语言是 Python" in provider.systems[0]
    assert "长期记忆：" in provider.systems[0]


def test_no_memory_no_block(tmp_path):
    """没有记忆时不该注入记忆块。

    判据用记忆块自己的标题「长期记忆：」（带冒号）——
    系统提示词里也有「长期记忆」这个词（在讲召回策略），
    只按词匹配会误判成「注入了」。
    """
    runtime, store, provider = make_runtime(tmp_path)
    runtime.run("你好", interaction="craft")
    assert "长期记忆：" not in provider.systems[0]


# ---------------------------------------------------------------- 用量


def test_usage_recorded_after_run(tmp_path):
    runtime, store, provider = make_runtime(tmp_path)
    runtime.run("你好", interaction="craft")

    summary = store.usage_summary()
    assert summary["total"]["calls"] == 1
    assert summary["total"]["input_tokens"] == 120
    assert summary["total"]["output_tokens"] == 45
    assert summary["by_model"][0]["calls"] == 1


def test_usage_ignored_when_provider_reports_nothing(tmp_path):
    runtime, store, provider = make_runtime(tmp_path, usage={})
    runtime.run("你好", interaction="craft")
    assert store.usage_summary()["total"]["calls"] == 0


def test_usage_window_filters(tmp_path):
    runtime, store, provider = make_runtime(tmp_path)
    runtime.run("一", interaction="craft")
    runtime.run("二", interaction="craft")
    assert store.usage_summary(24 * 30)["total"]["calls"] == 2
    assert store.usage_summary(0)["total"]["calls"] == 0
