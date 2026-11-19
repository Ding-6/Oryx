"""扩展注册表 —— Oryx 的扩展总线。

为什么要有这个
--------------
改造前的现状：`skills.py` / `experts.py` / `agents.py` 各自写了一遍
「扫目录 → 解析 frontmatter → 建对象」的逻辑。加一种扩展类型就得
**复制一遍**整套发现代码，四五套并行，越加越乱。

改造后的形态：**唯一的发现入口** `Registry.discover()`，
所有扩展类型（技能 / 专家 / Agent / 插件 / 工作流 / 策略）都变成
同一种 `ExtensionRecord`，上层只认记录，不认具体类型。

    声明层                注册层                  挂载层
    oryx.json         ExtensionRecord          HookBus / ToolBus
    SKILL.md     →   {kind,id,source,      →  PromptBus / PolicyBus
    EXPERT.md        trust,caps,meta}
    AGENT.md

三条硬约束
----------
1. **向后兼容**：现有 `skills/` `experts/` `agents/` 目录**一行不用改**，
   照旧能被加载。`oryx.json` 是可选增强，不是必需。
2. **信任分级**：项目内自带的扩展默认 `untrusted`，
   不能声明高危能力（如 `proc.spawn`）。这是「仓库里放个插件就能提权」
   的防线。
3. **不改语义**：注册表只做「发现与归一」，不做「加载与执行」——
   真正的加载仍由各自的 library 完成，注册表给出的是**统一视图**。

借鉴
----
* ECC 的 manifest 声明 + context-pack/profile 分层
  （`manifests/install-modules.json`、`context-profiles/lean@1.json`）
* agent-browser 的能力集与信任分级
  （`capabilities: [credential.read, browser.provider, launch.mutate]`；
    自动发现的项目配置只接受安全子集）
* auto-ontology 的「概念与实现解耦」——
  这里的体现是「扩展记录」与「扩展载荷」分离：
  记录只管元数据，载荷按 kind 交给各自的 loader。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from enum import Enum
from pathlib import Path
from typing import Any

# frontmatter 解析 —— **全项目唯一一份**（oryx/frontmatter.py）。
# 以前散在 memory / registry / skills / experts / agents 五处，
# 行为还不一致：有的认行内列表、有的只认单行 key: value，
# 同一个 `triggers: [a, b]` 在不同路径下能解析出不同结果。
from .frontmatter import (FRONTMATTER, as_bool, as_list,
                          parse_frontmatter)


# ── 扩展类型 ──────────────────────────────────────────────────────────
# 每种类型对应：目录名、必需文件、可选的清单文件名。
#
# 「必需文件」决定该目录下什么算一个扩展；
# 「清单文件」是可选的 oryx.json —— 有就用它覆盖元数据，没有就按约定推导。
KIND_SPECS: dict[str, dict[str, Any]] = {
    "skill": {"dir": "skills", "entry": "SKILL.md"},
    "expert": {"dir": "experts", "entry": "EXPERT.md"},
    "agent": {"dir": "agents", "entry": "AGENT.md"},
    "workflow": {"dir": "workflows", "entry": "workflow.json"},
    "policy": {"dir": "policies", "entry": "policy.yaml"},
    "plugin": {"dir": "plugins", "entry": "plugin.json"},
    # tool —— 提供可执行工具的扩展。入口文件是 plugin.json，
    # 与 plugin 同源；两者靠 kind 字段区分。
    "tool": {"dir": "extensions", "entry": "oryx.json"},
}

# 能力集：扩展能做什么。高危能力需要显式授权。
#
# 命名空间分层，方便按前缀授权：
#   net.*   外网访问
#   fs.*    文件系统
#   proc.*  进程（最高危）
#   ui.*    前端界面
#   mcp.*   MCP server
CAPABILITY_TIERS: dict[str, str] = {
    "net.egress": "normal",
    "net.fetch": "normal",
    "fs.read": "normal",
    "fs.write": "normal",
    "ui.render": "normal",
    "mcp.connect": "normal",
    "fs.delete": "elevated",
    "proc.spawn": "elevated",
    "proc.exec": "elevated",
    "credential.read": "elevated",
}

# 不可信来源允许的能力上限。
# 项目内自带的扩展（仓库里的 skills/ 目录）默认 untrusted ——
# 别人 clone 你的仓库，他的 skills/ 里的东西不该有提权能力。
UNTRUSTED_ALLOWED = frozenset({
    "net.egress", "net.fetch", "fs.read", "fs.write", "ui.render",
})

# ── 界面扩展的能力映射 ────────────────────────────────────────────────
#
# 界面扩展（uiext）能调后端 API，所以它也需要一张「哪些接口算特权操作」
# 的表。**这张表以前硬编码在前端的 uiext.js 里** ——
# 于是两边各有一份能力词表，改了一边另一边不知道，漂移了没人发现。
#
# 现在这里是**唯一来源**：前端从 `/api/ui/modules` 的清单里取，
# 不再自己写一份。测试（tests/test_caps_consistency.py）守住这份一致性。
#
# 结构：能力 → {rules: [{methods, paths}, …]}
#
# 为什么是**规则列表**而不是「一个 methods + 一个 paths」
# ----------------------------------------------------
# 因为「同一个路径，不同方法，不同能力」是真的：
#
#     GET  /api/memory   → 读记忆  → fs.read
#     POST /api/memory   → 写记忆  → fs.write
#     DELETE /api/memory/{id} → 删记忆 → fs.delete
#
# 塞进一个 methods+paths 就必然出歧义：
# fs.read 的 methods 里要加 POST（`/api/index/search` 是 POST 的搜索），
# 那 POST /api/memory 就同时落进 fs.read 和 fs.write —— 要两个能力都得声明，
# 「写记忆」变成了「还得会读记忆」。
#
# 拆成规则列表，每条规则自己带方法，歧义就没了。
UI_CAP_RULES: dict[str, dict[str, list[dict[str, list[str]]]]] = {
    "fs.read": {
        "rules": [
            {"methods": ["GET", "HEAD"], "paths": [
                "/api/files/list", "/api/files/preview", "/api/fs/list",
                "/api/image/thumb", "/api/doc",
                "/api/index/libraries", "/api/index/library", "/api/index/status",
                "/api/memory",
            ]},
            # 搜索也是读，只是参数走 body 所以是 POST
            {"methods": ["POST"], "paths": ["/api/index/search"]},
        ],
    },
    "fs.write": {
        "rules": [
            {"methods": ["POST", "PUT", "PATCH"], "paths": [
                "/api/files/", "/api/fs/",
                "/api/index/scan", "/api/index/clear",
                "/api/memory",
            ]},
        ],
    },
    "fs.delete": {
        "rules": [
            {"methods": ["DELETE"], "paths": [
                "/api/files/", "/api/fs/", "/api/memory/",
            ]},
        ],
    },
    "proc.exec": {
        "rules": [
            # 「让 AI 干活 / 调度 / 起进程」—— 不是「看团队列表」。
            {"methods": ["POST", "PUT", "DELETE"], "paths": [
                "/api/chat", "/api/agents", "/api/teams", "/api/approve",
                "/api/interaction", "/api/tools/search", "/api/prompt/optimize",
                "/api/apps/launch",
            ]},
        ],
    },
    "net.egress": {
        "rules": [
            # 数据出站 / 从外头下载东西 / 把机器暴露出去
            {"methods": ["POST", "PUT"], "paths": [
                "/api/paste", "/api/install",
                "/api/extensions/install", "/api/ui-extensions/install",
                "/api/remote/start",
            ]},
        ],
    },
    "mcp.connect": {
        "rules": [
            {"methods": ["POST", "PUT"], "paths": ["/api/mcp"]},
        ],
    },
    "credential.read": {
        "rules": [
            {"methods": ["GET", "HEAD"], "paths": [
                "/api/drives", "/api/apps", "/api/clipboard",
                "/api/remote/status",
            ]},
            # 只读地查系统设置状态 —— 是读，但走 POST
            {"methods": ["POST"], "paths": ["/api/sys/query"]},
        ],
    },
}

# 后端词表里有、但**界面扩展不需要声明**的能力 —— 连同原因一起写清楚。
#
# 为什么要有这张说明：不是「后端多了三个没人用的能力」，
# 而是「这三个能力在界面侧**语义上不成立**」——
# 不说清楚的话，测试只能报「不一致」，人也不知道该补哪一边。
UI_CAP_NOTES: dict[str, str] = {
    "ui.render": "界面扩展本来就在渲染界面，这个能力天然拥有，不需要声明",
    "net.fetch": "界面扩展不直接联网；要联网得通过后端扩展提供的工具",
    "proc.spawn": "界面扩展不能起进程；起进程是后端扩展的事",
}

# ── 能力词典 ──────────────────────────────────────────────────────────
#
# 写扩展的人要查「fs.read 到底能干什么」，
# 现在得翻源码或翻文档 —— 而文档里的表是**界面侧**的路径映射，
# 后端侧的能力语义散在各处注释里，谁也拼不出全貌。
#
# 这份词典是**给人读的**：设置页上摊开一张表，
# 写扩展的人不用猜「声明了这个能力到底放不放行」。
#
# 它**不是**执行口径 —— 执行口径在 CAPABILITY_TIERS（风险分级）、
# UNTRUSTED_ALLOWED（谁能声明）、UI_CAP_RULES（界面侧管哪些路径）里。
CAPABILITY_DOCS: dict[str, dict[str, str]] = {
    "net.egress": {
        "summary": "把数据送出本机，或从外头下载东西",
        "detail": "联网抓取、装扩展、启动远程访问都算这一类。"
                  "声明它意味着这个扩展能把本机的数据送出去。",
    },
    "net.fetch": {
        "summary": "联网抓取网页或接口",
        "detail": "只取回来、不往外送 —— 但取回来的内容会进对话上下文。",
    },
    "fs.read": {
        "summary": "读本机的文件与索引",
        "detail": "能列出目录、读文件内容、查索引。",
    },
    "fs.write": {
        "summary": "写本机的文件与索引",
        "detail": "能改文件、建索引、写记忆。",
    },
    "ui.render": {
        "summary": "往界面上渲染内容",
        "detail": "界面扩展天生就在渲染界面，这个能力它天然拥有。",
    },
    "mcp.connect": {
        "summary": "连接第三方 MCP server",
        "detail": "MCP server 是外部可执行程序 —— 接之前要想清楚。",
    },
    "fs.delete": {
        "summary": "删本机的文件与记忆",
        "detail": "不可逆。删了就没了（扩展的回收区只管扩展本身）。",
    },
    "proc.spawn": {
        "summary": "启动本机进程",
        "detail": "能起子进程 —— 装的程序会真的跑起来。",
    },
    "proc.exec": {
        "summary": "执行命令",
        "detail": "最高危的一档：能执行 shell 命令。"
                  "项目仓库里带来的扩展（PROJECT 信任）声明不了它。",
    },
    "credential.read": {
        "summary": "读凭据与账号信息",
        "detail": "模型配置、账号、剪贴板都在这一类 ——"
                  "用户复制过密码的话，剪贴板也在这。",
    },
}


def capability_catalog() -> dict[str, dict[str, Any]]:
    """能力词典 —— 给界面用的完整视图。

    把散在几处的信息收成一处，让「这个能力到底管什么」有唯一的答案：

    * tier —— 风险分级（CAPABILITY_TIERS）
    * summary / detail —— 人话说明（CAPABILITY_DOCS）
    * untrusted_allowed —— 不可信来源能不能声明（UNTRUSTED_ALLOWED）
    * ui_paths —— 界面侧覆盖的 API 路径（UI_CAP_RULES）
    * ui_note —— 界面侧为什么不需要声明（UI_CAP_NOTES）
    """
    out: dict[str, dict[str, Any]] = {}
    for cap in CAPABILITY_TIERS:
        doc = CAPABILITY_DOCS.get(cap) or {}
        spec = UI_CAP_RULES.get(cap) or {}
        paths: list[str] = []
        for rule in spec.get("rules", []):
            for p in rule.get("paths", []):
                if p not in paths:
                    paths.append(p)
        out[cap] = {
            "tier": CAPABILITY_TIERS[cap],
            "summary": doc.get("summary", ""),
            "detail": doc.get("detail", ""),
            "untrusted_allowed": cap in UNTRUSTED_ALLOWED,
            "ui_paths": paths,
            "ui_note": UI_CAP_NOTES.get(cap, ""),
        }
    return out


class Trust(str, Enum):
    """扩展的信任级别。"""

    BUILTIN = "builtin"      # 随产品发布
    USER = "user"            # 用户自己放的（用户目录）
    PROJECT = "project"      # 项目仓库里带的（clone 别人仓库也会遇到）
    PLUGIN = "plugin"        # 插件带来的


@dataclass(frozen=True)
class Contracts:
    """扩展的可执行契约（学 auto-ontology 的 skill-card）。

    纯自然语言的技能无法判断「做完了没有」。这三个字段把它变成可判定的：
      gates            —— 什么条件下算完成
      fail_closed_when —— 什么情况必须拒绝而不是硬做
      handoffs         —— 该转交给谁
    """

    gates: tuple[str, ...] = ()
    fail_closed_when: tuple[str, ...] = ()
    handoffs: tuple[str, ...] = ()


@dataclass
class ExtensionRecord:
    """一条扩展记录 —— 所有扩展类型的统一视图。

    上层（提示词组装、路由、UI 列表、策略加载）只认这个结构，
    不关心它是技能还是专家。这是「加一种扩展不改别处」的关键。
    """

    kind: str
    id: str
    path: Path
    trust: Trust = Trust.PROJECT
    source: str = ""              # 从哪发现的（根目录名 / 插件 id）
    version: str = ""
    display_name: str = ""
    description: str = ""
    triggers: tuple[str, ...] = ()       # 显式触发词（路由用）
    eager: bool = False                  # 是否常驻上下文
    allowed_tools: tuple[str, ...] = ()
    caps: tuple[str, ...] = ()
    requires: dict[str, list[str]] = field(default_factory=dict)
    hooks: tuple[dict[str, Any], ...] = ()
    contracts: Contracts = field(default_factory=Contracts)
    meta: dict[str, Any] = field(default_factory=dict)
    body_path: Path | None = None        # 正文文件（SKILL.md 等）
    entry_path: Path | None = None       # oryx.json / plugin.json
    # 内容指纹 —— 扩展的文件变了它就变。
    # 热重载靠它判断「要不要重新加载」。
    fingerprint: str = ""
    # 依赖不满足时的原因（空 = 依赖都满足）。
    #
    # 为什么保留在记录里而不是只放在一份报告里：
    # 上层各处（技能目录、工具加载、界面列表）都要判断
    # 「这个扩展能不能用」—— 判据散落各处时最容易漏。
    # 把它做成**记录自己的一个字段**，判断就只有一句
    # `if rec.blocked_reason:`。
    blocked_reason: str = ""

    # ── 展示 ──────────────────────────────────────────────────────
    def summary_line(self) -> str:
        return f"- {self.id}: {self.description}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "version": self.version,
            "display_name": self.display_name or self.id,
            "description": self.description,
            "triggers": list(self.triggers),
            "eager": self.eager,
            "trust": self.trust.value,
            "source": self.source,
            "caps": list(self.caps),
            # **实际生效**的能力 —— 与上面声明的不一定一样：
            # 不可信来源声明的 proc.* 在加载时就被裁掉了。
            # 界面上要显示这个（实际能做什么），并把被裁的标出来 ——
            # 只显示声明的话，用户会以为它真能执行命令。
            "effective_caps": list(self.effective_caps()),
            "requires": {k: list(v) for k, v in self.requires.items()},
            "blocked_reason": self.blocked_reason,
            "contracts": {
                "gates": list(self.contracts.gates),
                "fail_closed_when": list(self.contracts.fail_closed_when),
                "handoffs": list(self.contracts.handoffs),
            },
        }

    # ── 能力校验 ──────────────────────────────────────────────────
    def effective_caps(self) -> tuple[str, ...]:
        """按信任级别裁剪后的实际能力。

        不可信来源不能声明高危能力 —— 这条在**加载时**就生效，
        不是等到运行期才拦。
        """
        if self.trust is not Trust.PROJECT:
            return self.caps
        return tuple(c for c in self.caps if c in UNTRUSTED_ALLOWED)

    def has_cap(self, cap: str) -> bool:
        return cap in self.effective_caps()

    def digest(self) -> str:
        """内容摘要 —— 用于检测注册表漂移（学 ECC 的 registryDigest）。"""
        h = hashlib.sha256()
        h.update(self.kind.encode())
        h.update(self.id.encode())
        h.update(self.version.encode())
        h.update(self.description.encode())
        h.update(",".join(sorted(self.triggers)).encode())
        h.update(",".join(sorted(self.caps)).encode())
        if self.entry_path and self.entry_path.exists():
            h.update(self.entry_path.read_bytes())
        return h.hexdigest()[:16]

    def compute_fingerprint(self) -> str:
        """算扩展的内容指纹。

        覆盖**扩展目录下的所有 .py 文件**与清单 ——
        改了任何一个都要重新加载。

        为什么单独有这个（而不是用 digest）：
        `digest` 是「这个扩展是什么」（身份），
        `fingerprint` 是「这个扩展现在长什么样」（内容）。
        身份不变但内容变了 —— 那正是热重载要处理的情况。
        """
        h = hashlib.sha256()
        root = self.path
        if root and root.exists():
            for p in sorted(root.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix not in (".py", ".json", ".md", ".yaml", ".yml"):
                    continue
                try:
                    h.update(str(p.relative_to(root)).encode("utf-8"))
                    h.update(p.read_bytes())
                except OSError:
                    continue
        return h.hexdigest()[:16]


# ── 清单解析 ──────────────────────────────────────────────────────────

def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.replace(",", " ").split() if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _load_manifest(path: Path) -> dict[str, Any]:
    """读 oryx.json（若存在且合法）。"""
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _contracts_from(data: dict[str, Any]) -> Contracts:
    c = data.get("contracts") or {}
    if not isinstance(c, dict):
        return Contracts()
    return Contracts(
        gates=tuple(_as_list(c.get("gates"))),
        fail_closed_when=tuple(_as_list(c.get("fail_closed_when"))),
        handoffs=tuple(_as_list(c.get("handoffs"))),
    )



def _record_from_manifest(
    kind: str,
    ext_id: str,
    ext_path: Path,
    manifest: dict[str, Any],
    *,
    trust: Trust,
    source: str,
    entry_path: Path | None,
    body_path: Path | None,
    fallback_description: str = "",
    fallback_meta: dict[str, str] | None = None,
    fallback_requires: dict[str, list[str]] | None = None,
) -> ExtensionRecord:
    fm = fallback_meta or {}
    requires = manifest.get("requires") or {}
    if not isinstance(requires, dict):
        requires = {}
    # frontmatter 里的 requires 是**兜底** —— oryx.json 优先。
    # 但它必须被认：用户写在 SKILL.md 里的依赖要是静默失效，
    # 那就等于没有依赖保护。
    if not requires and fallback_requires:
        requires = dict(fallback_requires)
    hooks = manifest.get("hooks") or []
    if not isinstance(hooks, list):
        hooks = []

    # frontmatter 是**兜底**：oryx.json 是显式配置，优先级更高。
    # 统一在这里解析，避免 skills.py / experts.py / agents.py 各自再解一遍。
    fm_triggers = tuple(as_list(fm.get("triggers")))
    fm_eager = as_bool(fm.get("eager"))
    fm_tools = tuple(as_list(fm.get("allowed-tools")))
    fm_caps = tuple(as_list(fm.get("caps")))

    def pick(manifest_key: str, fm_value: tuple[str, ...]) -> tuple[str, ...]:
        """清单优先，frontmatter 兜底。统一成 tuple —— 类型一致才好比较。"""
        mv = _as_list(manifest.get(manifest_key))
        return tuple(mv) if mv else fm_value

    return ExtensionRecord(
        kind=kind,
        id=str(manifest.get("id") or ext_id),
        path=ext_path,
        trust=trust,
        source=source,
        version=str(manifest.get("version") or ""),
        display_name=str(manifest.get("displayName") or ""),
        description=str(manifest.get("description") or fm.get("description") or fallback_description),
        triggers=pick("triggers", fm_triggers),
        eager=bool(manifest.get("eager", fm_eager)),
        allowed_tools=pick("allowedTools", fm_tools),
        caps=pick("caps", fm_caps),
        requires={k: _as_list(v) for k, v in requires.items()},
        hooks=tuple(h for h in hooks if isinstance(h, dict)),
        contracts=_contracts_from(manifest),
        meta={k: v for k, v in manifest.items() if k not in {
            "id", "kind", "version", "displayName", "description", "triggers",
            "eager", "allowedTools", "caps", "requires", "hooks", "contracts",
        }},
        # 「tools」不排除 —— 工具声明要留在 meta 里给 toolplug 读。
        # 它是**声明**（schema + 风险等级），不含代码，所以放在 meta 里安全。
        body_path=body_path,
        entry_path=entry_path,
    )


class Registry:
    """扩展注册表 —— 唯一的发现入口。

    用法：

        reg = Registry(roots=[skills_dir, experts_dir, ...])
        reg.discover()
        reg.of_kind("skill")        # → list[ExtensionRecord]
        reg.get("skill", "web-research")
        reg.digest()                # → 注册表摘要（漂移检测用）
    """

    def __init__(self, roots: list[Path] | None = None,
                 trust_map: dict[str, Trust] | None = None) -> None:
        # roots: [(kind, Path)] 或 [{kind: path}]；这里统一存成 kind → [Path]
        self._roots: dict[str, list[Path]] = {}
        self._records: dict[str, dict[str, ExtensionRecord]] = {}
        self._trust: dict[str, Trust] = trust_map or {}
        # 按根目录记的信任级别：同一个 kind 可以有多个根，
        # 信任级别不同 —— 用户目录可信、仓库目录不可信。
        self._root_trust: dict[tuple[str, str], Trust] = {}
        # profile：当前生效的 profile 名。空 = 不排除任何扩展。
        self._profile_id: str = ""
        self._profiles: Any = None      # profiles.ProfileSet
        # 停用名单：用户主动关掉的扩展，不进入注册表。
        self._disabled: set[str] = set()
        # 依赖解析报告（最近一次 discover 的结果）。
        self.dep_report: Any = None      # extdeps.DependencyReport
        for item in roots or []:
            if isinstance(item, tuple):
                kind, path = item
            else:
                kind, path = next(iter(item.items()))
            self.add_root(kind, Path(path))

    # ── profile ─────────────────────────────────────────────────────
    def set_profiles(self, profile_set: Any, profile_id: str = "") -> None:
        """设 profile 集合与当前 profile。

        profile 只是**收窄**加载范围 —— 它不能用来绕过信任检查。
        """
        self._profiles = profile_set
        self._profile_id = profile_id or (getattr(profile_set, "default", "") or "")

    # ── 停用名单 ───────────────────────────────────────────────────
    def set_disabled(self, ids: set[str] | None) -> None:
        """设停用名单。

        名单里的扩展**不进入注册表** —— 不被发现、不被加载。
        与 profile 不同：profile 是「默认装哪套」，停用是「用户主动关掉的」，
        后者优先级更高（能覆盖 profile 的白名单）。
        """
        self._disabled = set(ids or ())

    def profile_id(self) -> str:
        return self._profile_id

    def _allowed_by_profile(self, ext_id: str) -> bool:
        if self._profiles is None:
            return True
        try:
            return bool(self._profiles.allows(self._profile_id, ext_id))
        except Exception:
            # profile 出问题不该让扩展全部消失 —— 宁可全放行
            return True

    # ── 根目录 ─────────────────────────────────────────────────────
    def add_root(self, kind: str, path: Path, trust: Trust | None = None) -> None:
        """挂一个根目录。

        `trust` 显式给定时按这个根算 —— 同一 kind 的不同根可以有不同信任级别
        （用户目录 USER vs 仓库目录 PROJECT）。
        """
        path = Path(path)
        self._roots.setdefault(kind, [])
        if path not in self._roots[kind]:
            self._roots[kind].append(path)
        if trust is not None:
            self._root_trust[(kind, str(path))] = trust
        self._load_root(kind, path)

    def set_trust(self, kind: str, trust: Trust) -> None:
        """给某个 kind 的**所有**根设信任级别（没有单独指定时的默认值）。"""
        self._trust[kind] = trust

    # ── 发现 ───────────────────────────────────────────────────────
    def discover(self) -> "Registry":
        """扫描全部根目录，重建记录表。幂等。"""
        self._records.clear()
        for kind, paths in self._roots.items():
            for path in paths:
                self._load_root(kind, path)
        self._resolve_dependencies()
        return self

    def _resolve_dependencies(self) -> None:
        """解析扩展之间的依赖，给不满足的记录打上 `blocked_reason`。

        为什么放在注册表这一层：
        依赖是**跨类型**的（技能依赖专家、工具扩展依赖技能），
        只有把全部类型的记录放在一起才看得出谁缺谁。
        放在各自的 library 里解析，就得让每个 library 都认识其他类型 ——
        那正是「加一种扩展类型要改一堆地方」的老毛病。

        依赖不满足**不删除记录** —— 删了用户在界面上就看不到它，
        也就不知道「我装了个东西但它没跑」。记录仍在，只是带一个原因。
        """
        from .extdeps import resolve

        records = self.all_records()
        report = resolve(records, disabled=self._disabled)
        self.dep_report = report
        for rec in records:
            rec.blocked_reason = report.reason(rec.kind, rec.id)

    def _load_root(self, kind: str, root: Path) -> None:
        if not root.exists() or not root.is_dir():
            return
        spec = KIND_SPECS.get(kind, {})
        entry_name = spec.get("entry")
        if not entry_name:
            return
        # 信任级别：根目录单独指定的优先，否则用 kind 的默认值
        trust = self._root_trust.get((kind, str(root))) or self._trust.get(kind, Trust.PROJECT)
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            entry = child / entry_name
            if not entry.exists():
                continue
            record = self._build_record(kind, child, entry, trust, root.name)
            if record is None:
                continue
            # 停用名单优先于一切 —— 用户主动关掉的，profile 也救不回来。
            #
            # 比的是**清单里的 id**（界面上显示、按钮上传的都是它），
            # 同时兼容目录名 —— 老配置里存的是目录名。
            # 只比目录名的话，「清单 id ≠ 目录名」的扩展就永远停不掉。
            if self._is_disabled(record.id, child.name):
                continue
            # profile 排除的扩展不进入注册表
            if not self._allowed_by_profile(record.id):
                continue
            self._records.setdefault(kind, {})[record.id] = record

    def _is_disabled(self, ext_id: str, dir_name: str = "") -> bool:
        """这个扩展是不是被用户停用了。

        两个口径都认：
          · **清单 id** —— 界面上显示的、写进 `disabled_extensions` 的那个
          · **目录名** —— 老配置里的写法（清单 id 出现之前的约定）
        """
        if ext_id and ext_id in self._disabled:
            return True
        return bool(dir_name) and dir_name in self._disabled

    def _build_record(
        self, kind: str, ext_path: Path, entry: Path, trust: Trust, source: str
    ) -> ExtensionRecord | None:
        manifest_path = ext_path / "oryx.json"
        manifest = _load_manifest(manifest_path) if manifest_path.exists() else {}

        fallback_meta: dict[str, str] = {}
        fallback_desc = ""
        fallback_requires: dict[str, list[str]] = {}
        body_path: Path | None = None

        if entry.suffix == ".md":
            try:
                text = entry.read_text("utf-8")
            except Exception:
                return None
            fallback_meta, _body = parse_frontmatter(text)
            fallback_desc = fallback_meta.get("description", "")
            # frontmatter 里的 requires —— 统一解析器**认得出嵌套块**了，
            # 不用再单独抠一遍。
            raw_requires = fallback_meta.get("requires")
            if isinstance(raw_requires, dict):
                fallback_requires = {k: as_list(v) for k, v in raw_requires.items()}
            body_path = entry
        elif entry.suffix == ".json":
            data = _load_manifest(entry)
            fallback_desc = str(data.get("description", ""))
            if not manifest:
                manifest = data
            manifest_path = entry
        elif entry.suffix in (".yaml", ".yml"):
            fallback_desc = f"{kind} 策略"

        record = _record_from_manifest(
            kind,
            ext_path.name,
            ext_path,
            manifest,
            trust=trust,
            source=source,
            entry_path=manifest_path if manifest_path.exists() else None,
            body_path=body_path,
            fallback_description=fallback_desc,
            fallback_meta=fallback_meta,
            fallback_requires=fallback_requires,
        )
        record.fingerprint = record.compute_fingerprint()
        return record

    # ── 查询 ───────────────────────────────────────────────────────
    def of_kind(self, kind: str) -> list[ExtensionRecord]:
        return list(self._records.get(kind, {}).values())

    def usable_of_kind(self, kind: str) -> list[ExtensionRecord]:
        """该类型里**依赖都满足**的扩展。

        与 `of_kind` 的区别：被依赖阻塞的扩展仍在 `of_kind` 里
        （用户要看得见「我装了个东西但它没跑」），
        但**不该被加载**（工具、提示词、路由都不该用它）。
        """
        return [r for r in self.of_kind(kind) if not r.blocked_reason]

    def blocked_records(self) -> list[ExtensionRecord]:
        """所有依赖不满足的扩展。"""
        return [r for r in self.all_records() if r.blocked_reason]

    def is_blocked(self, kind: str, ext_id: str) -> bool:
        rec = self.get(kind, ext_id)
        return bool(rec and rec.blocked_reason)

    def get(self, kind: str, ext_id: str) -> ExtensionRecord | None:
        return self._records.get(kind, {}).get(ext_id)

    def find_by_id(self, ext_id: str) -> ExtensionRecord | None:
        """按 id 找扩展（不分类型）。

        为什么需要：停用名单是一个**扁平的 id 集合**（不分类型）——
        界面上「停用」按钮传回来的就是裸 id。
        反查回记录才能知道它是什么类型、从哪来的。
        """
        for kind in sorted(self._records):
            rec = self._records[kind].get(ext_id)
            if rec is not None:
                return rec
        return None

    def is_disabled(self, ext_id: str, dir_name: str = "") -> bool:
        """这个扩展是不是被用户停用了（对外查询用）。"""
        return self._is_disabled(ext_id, dir_name)

    def roots_of_kind(self, kind: str) -> list[Path]:
        """某个类型的全部根目录。

        library 用它来决定「我该扫哪些目录」——
        这样「装在用户目录的技能」这类新增位置**只改注册表一处**，
        不用同步改每个 library。
        """
        return list(self._roots.get(kind, []))

    def all_records(self) -> list[ExtensionRecord]:
        out: list[ExtensionRecord] = []
        for kind in sorted(self._records):
            out.extend(self._records[kind].values())
        return out

    def kinds(self) -> list[str]:
        return sorted(self._records)

    def digest(self) -> str:
        """整个注册表的内容摘要。

        用途：把摘要写进 manifest / 快照，改了扩展但没同步摘要时能发现
        （学 ECC 的 `registryDigest`）。
        """
        h = hashlib.sha256()
        for rec in sorted(self.all_records(), key=lambda r: (r.kind, r.id)):
            h.update(rec.digest().encode())
        return h.hexdigest()[:16]

    def stats(self) -> dict[str, int]:
        return {kind: len(recs) for kind, recs in sorted(self._records.items())}


# ── 路由 ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=2048)
def tokenize(text: str) -> frozenset[str]:
    """把文本切成可匹配的词。

    **中文没有空格** —— 按空格切分的话，「按类型、时间或主题整理文件目录」
    会变成一整个长 token，永远匹配不上 query 里的「整理」「文件夹」。
    这是 Oryx 老 `SkillLibrary.match()` 一直存在的缺陷。

    这里用**二元切分（bigram）**：中文串切成所有相邻两字组合
    （「整理文件」→「整理」「理文」「文件」），英文仍按单词切。
    bigram 不需要词典，对短描述足够准；配合显式 `triggers` 更稳。

    带缓存 —— 工具检索会对每个工具反复切同一段文本。
    """
    return frozenset(_tokenize_uncached(text))


def _tokenize_uncached(text: str) -> set[str]:
    lowered = (text or "").lower()
    words: set[str] = set()
    # 英文/数字词
    for w in re.findall(r"[a-z0-9_]+", lowered):
        if len(w) >= 2:
            words.add(w)
    # 中文连续段 → bigram
    for seg in re.findall(r"[一-鿿]+", lowered):
        if len(seg) == 1:
            words.add(seg)
            continue
        for i in range(len(seg) - 1):
            words.add(seg[i:i + 2])
    return words


@dataclass
class RouteResult:
    """一次路由的结果。"""

    matched: list[ExtensionRecord]
    strategy: str            # triggers | description | name | none
    scores: dict[str, float] = field(default_factory=dict)


def route(
    records: list[ExtensionRecord],
    query: str,
    *,
    limit: int = 5,
    min_score: float = 0.05,
) -> RouteResult:
    """把一句用户输入路由到相关扩展。

    三级策略（从可靠到兜底）：
      1. **triggers** —— 声明式触发词，命中即用。显式配置最准。
      2. **description** —— 描述与查询的 bigram 重合度。
      3. **name token** —— 名称分词命中。

    为什么是这个顺序：调研的 ECC 用 `skill-triggers@1.json` 显式维护
    触发词表（314 个技能里只有 34 个配了触发词，其余靠检索路由）。
    Oryx 现有的 `SkillLibrary.match()` 是「名称子串 → 描述按空格分词」的
    朴素匹配 —— 对中文**根本不生效**（见 `tokenize` 的说明）。

    `min_score` 默认 0.05：bigram 的命中率天然比整词高，
    阈值太低会什么都命中，太高会什么都命中不了。
    """
    q = (query or "").lower().strip()
    if not q:
        return RouteResult([], "none")

    scores: dict[str, float] = {}
    strategy = "none"

    # 1) 显式触发词 —— 权重最高
    for rec in records:
        for trig in rec.triggers:
            t = trig.lower().strip()
            if t and t in q:
                scores[rec.id] = max(scores.get(rec.id, 0.0), 1.0)
                strategy = "triggers"

    # 2) 描述 bigram 重合
    if not scores:
        q_grams = tokenize(q)
        if q_grams:
            for rec in records:
                d_grams = tokenize(rec.description)
                if not d_grams:
                    continue
                hit = len(q_grams & d_grams)
                if hit:
                    # 用重合数占「较小集合」的比例 —— 描述长短不一，
                    # 只看绝对命中数会偏爱长描述。
                    ratio = hit / min(len(q_grams), len(d_grams))
                    scores[rec.id] = max(scores.get(rec.id, 0.0), ratio * 0.9)

    # 3) 名称 token 兜底
    if not scores:
        for rec in records:
            tokens = [t for t in re.split(r"[-_/. ]+", rec.id.lower()) if len(t) >= 2]
            if not tokens:
                continue
            hit = sum(1 for t in tokens if t in q)
            if hit:
                scores[rec.id] = hit / len(tokens) * 0.6

    by_id = {rec.id: rec for rec in records}
    matched = [
        by_id[rid] for rid, sc in sorted(scores.items(), key=lambda kv: -kv[1])
        if sc >= min_score
    ]
    return RouteResult(matched[:limit], strategy or "none", scores)
