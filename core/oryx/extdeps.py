"""扩展依赖 —— 让 `requires` 真的生效。

问题
----
`oryx.json` 里有一个 `requires` 字段：

    {
      "id": "receipt-ocr",
      "requires": { "skills": ["web-research"], "experts": [] }
    }

以前它**只被解析，从不被使用** —— 声明了依赖，装不上那个依赖，
扩展照样加载、照样出现在技能目录里、照样被路由到。
用户看到的是「这个技能好像不太行」，但没有任何地方告诉他**为什么**。

这比没有这个字段更糟：没声明的依赖是「不知道」，
声明了却不校验的依赖是「说了假话」。

语义
----
`requires` 的键是**扩展类型**（单数复数都认），值是**依赖的扩展 id 列表**：

    "requires": { "skills": ["web-research"], "tools": ["pdf-reader"] }

一条依赖记成 `id` 或 `id@约束`：

    "web-research"            任意版本
    "web-research@>=1.1.0"    版本下限
    "web-research@1.1.0"      精确版本
    "web-research@<2.0.0"     版本上限

判定结果分两级
--------------
**致命（fatal）—— 该扩展不能加载**：

* 依赖的扩展**不存在**
* 依赖的扩展**被停用**（用户主动关掉的）
* 依赖的扩展**版本不满足**
* 依赖形成**环**（A 要 B，B 要 A）

**警告（warning）—— 加载，但要说清楚**：

* 依赖的扩展存在，但**没有版本号** —— 约束无法验证

传递闭包
--------
A 依赖 B、B 依赖 C、C 缺失 → **B 致命、A 也致命**。
「C 没了所以 B 半残」是不够的 —— B 缺了它依赖的东西，
A 依赖 B 也就不成立。这条链要一路推到底。

为什么不在注册表里直接删掉被阻塞的扩展
------------------------------------
删了用户在界面上就**看不到它**，也就不知道「我装了个东西但它没跑」。
所以：被阻塞的扩展**仍然被发现、仍然列出**，
但带一个 `blocked` 标记与**人话原因** ——
「依赖 skills/web-research 不存在」。

借鉴
----
* ECC 的 manifest 声明 + 依赖解析（`manifests/install-modules.json`
  里的 `requires` 与 `optional` 语义）
* npm 的 `peerDependencies` 缺失即警告——
  但这里缺依赖是**致命**的：扩展缺了依赖就跑不对，
  与其让它半残不如不加载并说清楚。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── 类型别名 ──────────────────────────────────────────────────────────
#
# 清单里写单数还是复数都认 —— 写清单的人不会去查文档确认用哪个。
# "extension"/"extensions" = 任意类型（按 id 在所有类型里找）。
#
# `ui` / `backend` 是**跨端依赖**的类型：
#   · `ui`      —— 另一个**界面扩展**（见 uiext.py）
#   · `backend` —— 某个**后端扩展**（技能 / 工具扩展 / …，不分类型）
KIND_ALIASES: dict[str, str] = {
    "skill": "skill", "skills": "skill",
    "expert": "expert", "experts": "expert",
    "agent": "agent", "agents": "agent",
    "workflow": "workflow", "workflows": "workflow",
    "policy": "policy", "policies": "policy",
    "plugin": "plugin", "plugins": "plugin",
    "tool": "tool", "tools": "tool",
    "ui": "ui", "uis": "ui", "uiext": "ui", "uiextensions": "ui",
    "panels": "ui",
    "backend": "backend", "backends": "backend",
    "extension": "*", "extensions": "*",
}

# 「backend」= 后端扩展的**所有类型**（界面扩展是另一套，不算）。
BACKEND_KINDS: frozenset[str] = frozenset({
    "skill", "expert", "agent", "workflow", "policy", "plugin", "tool",
})

# 版本约束：`>=1.2.0` `<2.0.0` `1.2.0` `>1.0.0` `<=1.5.0` `!=2.0.0`
_CONSTRAINT_RE = re.compile(r"^(>=|<=|==|!=|>|<)?\s*v?([0-9][0-9A-Za-z.\-+]*)?$")


@dataclass(frozen=True)
class Requirement:
    """一条依赖声明。"""

    kind: str                # 归一化后的类型；"*" = 任意类型
    ext_id: str              # 依赖的扩展 id
    op: str = ""             # 版本运算符（空 = 不约束）
    version: str = ""        # 版本号
    raw: str = ""            # 原始写法（报错时给人看）

    def describe(self) -> str:
        """人话描述 —— 直接进界面与错误信息。"""
        kind_label = {
            "skill": "技能", "expert": "专家", "agent": "Agent",
            "workflow": "工作流", "policy": "策略", "plugin": "插件",
            "tool": "工具扩展", "*": "扩展",
        }.get(self.kind, self.kind)
        if self.op:
            return f"{kind_label} {self.ext_id}（需要版本 {self.op}{self.version}）"
        return f"{kind_label} {self.ext_id}"


# 依赖类型 → 这类依赖是干什么的。
#
# 为什么要有：依赖没满足时，用户看到的是「依赖的扩展 X 不存在」——
# 说了现象，没说**那是什么东西**。技能？专家？界面扩展？
# 分不清的话，用户根本不知道该去哪儿找它。
DEP_KIND_DOCS: dict[str, dict[str, str]] = {
    "skill": {"label": "技能", "hint": "提供可复用的流程与知识"},
    "expert": {"label": "专家", "hint": "一组预设的人格与打法"},
    "agent": {"label": "Agent", "hint": "有自己职责与工具的角色"},
    "tool": {"label": "工具扩展", "hint": "提供可执行工具"},
    "ui": {"label": "界面扩展", "hint": "往界面上加东西的模块"},
    "backend": {"label": "后端扩展", "hint": "装在 Oryx 里的后端模块"},
    "workflow": {"label": "工作流", "hint": "多步流程"},
    "policy": {"label": "策略", "hint": "约束 Agent 行为的规则"},
    "plugin": {"label": "插件", "hint": "带自己的技能与工具的一包东西"},
}

# 「缺了」与「修」是两件事。
#
# 「依赖的扩展 X 不存在」只说了现象 —— 用户看到这句话还是不知道该干什么。
# 这里按「缺的是什么类型 + 缺的原因」直接给出下一步。
def fix_hint(reason: str, required_kind: str, required_id: str,
             op: str = "", version: str = "") -> str:
    """依赖没满足时，用户该做什么。"""
    label = (DEP_KIND_DOCS.get(required_kind) or {}).get("label", "扩展")
    if reason == "disabled":
        return f"在设置页的扩展列表里重新启用「{required_id}」"
    if reason == "version":
        return f"把「{required_id}」升级到 {op}{version}"
    if reason == "version-unknown":
        return (f"「{required_id}」没写版本号，没法验证是否满足 "
                f"{op}{version} —— 给它补上版本号")
    if reason == "cycle":
        return "改掉环上的任意一个依赖"
    # missing（默认）
    if required_kind in ("ui",):
        return f"装一个叫「{required_id}」的{label}（设置页 → 扩展 → 安装界面）"
    return f"装一个叫「{required_id}」的{label}（设置页 → 扩展 → 安装后端）"


@dataclass
class DependencyIssue:
    """一条依赖问题。"""

    ext_id: str              # 谁出的问题
    kind: str
    required_id: str
    reason: str              # missing | disabled | version | cycle | version-unknown
    detail: str              # 人话说明
    fatal: bool = True       # 致命 = 该扩展不该加载
    actual_version: str = ""
    required_kind: str = ""  # **被依赖方**的类型（skill / expert / ui / …）
    fix: str = ""            # 用户该做什么

    def to_dict(self) -> dict[str, Any]:
        return {
            "ext_id": self.ext_id,
            "kind": self.kind,
            "required_id": self.required_id,
            "reason": self.reason,
            "detail": self.detail,
            "fatal": self.fatal,
            "actual_version": self.actual_version,
            "required_kind": self.required_kind,
            "required_kind_label": (DEP_KIND_DOCS.get(self.required_kind)
                                    or {}).get("label", ""),
            "required_kind_hint": (DEP_KIND_DOCS.get(self.required_kind)
                                   or {}).get("hint", ""),
            "fix": self.fix,
        }


@dataclass
class DependencyReport:
    """一次依赖解析的完整结果。"""

    # (kind, id) → 人话原因。被阻塞的扩展在这里。
    blocked: dict[tuple[str, str], str] = field(default_factory=dict)
    issues: list[DependencyIssue] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    def is_blocked(self, kind: str, ext_id: str) -> bool:
        return (kind, ext_id) in self.blocked

    def reason(self, kind: str, ext_id: str) -> str:
        return self.blocked.get((kind, ext_id), "")

    def blocked_ids(self) -> set[str]:
        """被阻塞的扩展 id（不分类型）。"""
        return {ext_id for _kind, ext_id in self.blocked}

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": [
                {"kind": k, "id": i, "reason": r}
                for (k, i), r in sorted(self.blocked.items())
            ],
            "issues": [i.to_dict() for i in self.issues],
            "cycles": [list(c) for c in self.cycles],
        }


# ── 版本比较 ──────────────────────────────────────────────────────────

def parse_version(text: str) -> tuple[int, ...]:
    """把版本号拆成可比较的元组。

    `1.2.0` → `(1, 2, 0)`；`1.2` → `(1, 2)`。
    抽不出数字的部分直接丢掉（`1.0.0-beta` → `(1, 0, 0)`）。
    """
    return tuple(int(x) for x in re.findall(r"\d+", str(text or "")))


def compare_versions(left: str, right: str) -> int:
    """比较两个版本：左大返回 1，左小返回 -1，相同返回 0。

    短的那个按 0 补齐（`1.2` 与 `1.2.0` 视为相等）。
    """
    a = parse_version(left)
    b = parse_version(right)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    if a > b:
        return 1
    if a < b:
        return -1
    return 0


def version_satisfies(actual: str, op: str, required: str) -> bool:
    """实际版本是否满足约束。"""
    if not op:
        return True
    if not actual:
        # 没有版本号 —— 无法验证。调用方决定这算致命还是警告。
        return False
    c = compare_versions(actual, required)
    if op == ">=":
        return c >= 0
    if op == ">":
        return c > 0
    if op == "<=":
        return c <= 0
    if op == "<":
        return c < 0
    if op == "==":
        return c == 0
    if op == "!=":
        return c != 0
    return True


# ── 解析 ──────────────────────────────────────────────────────────────

def parse_requirement(raw: str) -> Requirement | None:
    """解析一条依赖写法。

    `web-research` / `web-research@>=1.1.0` / `web-research@1.1.0`
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if "@" in text:
        ext_id, _, constraint = text.partition("@")
        ext_id = ext_id.strip()
        constraint = constraint.strip()
    else:
        ext_id, constraint = text, ""
    if not ext_id:
        return None
    op = ""
    version = ""
    if constraint:
        m = _CONSTRAINT_RE.match(constraint)
        if not m:
            # 约束写歪了 —— 当成没有约束，但记下来（不致命）。
            return Requirement("", ext_id, "", "", text)
        op = m.group(1) or ("" if not m.group(2) else "==")
        version = m.group(2) or ""
    return Requirement("", ext_id, op, version, text)


