"""两段式工具加载：延迟暴露 + 按需展开。

问题：Oryx 有 68 个内置工具，加上 MCP 与插件工具能到上百个。全部塞进
模型上下文既贵又干扰 —— 模型会被一屏工具名带偏，注意力花在"选工具"上。

WorkBuddy 的解法是**两段式**：
    第一段  上下文里只放常用工具 + ToolSearch（检索工具）+ DeferExecuteTool（执行工具）
    第二段  模型用 ToolSearch 找到需要的工具，再用 DeferExecuteTool 调用它

这样上下文里常驻的工具从上百个降到十几个，但模型依然能用全部能力。

Oryx 的对应物：
    tool_search    已经是语义检索的 ToolSearch —— 直接复用，不另造
    defer_execute  新增的 DeferExecuteTool —— 延迟工具的统一入口

暴露策略的来源（三处，优先级从高到低）：
    1. NoDefer(X)   显式豁免，恒定优先，任何地方都盖不掉
    2. Defer(X)     显式延迟
    3. MCP 工具级 defer_loading
    4. MCP 服务器级 defer_loading
    5. 配置里的 defer_tools

只作用于「工具列表」这个维度 —— 权限判定、风险分级一律照常走，
延迟加载只影响"模型能不能看见"，不影响"模型能不能用"。
"""

from __future__ import annotations

import fnmatch
import re
from typing import Iterable

# Defer(名字) / NoDefer(名字)
_MODIFIER = re.compile(r"\b(NoDefer|Defer)\s*\(\s*([^)]*?)\s*\)", re.IGNORECASE)


def parse_modifiers(spec: str) -> tuple[set[str], set[str]]:
    """从工具规格串里解析出 (defer 集合, nodefer 集合)。

    支持 `Defer(mcp__github__*)` 这种通配 —— 只有 `*` 本身能通配，
    不能反向枚举（"除了 X 之外全延迟"没法表达），这一点与 WorkBuddy 一致。
    """
    defer: set[str] = set()
    nodefer: set[str] = set()
    for match in _MODIFIER.finditer(spec or ""):
        kind = match.group(1).lower()
        target = match.group(2).strip()
        if not target:
            continue
        (defer if kind == "defer" else nodefer).add(target)
    return defer, nodefer


class DeferPolicy:
    """决定哪些工具延迟暴露。

    用法：
        policy = DeferPolicy()
        policy.add_spec("default,Defer(mcp__github__*)")
        policy.defer_server("github", ["create_issue", "list_repos"])
        policy.should_defer("mcp__github__create_issue")   # -> True
        policy.should_defer("fs_read")                      # -> False
    """

    def __init__(self) -> None:
        self._defer: set[str] = set()
        self._nodefer: set[str] = set()
        # 已经被 tool_search 找到并展开的工具（本次会话内不再算延迟）
        self._materialized: set[str] = set()

    # ---------------------------------------------------------------- 配置

    def add_spec(self, spec: str) -> None:
        """解析并加入一份工具规格串。"""
        defer, nodefer = parse_modifiers(spec)
        self._defer |= defer
        self._nodefer |= nodefer

    def defer_patterns(self, patterns: Iterable[str]) -> None:
        """加入一批延迟规则（配置里的 defer_tools 就是走这里）。"""
        self._defer |= {p.strip() for p in patterns if p and p.strip()}

    def defer_server(self, server: str, tool_names: Iterable[str]) -> None:
        """MCP 服务器/工具级 defer_loading。

        工具级优先于服务器级：服务器级 defer=True 时工具级可单独豁免。
        """
        names = [n for n in tool_names if n]
        if names:
            self._defer |= {f"mcp__{server}__{n}" for n in names}
        else:
            self._defer.add(f"mcp__{server}__*")

    def undefer_server(self, server: str, tool_names: Iterable[str]) -> None:
        """工具级 defer_loading=false —— 服务器级延迟但这个工具不延迟。"""
        names = [n for n in tool_names if n]
        if names:
            self._nodefer |= {f"mcp__{server}__{n}" for n in names}

    def nodefer(self, pattern: str) -> None:
        """给单个工具名加豁免（不带服务器前缀时直接用工具名匹配）。"""
        pattern = (pattern or "").strip()
        if pattern:
            self._nodefer.add(pattern)

    # ---------------------------------------------------------------- 判定

    def _match(self, name: str, patterns: set[str]) -> bool:
        for pattern in patterns:
            if pattern == name:
                return True
            if "*" in pattern and fnmatch.fnmatchcase(name, pattern):
                return True
        return False

    def should_defer(self, name: str) -> bool:
        """这个工具是否延迟暴露。"""
        if name in self._materialized:
            return False
        # NoDefer 恒定优先
        if self._match(name, self._nodefer):
            return False
        return self._match(name, self._defer)

    def materialize(self, name: str) -> None:
        """标记为已展开 —— 之后不再延迟。"""
        self._materialized.add(name)

    def is_materialized(self, name: str) -> bool:
        return name in self._materialized

    # ---------------------------------------------------------------- 状态

    def split(self, registry: dict) -> tuple[list, list]:
        """把工具集拆成 (立即暴露, 延迟) 两组。"""
        exposed, deferred = [], []
        for name in registry:
            (deferred if self.should_defer(name) else exposed).append(name)
        return exposed, deferred

    def status(self) -> dict:
        return {
            "defer_patterns": sorted(self._defer),
            "nodefer_patterns": sorted(self._nodefer),
            "materialized": sorted(self._materialized),
        }
