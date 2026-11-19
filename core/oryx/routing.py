"""多模型运行时路由：按任务复杂度 / 成本 / 网络条件自动选 provider。

诊断记录（维度 7，2 分）：``cost_hint`` 只是给人看的标签，
``loop.py`` 每轮固定用 ``config.provider_config()`` 返回的同一个 provider，
系统没有复杂度路由、没有成本预算、没有网络降级、没有失败切换。

这一层是**执行期策略**，不改用户手动切换的配置语义：
- 默认关闭（``EnableModelRouting``），关着的时候行为与以前逐字节一致；
- 只从 ``config.providers`` 里**真实存在**的条目里挑，绝不编造模型；
- 选择结果发 ``model.routed`` 事件，界面能看到「这一轮换了哪个模型、为什么」；
- 任何异常都退回当前 provider，不让路由本身变成新的失败点。

评分是确定性的启发式（不是再调一次模型）：复杂任务偏大窗口/云端，
简单任务偏本地/免费，云端报错时降级到本地。
"""

from __future__ import annotations

from typing import Any

COMPLEX_KEYWORDS = (
    "并行", "分别", "多个", "整仓", "批量", "全量", "巡检", "分诊", "交叉",
    "重构", "架构", "设计", "实现", "优化", "分析", "深入", "详细", "调研",
    "全流程", "逐个", "每个", "代码", "方案",
)
SIMPLE_KEYWORDS = ("你好", "几点", "是什么", "查一下", "打开", "关闭", "多少钱", "翻译")
NETWORK_ERROR_HINTS = ("timeout", "timed out", "超时", "连接", "网络", "connect",
                       "unreachable", "refused", "dns", "offline")
LOCAL_HOST_HINTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _is_local(spec: dict[str, Any]) -> bool:
    kind = str(spec.get("type") or "").lower()
    if kind in ("ollama", "local", "lmstudio", "llama.cpp"):
        return True
    url = str(spec.get("base_url") or spec.get("url") or "").lower()
    return any(hint in url for hint in LOCAL_HOST_HINTS)


def _complexity(goal: str) -> str:
    text = str(goal or "")
    score = 0
    if len(text) > 120:
        score += 2
    if len(text) > 300:
        score += 2
    score += sum(1 for kw in COMPLEX_KEYWORDS if kw in text)
    if any(kw in text for kw in SIMPLE_KEYWORDS) and len(text) < 30:
        score -= 3
    if score >= 4:
        return "high"
    if score <= 0:
        return "low"
    return "medium"


def _routing_enabled(config: Any) -> bool:
    try:
        from .features import FeatureFlags

        return FeatureFlags.load(config.features_path()).is_enabled("EnableModelRouting")
    except Exception:
        return False


def route(
    config: Any,
    goal: str,
    *,
    tools_count: int = 0,
    last_error: str = "",
) -> dict[str, Any]:
    """选本轮该用哪个 provider。

    返回 ``{"name", "switched", "reason", "complexity", "candidates"}``。
    ``switched=False`` 时调用方应继续用 ``config.provider_config()``。
    """
    try:
        current = str(config.active_provider_name or "")
    except Exception:
        current = ""
    base = {"name": current, "switched": False, "reason": "", "complexity": "",
            "candidates": []}
    if not _routing_enabled(config):
        base["reason"] = "运行时路由未开启（EnableModelRouting）"
        return base

    providers = (getattr(config, "data", {}) or {}).get("providers") or {}
    candidates = [(str(n), s) for n, s in providers.items() if isinstance(s, dict)]
    base["candidates"] = [n for n, _ in candidates]
    if len(candidates) < 2:
        base["reason"] = "只配置了一个模型，没有可路由的候选"
        return base
    if not current or current not in dict(candidates):
        base["reason"] = "当前模型不在候选清单里，保持原配置"
        return base

    complexity = _complexity(goal)
    network_down = any(h in str(last_error or "").lower() for h in NETWORK_ERROR_HINTS)
    scored: list[tuple[float, str, str]] = []
    for name, spec in candidates:
        local = _is_local(spec)
        try:
            window = int(spec.get("context_window") or 0)
        except (TypeError, ValueError):
            window = 0
        cost = str(spec.get("cost_hint") or "")
        score = 0.0
        if complexity == "high":
            score += min(window / 8000.0, 6.0)
            if not local:
                score += 2.0
            if tools_count > 60:
                score += 0.5
        elif complexity == "low":
            if local:
                score += 4.0
            if "免费" in cost or "本地" in cost:
                score += 2.0
            if window and window <= 8000:
                score += 0.5
        else:
            if local:
                score += 1.0
            score += min(window / 16000.0, 3.0)
        if network_down and not local:
            score -= 10.0
        if name == current:
            score += 0.5  # 粘性：同分时别无意义来回跳
        reason = ("本地模型" if local else "云端模型") + (
            f"，窗口 {window}" if window else "，窗口未声明"
        )
        scored.append((score, name, reason))

    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_name, best_reason = scored[0]
    if best_name == current:
        base.update({
            "complexity": complexity,
            "reason": f"按{complexity}复杂度评估后仍沿用当前模型（{best_reason}）",
        })
        return base
    base.update({
        "name": best_name,
        "switched": True,
        "complexity": complexity,
        "reason": (
            f"任务复杂度={complexity}"
            + ("；检测到网络类失败，优先本地模型" if network_down else "")
            + f"；选择 {best_reason}（评分 {best_score:.1f}）"
        ),
    })
    return base
