"""插件清单兼容：`.codebuddy-plugin` 与 `.claude-plugin` 两种格式。"""

from __future__ import annotations

import json
from pathlib import Path

from oryx.plugins import discover, load_plugin
from oryx.skills import SkillLibrary

SKILL_MD = """---
name: demo-skill
description: 插件自带的示例技能
---

步骤：先这样，再那样。
"""


def make_plugin(root: Path, manifest_dir: str = ".codebuddy-plugin", with_mcp: bool = True) -> Path:
    plugin = root / "demo-plugin"
    manifest = plugin / manifest_dir
    manifest.mkdir(parents=True)
    data = {
        "name": "demo-plugin",
        "version": "1.2.3",
        "description": "测试用插件",
        "skills": "./skills",
    }
    if with_mcp:
        data["mcpServers"] = {
            "demo": {"command": "node", "args": ["mcp/start.mjs"], "env": {"A": "1"}}
        }
    (manifest / "plugin.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    skills = plugin / "skills" / "demo-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(SKILL_MD, "utf-8")
    return plugin


def test_discover_codebuddy_manifest(tmp_path):
    make_plugin(tmp_path)
    plugins = discover(tmp_path)
    assert len(plugins) == 1

    plugin = plugins[0]
    assert plugin.name == "demo-plugin"
    assert plugin.version == "1.2.3"
    assert plugin.skills_dir and plugin.skills_dir.exists()
    assert "demo" in plugin.mcp_servers
    assert plugin.mcp_servers["demo"]["command"] == "node"


def test_discover_claude_manifest(tmp_path):
    make_plugin(tmp_path, manifest_dir=".claude-plugin")
    plugins = discover(tmp_path)
    assert len(plugins) == 1
    assert plugins[0].skills_dir


def test_plugin_skills_load(tmp_path):
    plugin_dir = make_plugin(tmp_path)
    plugin = load_plugin(plugin_dir / ".codebuddy-plugin" / "plugin.json")
    assert plugin

    library = SkillLibrary(tmp_path / "empty")
    library.add_root(plugin.skills_dir)
    assert "demo-skill" in library.skills
    assert "示例技能" in library.skills["demo-skill"].description


def test_dir_without_manifest_ignored(tmp_path):
    (tmp_path / "just-a-folder").mkdir()
    assert discover(tmp_path) == []


def test_summary_shape(tmp_path):
    make_plugin(tmp_path)
    summary = discover(tmp_path)[0].summary()
    assert set(summary) >= {"name", "version", "description", "path", "mcp_servers"}
