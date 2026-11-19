r"""工具失败分类：把「失败了」变成「这类失败该怎么办」。

为什么要有这一层：
提示词里写了「先分类再决策，禁止笼统重试」，但模型收到的是
`[失败] 目标不存在：D:\xxx\a.txt` 这样一句话 —— 它得自己猜这是
E1（环境不可达）还是 E5（依赖缺失），猜错了就会用错策略
（比如对「文件不存在」反复重试，或者把「权限不足」当环境问题去重连）。

所以在**工具结果出口**做一次分类，把 E1-E5 的判据和对应动作
一起交给模型。分类是**确定性规则**（看错误文本的特征），
不是让模型自己判断 —— 否则同一个错误两次调用可能给出两个分类。

五类与对应动作（对齐主控提示词的第七条）：
    E1 环境不可达 —— 报告对象 + 恢复步骤，不重试
    E2 权限不足   —— 说明需要什么权限、由谁授予，禁止绕过
    E3 工具不支持 —— 换同级工具；没有替代就升级层级
    E4 理解偏差   —— 停下来复述「我理解你要的是 X」
    E5 依赖缺失   —— 能补的自己补；要凭据的向用户索取
"""

from __future__ import annotations

import re
from typing import Any

# 分类代码
E1_UNREACHABLE = "E1"
E2_PERMISSION = "E2"
E3_UNSUPPORTED = "E3"
E4_MISMATCH = "E4"
E5_MISSING = "E5"
E0_UNKNOWN = "E0"

# 每一类给模型的建议动作。
#
# 写给模型看，所以要说「该做什么」而不是「这是什么」。
ADVICE: dict[str, str] = {
    E1_UNREACHABLE: (
        "环境不可达：报告具体不可达的对象并给出恢复步骤，不要重试。"
    ),
    E2_PERMISSION: (
        "权限不足：说明需要什么权限、由谁授予，不要尝试绕过。"
    ),
    E3_UNSUPPORTED: (
        "工具不支持：换用同级的其它工具；没有替代就升级到更通用的做法，"
        "或把这件事交还用户。"
    ),
    E4_MISMATCH: (
        "结果与预期不符：停下来用一句话复述「我理解你要的是 X」，"
        "确认后再继续，不要接着往下做。"
    ),
    E5_MISSING: (
        "依赖缺失：能自己补齐的（建目录、装可选项）立即补齐；"
        "需要凭据或用户私有文件的，向用户索取。"
    ),
    E0_UNKNOWN: (
        "失败原因不明：先看报错原文定位；同一工具同一子目标最多再试 1 次，"
        "仍失败就换策略。"
    ),
}

# 分类判据。按顺序匹配 —— 命中即停。
#
# 用「特征词」而不是精确匹配错误消息：工具的错误文案五花八门
#（「没有权限读取」/「拒绝访问」/「Access is denied」），
# 但指向的是同一类问题。
_RULES: list[tuple[str, re.Pattern[str]]] = [
    # E2 权限：放在 E5 之前 —— 「权限不足」和「文件不存在」措辞有时很像
    (E2_PERMISSION, re.compile(
        r"(拒绝访问|access\s+(is\s+)?denied|permission\s+(was\s+)?denied|"
        r"没有权限|权限不足|unauthorized|forbidden|拒绝连接|"
        r"operation\s+not\s+permitted|requires?\s+elevation|需要提权|administrator)",
        re.IGNORECASE,
    )),
    # E3 工具不支持
    (E3_UNSUPPORTED, re.compile(
        r"(未知工具|参数不合法|不支持|unsupported|not\s+implemented|"
        r"invalid\s+argument|参数非法|没有这个能力|unknown\s+tool|"
        r"invalid\s+regular\s+expression|正则不合法|版本不匹配)",
        re.IGNORECASE,
    )),
    # E5 依赖缺失（缺文件 / 缺包 / 缺配置）
    (E5_MISSING, re.compile(
        r"(不存在|没有找到|未找到|not\s+found|no\s+such\s+file|"
        r"未安装|没有安装|not\s+installed|缺少|缺失|"
        r"module\s+not\s+found|no\s+module\s+named|import\s+error|"
        r"目标不存在|路径不存在|文件不存在)",
        re.IGNORECASE,
    )),
    # E1 环境不可达
    (E1_UNREACHABLE, re.compile(
        r"(连接失败|无法连接|连接超时|超时|timeout|timed\s+out|"
        r"connection\s+refused|could\s+not\s+resolve|"
        r"dns|name\s+resolution|网络不可用|host\s+is\s+unreachable|"
        r"服务未启动|device\s+offline)",
        re.IGNORECASE,
    )),
    # E4 理解偏差（结果与预期不符）
    (E4_MISMATCH, re.compile(
        r"(结果不符|与预期不符|不匹配|does\s+not\s+match|unexpected\s+result|"
        r"与要求不一致)",
        re.IGNORECASE,
    )),
]


def classify(message: str) -> str:
    """给一条错误消息分类，返回 E1-E5 之一（认不出来给 E0）。"""
    text = (message or "").strip()
    if not text:
        return E0_UNKNOWN
    for code, pattern in _RULES:
        if pattern.search(text):
            return code
    return E0_UNKNOWN


def annotate(result: dict[str, Any], tool_name: str = "") -> dict[str, Any]:
    """给失败的工具结果补上分类与建议动作。

    只动失败的结果 —— 成功的不用加，加了只是白占上下文。
    返回**新的**字典（不改原对象）：调用方要的是能直接进上下文的文本。
    """
    if not isinstance(result, dict) or result.get("ok"):
        return result
    content = result.get("content")
    text = content if isinstance(content, str) else str(content or "")
    code = classify(text)
    # 没有可分类的信息（错误消息本身是空的）就不加标签 —— 加了也是噪声
    if code == E0_UNKNOWN and not text.strip():
        return result
    advice = ADVICE.get(code, ADVICE[E0_UNKNOWN])
    lines = [f"[{code}] {text}"]
    if tool_name:
        lines.append(f"（工具：{tool_name}）")
    lines.append(f"建议：{advice}")
    out = dict(result)
    out["content"] = "\n".join(lines)
    out["error_class"] = code
    return out
