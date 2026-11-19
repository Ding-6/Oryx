"""系统提示词的加载与拼装。

为什么把提示词挪到 md 文件
--------------------------
以前主控系统提示是 `loop.py` 里一个 5950 字符的三引号常量。
问题不是长，是**改一句提示词要翻 Python 代码** —— 而且改坏了编译期才发现，
跑起来才知道少了一句。

挪到 md 之后：
  · 改提示词不用碰 Python，编辑器直接改；
  · 段落边界清晰（每个文件一个语义段），不用在 175 行里找位置；
  · 能在版本控制里 diff 出「改了哪句提示」。

三条硬约束（写进代码，也写进测试）
----------------------------------
1. **拼装结果必须与拆分前逐字节相同**。
   提示词改动会影响模型行为 —— 哪怕只差一个空格都可能让输出变样。
   所以有基准快照 + 逐字节对比的测试。

2. **占位符缺失要报错**，不能静默把 `{agents}` 留给模型看 ——
   模型会把字面量当内容读，是很难查的行为漂移。

3. **md 文件缺失要报错**，不能降级成空字符串 ——
   少一段提示词同样会让行为悄悄变样。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = PROMPTS_DIR / "system"

# 段落文件的固定顺序 —— 拼装顺序必须稳定，否则提示词会随文件名字典序漂移
SECTION_ORDER = (
    "00-opening",
    "01-closed-loop",
    "02-routing",
    "03-discipline",
    "04-safety",
    "05-output",
    "06-memory",
    "07-failure",
    "08-self-check",
    "09-tools",
    "10-multi-agent",
    "11-workflow",
    "12-security-rules",
    "13-skills",
)

# 占位符语法：{name}。只认单词字符，避开 JSON 片段那种 { "a": 1 }
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class PromptError(RuntimeError):
    """提示词加载或拼装失败。

    单独一个异常类型，是为了调用方能只捕获「提示词有问题」这一类，
    而不是把别的异常也一起吞掉。
    """


def section_path(name: str) -> Path:
    """某个段落文件的位置。"""
    return SYSTEM_DIR / f"{name}.md"


@lru_cache(maxsize=None)
def _read_section(name: str) -> str:
    """读一个段落文件的内容（带缓存）。

    缓存的理由：系统提示在每一轮对话都要拼一次，
    而文件在进程生命周期内不会变 —— 每次读盘是纯浪费。
    缓存后读取只发生一次。
    """
    path = section_path(name)
    if not path.exists():
        raise PromptError(
            f"提示词段落缺失：{path}\n"
            f"可用段落：{', '.join(SECTION_ORDER)}\n"
            f"缺任何一段都会让系统提示悄悄变短 —— 所以这里直接报错，不降级。"
        )
    try:
        return path.read_text("utf-8")
    except OSError as exc:
        raise PromptError(f"读不了提示词段落 {path}：{exc}") from None


def section_names() -> tuple[str, ...]:
    """全部段落名（固定顺序）。"""
    return SECTION_ORDER


def raw_sections() -> dict[str, str]:
    """全部段落的原始内容（未做变量替换）。"""
    return {name: _read_section(name) for name in SECTION_ORDER}


def find_placeholders(text: str) -> set[str]:
    """找出文本里用到的占位符。"""
    return set(_PLACEHOLDER_RE.findall(text))


def render(text: str, variables: dict[str, str]) -> str:
    """做变量替换。

    **只替换给定的变量**，其余占位符原样保留 —— 这样
    「拼装后再检查还有没有没替换的占位符」这个校验才有意义
    （见 `load_system_prompt` 的检查）。
    """
    out = text
    for key, value in variables.items():
        out = out.replace("{" + key + "}", value)
    return out


_LEGACY: str | None = None


def _legacy_prompt() -> str:
    """对照用：直接返回基准快照（等价于外置前的内联常量）。

    **这是诊断开关，不是生产路径。**
    设 `ORYX_PROMPT_LEGACY=1` 后，loader 会绕过 md 文件、
    直接返回拆分前那份常量的快照 —— 用来做 A/B 对照：
    「如果把提示词换回内联常量，某个偶发问题还会不会发生？」

    事故背景：提示词外置后，`test_context_resume` 偶发失败过一次
    （报「历史摘要不在 system 里」）。要判断是不是外置引起的，
    就得能随时切回外置前的形态 —— 这个开关提供了这个能力。
    （后来穷举 768 种组合证明「事件发了」与「有摘要」逻辑上必然同时成立，
    判定该失败与外置无关；但开关留着，下次有类似疑问能立刻验证。）

    基准快照是**真值来源**：它就是拆分前那份常量的原样保存。
    """
    global _LEGACY
    if _LEGACY is None:
        p = PROMPTS_DIR.parents[1] / "tests" / "fixtures" / "system_prompt_baseline.txt"
        _LEGACY = p.read_text("utf-8")
    return _LEGACY


def raw_system_prompt() -> str:
    """拼装完整的主控系统提示，**保留占位符原样**。

    用途：模块级常量 `loop.SYSTEM_PROMPT` 用它 ——
    那个常量保留 `{agents}` / `{skills}` 占位符，
    实际发给模型前才替换（运行时才知道有哪些 Agent 与技能）。

    保留占位符是有意的：`tests/test_prompt_contract.py` 断言的是
    「提示词里必须有某段某句」，用带占位符的版本才能直接查。
    """
    import os
    if os.environ.get("ORYX_PROMPT_LEGACY") == "1":
        return _legacy_prompt()
    parts = [_read_section(name).rstrip("\n") for name in SECTION_ORDER]
    return "\n\n".join(parts) + "\n"


def load_system_prompt(**variables: str) -> str:
    """拼装完整的主控系统提示，并替换占位符。

    参数
    ----
    **variables
        占位符的值。当前系统提示用到两个：
        `agents`（可用专职 Agent 清单）、`skills`（技能目录）。

    少传变量会**报错**而不是留下占位符 ——
    占位符原样送到模型面前，模型会把字面量当内容读，
    是很难查的行为漂移。

    拼装规则
    --------
    段落之间用**一个空行**（两个换行）分隔 —— 与拆分前的
    三引号字面量逐字节一致。
    """
    parts: list[str] = []
    for name in SECTION_ORDER:
        raw = _read_section(name)
        rendered = render(raw, variables)
        leftover = find_placeholders(rendered)
        if leftover:
            raise PromptError(
                f"提示词段落 {name}.md 里有未替换的占位符：{sorted(leftover)}\n"
                f"调用方需要提供这些变量。占位符原样送到模型面前会让模型"
                f"把字面量当内容读 —— 是很难查的行为漂移。"
            )
        parts.append(rendered.rstrip("\n"))

    return "\n\n".join(parts) + "\n"


def cache_info():
    """缓存统计（测试用：确认重复调用没有反复读盘）。"""
    return _read_section.cache_info()
