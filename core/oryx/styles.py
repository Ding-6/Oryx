"""回答风格预设。

只影响"怎么说"，不影响"做什么"——风格片段追加在系统提示末尾，
不改变工具权限，也不改变安全策略。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    name: str
    label: str
    prompt: str


STYLES: dict[str, Style] = {
    "professional": Style(
        name="professional",
        label="专业",
        prompt=(
            "表达风格：专业克制。先给结论，再给依据。用准确的术语，但不堆砌名词。"
            "不确定的地方明确说不确定，不夸大、不用感叹号。"
        ),
    ),
    "efficient": Style(
        name="efficient",
        label="高效",
        prompt=(
            "表达风格：极简直给。能一句话说完就别说两句。去掉寒暄、铺垫和重复。"
            "多用短句、列表、表格，少用形容词。信息密度优先。"
        ),
    ),
    "friendly": Style(
        name="friendly",
        label="亲切",
        prompt=(
            "表达风格：温和耐心。像坐在旁边的同事，而不是念说明书。"
            "术语出现时顺带解释一句。可以有温度，但不啰嗦、不谄媚。"
        ),
    ),
    "straightforward": Style(
        name="straightforward",
        label="直白",
        prompt=(
            "表达风格：直来直去。有问题直接指出，不绕弯子。"
            "不同意就说明为什么，而不是和稀泥。语气可以硬，但不能刻薄。"
        ),
    ),
    "creative": Style(
        name="creative",
        label="创意",
        prompt=(
            "表达风格：有想象力。除了最直接的方案，主动给出 1-2 个不同角度的思路，"
            "并说明各自的取舍。但落地的部分必须具体、可执行，不能停在概念上。"
        ),
    ),
    "socratic": Style(
        name="socratic",
        label="引导",
        prompt=(
            "表达风格：启发式。在给出答案前，先点出关键前提或容易忽略的假设；"
            "必要时用一个问题帮用户想清楚目标。但用户明确要答案时，直接把答案给出来。"
        ),
    ),
}

DEFAULT_STYLE = "professional"


def get_style(name: str | None) -> Style:
    return STYLES.get((name or DEFAULT_STYLE).lower(), STYLES[DEFAULT_STYLE])


def catalog() -> list[dict[str, str]]:
    return [{"name": s.name, "label": s.label} for s in STYLES.values()]
