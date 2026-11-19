"""「意图 → 编排草案」中间层 + 多模型运行时路由的回归测试。

对应诊断报告 P1-3 / P2-1：
- workflow_run 以前要模型自己从零拼 stages/steps JSON；
- loop 每轮固定用同一个 provider，cost_hint 只是标签，没有执行期路由。

这里锁住：草案能被 WorkflowPlan.parse 自检、只引用真实子 Agent id；
路由默认关闭、开启后确实改变 provider，且只从配置里真实存在的条目里挑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx import planning, routing
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.features import DEFAULT_FEATURES
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply
from oryx.store import Store
from oryx.workflow import WorkflowPlan

AGENTS = ["app-agent", "browser-agent", "computer-agent", "doc-manager",
          "file-agent", "researcher", "reviewer", "search-agent"]


# ---------------------------------------------------------------- 编排草案
def test_parallel_draft_is_valid_and_uses_real_agents():
    goal = "请分别调研三个仓库的代码结构、整理接口清单、核对测试覆盖率"
    out = planning.draft_workflow(goal, AGENTS)
    assert out["ok"] and out["valid"] and out["total_steps"] >= 2
    draft = out["draft"]
    # 关键：草案能被引擎自己解析回去，不是一份看起来像 JSON 的废纸
    assert WorkflowPlan.parse(draft).total_steps == out["total_steps"]
    steps = draft["stages"][0]["steps"]
    assert all(s["agent"] in AGENTS for s in steps), "编了不存在的子 Agent id"
    assert all(s["parallel"] for s in steps)
    assert out["notes"] and "推断" in "".join(out["notes"])


def test_non_parallel_draft_is_a_serial_pipeline_with_dependencies():
    out = planning.draft_workflow("帮我看看这个项目的整体情况", AGENTS)
    assert out["ok"] and out["confidence"] == "low"
    draft = out["draft"]
    assert len(draft["stages"]) == 3
    assert draft["stages"][2]["steps"][0]["needs"] == ["s1"]
    WorkflowPlan.parse(draft)


def test_draft_rejects_empty_goal_or_no_agents():
    assert planning.draft_workflow("", AGENTS)["ok"] is False
    assert planning.draft_workflow("做事", [])["ok"] is False


def test_workflow_draft_tool_is_registered_and_readonly(tmp_path: Path):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())
    spec = runtime.registry.get("workflow_draft")
    assert spec is not None
    assert int(spec.risk) == 0, "草案工具是只读，不能标成写入"
    result = spec.fn(goal="分别检查代码、文档和测试")
    assert result["ok"] and result["content"]["valid"]


# ---------------------------------------------------------------- 路由纯函数
def _config(home: Path, providers: dict, active: str) -> Config:
    data = dict(DEFAULT_CONFIG)
    data["providers"] = providers
    data["active_provider"] = active
    data["local_provider"] = ""
    return Config(data)


def _set_flags(home: Path, enabled: bool, monkeypatch) -> None:
    import oryx.config as config_mod

    home.mkdir(parents=True, exist_ok=True)
    flags = dict(DEFAULT_FEATURES)
    flags["EnableModelRouting"] = enabled
    (home / "features.yaml").write_text(
        json.dumps({"profile": "personal", "flags": flags}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "FEATURES_PATH", home / "features.yaml")


CLOUD = {"type": "openai", "base_url": "https://api.example.com/v1",
         "api_key": "", "model": "big", "context_window": 128000, "cost_hint": "按 token 计费"}
LOCAL = {"type": "ollama", "base_url": "http://127.0.0.1:11434/v1",
         "api_key": "", "model": "small", "context_window": 8000, "cost_hint": "本地免费"}


def test_routing_disabled_by_default(tmp_path: Path, monkeypatch):
    _set_flags(tmp_path / "home", False, monkeypatch)
    cfg = _config(tmp_path, {"cloud": CLOUD, "local": LOCAL}, "cloud")
    out = routing.route(cfg, "你好")
    assert out["switched"] is False
    assert "未开启" in out["reason"]


def test_routing_picks_local_for_simple_task(tmp_path: Path, monkeypatch):
    _set_flags(tmp_path / "home", True, monkeypatch)
    cfg = _config(tmp_path, {"cloud": CLOUD, "local": LOCAL}, "cloud")
    out = routing.route(cfg, "你好")
    assert out["switched"] is True and out["name"] == "local"
    assert out["complexity"] == "low"
    assert "local" in out["candidates"] and "cloud" in out["candidates"]


def test_routing_picks_large_context_for_complex_task(tmp_path: Path, monkeypatch):
    _set_flags(tmp_path / "home", True, monkeypatch)
    cfg = _config(tmp_path, {"cloud": CLOUD, "local": LOCAL}, "local")
    goal = "请并行对整仓代码做批量巡检，分别分析架构、重构方案与交叉验证，给出详细实现计划"
    out = routing.route(cfg, goal)
    assert out["switched"] is True and out["name"] == "cloud"
    assert out["complexity"] == "high"


def test_routing_degrades_to_local_on_network_error(tmp_path: Path, monkeypatch):
    _set_flags(tmp_path / "home", True, monkeypatch)
    cfg = _config(tmp_path, {"cloud": CLOUD, "local": LOCAL}, "cloud")
    out = routing.route(cfg, "请并行分析多个模块的架构并给出重构方案",
                        last_error="ConnectTimeout: 连接超时")
    assert out["name"] == "local", "云端网络失败却没降级"
    assert "网络" in out["reason"]


def test_routing_never_invents_providers(tmp_path: Path, monkeypatch):
    _set_flags(tmp_path / "home", True, monkeypatch)
    cfg = _config(tmp_path, {"cloud": CLOUD}, "cloud")
    out = routing.route(cfg, "你好")
    assert out["switched"] is False and out["name"] == "cloud"


def test_provider_config_for_resolves_named_provider(tmp_path: Path):
    cfg = _config(tmp_path, {"cloud": CLOUD, "local": LOCAL}, "cloud")
    assert cfg.provider_config_for("local")["name"] == "local"
    assert cfg.provider_config_for("local")["model"] == "small"
    with pytest.raises(KeyError):
        cfg.provider_config_for("不存在的模型")


# ---------------------------------------------------------------- 内核接线
class _Provider:
    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def test_run_switches_provider_and_publishes_event(tmp_path: Path, monkeypatch):
    _set_flags(tmp_path / "home", True, monkeypatch)
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["providers"] = {"cloud": CLOUD, "local": LOCAL}
    data["active_provider"] = "cloud"
    bus = EventBus()
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), bus)

    cfgs: list[dict] = []

    def fake_build(cfg):
        cfgs.append(cfg)
        return _Provider()

    from oryx import loop as loop_mod
    monkeypatch.setattr(loop_mod, "build_provider", fake_build)

    runtime.run("你好")
    assert cfgs, "模型没被构建"
    assert cfgs[0]["name"] == "local", f"路由没生效：{cfgs[0].get('name')}"
    routed = [e for e in events if e.get("type") == "model.routed"]
    assert routed and routed[0]["switched"] and routed[0]["name"] == "local"
