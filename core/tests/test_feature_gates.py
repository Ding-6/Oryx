"""特性开关必须**真的**改变系统行为。

这条链路以前是装饰：20 个开关里 17 个在代码里零引用。
用户在设置页关掉「浏览器 Agent」，浏览器工具照旧可用 ——
界面上显示的「已关闭」是假的。

这组测试锁住：
1. 每个开关都要对应一组真实存在的工具；
2. 关掉开关 → 那组工具不进模型上下文；
3. 开关切换后**下一轮立即生效**（不用重启）；
4. 没实现的能力要如实标注，不能伪装成可切换的开关。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.features import (
    DEFAULT_FEATURES,
    FEATURE_GATES,
    UNIMPLEMENTED_FLAGS,
    FeatureFlags,
    feature_catalog,
    gated_tools,
)
from oryx.loop import AgentRuntime
from oryx.store import Store


def _write_flags(home: Path, flags: dict[str, bool], profile: str = "personal") -> Path:
    """写一份 features 配置（JSON 格式 —— 是 YAML 的子集，两边都能读）。"""
    path = home / "features.yaml"
    path.write_text(
        json.dumps({"profile": profile, "flags": flags}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _runtime(tmp_path: Path, flags: dict[str, bool] | None = None) -> AgentRuntime:
    """造一个 runtime，开关写到它自己的 config 路径下。"""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    if flags is not None:
        _write_flags(home, flags)

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    # features_path 是模块级常量，测试里要指到临时目录
    import oryx.config as config_mod

    config_mod.FEATURES_PATH = home / "features.yaml"
    store = Store(tmp_path / "oryx.db")
    return AgentRuntime(config, store, EventBus())


# ------------------------------------------------------------------ 映射本身


def test_every_gate_points_at_real_tools():
    """每个开关映射的工具必须真的存在 —— 映射错了等于没接。"""
    runtime = _runtime(Path(__import__("tempfile").gettempdir()) / "oryx-gate-probe")
    registered = set(runtime.registry)
    for feature, tools in FEATURE_GATES.items():
        for tool in tools:
            assert tool in registered, (
                f"{feature} 映射到不存在的工具 {tool} —— 映射错了，关掉它也没用"
            )


def test_gate_covers_all_enabled_features():
    """每个「已实现」的开关都要有工具组 —— 否则它又变成装饰。"""
    implemented = {
        name for name in DEFAULT_FEATURES
        if name.startswith("Enable") and name not in UNIMPLEMENTED_FLAGS
    }
    missing = implemented - set(FEATURE_GATES) - {
        "EnableFeatureFlags",   # 控制的是开关本身
        "EnableReaMcp",         # 控制的是 MCP server，不是内置工具
        "EnableExperts",        # 控制的是提示词注入，不是工具
        "EnableModelRouting",   # 控制的是执行期模型选择，不是工具暴露
    }
    assert not missing, f"这些开关还没有工具组，仍然是装饰：{sorted(missing)}"


def test_unimplemented_flags_have_no_tools():
    """未实现的能力不能映射到工具 —— 有映射就说明它实现了。"""
    for name in UNIMPLEMENTED_FLAGS:
        assert name not in FEATURE_GATES, f"{name} 声明未实现，却又有工具组"


# ------------------------------------------------------------------ 裁剪生效


def test_disabling_browser_removes_browser_tools(tmp_path: Path):
    """关掉浏览器 → 浏览器工具从上下文里消失。"""
    flags = dict(DEFAULT_FEATURES)
    flags["EnableBrowser"] = False
    runtime = _runtime(tmp_path, flags)

    exposed = set(runtime._exposed_registry())
    assert "browser_open" not in exposed, "关掉浏览器后 browser_open 还在"
    assert "browser_screenshot" not in exposed
    # 别的能力不该受影响
    assert "fs_read" in exposed


def test_disabling_office_removes_office_tools(tmp_path: Path):
    flags = dict(DEFAULT_FEATURES)
    flags["EnableOffice"] = False
    runtime = _runtime(tmp_path, flags)

    exposed = set(runtime._exposed_registry())
    assert "docx_write" not in exposed
    assert "xlsx_write" not in exposed
    assert "pptx_write" not in exposed


def test_disabling_subagents_removes_spawn_and_team(tmp_path: Path):
    flags = dict(DEFAULT_FEATURES)
    flags["EnableSubAgents"] = False
    runtime = _runtime(tmp_path, flags)

    exposed = set(runtime._exposed_registry())
    assert "spawn_agent" not in exposed
    assert "team_create" not in exposed
    assert "workflow_run" not in exposed


def test_disabling_sandbox_removes_sandbox_tool(tmp_path: Path):
    """DisableSandbox 是 Disable* 语义：真值 = 关掉沙箱工具。"""
    flags = dict(DEFAULT_FEATURES)
    flags["DisableSandbox"] = True
    runtime = _runtime(tmp_path, flags)

    exposed = set(runtime._exposed_registry())
    assert "shell_sandbox" not in exposed, "关掉沙箱后 shell_sandbox 还在"
    assert "shell" in exposed, "关沙箱不该影响裸 shell"


def test_all_enabled_keeps_everything(tmp_path: Path):
    """全开时不该误伤 —— 每个开关打开后，它管的工具都该在。

    注意这里用的是「**所有 Enable\* 都打开**」，不是 `DEFAULT_FEATURES` ——
    有些能力**默认就是关的**（`EnableWebSearch` 要外部 key、
    `EnableReaMcp` 要额外环境），它们对应的工具在默认配置下被裁掉是**对的**。

    早先这个测试直接用 `DEFAULT_FEATURES`，隐含假设「默认全开」——
    加一个默认关的能力就会误报。
    """
    all_on = {
        name: (False if name.startswith("Disable") else True)
        for name in DEFAULT_FEATURES
    }
    runtime = _runtime(tmp_path, all_on)
    exposed = set(runtime._exposed_registry())
    for feature, tools in FEATURE_GATES.items():
        if feature.startswith("Disable"):
            continue
        for tool in tools:
            assert tool in exposed, (
                f"{feature} 打开后 {tool} 还在被裁 —— 映射错了"
            )


def test_default_config_keeps_everything_except_opt_in_abilities(tmp_path: Path):
    """默认配置下，除了「需要额外条件」的能力，工具必须齐全。

    默认关的是那几项：要外部 key、要额外环境才能用的。
    """
    runtime = _runtime(tmp_path, dict(DEFAULT_FEATURES))
    exposed = set(runtime._exposed_registry())
    opt_in = {"web_search"}   # 需要用户自己配 key
    for feature, tools in FEATURE_GATES.items():
        if feature.startswith("Disable"):
            continue
        for tool in tools:
            if tool in opt_in:
                assert tool not in exposed, (
                    f"{tool} 需要用户自己配 key，默认不该暴露"
                )
                continue
            assert tool in exposed, f"{tool} 在默认配置下不该被裁掉"


# ------------------------------------------------------------------ 立即生效


def test_toggle_takes_effect_without_restart(tmp_path: Path):
    """用户改开关后，下一轮立即生效 —— 不用重启内核。

    这是「开关是真开关」的关键：注册时判断的话，用户点了开关，
    这一轮还是老样子，得重启才生效 —— 那还是半成品。
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    flags = dict(DEFAULT_FEATURES)
    flags["EnableBrowser"] = True
    _write_flags(home, flags)

    runtime = _runtime(tmp_path, flags)
    assert "browser_open" in runtime._exposed_registry(), "初始状态应该开着"

    # 用户在界面上关掉它
    _write_flags(home, {**flags, "EnableBrowser": False})

    exposed = runtime._exposed_registry()
    assert "browser_open" not in exposed, "改开关后下一轮就该生效"


