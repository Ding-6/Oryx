"""子代理输出去毒：拦住"伪装成系统指令"的注入内容。

子代理会读到网页、代码注释、文件内容这些东西。恶意内容可以伪装成
系统提示（`<system-reminder>`）或对话轮次（`Human:` / `Assistant:`），
试图劫持主 Agent 的判断。

处理方式是**改写而不是删除**：
- 仿冒的 `<system-reminder>` → 反斜杠转义的 `<\\system-reminder>`
- 行首的 `Human:` / `Assistant:` → 加反斜杠转义
- 命中时在报告头部加一行标记，让模型和用户都知道这里被动过手脚

删除内容会破坏正常输出（比如子代理引用了文档里的一段话），
所以宁可改写。改写后的内容仍然进主上下文，但伪装标签失效了。

对齐 WorkBuddy 的 subagent output sanitization 做法。
"""

from __future__ import annotations

import re

# 伪装成系统标签的片段。真实系统提示由系统注入，不该出现在子代理输出里。
_FAKE_SYSTEM_TAG = re.compile(r"<(\s*/?\s*(system-reminder|system|important|systemMessage)\s*>)", re.IGNORECASE)

# 行首伪装对话轮次。允许前导空白和 markdown 引用符号。
_FAKE_TURN = re.compile(r"^(\s*(?:>\s*)?)(Human|Assistant|User|System)\s*:", re.MULTILINE)

# 头部标记
MARKER = "[harness: subagent output sanitized]"


def sanitize(text: str) -> tuple[str, bool]:
    """清洗子代理输出。

    返回 (清洗后的文本, 是否命中过)。
    命中时会在头部插入 MARKER 行。
    """
    if not text:
        return text, False

    changed = False
    out = text

    # 1. 系统标签：<system-reminder> → <\system-reminder>
    def _tag(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return "<\\" + match.group(1)

    out = _FAKE_SYSTEM_TAG.sub(_tag, out)

    # 2. 行首对话轮次：Human: → \Human:
    def _turn(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group(1)}\\{match.group(2)}:"

    out = _FAKE_TURN.sub(_turn, out)

    if changed:
        out = f"{MARKER}\n{out}"
    return out, changed
