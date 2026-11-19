"""统一工具表 —— 内置、扩展、MCP 三种来源合成一张。

为什么要有
----------
工具原本有三个来源，各自登记：

| 来源 | 登记在哪 | 命名 |
|---|---|---|
| 内置 | `tools.py` 的 `build_registry()` + `loop.py` 里 70 段注册 | `fs_read` |
| 扩展 | `toolplug.py` 加载后写进 `runtime.registry` | 扩展自定义 |
| MCP | `loop.py` 的 `_register_mcp_tools()` | `mcp__<server>__<name>` |

问题不在「代码放哪个文件」，而在：

1. **没有冲突检测** —— 扩展工具与内置同名时，
   `self.registry[name] = spec` 会**静默覆盖**。
   一个扩展可以把 `fs_read` 换成返回任意内容的实现，
   而模型看到的工具清单毫无异常。**这是安全问题。**
2. **没有统一视图** —— 「现在到底有哪些工具、谁提供的、什么风险」
   要翻三个地方才能拼出来。
3. **没有统一校验** —— schema 是否完整、risk 是否合法，
   靠各写各的时没人管。

所以这里只加**声明层与校验层**，不动实现 ——
把 70 段闭包注册搬去重构成纯体力活且回归风险高，收益却小。

优先级（谁能覆盖谁）
--------------------
    内置 > MCP > 扩展

扩展**永远不能**覆盖内置或 MCP 的工具。
同名时**拒绝并记录**，不静默替换 —— 用户能在加载报告里看到。

借鉴
----
* ECC 的「同层多 provider 不覆盖，跨层才覆盖」——
  这里改成更严的「扩展不能覆盖任何内置来源」。
* agent-browser 的能力集 —— 每个工具都带来源与信任级别。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

# ── mutates 声明的启发式检查 ──────────────────────────────────────────
#
# 为什么需要
# ----------
# `mutates` 决定工具结果里那句「我没动过任何东西」是真是假
#（`loop.py`：`result["mutated"] = bool(spec.mutates)`）。
# 声明漏了就是在**说谎** —— 比不说更糟。
#
# 但 `mutates` 靠手工维护的 `MUTATING_TOOLS` 清单，
# 散着写一定会有人漏。所以这里做**启发式复核**：
# 名字里带「明显会改动」的动词，却没声明 mutates → 报出来。
#
# 启发式不可能替代人判断 —— 它的价值是**把可疑项推到人眼前**，
# 而不是替人决定。

# 名字里出现这些词，通常意味着会改动状态。
MUTATING_NAME_HINTS: tuple[str, ...] = (
    "write", "delete", "remove", "move", "rename", "create", "update",
    "save", "forget", "insert", "append", "replace", "modify", "edit",
    "patch", "put", "add", "set", "toggle", "clear", "purge", "restore",
    "run", "exec", "execute", "spawn", "dispatch", "send", "publish",
    "install", "uninstall", "upload", "download", "export", "import",
    "sync", "lock", "power", "notify", "launch", "apply", "deploy",
    "commit", "push", "merge", "rebase", "checkout", "format",
    "rewind", "cancel", "interrupt", "start", "stop", "kill", "close",
    "open", "click", "type", "screenshot", "capture", "generate", "render",
    "switch", "shell", "rewind", "invoke", "call", "apply", "bind",
)

# 这些词说明「看着像动词但其实是只读」—— 豁免。
#
# 例：`browser_open` 确实会切页面（清单里已声明），
# 但 `sys_power_status` 只是查状态 —— 含 "power" 不代表会改。
READONLY_NAME_HINTS: tuple[str, ...] = (
    "status", "stat", "list", "read", "search", "find", "count", "check",
    "show", "view", "query", "describe", "inspect", "report", "get",
    "info", "index", "scan", "grep", "glob", "snapshot", "preview",
)


class Source(IntEnum):
    """工具来源。数值越大优先级越高（冲突时保留优先级高的）。"""

    EXTENSION = 10     # 扩展提供的
    MCP = 20           # MCP server 提供的
    BUILTIN = 30       # 产品内置


SOURCE_NAMES = {
    Source.EXTENSION: "extension",
    Source.MCP: "mcp",
    Source.BUILTIN: "builtin",
}


@dataclass
class ToolDecl:
    """一个工具的统一声明视图。

    从三种来源里**提取**出来的 —— 不管它原来长什么样，
    对外都是这一个结构。
    """

    name: str
    description: str
    source: Source
    source_detail: str = ""      # 扩展 id / MCP server 名
    trust: str = "builtin"
    risk: int = 0
    risk_label: str = "只读"
    mutates: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": SOURCE_NAMES.get(self.source, "?"),
            "source_detail": self.source_detail,
            "trust": self.trust,
            "risk": self.risk,
            "risk_label": self.risk_label,
            "mutates": self.mutates,
            "group": self.group,
        }


@dataclass
class Conflict:
    """一次命名冲突。"""

    name: str
    kept: str          # 保留了谁的
    rejected: str      # 拒绝了谁的

    def describe(self) -> str:
        return f"{self.name}：保留了 {self.kept}，拒绝了 {self.rejected}"


class ToolTable:
    """所有工具的统一表。

    用法：

        table = ToolTable()
        table.add_builtin(spec)          # 内置
        table.add_mcp(spec, server="x")  # MCP
        table.add_extension(spec, ext_id="y")  # 扩展

        table.conflicts   # → list[Conflict]
        table.problems    # → list[str]（schema 不完整等）
        table.all()       # → list[ToolDecl]
    """

    def __init__(self) -> None:
        self._decls: dict[str, ToolDecl] = {}
        self._sources: dict[str, str] = {}
        self.conflicts: list[Conflict] = []
        self.problems: list[str] = []

    # ── 登记 ────────────────────────────────────────────────────────
    def _add(self, decl: ToolDecl) -> bool:
        """登记一个工具。返回是否成功（False = 因冲突被拒）。"""
        existing = self._decls.get(decl.name)
        if existing is not None:
            # 优先级低的被拒 —— 扩展永远不能覆盖内置/MCP
            if decl.source < existing.source:
                self.conflicts.append(Conflict(
                    name=decl.name,
                    kept=f"{SOURCE_NAMES.get(existing.source, '?')}"
                         f"{'/' + existing.source_detail if existing.source_detail else ''}",
                    rejected=f"{SOURCE_NAMES.get(decl.source, '?')}"
                             f"{'/' + decl.source_detail if decl.source_detail else ''}",
                ))
                return False
            # 同优先级：先来的赢（内置之间不该重名，重名要报出来）
            self.conflicts.append(Conflict(
                name=decl.name,
                kept=f"{SOURCE_NAMES.get(existing.source, '?')}",
                rejected=f"{SOURCE_NAMES.get(decl.source, '?')}"
                         f"{'/' + decl.source_detail if decl.source_detail else ''}"
                         f"（同级重名）",
            ))
            if decl.source > existing.source:
                self._decls[decl.name] = decl
            return False
        self._decls[decl.name] = decl
        return True

    def add_builtin(self, spec: Any) -> bool:
        """登记内置工具。"""
        return self._add(ToolDecl(
            name=spec.name,
            description=spec.description,
            source=Source.BUILTIN,
            source_detail="",
            trust="builtin",
            risk=int(spec.risk),
            risk_label=getattr(spec.risk, "label", ""),
            mutates=bool(getattr(spec, "mutates", False)),
            parameters=dict(getattr(spec, "parameters", {}) or {}),
            group=spec.name.split("_")[0] if "_" in spec.name else spec.name,
        ))

    def add_mcp(self, spec: Any, server: str = "") -> bool:
        """登记 MCP 工具。"""
        return self._add(ToolDecl(
            name=spec.name,
            description=spec.description,
            source=Source.MCP,
            source_detail=server,
            trust="mcp",
            risk=int(spec.risk),
            risk_label=getattr(spec.risk, "label", ""),
            mutates=bool(getattr(spec, "mutates", False)),
            parameters=dict(getattr(spec, "parameters", {}) or {}),
            group="mcp",
        ))

    def add_extension(self, spec: Any, ext_id: str = "", trust: str = "user") -> bool:
        """登记扩展工具。

        **扩展永远不能覆盖内置/MCP** —— 同名会被拒并记进 `conflicts`。
        """
        return self._add(ToolDecl(
            name=spec.name,
            description=spec.description,
            source=Source.EXTENSION,
            source_detail=ext_id,
            trust=trust,
            risk=int(spec.risk),
            risk_label=getattr(spec.risk, "label", ""),
            mutates=bool(getattr(spec, "mutates", False)),
            parameters=dict(getattr(spec, "parameters", {}) or {}),
            group=spec.name.split("_")[0] if "_" in spec.name else spec.name,
        ))

    # ── 查询 ────────────────────────────────────────────────────────
    def all(self) -> list[ToolDecl]:
        return [self._decls[k] for k in sorted(self._decls)]

    def get(self, name: str) -> ToolDecl | None:
        return self._decls.get(name)

    def by_source(self, source: Source) -> list[ToolDecl]:
        return [d for d in self.all() if d.source == source]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.all():
            key = SOURCE_NAMES.get(d.source, "?")
            out[key] = out.get(key, 0) + 1
        return out

    # ── 校验 ────────────────────────────────────────────────────────
    def validate(self) -> list[str]:
        """检查每个工具的声明是否完整。

        返回问题列表（空 = 全部合规）。

        查什么：
          · schema 结构对不对（type/properties/required）
          · 有没有可用的 description（模型要靠它选工具）
          · risk 是不是合法值
        """
        problems: list[str] = []
        for decl in self.all():
            params = decl.parameters
            if not isinstance(params, dict) or not params:
                problems.append(f"{decl.name}：parameters 为空或不是对象")
                continue
            ptype = params.get("type")
            if ptype != "object":
                problems.append(f"{decl.name}：parameters.type 应为 object，实际 {ptype!r}")
            props = params.get("properties")
            if props is None:
                problems.append(f"{decl.name}：parameters 缺 properties")
            elif not isinstance(props, dict):
                problems.append(f"{decl.name}：parameters.properties 不是对象")
            req = params.get("required")
            if req is not None and not isinstance(req, list):
                problems.append(f"{decl.name}：parameters.required 不是数组")
            if not decl.description.strip():
                problems.append(f"{decl.name}：缺 description（模型要靠它选工具）")
        self.problems = problems
        return problems

    def check_mutates(self) -> list[str]:
        """复核 mutates 声明 —— 找出「看着会改动却说自己只读」的工具。

        漏声明的后果是**谎报**：工具结果里那句「我没动过任何东西」
        变成假的（`loop.py`：`result["mutated"] = bool(spec.mutates)`）。
        比不说更糟。

        这是**启发式**，不是判定 —— 它的作用是把可疑项推到人眼前。

        匹配方式：**按 `_` 分词后比对完整词**，不用子串。
        子串匹配会造成 100% 误报 —— 第一版就翻过车：
          · `use_skill` 里的 "kill"（读技能，不改状态）
          · `sys_input_methods` 里的 "put"（列出输入法，不改状态）
          · `sys_display_settings` 里的 "set"（读分辨率，不改状态）
        分词后这些都不再命中，而 `fs_write` / `term_run` / `browser_click`
        照样能被抓出来。
        """
        suspicious: list[str] = []
        for decl in self.all():
            if decl.mutates:
                continue
            words = {w for w in decl.name.lower().split("_") if w}
            # 词里明确只读 → 豁免
            if words & set(READONLY_NAME_HINTS):
                continue
            hits = sorted(words & set(MUTATING_NAME_HINTS))
            if hits:
                suspicious.append(
                    f"{decl.name}：名字里有 {hits[0]!r} 却没声明 mutates —— "
                    f"确认它是否真的会改动状态"
                )
        return suspicious

    # ── 导出 ────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self._decls),
            "counts": self.counts(),
            "conflicts": [
                {"name": c.name, "kept": c.kept, "rejected": c.rejected}
                for c in self.conflicts
            ],
            "problems": list(self.problems),
            "tools": [d.to_dict() for d in self.all()],
        }

    def summary(self) -> str:
        lines = [f"工具 {len(self._decls)} 个：{self.counts()}"]
        if self.conflicts:
            lines.append(f"命名冲突 {len(self.conflicts)} 处：")
            for c in self.conflicts:
                lines.append(f"  · {c.describe()}")
        if self.problems:
            lines.append(f"声明问题 {len(self.problems)} 处：")
            for p in self.problems[:10]:
                lines.append(f"  · {p}")
        return "\n".join(lines)
