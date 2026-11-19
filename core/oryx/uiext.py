"""前端扩展 —— 让「加一个界面模块」不用改 boot.js。

现状
----
界面是零构建的 ES Module，入口只有一个：

    index.html  →  /web/modules/boot.js  →  静态 import 12 个模块

所以加一个界面模块 = **改 boot.js 加一行 import**。这就是「写死」——
每加一个功能都要动核心入口，改多了还会互相干扰。

目标
----
加一个界面模块 = 在用户目录放一个文件夹，**不用碰任何核心文件**。

形态
----
    %APPDATA%/Oryx/ui-extensions/<id>/
    ├── oryx.json     声明：id / 名称 / 图标 / 挂载点
    └── index.js      实现：export default { mount, label, icon, setup(ctx) }

后端只做两件事：**发现**与**给清单**。真正的加载在前端（浏览器里）。
清单里带静态资源 URL，前端动态 `import()` —— 仍然是零构建。

安全边界（与 Python 扩展不同，但要同样严）
-------------------------------------------
Python 扩展在**你的机器上**执行代码；前端扩展在**浏览器里**跑，
拿不到系统权限，但能：

* 调后端 API（带 token）—— 相当于以你的身份操作
* 读页面上的内容（你的对话、文件列表）
* 改界面

所以：

1. **只从用户目录加载** —— `%APPDATA%/Oryx/ui-extensions`。
   仓库里自带的（`git clone` 来的）**不自动加载**：
   clone 一个仓库不该等于让它往你的界面里插代码。
   这与 Python 扩展的 project 信任规则一致。
2. **要显式启用** —— 配置 `ui_extensions` 里列了 id 才加载。
   放进去 ≠ 自动生效。（默认只加载 `enabled: true` 的，见 `UiExtension.enabled`）
3. **清单接口不返回 token** —— 只返回 id、名称、图标、URL。
   token 走 HttpOnly cookie，JS 里根本没有它 —— 扩展拿不到凭据本身。

能力（caps）
----------
扩展可以在清单里声明 `caps`，前端据此收窄 `ctx.api`：
调用需要别的能力的接口时**明确拒绝**并说清缺哪个。

caps 的语义是「**特权清单**」不是「许可清单」——
落进能力表的路径需要对应能力，其余路径（读状态、读清单）默认放行。

**caps 是契约，不是安全边界。** 扩展与核心同源，
铁了心绕开 `ctx.api` 直接 fetch，前端拦不住（cookie 是浏览器自动带的）。
caps 防的是「作者不小心越界」与「从别的扩展复制粘贴代码」；
真正的边界是上面那三条 —— **只从用户目录加载**、**要显式启用**、
**拿不到凭据本身**。

借鉴
----
* ECC 的「同层多 provider 不覆盖」—— 这里扩展只能**新增**挂载点，
  不能替换核心模块（核心 id 是保留字）。
* agent-browser 的能力集 —— 扩展可以声明 `caps`，前端据此决定给不给某些 API。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 允许挂载的位置。前端按这些值找锚点元素。
MOUNT_POINTS: tuple[str, ...] = ("rail", "railfoot", "topbar", "side")

# 核心模块的 id 是保留字 —— 扩展不能顶掉它们。
# 顶掉了会让核心功能「莫名其妙没了」，比扩展加载失败更难查。
RESERVED_IDS: frozenset[str] = frozenset({
    "agui", "layout", "models", "theme", "core", "wiring", "boot",
    "promptopt", "search", "terminal", "topbar-ui",
    "paste", "policy", "skillcall",
})


@dataclass
class UiExtension:
    """一个前端扩展的声明。"""

    id: str
    name: str = ""
    version: str = ""
    description: str = ""
    mount: str = "rail"
    icon: str = ""
    url: str = ""              # 静态资源 URL（给前端 import 用）
    enabled: bool = True
    order: int = 0
    caps: tuple[str, ...] = ()
    # 要复用的**核心类名**（不带点），如 ("btn", "inp", "iconbtn")。
    # 后端会从 main.css 里提取这些类的规则给扩展用 ——
    # 扩展不用把核心样式抄一遍（抄了就不会跟着核心一起变）。
    base_css: tuple[str, ...] = ()
    path: Path | None = None
    problems: list[str] = field(default_factory=list)
    # 依赖声明 —— 键是类型，值是 id 列表。
    #
    # 与后端扩展的 `requires` 同一套写法（见 extdeps.parse_requires），
    # 但这里的键多两个：
    #   · `ui`      —— 依赖**另一个界面扩展**
    #   · `backend` —— 依赖某个**后端扩展**（技能 / 工具扩展 / …）
    #
    # 为什么界面扩展也要能声明依赖：
    # 一个面板可能要靠另一个面板提供的组件，也可能要靠某个后端技能
    # 提供数据。缺了它们，面板挂上去也是空的 —— 与其挂个空壳，
    # 不如不挂并说清楚缺什么。
    requires: dict[str, list[str]] = field(default_factory=dict)
    # 依赖不满足时的原因（空 = 依赖都满足）。
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "version": self.version,
            "description": self.description,
            "mount": self.mount,
            "icon": self.icon,
            "url": self.url,
            "enabled": self.enabled,
            "order": self.order,
            "caps": list(self.caps),
            "base_css": list(self.base_css),
            "requires": {k: list(v) for k, v in self.requires.items()},
            "blocked_reason": self.blocked_reason,
        }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _fingerprint(paths: list[Path]) -> str:
    """算一组文件的指纹。

    **为什么需要**：浏览器会缓存 ES Module —— 同一个 URL 第二次 import
    拿到的还是旧代码，扩展改了也不生效。

    解法：把指纹放进 URL（`index.js?v=abc123`）。
    文件改了 → 指纹变 → URL 变 → 浏览器以为是新资源，重新加载。
    这样**刷新页面就生效**，不用重启内核，也不用做任何缓存清除操作。
    """
    import hashlib

    h = hashlib.sha256()
    for p in paths:
        try:
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()[:12]


def discover_ui_extensions(root: Path) -> list[UiExtension]:
    """扫一个目录下的前端扩展。

    每个子目录算一个扩展，要有 `oryx.json` 与 `index.js`。
    坏的扩展**记问题、不静默跳过** —— 静默会让用户以为装上了。
    """
    out: list[UiExtension] = []
    if not root.exists() or not root.is_dir():
        return out

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "oryx.json"
        entry = child / "index.js"
        if not manifest_path.exists():
            continue

        problems: list[str] = []
        manifest = _read_manifest(manifest_path)
        ext_id = str(manifest.get("id") or child.name).strip()

        if not entry.exists():
            problems.append("缺 index.js")
        if ext_id in RESERVED_IDS:
            problems.append(f"id {ext_id!r} 是核心保留名，不能占用")
        mount = str(manifest.get("mount") or "rail")
        if mount not in MOUNT_POINTS:
            problems.append(f"mount {mount!r} 不在允许范围（{', '.join(MOUNT_POINTS)}）")
            mount = "rail"

        # 指纹覆盖 oryx.json 与 index.js —— 改任何一个都要重新加载
        fp = _fingerprint([manifest_path, entry])
        # 依赖声明：与后端扩展同一套写法（见 extdeps.parse_requires）。
        # 这里只做**解析**，判定交给 resolve_ui_dependencies ——
        # 依赖要跨界面扩展与后端扩展，判定时得把两边放一起看。
        requires = _parse_ui_requires(manifest.get("requires"))
        out.append(UiExtension(
            id=ext_id,
            name=str(manifest.get("name") or ext_id),
            version=str(manifest.get("version") or ""),
            description=str(manifest.get("description") or ""),
            mount=mount,
            icon=str(manifest.get("icon") or ""),
            url=f"/web/ui-extensions/{ext_id}/index.js?v={fp}",
            enabled=bool(manifest.get("enabled", True)),
            order=int(manifest.get("order") or 0),
            caps=tuple(str(c) for c in (manifest.get("caps") or [])),
            base_css=tuple(
                str(c).lstrip(".")
                for c in (manifest.get("baseCss") or [])
                if str(c).strip()
            ),
            requires=requires,
            path=child,
            problems=problems,
        ))
    out.sort(key=lambda e: (e.order, e.id))
    return out


# 界面扩展依赖的类型别名。
#
# `ui`     —— 另一个**界面扩展**
# `backend`—— 某个**后端扩展**（技能 / 工具扩展 / …）
# 其余的（skills / experts / tools …）直接写后端扩展的类型名。
UI_REQ_ALIASES: dict[str, str] = {
    "ui": "ui",
    "uis": "ui",
    "uiext": "ui",
    "uiextensions": "ui",
    "panels": "ui",
    "backend": "backend",
    "backends": "backend",
}


def _parse_ui_requires(raw: Any) -> dict[str, list[str]]:
    """解析界面扩展的 `requires`。

    值支持两种写法：
      · 列表 → `["a", "b"]`
      · 字符串 → 当成单个依赖（类型为「任意」）

    类型键做归一化（见 `UI_REQ_ALIASES`），不认识的键当「任意类型」。
    """
    out: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for key, values in raw.items():
            kind = UI_REQ_ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, (list, tuple)):
                continue
            items = [str(v).strip() for v in values if str(v).strip()]
            if not items:
                continue
            # 归一化后**合并**而不是覆盖 ——
            # 写 `{"ui": ["a"], "panels": ["b"]}` 的人想要的是两个依赖。
            bucket = out.setdefault(kind, [])
            for item in items:
                if item not in bucket:
                    bucket.append(item)
    elif isinstance(raw, str):
        text = raw.strip()
        if text:
            out["*"] = [text]
    return out


def ui_extension_roots(app_dir: Path) -> list[Path]:
    """前端扩展的搜索目录。

    **只有用户目录** —— 仓库自带的（clone 来的）不自动加载。
    理由见模块文档：clone 不该等于往你的界面里插代码。
    """
    return [app_dir / "ui-extensions"]


def resolve_ui_dependencies(
    ui_exts: list[UiExtension],
    backend_records: list[Any] | None = None,
    *,
    disabled_ids: set[str] | None = None,
) -> tuple[list[UiExtension], list[dict[str, Any]]]:
    """解析界面扩展之间的依赖关系。

    参数
    ----
    ui_exts
        已发现的界面扩展。
    backend_records
        后端扩展的记录（`registry.ExtensionRecord` 列表）。
        界面扩展可以声明依赖某个后端扩展 —— 缺了就该拦下。
    disabled_ids
        被停用的 id 集合。界面扩展与后端扩展**共用一套停用名单**，
        所以被停用的后端扩展也算「依赖不满足」。

    返回
    ----
    (依赖都满足的扩展, 被拦下的说明)

    为什么依赖由**后端**解析
    ----------------------
    界面扩展跑在浏览器里，它看不见后端扩展的存在。
    只有后端知道「web-research 这个技能装了没有」、
    「另一个面板装了没有」。所以判定放在后端，
    前端只拿到「该挂哪些 + 哪些被拦下 + 为什么」。
    """
    from .extdeps import (
        compare_versions,
        parse_requires,
        version_satisfies,
    )

    off = set(disabled_ids or ())
    problems: dict[str, str] = {}

    # ── 建索引 ──────────────────────────────────────────────────────
    ui_ids = {e.id for e in ui_exts}
    # 后端记录：按 id 索引（界面扩展不关心后端的类型）
    backend_by_id: dict[str, Any] = {}
    for rec in backend_records or []:
        # 后端记录可能是对象（ExtensionRecord），也可能是 dict
        # （server 侧从内核拿到的是轻量 dict 索引）。
        rid = str(getattr(rec, "id", "") or (rec.get("id") if isinstance(rec, dict) else "") or "")
        if rid:
            backend_by_id.setdefault(rid, rec)

    def _version_of(obj: Any) -> str:
        if isinstance(obj, dict):
            return str(obj.get("version") or "")
        return str(getattr(obj, "version", "") or "")

    def _blocked_by_dependency(ext_id: str, kind: str, target: str,
                               op: str, version: str) -> str | None:
        """这条依赖满足吗？不满足返回原因，满足返回 None。"""
        # 0) 停用优先 —— 被停用的扩展**不算存在**。
        #    这一步必须在「存在吗」之前判：停用的扩展已经不在列表里了，
        #    先判存在的话会误报成「不存在」，用户以为自己装错了。
        if target in off:
            label = "界面扩展" if kind == "ui" else "后端扩展"
            return f"依赖的{label} {target} 已被停用"

        # 1) 界面扩展依赖
        if kind in ("ui",):
            if target in ui_ids:
                # 目标存在 —— 检查版本
                tgt = next((e for e in ui_exts if e.id == target), None)
                if tgt is not None and op and not _version_of(tgt):
                    return None   # 没版本号 → 无法验证，不拦
                if tgt is not None and op and not version_satisfies(
                        _version_of(tgt), op, version):
                    return (f"依赖的界面扩展 {target} 版本为 "
                            f"{_version_of(tgt) or '未标注'}，不满足要求 {op}{version}")
                return None
            return f"依赖的界面扩展 {target} 不存在"

        # 2) 后端扩展依赖（显式写 backend，或写具体类型）
        rec = backend_by_id.get(target)
        if rec is None:
            return f"依赖的后端扩展 {target} 不存在"
        # 类型要对得上 —— 「依赖 skills/web-research」不该被一个
        # 同名的 tool 扩展顶替。（写 backend 的才是「不分类型」）
        rec_kind = (str(rec.get("kind") or "") if isinstance(rec, dict)
                    else str(getattr(rec, "kind", "") or ""))
        if kind not in ("backend", "*") and rec_kind and rec_kind != kind:
            return (f"依赖的扩展 {target} 是 {rec_kind} 类型，"
                    f"不是 {kind}")
        actual = _version_of(rec)
        if op and not actual:
            return None   # 没版本号 → 无法验证，不拦
        if op and not version_satisfies(actual, op, version):
            return (f"依赖的后端扩展 {target} 版本为 {actual}，"
                    f"不满足要求 {op}{version}")
        return None

    # ── 逐条检查直接依赖 ────────────────────────────────────────────
    # edges[ext_id] = [(kind, target, op, version)]
    edges: dict[str, list[tuple[str, str, str, str]]] = {}
    for ext in ui_exts:
        reqs = parse_requires(ext.requires)
        if not reqs:
            continue
        pairs: list[tuple[str, str, str, str]] = []
        for kind, req in reqs:
            # 「任意类型」的依赖：界面扩展与后端扩展里找
            if kind == "*":
                # 找到一个**满足**的就算满足；都找不到再报错。
                # 报哪个原因？取「最具体」的那个 ——
                # 两边都没有时说「不存在」，只有一边有但版本不对时说版本。
                reasons: list[str] = []
                satisfied = False
                for cand_kind in ("ui", "backend"):
                    why = _blocked_by_dependency(
                        ext.id, cand_kind, req.ext_id, req.op, req.version)
                    if why is None:
                        satisfied = True
                        break
                    reasons.append(why)
                if not satisfied:
                    # 优先报「不存在」——比版本问题更根本
                    missing = [r for r in reasons if "不存在" in r]
                    problems[ext.id] = (missing or reasons)[0]
                    continue
                pairs.append(("*", req.ext_id, req.op, req.version))
                continue
            why = _blocked_by_dependency(
                ext.id, kind, req.ext_id, req.op, req.version)
            if why:
                problems[ext.id] = why
                continue
            pairs.append((kind, req.ext_id, req.op, req.version))
        edges[ext.id] = pairs

    # ── 环检测 ──────────────────────────────────────────────────────
    cycles = _find_ui_cycles(edges)
    for cycle in cycles:
        for ext_id in cycle:
            problems.setdefault(ext_id, "依赖形成环：" + " → ".join(cycle))

    # ── 传递闭包 ────────────────────────────────────────────────────
    # 反向图 + BFS（与 extdeps 同一套思路）。
    dependents: dict[str, list[str]] = {}
    for src, pairs in edges.items():
        for _kind, target, _op, _ver in pairs:
            dependents.setdefault(target, []).append(src)
    queue = list(problems.keys())
    seen = set(queue)
    while queue:
        blocked_id = queue.pop(0)
        for dep_id in dependents.get(blocked_id, ()):
            if dep_id in seen:
                continue
            seen.add(dep_id)
            problems[dep_id] = f"依赖的扩展 {blocked_id} 不可用（{problems[blocked_id]}）"
            queue.append(dep_id)

    # ── 分流 ────────────────────────────────────────────────────────
    ok: list[UiExtension] = []
    blocked: list[dict[str, Any]] = []
    for ext in ui_exts:
        # **被停用的自己也不该挂载** —— 这不是「依赖不满足」，
        # 是用户主动关掉了它。理由要说清楚，别混进依赖问题里。
        if ext.id in off:
            ext.blocked_reason = "已停用"
            blocked.append({"id": ext.id, "reason": "已停用"})
            continue
        why = problems.get(ext.id, "")
        ext.blocked_reason = why
        if why:
            blocked.append({"id": ext.id, "reason": why})
        else:
            ok.append(ext)
    return ok, blocked


def _find_ui_cycles(edges: dict[str, list[tuple[str, str, str, str]]]) -> list[list[str]]:
    """在界面扩展的依赖图里找环。

    迭代式 DFS（不用递归）—— 依赖链可能很长。
    """
    cycles: list[list[str]] = []
    seen_sigs: set[tuple[str, ...]] = set()
    graph = {src: [t for _k, t, _o, _v in pairs] for src, pairs in edges.items()}

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    stack: list[str] = []

    for start in list(graph.keys()):
        if color.get(start, WHITE) != WHITE:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
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
                try:
                    pos = stack.index(nxt)
                except ValueError:
                    continue
                cycle = stack[pos:] + [nxt]
                sig = tuple(sorted(set(cycle)))
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    cycles.append(cycle)
            elif state == WHITE:
                color[nxt] = GREY
                stack.append(nxt)
                work.append((nxt, 0))
    return cycles


def load_ui_extensions(
    app_dir: Path,
    *,
    allow_ids: list[str] | None = None,
    disabled_ids: set[str] | None = None,
    backend_records: list[Any] | None = None,
) -> tuple[list[UiExtension], list[dict[str, Any]]]:
    """加载前端扩展。

    参数
    ----
    app_dir
        应用数据目录（`config.app_dir()`）。
    allow_ids
        白名单。给了就只加载名单里的；没给就加载所有 `enabled: true` 的。
    disabled_ids
        黑名单。名单里的**一律不加载** —— 这是「停用」的实现。
        与白名单不同：黑名单是用户主动关掉的，要能覆盖 `enabled: true`。
    backend_records
        后端扩展记录 —— 用来判定界面扩展声明的跨端依赖。

    返回
    ----
    (可加载的扩展, 被跳过的说明)
    """
    loaded: list[UiExtension] = []
    skipped: list[dict[str, Any]] = []
    off = disabled_ids or set()

    for root in ui_extension_roots(app_dir):
        for ext in discover_ui_extensions(root):
            if ext.id in off:
                skipped.append({"id": ext.id, "reason": "已停用"})
                continue
            if ext.problems:
                skipped.append({
                    "id": ext.id,
                    "reason": "；".join(ext.problems),
                })
                continue
            if not ext.enabled:
                skipped.append({"id": ext.id, "reason": "清单里 enabled=false"})
                continue
            if allow_ids is not None and ext.id not in allow_ids:
                skipped.append({"id": ext.id, "reason": "不在启用名单里"})
                continue
            loaded.append(ext)

    # 依赖判定 —— **放在最后**：先把「自己能加载的」筛出来，
    # 再看它们的依赖是否满足。依赖不满足的也要算进 skipped。
    usable, blocked = resolve_ui_dependencies(
        loaded, backend_records, disabled_ids=off)
    skipped.extend(blocked)
    return usable, skipped
