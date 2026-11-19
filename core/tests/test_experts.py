"""专家 persona 回归。"""

from __future__ import annotations

from pathlib import Path

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.experts import ExpertLibrary
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply
from oryx.store import Store

EXPERTS_ROOT = Path(__file__).resolve().parents[2] / "experts"


class SilentProvider:
    def __init__(self):
        self.systems: list[str] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.systems.append(system)
        return ProviderReply(text="好的", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = SilentProvider()
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    return runtime, provider


def test_library_loads_builtin_experts():
    lib = ExpertLibrary(EXPERTS_ROOT)
    assert len(lib.experts) >= 6
    for key in ("senior-developer", "data-analyst", "doc-writer"):
        assert key in lib.experts


def test_frontmatter_parsed():
    lib = ExpertLibrary(EXPERTS_ROOT)
    expert = lib.experts["data-analyst"]
    assert expert.name == "数据分析师"
    assert expert.description
    assert "数据清洗" in expert.scenarios
    assert expert.body.strip()


def test_prompt_block_contains_persona():
    lib = ExpertLibrary(EXPERTS_ROOT)
    block = lib.prompt_block(lib.experts["ppt-maker"])
    assert "演示设计" in block
    assert "一页一个观点" in block or "标题写成结论句" in block


def test_expert_injected_into_system_prompt(tmp_path):
    runtime, provider = make_runtime(tmp_path)
    runtime.run("帮我梳理一下这个需求", interaction="craft", expert="product-manager")
    system = provider.systems[0]
    assert "产品经理" in system
    assert "验收标准" in system


def test_no_expert_means_no_persona(tmp_path):
    runtime, provider = make_runtime(tmp_path)
    runtime.run("你好", interaction="craft", expert="")
    system = provider.systems[0]
    assert "你现在以「" not in system


def test_unknown_expert_is_ignored(tmp_path):
    runtime, provider = make_runtime(tmp_path)
    runtime.run("你好", interaction="craft", expert="does-not-exist")
    assert "你现在以「" not in provider.systems[0]


def test_catalog_shape():
    lib = ExpertLibrary(EXPERTS_ROOT)
    catalog = lib.catalog()
    assert catalog and {"id", "name", "description", "scenarios"} <= set(catalog[0])