def parse_requires(raw: Any) -> list[tuple[str, Requirement]]:
    """把清单里的 `requires` 解析成 [(归一化类型, 需求)]。

    写法容错：
      · 值是字符串 → 当成单个依赖（类型为「任意」）
      · 值不是列表 → 忽略（清单坏了不该让整个扩展加载失败）
      · 类型名不认识 → 当成「任意类型」
    """
    out: list[tuple[str, Requirement]] = []
    if not isinstance(raw, dict):
        return out
    for kind_key, values in raw.items():
        kind = KIND_ALIASES.get(str(kind_key).strip().lower(), "*")
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            continue
        for item in values:
            req = parse_requirement(item)
            if req is None:
                continue
            out.append((kind, Requirement(
                kind=kind, ext_id=req.ext_id, op=req.op,
                version=req.version, raw=req.raw,
            )))
    return out


# ── 解析依赖关系 ──────────────────────────────────────────────────────

def _record_kind(rec: Any) -> str:
    return str(getattr(rec, "kind", "") or "")


def _record_id(rec: Any) -> str:
    return str(getattr(rec, "id", "") or "")


def _record_version(rec: Any) -> str:
    return str(getattr(rec, "version", "") or "")


def resolve(
    records: list[Any],
    *,
    disabled: set[str] | None = None,
) -> DependencyReport:
    """解析一批扩展之间的依赖关系。

    参数
    ----
    records
        `registry.ExtensionRecord` 列表（**所有类型**的都要传 ——
        依赖可能跨类型：技能依赖专家、工具扩展依赖技能）。
    disabled
        被停用的扩展 id 集合。用来区分「没装」与「装了但被关掉」——
        这两种原因给用户的提示不一样。

    返回
    ----
    `DependencyReport`
    """
    report = DependencyReport()
    off = set(disabled or ())

    # 索引：按 (kind, id) 与按 id 各一份
    by_key: dict[tuple[str, str], Any] = {}
    by_id: dict[str, list[Any]] = {}
    for rec in records:
        kind, ext_id = _record_kind(rec), _record_id(rec)
        if not ext_id:
            continue
        by_key[(kind, ext_id)] = rec
        by_id.setdefault(ext_id, []).append(rec)

    # 1) 逐条检查直接依赖
    #    edges[ext_key] = [(req, target_key 或 None)]
    edges: dict[tuple[str, str], list[tuple[Requirement, tuple[str, str] | None]]] = {}

    for rec in records:
        kind, ext_id = _record_kind(rec), _record_id(rec)
        if not ext_id:
            continue
        reqs = parse_requires(getattr(rec, "requires", None))
        if not reqs:
            continue
        key = (kind, ext_id)
        pairs: list[tuple[Requirement, tuple[str, str] | None]] = []

        for req_kind, req in reqs:
            target_key: tuple[str, str] | None = None
            target = None

            if req_kind == "*":
                # 任意类型：按 id 在所有类型里找
                cands = by_id.get(req.ext_id) or []
                # 先找版本满足的；都不满足就取第一个（好让错误信息说清版本）
                for cand in cands:
                    if version_satisfies(_record_version(cand), req.op, req.version):
                        target = cand
                        break
                if target is None and cands:
                    target = cands[0]
            else:
                cand = by_key.get((req_kind, req.ext_id))
                if cand is not None:
                    target = cand

            if target is None:
                # 没找到 —— 区分「被停用」与「压根没装」
                if req.ext_id in off:
                    report.issues.append(DependencyIssue(
                        ext_id=ext_id, kind=kind, required_id=req.ext_id,
                        reason="disabled",
                        detail=f"依赖的扩展 {req.ext_id} 已被停用",
                        fatal=True,
                        required_kind=req.kind,
                        fix=fix_hint("disabled", req.kind, req.ext_id),
                    ))
                else:
                    report.issues.append(DependencyIssue(
                        ext_id=ext_id, kind=kind, required_id=req.ext_id,
                        reason="missing",
                        detail=f"依赖的扩展 {req.ext_id} 不存在",
                        fatal=True,
                        required_kind=req.kind,
                        fix=fix_hint("missing", req.kind, req.ext_id),
                    ))
                pairs.append((req, None))
                continue

            t_key = (_record_kind(target), _record_id(target))
            actual_ver = _record_version(target)

            if req.op and not actual_ver:
                # 有约束但对方没写版本 —— 无法验证，只警告不致命
                report.issues.append(DependencyIssue(
                    ext_id=ext_id, kind=kind, required_id=req.ext_id,
                    reason="version-unknown",
                    detail=(
                        f"依赖的扩展 {req.ext_id} 没有版本号，"
                        f"无法验证是否满足 {req.op}{req.version}"
                    ),
                    fatal=False, actual_version="",
                    required_kind=req.kind,
                    fix=fix_hint("version-unknown", req.kind, req.ext_id,
                                 req.op, req.version),
                ))
            elif not version_satisfies(actual_ver, req.op, req.version):
                report.issues.append(DependencyIssue(
                    ext_id=ext_id, kind=kind, required_id=req.ext_id,
                    reason="version",
                    detail=(
                        f"依赖的扩展 {req.ext_id} 版本为 {actual_ver or '未标注'}，"
                        f"不满足要求 {req.op}{req.version}"
                    ),
                    fatal=True, actual_version=actual_ver,
                    required_kind=req.kind,
                    fix=fix_hint("version", req.kind, req.ext_id,
                                 req.op, req.version),
                ))

            pairs.append((req, t_key))

        edges[key] = pairs

    # 2) 环检测 —— 依赖图里的环，两端都别加载
    for cycle in _find_cycles(edges):
        report.cycles.append(cycle)
        for ext_id in cycle:
            report.issues.append(DependencyIssue(
                ext_id=ext_id, kind=_kind_of_id(records, ext_id),
                required_id=ext_id, reason="cycle",
                detail="依赖形成环：" + " → ".join(cycle),
                fatal=True,
                fix=fix_hint("cycle", _kind_of_id(records, ext_id), ext_id),
            ))

    # 3) 致命问题 → 直接阻塞
    for issue in report.issues:
        if not issue.fatal:
            continue
        key = (issue.kind, issue.ext_id)
        if key not in report.blocked:
            report.blocked[key] = issue.detail

    # 4) 传递闭包 —— 依赖被阻塞的，自己也被阻塞。
    #
    # 用**反向图 + 广度优先**，不是反复迭代整张表：
    # 迭代法每轮只能往前推一层（200 层的依赖链要跑 200 轮，
    # 而且轮数一多就变成 O(n²)）。反向图一次 BFS 到底。
    #
    # 反向边：target → 依赖它的那些扩展。
    dependents: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key, pairs in edges.items():
        for _req, t_key in pairs:
            if t_key is not None:
                dependents.setdefault(t_key, []).append(key)

    queue: list[tuple[str, str]] = list(report.blocked.keys())
    seen: set[tuple[str, str]] = set(queue)
    while queue:
        blocked_key = queue.pop(0)
        blocked_reason = report.blocked[blocked_key]
        for dep_key in dependents.get(blocked_key, ()):
            if dep_key in seen:
                continue
            seen.add(dep_key)
            reason = (
                f"依赖的扩展 {blocked_key[1]} 不可用（{blocked_reason}）"
            )
            report.blocked[dep_key] = reason
            report.issues.append(DependencyIssue(
                ext_id=dep_key[1], kind=dep_key[0], required_id=blocked_key[1],
                reason="upstream", detail=reason, fatal=True,
            ))
            queue.append(dep_key)

    return report


