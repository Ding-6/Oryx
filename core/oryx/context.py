"""长会话上下文预算：按 token 裁剪历史，而不是把几百轮全塞进窗口。

诊断报告里的原话：`loop.py` 取历史时只取 user/assistant、不截断，
长会话累积到几百轮就会撑爆上下文窗口。这一层把预算管起来。

设计取舍：
1. **确定性估算**，不调 tokenizer（各家 tokenizer 不同，且引入重依赖）：
   中文按 1 字 ≈ 1 token 保守估，ASCII 按 4 字符 ≈ 1 token。
   宁可多裁一点，也不能低估导致超窗口。
2. **永远保留最近 N 轮**，从最老的开始丢 —— 最近上下文对当前任务最有用。
3. 被丢掉的历史**生成一段摘要**注入系统提示，而不是无声消失。
   摘要只写「说过什么」，明确标注「细节已省略，不要据此编造数据」，
   防止模型把压缩过的片段当成完整事实。
4. 摘要放系统提示里，不插进消息序列 —— 不破坏 user/assistant 交替，
   也不会造成角色连续（OpenAI 兼容协议对此很敏感）。
"""

from __future__ import annotations

from typing import Any

DEFAULT_CONTEXT_BUDGET = 24_000
DEFAULT_KEEP_RECENT = 8
MIN_KEEP = 2
MAX_SUMMARY_CHARS = 1400


def estimate_tokens(text: str) -> int:
    """保守估算 token 数：中文 1 字 1 token，ASCII 4 字符 1 token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    ascii_chars = len(text) - cjk
    return cjk + (ascii_chars + 3) // 4


def resolve_budget(config: Any, provider_cfg: dict[str, Any] | None = None) -> int:
    """取本轮历史预算。

    优先级：用户显式配置 > provider 声明的窗口推导 > 默认值。
    留一半窗口给系统提示、工具结果和模型输出 —— 历史不能吃满整个窗口。
    """
    raw = (getattr(config, "data", {}) or {}).get("context_budget")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    spec = provider_cfg or {}
    window = int(spec.get("context_window") or 0)
    max_output = int(spec.get("max_output") or 4096)
    if window > max_output:
        return max(4000, (window - max_output) // 2)
    return DEFAULT_CONTEXT_BUDGET


def _summarize(dropped: list[dict[str, Any]]) -> str:
    if not dropped:
        return ""
    lines: list[str] = []
    for msg in dropped[-6:]:
        role = "用户" if msg.get("role") == "user" else "Oryx"
        snippet = " ".join(str(msg.get("content") or "").split())[:90]
        if snippet:
            lines.append(f"- {role}：{snippet}")
    head = (
        f"[历史摘要] 之前 {len(dropped)} 条对话因上下文预算被压缩，"
        "只保留要点；细节已省略，不要据此编造文件名、数字或结论，"
        "需要时重新查询。\n"
    )
    return (head + "\n".join(lines))[:MAX_SUMMARY_CHARS]


def trim_history(
    history: list[dict[str, Any]],
    budget_tokens: int,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[dict[str, Any]], str]:
    """按预算裁掉最老的历史，返回 (保留的消息, 摘要文本)。

    保证：
    - 最近 keep_recent 条**一定**保留（除非它们自己就超预算，则至少留 2 条）；
    - 保留段第一条尽量是 user，避免出现连续同角色；
    - 预算 <= 0 时原样返回（等于关闭裁剪，便于用户关掉这个功能）。
    """
    if not history or budget_tokens <= 0:
        return list(history), ""
    keep = max(MIN_KEEP, int(keep_recent))
    start = max(0, len(history) - keep)
    total = sum(estimate_tokens(str(m.get("content") or "")) for m in history[start:])

    # 最近一段自己就超预算：只留最后 MIN_KEEP 条，不再往前加
    if total > budget_tokens and len(history) - start > MIN_KEEP:
        start = len(history) - MIN_KEEP
        total = sum(estimate_tokens(str(m.get("content") or "")) for m in history[start:])

    # 还有余量就继续往前捞更老的消息
    while start > 0 and total + estimate_tokens(str(history[start - 1].get("content") or "")) <= budget_tokens:
        start -= 1
        total += estimate_tokens(str(history[start].get("content") or ""))

    kept = list(history[start:])
    # 保留段以 user 开头更稳妥（历史可能因为「重新生成」摘掉过 user）
    while len(kept) > MIN_KEEP and kept[0].get("role") != "user":
        kept.pop(0)
        start += 1
    return kept, _summarize(list(history[:start]))


def trim_report(history: list[dict[str, Any]], kept: list[dict[str, Any]]) -> dict[str, Any]:
    """给事件/体检用的裁剪报告。"""
    return {
        "before": len(history),
        "after": len(kept),
        "dropped": max(0, len(history) - len(kept)),
        "tokens_before": sum(estimate_tokens(str(m.get("content") or "")) for m in history),
        "tokens_after": sum(estimate_tokens(str(m.get("content") or "")) for m in kept),
    }
