"""管理工作区：provider 增删改、工作目录切换、批量安装、应用库。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.config import Config, DEFAULT_CONFIG
from oryx.manage import (
    delete_provider,
    detect_kind,
    install_extensions,
    list_installed_apps,
    mask_key,
    provider_template,
    switch_provider,
    switch_workspace,
    upsert_provider,
)


@pytest.fixture()
def config(tmp_path: Path, monkeypatch) -> Config:
    data = dict(DEFAULT_CONFIG)
    # 默认配置里不再预置本地 provider（写死的模型名早就不在本机了），
    # 测试要自己造一条本地 provider。
    data["providers"] = {
        "anthropic": dict(DEFAULT_CONFIG["providers"]["anthropic"]),
        "ollama": {
            "type": "openai",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "ollama",
            "model": "qwen",
        },
    }
    cfg = Config(data)
    monkeypatch.setattr("oryx.manage.Config.save", lambda self: None, raising=False)
    monkeypatch.setattr("oryx.config.Config.save", lambda self: None, raising=False)
    return cfg


# ---------------------------------------------------------------- provider


def test_upsert_new_provider(config):
    result = upsert_provider(
        config,
        "my-llm",
        {"type": "openai", "base_url": "http://192.168.1.10:8000/v1", "model": "qwen3-32b"},
    )
    assert result["name"] == "my-llm"
    assert config.data["providers"]["my-llm"]["base_url"] == "http://192.168.1.10:8000/v1"


def test_upsert_updates_existing(config):
    upsert_provider(config, "ollama", {"type": "openai", "base_url": "http://x/v1", "model": "m2"})
    assert config.data["providers"]["ollama"]["model"] == "m2"
    assert config.data["providers"]["ollama"]["base_url"] == "http://x/v1"


def test_upsert_rejects_bad_type(config):
    with pytest.raises(ValueError):
        upsert_provider(config, "bad", {"type": "gemini", "base_url": "x", "model": "m"})


def test_upsert_rejects_empty_name(config):
    with pytest.raises(ValueError):
        upsert_provider(config, "", {"type": "openai", "base_url": "x", "model": "m"})


def test_switch_provider(config):
    assert switch_provider(config, "ollama")["active_provider"] == "ollama"
    with pytest.raises(ValueError):
        switch_provider(config, "nope")


def test_delete_provider(config):
    assert delete_provider(config, "ollama") is True
    assert "ollama" not in config.data["providers"]
    assert delete_provider(config, "ollama") is False


def test_cannot_delete_active_provider(config):
    config.data["active_provider"] = "ollama"
    with pytest.raises(ValueError):
        delete_provider(config, "ollama")


def test_mask_key_hides_secret():
    masked = mask_key({"api_key": "sk-1234567890abcdef", "model": "m"})
    assert "1234567890" not in masked["api_key"]
    assert masked["has_key"] is True
    assert mask_key({"api_key": ""})["has_key"] is False


def test_provider_template_defaults():
    assert provider_template("anthropic")["type"] == "anthropic"
    assert provider_template("openai")["type"] == "openai"


# ---------------------------------------------------------------- 工作目录


def test_switch_workspace(config, tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    result = switch_workspace(config, str(target))
    assert result["workspace"] == str(target.resolve())
    assert str(target.resolve()) in result["allowed_roots"]


def test_switch_workspace_rejects_missing(config):
    with pytest.raises(ValueError):
        switch_workspace(config, "D:/definitely/not/here")


def test_switch_workspace_rejects_file(config, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        switch_workspace(config, str(f))


# ---------------------------------------------------------------- 批量安装


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    (source / "my-skill").mkdir(parents=True)
    (source / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 测试技能\n---\n步骤", encoding="utf-8"
    )
    (source / "my-expert").mkdir(parents=True)
    (source / "my-expert" / "EXPERT.md").write_text(
        "---\nname: 测试专家\ndescription: d\n---\n正文", encoding="utf-8"
    )
    plugin = source / "my-plugin" / ".claude-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "my-plugin", "version": "1.0"}), encoding="utf-8"
    )
    (source / "not-a-plugin").mkdir()
    (source / "not-a-plugin" / "readme.txt").write_text("x", encoding="utf-8")
    return source


def test_detect_kind(tmp_path):
    source = make_source(tmp_path)
    assert detect_kind(source / "my-skill") == "skill"
    assert detect_kind(source / "my-expert") == "expert"
    assert detect_kind(source / "my-plugin") == "plugin"
    assert detect_kind(source / "not-a-plugin") is None


def test_install_extensions_bulk(tmp_path):
    source = make_source(tmp_path)
    root = tmp_path / "project"
    root.mkdir()

    result = install_extensions(str(source), root)
    assert sorted(result.installed) == ["expert:my-expert", "plugin:my-plugin", "skill:my-skill"]
    assert (root / "skills" / "my-skill" / "SKILL.md").exists()
    assert (root / "experts" / "my-expert" / "EXPERT.md").exists()
    assert (root / "plugins" / "my-plugin" / ".claude-plugin" / "plugin.json").exists()
    assert result.skipped  # not-a-plugin 被跳过


def test_install_extensions_reinstall_overwrites(tmp_path):
    source = make_source(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    install_extensions(str(source), root)

    (source / "my-skill" / "SKILL.md").write_text("---\nname: changed\n---\n新内容", encoding="utf-8")
    result = install_extensions(str(source), root)
    text = (root / "skills" / "my-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "changed" in text
    assert result.installed


def test_install_extensions_missing_source(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    result = install_extensions(str(tmp_path / "nope"), root)
    assert result.errors


def test_install_extensions_filter_kinds(tmp_path):
    source = make_source(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    result = install_extensions(str(source), root, kinds=("skill",))
    assert result.installed == ["skill:my-skill"]
    assert not (root / "experts" / "my-expert").exists()


# ---------------------------------------------------------------- 应用库


def test_list_installed_apps():
    result = list_installed_apps(limit=50)
    assert result["count"] >= 0
    assert isinstance(result["apps"], list)
    for app in result["apps"]:
        assert {"name", "version", "publisher", "source"} <= set(app)


def test_installed_apps_are_all_launchable():
    """应用库里每一条都得**真的能打开**。

    注册表里会留着一大堆"卸载记录"（VC++ 运行时、Windows SDK、
    IntelliSense…），它们没有可启动的目标 —— 显示出来点了没反应，
    比不显示更糟。
    """
    from oryx.appicon import launch_target

    result = list_installed_apps(limit=1000)
    ghosts = [
        app["name"]
        for app in result["apps"]
        if app["source"] == "registry" and not launch_target(app)
    ]
    assert not ghosts, f"这些条目打不开：{ghosts[:10]}"


def test_installed_apps_expose_display_icon():
    """注册表条目要带上 DisplayIcon —— 这是最权威的图标声明。"""
    result = list_installed_apps(limit=1000)
    registry = [a for a in result["apps"] if a["source"] == "registry"]
    if registry:
        assert any(a.get("display_icon") for a in registry), (
            "注册表条目一个 DisplayIcon 都没有，图标提取会大面积失败"
        )


def test_installed_apps_cache_returns_same_data():
    """缓存命中时给的数据必须和冷启动一致 —— 不能少字段。"""
    from oryx import manage

    manage.invalidate_app_cache()
    cold = manage._cached_installed_apps(200)
    warm = manage._cached_installed_apps(200)
    assert [a["name"] for a in cold["apps"]] == [a["name"] for a in warm["apps"]]
    assert len(warm["apps"]) <= len(cold["apps"])


def test_installed_apps_cache_invalidated_on_custom_change(tmp_path, monkeypatch):
    """新增自定义应用后，缓存要失效 —— 否则新应用不出现。"""
    from oryx import manage

    monkeypatch.setenv("ORYX_HOME", str(tmp_path))
    manage.invalidate_app_cache()
    exe = tmp_path / "mytool.exe"
    exe.write_bytes(b"MZ")
    before = {a["name"] for a in manage._cached_installed_apps(1000)["apps"]}
    manage.save_custom_app({"name": "我的工具", "target": str(exe)})
    after = {a["name"] for a in manage._cached_installed_apps(1000)["apps"]}
    assert "我的工具" not in before
    assert "我的工具" in after