def _kind_of_id(records: list[Any], ext_id: str) -> str:
    for rec in records:
        if _record_id(rec) == ext_id:
            return _record_kind(rec)
    return ""


def _find_cycles(
    edges: dict[tuple[str, str], list[tuple[Requirement, tuple[str, str] | None]]],
) -> list[list[str]]:
    """在依赖图里找环。

    用迭代式 DFS（不用递归）—— 依赖链可能很长，
    递归会撞 Python 的递归上限。

    返回的每个环是一串扩展 id（如 `["a", "b", "a"]`）。
    """
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    # 只保留指向「确实存在」的边 —— 缺依赖不是环
    graph: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key, pairs in edges.items():
        graph[key] = [t for _req, t in pairs if t is not None]

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[tuple[str, str], int] = {}
    stack: list[tuple[str, str]] = []

    for start in list(graph.keys()):
        if color.get(start, WHITE) != WHITE:
            continue
        # 迭代 DFS：栈里存 (节点, 下一个待看的邻居下标)
        work: list[tuple[tuple[str, str], int]] = [(start, 0)]
        color[start] = GREY
        stack.append(start)
        while work:
            node, idx = work[-1]
            nbrs = graph.get(node, [])
            if idx >= len(nbrs):
                work.pop()
                color[node] = BLACK
                if stack and stack[-1] == node:
                    stack.pop()
                continue
            work[-1] = (node, idx + 1)
            nxt = nbrs[idx]
            state = color.get(nxt, WHITE)
            if state == GREY:
                # 找到环：从栈里 nxt 的位置切出来
                try:
                    pos = stack.index(nxt)
                except ValueError:
                    continue
                cycle = stack[pos:] + [nxt]
                ids = [c[1] for c in cycle]
                sig = tuple(sorted(set(ids)))
                if sig not in seen_cycles:
                    seen_cycles.add(sig)
                    cycles.append(ids)
            elif state == WHITE:
                color[nxt] = GREY
                stack.append(nxt)
                work.append((nxt, 0))

    return cycles
