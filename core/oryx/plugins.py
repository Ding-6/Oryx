"""插件清单：兼容 WorkBuddy 的 `.codebuddy-plugin` 与 Claude Code 的 `.claude-plugin`。

一个插件目录长这样（两种清单目录名都认）：

    my-plugin/
    ├─ .codebuddy-plugin/plugin.json   （或 .claude-plugin/plugin.json）
    ├─ skills/           SKILL.md 技能
    ├─ commands/         斜杠命令（当作技能处理）
    ├─ agents/           子 Agent 定义（当作专家处理）
    └─ mcpServers        清单内声明，启动需显式授权

我们只取三样东西：技能、专家、MCP server 声明。其余字段读进来留着展示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_DIRS = (".codebuddy-plugin", ".claude-plugin", ".workbuddy-plugin")


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    root: Path
    manifest_path: Path
    skills_dir: Path | None = None
    agents_dir: Path | None = None
    commands_dir: Path | None = None
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "path": str(self.root),
            "mcp_servers": list(self.mcp_servers),
            "has_skills": self.skills_dir is not None,
        }


def _find_manifest(root: Path) -> Path | None:
    for dirname in MANIFEST_DIRS:
        candidate = root / dirname / "plugin.json"
        if candidate.exists():
            return candidate
    fallback = root / "plugin.json"
    return fallback if fallback.exists() else None


def _resolve(root: Path, value: Any) -> Path | None:
    """清单里的路径可能是字符串或数组，统一取第一个存在的目录。"""
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str) or not value:
        return None
    candidate = (root / value).resolve()
    return candidate if candidate.exists() else None


def load_plugin(manifest_path: Path) -> Plugin | None:
    try:
        data = json.loads(manifest_path.read_text("utf-8"))
    except Exception:
        return None
    root = manifest_path.parent.parent if manifest_path.parent.name.startswith(".") else manifest_path.parent

    skills_dir = _resolve(root, data.get("skills")) or (
        root / "skills" if (root / "skills").exists() else None
    )
    agents_dir = _resolve(root, data.get("agents")) or (
        root / "agents" if (root / "agents").exists() else None
    )
    commands_dir = _resolve(root, data.get("commands")) or (
        root / "commands" if (root / "commands").exists() else None
    )

    servers: dict[str, dict[str, Any]] = {}
    for name, spec in (data.get("mcpServers") or {}).items():
        if not isinstance(spec, dict):
            continue
        servers[name] = {
            "command": spec.get("command", ""),
            "args": spec.get("args", []),
            "env": spec.get("env", {}),
            "cwd": str(root),
        }

    return Plugin(
        name=data.get("name") or root.name,
        version=str(data.get("version", "")),
        description=data.get("description", ""),
        root=root,
        manifest_path=manifest_path,
        skills_dir=skills_dir,
        agents_dir=agents_dir,
        commands_dir=commands_dir,
        mcp_servers=servers,
        raw=data,
    )


def discover(root: Path) -> list[Plugin]:
    """扫描插件根目录，每个子目录最多识别一个插件。"""
    plugins: list[Plugin] = []
    if not root.exists():
        return plugins
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _find_manifest(child)
        if manifest is None:
            continue
        plugin = load_plugin(manifest)
        if plugin:
            plugins.append(plugin)
    return plugins
