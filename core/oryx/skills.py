"""技能加载：SKILL.md 约定（与 Claude Code / claude-plugins-official 一致）。

目录结构：
    skills/<skill-name>/SKILL.md      —— 必需，带 YAML frontmatter
    skills/<skill-name>/scripts/...   —— 可选，技能自带脚本
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# frontmatter 解析 —— **全项目唯一一份**（oryx/frontmatter.py）。
from .frontmatter import as_bool, as_list, parse_frontmatter



# 技能正文注入上下文时的最大字符数。
#
# 放在这里而不是 `loop.py` —— 读技能的工具（`use_skill`）
# 已经拆到 `oryx/toolsets/use_skill.py`，两边都要用同一个值。
MAX_SKILL_CHARS = 8000


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str
    # ── 以下字段由 registry 补充（见 oryx/registry.py）──────────────
    # 老技能不写 oryx.json 时全部用默认值 —— 行为与改造前一致。
    triggers: tuple[str, ...] = ()
    eager: bool = False
    allowed_tools: tuple[str, ...] = ()
    contracts: Any = None      # registry.Contracts | None
    # 依赖不满足时的原因（空 = 依赖都满足）。
    # 非空的技能**不出现在目录里** —— 让模型去用一个缺依赖的技能，
    # 只会让它按流程走一半然后卡住。
    blocked_reason: str = ""

    def summary_line(self) -> str:
        return f"- {self.name}: {self.description}"


class SkillLibrary:
    def __init__(self, root: Path, registry: Any = None):
        self.root = root
        self._roots: list[Path] = [root]
        # registry 是可选的：有它就能拿到触发词 / 常驻标记 / 契约。
        # 没有它（老调用方）行为与改造前完全一致。
        self._registry = registry
        self.skills: dict[str, Skill] = {}
        self.reload()

    def add_root(self, path: Path | None) -> None:
        """挂载额外的技能目录（插件自带技能走这里）。"""
        if path is None:
            return
        path = Path(path)
        if path in self._roots or not path.exists():
            return
        self._roots.append(path)
        self._load_root(path)

    def reload(self) -> None:
        self.skills.clear()
        # 有注册表时，**扫描哪些目录由注册表说了算**。
        #
        # 以前这里只扫 `self.root`（仓库的 skills/），
        # 而注册表同时挂着用户目录（app_dir()/skills）——
        # 于是「装在用户目录的技能」注册表看得见、这里看不见：
        # 设置页里显示着，模型却用不了。
        #
        # 让注册表当唯一的目录来源，这类新增位置就只改一处。
        roots = self._roots
        if self._registry is not None:
            reg_roots = [Path(p) for p in self._registry.roots_of_kind("skill")]
            for p in reg_roots:
                if p not in roots:
                    roots = roots + [p]
        for root in roots:
            self._load_root(root)

    def _load_root(self, root: Path) -> None:
        if not root.exists():
            return
        for skill_file in sorted(root.glob("*/SKILL.md")):
            try:
                text = skill_file.read_text("utf-8")
            except Exception:
                continue
            meta, body = parse_frontmatter(text)
            name = meta.get("name") or skill_file.parent.name
            description = meta.get("description") or ""
            skill = Skill(
                name=name, description=description, path=skill_file, body=body,
            )
            # 元数据（触发词 / 常驻 / 能力 / 契约）由 registry 统一提供 ——
            # 它已经解析过 frontmatter 与 oryx.json，这里不再重复解析一遍。
            if self._registry is not None:
                rec = self._registry.get("skill", name)
                if rec is None:
                    # 清单 id 与 frontmatter name 不一致时，按目录名再找一次。
                    # 不一致不是罕见情况 —— 目录叫 file-search，
                    # 清单里 id 写成 file_search，就找不到。
                    rec = self._registry.get("skill", skill_file.parent.name)
                if rec is not None:
                    skill.triggers = rec.triggers
                    skill.eager = rec.eager
                    skill.allowed_tools = rec.allowed_tools
                    skill.contracts = rec.contracts
                    # 依赖不满足的技能**不进目录** —— 但仍留在 skills 里，
                    # 这样 use_skill 能回一句「它缺什么」而不是「没这个技能」。
                    skill.blocked_reason = rec.blocked_reason
                # **停用的技能不加载**。
                # 停用名单是扁平的 id 集合，注册表里已经把它剔掉了，
                # 这里再查一次是为了覆盖「注册表没扫到但停用了」的目录
                # （比如用户目录里的技能）。
                if self._registry.is_disabled(name, skill_file.parent.name):
                    continue
            else:
                # 没有注册表 —— 自己从 frontmatter 取。
                #
                # 改造前就是这样（触发词从 frontmatter 直接解析）。
                # 现在只走注册表的话，**老调用方拿到的技能触发词是空的** ——
                # 技能永远匹配不上，等于没有。
                skill.triggers = tuple(as_list(meta.get("triggers")))
                skill.eager = as_bool(meta.get("eager"))
                skill.allowed_tools = tuple(as_list(meta.get("allowed-tools")))
            self.skills[name] = skill

    def catalog(self, query: str = "", limit_tokens: int = 0) -> str:
        """技能目录（会进系统提示的那一段）。

        改造后：**按本轮 query 与预算裁剪**，而不是全量列出。
        技能一多，全量目录就是几千 token 的纯浪费 —— 而且大部分这轮用不上。

        `limit_tokens <= 0` 表示不限制（老行为）。
        裁剪后仍会把「还有多少个没列出来」写清楚 —— 模型得知道
        「不一定只有这几个技能」，否则会漏掉本该用的。

        **依赖不满足的技能不列** —— 让模型去用一个缺依赖的技能，
        只会让它按流程走一半然后卡住。
        """
        if not self.skills:
            return "（当前没有可用技能）"

        # 先滤掉依赖不满足的 —— 目录是「能用的技能」清单，不是「装了什么」清单
        usable = [s for s in self.skills.values() if not s.blocked_reason]
        if not usable:
            return "（当前没有可用技能）"

        items = list(usable)

        if limit_tokens > 0 and self._registry is not None:
            from .context_budget import ContextProfile, select

            records = [r for r in self._registry.of_kind("skill") if r.id in self.skills]
            if records:
                prof = ContextProfile(limit_tokens=limit_tokens, mode="report-only")
                decision = select(records, prof, query=query)
                keep = set(decision.included)
                if keep:
                    items = [s for s in items if s.name in keep]

        lines = [s.summary_line() for s in items]
        hidden = [s for s in usable if s not in items]
        if hidden:
            # 未列出的技能**保留名字** —— 只省掉描述的 token。
            # 理由：模型得知道它们存在，否则不会想到去 use_skill 读。
            # 只写「另有 4 个技能」是不够的，模型不知道该读哪个。
            names = "、".join(s.name for s in hidden)
            lines.append(f"- （另有 {len(hidden)} 个技能未展开描述：{names}；用 use_skill 按名称读取）")
        return "\n".join(lines)

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def usable(self, name: str) -> Skill | None:
        """取一个**依赖都满足**的技能。

        与 `get` 的区别：`get` 会返回被阻塞的技能（用户得能看到它存在），
        `usable` 只给真正能用的。
        """
        skill = self.skills.get(name)
        if skill is None or skill.blocked_reason:
            return None
        return skill

    def match(self, query: str) -> list[Skill]:
        """粗粒度匹配：名称命中优先，其次描述命中。供 UI 展示建议。

        改造后：优先用 registry 的**三级路由**（显式触发词 → 描述 →
        名称 token），带分数下限，避免「描述里有个词就算命中」。
        没有 registry 时退回原来的朴素匹配 —— 老行为一条不少。

        依赖不满足的技能不参与匹配 —— 建议一个用不了的技能没有意义。
        """
        if self._registry is not None:
            from .registry import route

            records = [
                r for r in self._registry.of_kind("skill")
                if r.id in self.skills and not r.blocked_reason
            ]
            if records:
                result = route(records, query)
                if result.matched:
                    return [self.skills[r.id] for r in result.matched if r.id in self.skills]
        lowered = query.lower()
        hits: list[Skill] = []
        for skill in self.skills.values():
            if skill.blocked_reason:
                continue
            if skill.name.lower() in lowered or any(
                token and token in lowered for token in skill.name.split("-")
            ):
                hits.append(skill)
            elif any(word and word in lowered for word in skill.description.split()):
                hits.append(skill)
        return hits
