"""提示词优化：把用户随手一句话，变成模型能直接执行的指令。

优化不是"改写得更漂亮"，而是补齐执行所需的三件事：
1. 目标是什么（动词 + 对象）
2. 在哪做（工作目录 / 范围）
3. 怎么算完成（验收标准）

可选绑定技能：绑了 skill 就把该技能的使用要点带进优化结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RULES = [
    {
        "id": "no-verb",
        "test": lambda t: not any(v in t for v in ("查", "找", "改", "写", "删", "整理", "分析", "生成", "打开", "看看", "做", "创建", "下载", "安装", "运行", "执行", "提取", "汇总")),
        "advice": "补上要执行的动作，比如「查」「改」「整理」",
    },
    {
        "id": "no-scope",
        "test": lambda t: not any(v in t for v in ("D:", "C:", "目录", "文件夹", "项目", "仓库", "根目录", "当前")),
        "advice": "说明在哪个范围做，比如「在 D:\\项目 下」",
    },
    {
        "id": "no-criteria",
        "test": lambda t: not any(v in t for v in ("输出", "格式", "表格", "报告", "列表", "生成到", "保存到", "完成", "标准", "到", "成")),
        "advice": "说清要什么结果，比如「输出成表格」",
    },
    {
        "id": "too-short",
        "test": lambda t: len(t) < 8,
        "advice": "太短了，模型只能猜你的意图",
    },
]


@dataclass
class Critique:
    id: str
    advice: str


def critique(text: str) -> list[Critique]:
    """指出这句话缺了什么。"""
    stripped = (text or "").strip()
    if not stripped:
        return [Critique("empty", "还没写内容")]
    return [Critique(r["id"], r["advice"]) for r in RULES if r["test"](stripped)]


def optimize(
    text: str,
    skills: dict[str, Any] | None = None,
    bound: list[str] | None = None,
    expert: str | None = None,
    style: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """返回优化后的提示词 + 说明改了什么。

    skills 是可用技能表（name → skill 对象），bound 是要绑定的技能名。
    """
    original = (text or "").strip()
    if not original:
        return {
            "ok": False,
            "content": "先写点东西再优化。",
            "original": original,
            "optimized": original,
            "notes": [],
        }

    notes: list[str] = []
    parts: list[str] = [original]

    problems = critique(original)
    if problems:
        notes.append("原文缺少：" + "；".join(p.advice for p in problems))

    # 补工作目录
    if workspace and not any(k in original for k in ("D:", "C:", "当前目录", "这个目录")):
        parts.append(f"（工作目录：{workspace}）")
        notes.append("已补上工作目录")

    # 绑定技能
    available = skills or {}
    chosen = [s.strip() for s in (bound or []) if s.strip()]
    if chosen:
        skill_notes = []
        missing = []
        for name in chosen:
            skill = available.get(name)
            if skill is None:
                missing.append(name)
                continue
            desc = getattr(skill, "description", "") or ""
            skill_notes.append(f"- {name}：{desc}" if desc else f"- {name}")
        if skill_notes:
            parts.append("可参考的技能：\n" + "\n".join(skill_notes))
            notes.append("已绑定技能：" + "、".join(s.split(":")[0] for s in skill_notes))
        if missing:
            notes.append("没有这些技能：" + "、".join(missing))
    else:
        notes.append("没有绑定技能；知道要用哪个技能就在句子里点名它")

    if expert:
        parts.append(f"以「{expert}」的角色处理")
        notes.append(f"指定了专家角色：{expert}")

    if style:
        parts.append(f"用「{style}」风格回答")
        notes.append(f"指定了风格：{style}")

    optimized = "。".join(p.rstrip("。") for p in parts if p.strip())
    return {
        "ok": True,
        "content": {
            "original": original,
            "optimized": optimized,
            "notes": notes,
            "critique": [{"id": p.id, "advice": p.advice} for p in problems],
        },
    }
