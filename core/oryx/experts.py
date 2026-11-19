"""专家人设：给助手挂一个"角色"，改变它的工作方式与输出标准。

约定与 SKILL.md 一致——每个专家一个目录，一个 `EXPERT.md`：

    experts/<id>/EXPERT.md
    ---
    name: 数据分析师
    description: 一句话说明这个专家擅长什么
    scenarios: 数据清洗, 报表制作, 指标口径
    ---
    正文是这个专家的工作方式、输出标准、常见错误。

专家只影响"怎么做"，不影响"能不能做"——工具权限仍由交互模式与策略引擎控制。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# frontmatter 解析 —— **全项目唯一一份**（oryx/frontmatter.py）。
from .frontmatter import as_list, parse_frontmatter
from typing import Any



@dataclass
class Expert:
    id: str
    name: str
    description: str
    scenarios: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None
    # 人设要素：性格关键词 / 说话风格 / 适用场景 / 自我介绍 / 标签
    tags: list[str] = field(default_factory=list)
    personality: str = ""
    speaking_style: str = ""
    use_cases: str = ""
    self_intro: str = ""

    def catalog_line(self) -> str:
        return f"- {self.name}：{self.description}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "scenarios": self.scenarios,
            "tags": self.tags,
            "personality": self.personality,
            "speaking_style": self.speaking_style,
            "use_cases": self.use_cases,
            "self_intro": self.self_intro,
        }


class ExpertLibrary:
    def __init__(self, root: Path, registry: Any = None):
        self.root = root
        # registry 是可选的：有它就能拿到「这个专家被停用了」，
        # 也能拿到触发词 / 能力 / 契约。
        # 没有它（老调用方）行为与改造前完全一致。
        self._registry = registry
        self.experts: dict[str, Expert] = {}
        self.reload()

    def reload(self) -> None:
        self.experts.clear()
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*/EXPERT.md")):
            try:
                text = path.read_text("utf-8")
            except Exception:
                continue
            meta, body = parse_frontmatter(text)
            expert_id = path.parent.name
            # **停用的专家不加载**。
            #
            # 以前这个库自己扫目录、从不问注册表 ——
            # 于是界面上点「停用」，注册表里确实没了，
            # 但这个库照常加载，专家照样能被选中。
            # 「用户主动关掉的，不加载」这条规则对专家是装饰品。
            if self._registry is not None and self._registry.is_disabled(
                    expert_id, path.parent.name):
                continue
            scenarios = as_list(meta.get("scenarios"))
            tags = as_list(meta.get("tags"))
            self.experts[expert_id] = Expert(
                id=expert_id,
                name=meta.get("name") or expert_id,
                description=meta.get("description") or "",
                scenarios=scenarios,
                body=body.strip(),
                path=path,
                tags=tags,
                personality=meta.get("personality", ""),
                speaking_style=meta.get("speaking_style", ""),
                use_cases=meta.get("use_cases", ""),
                self_intro=meta.get("self_intro", ""),
            )

    def get(self, expert_id: str | None) -> Expert | None:
        if not expert_id:
            return None
        return self.experts.get(expert_id)

    def catalog(self) -> list[dict]:
        return [e.to_dict() for e in self.experts.values()]

    def prompt_block(self, expert: Expert) -> str:
        parts = [f"你现在以「{expert.name}」的身份工作。{expert.description}"]
        if expert.personality:
            parts.append(f"性格关键词：{expert.personality}")
        if expert.speaking_style:
            parts.append(f"说话风格：{expert.speaking_style}")
        if expert.use_cases:
            parts.append(f"适用场景：{expert.use_cases}")
        parts.append(expert.body)
        return "\n\n".join(parts)
