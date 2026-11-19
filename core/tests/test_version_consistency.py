"""版本号一致性的测试。

问题
----
版本号散在三处：
  · `core/oryx/__init__.py` 的 `__version__`
  · `core/oryx/mcp.py` 里 MCP 客户端的 `clientInfo.version`
  · `CHANGELOG.md` 的最新条目

以前它们**各写各的** —— `__init__.py` 写着 `0.1.0`，
CHANGELOG 已经到 1.9.0 了。谁也不看谁。

这类漂移的害处：用户报 bug 时说的版本号对不上，
排查的人按错版本找；MCP server 看到的客户端版本也是错的。

要锁住的：
1. `__version__` 与 CHANGELOG 最新条目**一致**
2. MCP 客户端报的是 `__version__`（不是写死的）
3. 版本号有合法格式
"""
from __future__ import annotations

import re
from pathlib import Path

from oryx import __version__

# CHANGELOG 在仓库根 —— core/tests/ 往上两层
CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

# CHANGELOG 的条目标题：`## 1.9.0 — 2026-11-10`
_HEADING = re.compile(r"^##\s+(\d+\.\d+\.\d+)\s+—", re.M)


def _changelog_versions() -> list[str]:
    text = CHANGELOG.read_text("utf-8")
    return _HEADING.findall(text)


def test_version_has_valid_format():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), \
        f"版本号格式不对：{__version__}"


def test_changelog_latest_matches_version():
    """`__version__` 必须等于 CHANGELOG 的最新版本。

    改了版本忘了写 CHANGELOG（或反过来）—— 这条测试会红。
    """
    versions = _changelog_versions()
    assert versions, "CHANGELOG 里没找到版本条目"
    assert versions[0] == __version__, (
        f"版本对不上：`__version__` 是 {__version__}，"
        f"CHANGELOG 最新是 {versions[0]}"
    )


def test_changelog_versions_are_ordered():
    """CHANGELOG 从新到旧排列 —— 反了的话「最新版本」就找错了。"""
    versions = _changelog_versions()
    parsed = [tuple(int(x) for x in v.split(".")) for v in versions]
    for i in range(len(parsed) - 1):
        assert parsed[i] > parsed[i + 1], \
            f"CHANGELOG 版本顺序不对：{versions[i]} 之后是 {versions[i + 1]}"


def test_mcp_client_uses_package_version():
    """MCP 客户端报的是包版本，不是写死的字符串。"""
    from oryx import mcp

    assert mcp._version() == __version__


def test_mcp_client_version_not_hardcoded():
    """源码里不该再有写死的版本号。"""
    src = (Path(__file__).resolve().parents[1] / "oryx" / "mcp.py").read_text("utf-8")
    # 去掉注释里的版本号不算 —— 只查代码里的
    code = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    )
    assert '"version": "0.1.0"' not in code, "MCP 客户端版本写死了"