def test_gated_tools_reads_disk_changes(tmp_path: Path):
    """缓存不能把改动挡住 —— 文件变了要重新读。"""
    flags = dict(DEFAULT_FEATURES)
    runtime = _runtime(tmp_path, flags)

    assert "ocr_image" in runtime._exposed_registry()
    _write_flags(tmp_path / "home", {**flags, "EnableOcr": False})
    assert "ocr_image" not in runtime._exposed_registry()


# ------------------------------------------------------------------ 读取健壮性


def test_flags_file_written_as_json_is_readable(tmp_path: Path):
    """写 JSON、读 JSON —— 不依赖 PyYAML。

    以前写的是 JSON，读的时候 PyYAML 缺失就把整个文件丢弃 ——
    用户手改的开关全部失效，且没有任何提示。
    """
    path = tmp_path / "features.yaml"
    path.write_text(
        json.dumps({"profile": "personal", "flags": {"EnableBrowser": False}}),
        encoding="utf-8",
    )
    flags = FeatureFlags.load(path)
    assert flags.flags["EnableBrowser"] is False
    assert flags.is_enabled("EnableBrowser") is False


def test_profile_is_honored(tmp_path: Path):
    """profile 不再被硬编码成 personal —— 用户写 team 就该生效。"""
    path = tmp_path / "features.yaml"
    path.write_text(
        json.dumps({"profile": "team", "flags": {}}), encoding="utf-8"
    )
    flags = FeatureFlags.load(path)
    # team profile 把 DisableWebSearch 翻成 False（= 开启）
    assert flags.is_enabled("DisableWebSearch") is True


def test_broken_flags_file_does_not_crash(tmp_path: Path):
    """坏文件不能让整个内核起不来。"""
    path = tmp_path / "features.yaml"
    path.write_text("这不是 JSON 也不是 YAML: {{{", encoding="utf-8")
    flags = FeatureFlags.load(path)
    # 退回默认值，至少 EnableOffice 还在
    assert flags.is_enabled("EnableOffice") is True


# ------------------------------------------------------------------ 目录标注


def test_catalog_marks_unimplemented_flags():
    """未实现的能力要标 implemented=False —— 界面据此如实显示。"""
    cards = {c["key"]: c for c in feature_catalog(FeatureFlags(flags=dict(DEFAULT_FEATURES)))}
    assert cards["EnableBrowser"]["implemented"] is True
    assert cards["DisableWebSearch"]["implemented"] is False
    assert "暂无" in cards["DisableWebSearch"]["description"]


def test_gated_tools_helper():
    """gated_tools 独立可用（给不依赖 AgentRuntime 的调用方）。"""
    flags = FeatureFlags(flags={**DEFAULT_FEATURES, "EnableBrowser": False})
    blocked = gated_tools(flags)
    assert "browser_open" in blocked
    assert "fs_read" not in blocked
