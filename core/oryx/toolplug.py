"""工具扩展 —— 让「加工具」不用改 Python。

形态
----
一个扩展目录里放一个 `tools.py`，导出 `TOOLS` 列表；
`oryx.json` 里声明每个工具的 **schema 与风险等级**：

    extensions/my-ext/
    ├── oryx.json          声明层：工具清单（可审计，不含代码）
    └── tools.py           实现层：TOOLS = [{"name": ..., "fn": ...}]

为什么元数据与实现分开
----------------------
风险等级写在**清单**里，而不是代码里 —— 这样「这个工具会干什么、有多危险」
可以在**不执行任何代码**的情况下审阅。实现层只提供 `fn`。

安全边界（这一层是整个设计的关键）
--------------------------------
执行扩展里的 Python = 任意代码执行。所以：

1. **信任分级决定能不能执行**：
   · `builtin` / `user` —— 可以加载可执行工具
   · `project`（`git clone` 来的仓库自带）—— **拒绝执行**。
     只允许声明工具元数据（供路由与提示词使用）。
   · `plugin` —— 取决于插件自身的信任级别。
2. **风险等级必须声明**：清单里没写 risk 的工具一律按 `L2`（破坏性）处理 ——
   与 MCP 的既有约定一致（「第三方能力…一律按 L2 处理」）。
3. **加载要留痕**：`load_report()` 记录加载了哪些、跳过了哪些、为什么。
   用户能问「现在到底加载了谁的代码」。
4. **失败要说清楚**：加载不了就报错，不静默跳过 ——
   静默跳过会让人以为工具可用。

借鉴
----
* auto-ontology 的「概念层与实现层解耦」——
  这里是「声明层（清单）」与「实现层（代码）」解耦。
* agent-browser 的能力集与信任分级。
* ECC 的 manifest 声明 + 侧车指纹（这里用加载报告代替）。
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .policy import Risk
from .tools import ToolSpec

# 允许提供可执行工具的信任级别。
#
# 为什么 `project` 不在列表里：`git clone` 一个仓库不该等于授权它的代码
# 在你机器上跑。仓库自带的扩展只能声明元数据。
EXECUTABLE_TRUST = frozenset({"builtin", "user"})

# 风险等级的名字 → 枚举值
RISK_NAMES: dict[str, Risk] = {
    "L0_READ": Risk.L0_READ,
    "L1_WRITE": Risk.L1_WRITE,
    "L2_DESTRUCTIVE": Risk.L2_DESTRUCTIVE,
    "L3_FORBIDDEN": Risk.L3_FORBIDDEN,
}


class ToolPluginError(RuntimeError):
    """工具扩展加载失败。

    单独一个异常类型，是为了让调用方能**区分**「扩展坏了」与
    「平台出错了」—— 前者是用户能修的（改清单），后者不是。
    """


@dataclass
class LoadedTool:
    """一个从扩展加载进来的工具。"""

    spec: ToolSpec
    extension_id: str
    trust: str
    declared_risk: str


@dataclass
class LoadReport:
    """加载报告 —— 加载了什么、跳过了什么、为什么。

    用户可以随时问「现在到底加载了谁的代码」，答案就在这里。
    """

    loaded: list[LoadedTool] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (工具名, 原因)
    errors: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)               # 加载过的代码文件

    def summary(self) -> str:
        if not self.loaded and not self.skipped and not self.errors:
            return "（没有工具扩展）"
        lines = []
        if self.loaded:
            lines.append(f"已加载 {len(self.loaded)} 个扩展工具：")
            for t in self.loaded:
                lines.append(f"  · {t.spec.name}（来自 {t.extension_id}，风险 {t.declared_risk}）")
        if self.skipped:
            lines.append(f"跳过 {len(self.skipped)} 个：")
            for name, why in self.skipped:
                lines.append(f"  · {name} —— {why}")
        if self.errors:
            lines.append(f"出错 {len(self.errors)} 个：")
            for e in self.errors:
                lines.append(f"  · {e}")
        return "\n".join(lines)


def _parse_risk(value: Any) -> tuple[Risk, str]:
    """读风险等级。

    缺失或认不出来 → 一律按 L2（破坏性）处理。
    宁可严一点：把未知工具当成破坏性操作，它就会被审批流程拦一下。
    """
    if value is None:
        return Risk.L2_DESTRUCTIVE, "L2_DESTRUCTIVE"
    if isinstance(value, int):
        try:
            r = Risk(value)
        except ValueError:
            return Risk.L2_DESTRUCTIVE, "L2_DESTRUCTIVE"
        return r, r.name
    name = str(value).strip().upper()
    if name in RISK_NAMES:
        return RISK_NAMES[name], name
    # 也接受 "L2" / "l2" 这种简写
    short = name.replace("_DESTRUCTIVE", "").replace("_READ", "").replace("_WRITE", "")
    for key, r in RISK_NAMES.items():
        if key.startswith(short) and short in ("L0", "L1", "L2", "L3"):
            return r, r.name
    return Risk.L2_DESTRUCTIVE, "L2_DESTRUCTIVE"


def _load_module(path: Path) -> Any:
    """从文件路径加载一个 Python 模块。

    **不污染 sys.path** —— 用 importlib 直接指定文件位置，
    免得扩展目录里的模块名跟平台模块撞车（或者反过来污染平台）。
    """
    mod_name = f"oryx_ext_{abs(hash(str(path))):x}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ToolPluginError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    # 放进 sys.modules 是必须的：模块内部若有相对导入或装饰器会依赖它
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — 扩展的错不该拖垮平台
        sys.modules.pop(mod_name, None)
        raise ToolPluginError(f"{path.name} 执行失败：{type(exc).__name__}: {exc}") from exc
    return module


def _resolve_entry(module: Any, entry: str) -> Callable[..., Any]:
    """按 `模块:函数` 或 `模块.函数` 取函数。"""
    if ":" in entry:
        _, _, attr = entry.partition(":")
    else:
        attr = entry
    obj: Any = module
    for part in attr.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            raise ToolPluginError(f"入口 {entry!r} 找不到")
    if not callable(obj):
        raise ToolPluginError(f"入口 {entry!r} 不是可调用对象")
    return obj


def changed_extensions(
    records: list[Any],
    known: dict[str, str],
) -> list[Any]:
    """找出指纹变了的扩展。

    `known` 是 {扩展 id: 上次的指纹}。新的扩展（不在 known 里）也算「变了」——
    它需要被加载。
    """
    out: list[Any] = []
    for rec in records:
        fp = getattr(rec, "fingerprint", "") or ""
        if known.get(rec.id) != fp:
            out.append(rec)
    return out


def load_tool_plugins(
    records: list[Any],
    *,
    trust_of: Callable[[Any], str] | None = None,
) -> tuple[dict[str, ToolSpec], LoadReport]:
    """从扩展记录里加载工具。

    参数
    ----
    records
        `registry.ExtensionRecord` 列表。
    trust_of
        取扩展信任级别的函数；默认读 `record.trust.value`。

    返回
    ----
    (工具表, 加载报告)

    加载的顺序按扩展 id 排序 —— 保证同一组扩展每次加载出的顺序一致，
    否则「工具清单」在界面上会随机跳动。
    """
    report = LoadReport()
    specs: dict[str, ToolSpec] = {}

    def trust_of_rec(rec: Any) -> str:
        if trust_of is not None:
            return trust_of(rec)
        t = getattr(getattr(rec, "trust", None), "value", None)
        return str(t or "project")

    for rec in sorted(records, key=lambda r: getattr(r, "id", "")):
        meta = dict(getattr(rec, "meta", {}) or {})
        declared = meta.get("tools")
        if not declared:
            continue
        if not isinstance(declared, list):
            report.errors.append(f"{rec.id}: tools 字段不是数组")
            continue

        # 依赖不满足 —— **不加载这个扩展的任何工具**。
        # 扩展缺了依赖就跑不对，让它半跑比不跑更糟：
        # 模型会以为工具可用，调了才出错。
        blocked = str(getattr(rec, "blocked_reason", "") or "")
        if blocked:
            for item in declared:
                if isinstance(item, dict):
                    name = str(item.get("name") or rec.id)
                    report.skipped.append((name, f"{rec.id}: 依赖不满足 —— {blocked}"))
            continue

        trust = trust_of_rec(rec)
        for item in declared:
            if not isinstance(item, dict):
                report.skipped.append(("?", f"{rec.id}: 工具声明不是对象"))
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                report.skipped.append(("?", f"{rec.id}: 工具缺 name"))
                continue

            entry = str(item.get("entry") or "").strip()
            if not entry:
                # 只声明元数据、不带实现 —— 这是「project 信任级别」的合法形态
                report.skipped.append((name, f"{rec.id}: 只声明了元数据（无 entry），不注册为可调用工具"))
                continue

            if trust not in EXECUTABLE_TRUST:
                report.skipped.append(
                    (name, f"{rec.id}: 信任级别 {trust} 不允许执行扩展代码")
                )
                continue

            code_file = rec.path / entry.split(":")[0].split(".")[0]
            code_file = code_file.with_suffix(".py") if not code_file.suffix else code_file
            if not code_file.exists():
                report.errors.append(f"{rec.id}: 找不到代码文件 {code_file.name}")
                continue

            try:
                module = _load_module(code_file)
                fn = _resolve_entry(module, entry)
            except ToolPluginError as exc:
                report.errors.append(f"{rec.id}.{name}: {exc}")
                continue

            risk, risk_name = _parse_risk(item.get("risk"))
            params = item.get("parameters")
            if not isinstance(params, dict):
                params = {"type": "object", "properties": {}, "required": []}

            spec = ToolSpec(
                name=name,
                description=str(item.get("description") or f"{rec.id} 提供的工具 {name}"),
                parameters=params,
                risk=risk,
                fn=fn,
                embed_text=str(item.get("embed_text") or ""),
                mutates=item.get("mutates") if isinstance(item.get("mutates"), bool) else None,
            )
            # 标记这个工具属于哪个扩展 —— 热重载时要靠它摘掉旧工具。
            setattr(spec, _TOOL_OWNER_ATTR, rec.id)
            if name in specs:
                report.errors.append(f"{name}: 与已有工具重名，已跳过（来自 {rec.id}）")
                continue
            specs[name] = spec
            report.loaded.append(LoadedTool(spec, rec.id, trust, risk_name))
            if str(code_file) not in report.sources:
                report.sources.append(str(code_file))

    return specs, report


def tools_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """从 oryx.json 里取出工具声明。

    清单格式（写在扩展的 oryx.json 里）：

        "tools": [
          {"name": "excel_query", "description": "…",
           "parameters": {...}, "risk": "L0_READ",
           "entry": "tools.py:query_excel"}
        ]
    """
    tools = manifest.get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


# ── 热重载 ────────────────────────────────────────────────────────────

def reload_tool_plugins(
    records: list[Any],
    current: dict[str, ToolSpec],
    known: dict[str, str],
    *,
    trust_of: Callable[[Any], str] | None = None,
) -> tuple[dict[str, ToolSpec], LoadReport, list[str]]:
    """重新加载指纹变了的扩展。

    返回
    ----
    (新的工具表, 加载报告, 被重载的扩展 id 列表)

    做了什么
    --------
    1. 找出指纹变了的扩展（`known` 是上次的 {id: 指纹}）
    2. **把它们的旧工具从表里摘掉** —— 不摘的话，改了名的工具会留下残骸
    3. 重新执行扩展的 Python 模块（模块缓存里的旧版本要清掉）
    4. 重建 ToolSpec —— 旧 ToolSpec 里的 `fn` 指向旧函数，不重建就没生效

    为什么**完全重新执行**而不是 `importlib.reload()`：
    reload 会保留模块的全局状态 —— 扩展改了代码但旧状态还在，
    行为可能对不上。重新执行是干净的，代价是模块里的状态归零
    （对工具扩展来说，这通常正是想要的）。

    注意：扩展代码在**你的机器上**执行，所以热重载只对
    **可信来源**（builtin / user）生效 —— project 信任的本来就不执行代码。
    """
    report = LoadReport()
    changed = changed_extensions(records, known)
    changed_ids = {r.id for r in changed}
    reloaded: list[str] = []

    if not changed_ids:
        return dict(current), report, reloaded

    # 1) 摘掉这些扩展的旧工具
    specs: dict[str, ToolSpec] = {
        name: spec for name, spec in current.items()
        if getattr(spec, _TOOL_OWNER_ATTR, None) not in changed_ids
    }

    # 2) 重新加载
    new_specs, sub = load_tool_plugins(changed, trust_of=trust_of)
    specs.update(new_specs)
    report.loaded.extend(sub.loaded)
    report.skipped.extend(sub.skipped)
    report.errors.extend(sub.errors)
    report.sources.extend(sub.sources)
    reloaded = sorted(changed_ids)
    return specs, report, reloaded


# 记录「这个工具属于哪个扩展」—— 热重载时要靠它摘掉旧工具。
# 挂在 ToolSpec 上而不是另开表：ToolSpec 本来就是工具的载体。
_TOOL_OWNER_ATTR = "_uiext_owner"


def is_extension_tool(spec: Any) -> bool:
    """这个工具是扩展提供的吗（而不是内置 / MCP）。

    热重载时要用它区分：
      · registry 里已有的是**扩展自己上次加载的** → 可以直接替换
      · registry 里已有的是**内置或 MCP** → 扩展不能覆盖（要拒绝并记冲突）
    """
    return getattr(spec, _TOOL_OWNER_ATTR, None) is not None


def tool_owner(spec: Any) -> str:
    """这个工具属于哪个扩展（空串 = 不是扩展提供的）。"""
    return str(getattr(spec, _TOOL_OWNER_ATTR, "") or "")
