"""MCP 配置文件：三个作用域 + JSONC + 环境变量展开。

对齐 WorkBuddy 的 .mcp.json 设计：

作用域（同名服务器后者覆盖前者，不是合并）
    user     ~/.oryx/.mcp.json
    project  <工作区>/.mcp.json
    local    ~/.oryx.json#/projects/<工作区路径>

同作用域内**只取第一个存在的文件**，不合并 —— 用户把配置写进第二个文件
却"不生效"是最常见的困惑来源，所以这里明确只认第一个。

格式是 JSONC：支持 // 注释、/* */ 注释和尾随逗号。

环境变量展开：${VAR} 与 ${VAR:-默认值}。
只有**值**能展开，键不能 —— 服务器名带变量会让工具前缀变得不可预测。
变量未设置且无默认值时保留占位符原文并告警，不静默替换成空串。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import app_dir

# ${VAR} / ${VAR:-default}
_ENV = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")
_VAR_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# 同名服务器的优先级：local > project > user
SCOPES = ("user", "project", "local")


def _strip_jsonc(text: str) -> str:
    """去掉注释和尾随逗号，让 JSONC 能被 json 模块解析。"""
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        out.append(ch)
        i += 1
    # 去掉尾随逗号
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return cleaned


def _expand(value: Any, warnings: list[str] | None = None) -> Any:
    """递归展开字符串里的环境变量。"""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            got = os.environ.get(name)
            if got is not None:
                return got
            if default is not None:
                return default
            if warnings is not None:
                warnings.append(f"环境变量 {name} 未设置，保留占位符")
            return match.group(0)

        return _ENV.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v, warnings) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, warnings) for v in value]
    return value


def _read_jsonc(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text("utf-8")
    except OSError:
        return None
    try:
        data = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def user_config_path() -> Path:
    return app_dir() / ".mcp.json"


def project_config_path(workspace: str | Path | None) -> Path | None:
    if not workspace:
        return None
    return Path(workspace) / ".mcp.json"


def local_config_path(workspace: str | Path | None) -> Path | None:
    """local 作用域存在 ~/.oryx.json 的 projects 段里。"""
    if not workspace:
        return None
    path = app_dir() / ".oryx.json"
    if not path.exists():
        return None
    data = _read_jsonc(path)
    if not data:
        return None
    key = str(Path(workspace).resolve())
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return None
    section = projects.get(key)
    return section if isinstance(section, dict) else None


def load_servers(
    workspace: str | Path | None = None,
    warnings: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """按 user → project → local 的顺序合并同名服务器。

    返回 {服务器名: 完整配置}，配置里已带上 name 字段。
    """
    merged: dict[str, dict[str, Any]] = {}

    # 1. user
    user = _read_jsonc(user_config_path())
    if user:
        _absorb(merged, user.get("mcpServers") or user, warnings)

    # 2. project
    ppath = project_config_path(workspace)
    if ppath and ppath.exists():
        project = _read_jsonc(ppath)
        if project:
            _absorb(merged, project.get("mcpServers") or project, warnings)

    # 3. local（最高优先级）
    local = local_config_path(workspace)
    if local:
        _absorb(merged, local.get("mcpServers") or local, warnings)

    return merged


def _absorb(
    target: dict[str, dict[str, Any]],
    servers: Any,
    warnings: list[str] | None,
) -> None:
    if not isinstance(servers, dict):
        return
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        # 文件里可能有 type/command/url 等顶层键，也可能是 {"mcpServers": {...}} 包一层
        if "mcpServers" in spec and isinstance(spec["mcpServers"], dict):
            _absorb(target, spec["mcpServers"], warnings)
            continue
        entry = _expand(dict(spec), warnings)
        entry.setdefault("name", str(name))
        target[str(name)] = entry


def tool_defer_map(servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """从服务器配置里抽出 defer_loading 规则。

    返回 {服务器名: {"defer": [工具名...], "nodefer": [工具名...]}}。

    继承规则（对齐 WorkBuddy）：
        服务器级 true  + 工具级未设  → 延迟
        服务器级 true  + 工具级 false → 不延迟
        服务器级 false + 工具级 true  → 延迟
        服务器级 false + 工具级未设  → 不延迟
    工具级永远覆盖服务器级。
    """
    result: dict[str, dict[str, list[str]]] = {}
    for name, spec in servers.items():
        server_level = bool(spec.get("defer_loading"))
        defer: list[str] = []
        nodefer: list[str] = []
        for tool in spec.get("tools") or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            tn = str(tool["name"])
            tool_level = tool.get("defer_loading")
            if tool_level is None:
                effective = server_level
            else:
                effective = bool(tool_level)
            (defer if effective else nodefer).append(tn)
        if not server_level and not defer and not nodefer:
            continue
        result[name] = {"defer": defer, "nodefer": nodefer}
    return result


def summarize(servers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """给界面看的服务器清单。"""
    items = []
    for name, spec in sorted(servers.items()):
        kind = str(spec.get("type") or ("stdio" if spec.get("command") else "http"))
        items.append({
            "name": name,
            "type": kind,
            "target": str(spec.get("command") or spec.get("url") or ""),
            "defer_loading": bool(spec.get("defer_loading")),
            "tool_count": len(spec.get("tools") or []),
        })
    return items
