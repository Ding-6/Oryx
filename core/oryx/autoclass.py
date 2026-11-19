"""自动判定模式（auto）的分类器。

为什么需要它：
``modes.py`` 里 auto 模式声明了 ``use_classifier=True``，但全项目**没有任何地方
读这个字段** —— 也就是说切到 auto 模式后，提示词告诉用户「由分类器替你决定」，
实际行为却和 craft 一模一样：L2 照样弹确认卡。用户以为开了自动，其实没开。

这个模块把那句话变成真的。

判定分三档（对齐 WorkBuddy 的 auto 模式：fail-closed，判不准就拒）：

    allow  免打扰，直接执行
    ask    照常走确认卡（有人在场时仍会弹）
    deny   直接拒绝

**fail-closed 是硬要求**：任何判定不出来的情况一律落到 ``ask``，
绝不能落到 ``allow``。判不准就多问一句，比猜错放行安全得多。

三类动作的归类依据：

1. **可回滚 → allow**
   项目已经有检查点回滚机制（``checkpoint.py``），它覆盖
   ``fs_write`` / ``fs_delete`` / ``fs_move`` 以及三种文档生成工具
   （见 ``loop.py`` 的 ``_snapshot_before_write``）。
   也就是说这些动作**做错了能撤回来**，不必每次都打断用户。
   shell 造成的改动不在覆盖范围内 —— 所以 shell 不享受这个豁免。

2. **要凭据 / 越权 → deny**
   凭据类文件（.env / .ssh / .aws / .kube）走的是「需要你明确授权」的通道。
   自动判定的前提是「没人在旁边」，那就没人能授权 —— 直接拒，
   并让 Agent 告诉用户「这件事需要你本人来做」。

3. **其余破坏性动作 → ask**
   shell、终端、系统设置、远程控制这类：要么不可回滚，要么影响面超出工作区。
   有人在场就照常弹确认卡确认；无人值守时由上层（dontAsk）接手拒绝。
"""

from __future__ import annotations

from typing import Any

# 三档判定结果
ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# 可以靠检查点回滚救回来的工具。
#
# 判据不是「这个工具危险不危险」，而是「**改错了能不能撤回来**」。
# 检查点只快照了这些工具改过的文件（loop.py 的 _snapshot_before_write），
# 所以只有它们能享受「免打扰」豁免 —— shell 能改的东西太杂，
# 快照不下来，给它豁免等于给了一个假的安全感。
RECOVERABLE_TOOLS = frozenset({
    "fs_write",
    "fs_delete",
    "fs_move",
    "docx_write",
    "xlsx_write",
    "pptx_write",
    # 三种新交付格式同样只产出新文件、有检查点快照兜底
    "html_write",
    "pdf_write",
    "image_write",
})

# 涉及凭据、远程控制、系统级改动的工具 —— 自动模式下直接拒。
#
# 这几个的共同点：**需要一个人在场做决定**。
# 自动判定的场景恰恰是没人盯着，所以宁可拒，也不能替他做主。
HUMAN_ONLY_TOOLS = frozenset({
    "remote_start",
    "provider_add",
    "provider_use",
    "provider_delete",
    "credential_store",
})


def classify(
    tool_name: str,
    args: dict[str, Any] | None = None,
    risk: int = 0,
    credential_paths: list[str] | None = None,
) -> tuple[str, str]:
    """判一个工具调用该放行、该确认还是该拒绝。

    返回 ``(判定, 理由)``。理由会写进审计与给模型看的提示里。

    **任何拿不准的情况都返回 ask** —— fail-closed。
    """
    args = args or {}
    credential_paths = credential_paths or []

    # 1. 凭据类路径：需要人明确授权，自动判定模式下没人能授权 → 拒
    if credential_paths:
        return DENY, (
            "该操作涉及凭据类文件，需要用户本人明确授权；"
            "自动判定模式下没有人能授权，已拒绝"
        )

    # 2. 需要人拍板的工具 → 拒
    if tool_name in HUMAN_ONLY_TOOLS:
        return DENY, f"{tool_name} 需要用户本人确认，自动判定模式下已拒绝"

    # 3. 可回滚的动作 → 放行
    if tool_name in RECOVERABLE_TOOLS:
        return ALLOW, "该动作有检查点快照兜底，做错了可以回退"

    # 4. 其余 L2 破坏性动作 → 照常确认
    if int(risk) >= 2:
        return ASK, "该操作不可回滚，按常规走确认流程"

    # 5. 只读与可逆写入 → 放行
    return ALLOW, "只读或低风险操作"
