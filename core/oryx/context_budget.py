"""上下文预算 —— 决定「这一轮把哪些扩展的正文塞进提示词」。

问题
----
扩展一多，全量注入就炸上下文。Oryx 现在的做法是把**全部**技能目录
（每个技能一行摘要）塞进系统提示 —— 技能少的时候没问题，
但一旦装了几十个技能，这一段就能吃掉几千 token，而且大部分用不上。

借鉴
----
ECC 的 context-profile（`context-profiles/lean@1.json`）：

    "selection": { "eager": [...], "required": [...], "remainder": "routed" },
    "budget": { "tokens": 8000, "mode": "blocking" }

三段语义：
  · **eager**   —— 永远常驻（数量必须很少，都是「怎么用这个平台」这类元技能）
  · **required**—— 命中即载（内容确定要用，但不用一直占着）
  · **routed**  —— 按触发词/语义检索加载（绝大多数技能该在这一档）

加预算
----
`budget.tokens` 是这一段允许占的 token 上限。
`mode`：
  · ``blocking``  —— 超了就**拒绝**（并报出是哪些扩展撑爆的）
  · ``report-only`` —— 超了只告警，照常注入

为什么不直接算 token
--------------------
项目里已有 `estimate_tokens`（core/oryx/context.py），用它保持口径一致。
但**粗估就行** —— 这里的目的是「别让提示词无限膨胀」，
不是精确计费。误差 20% 不影响决策。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import estimate_tokens

# 预算模式
MODE_BLOCKING = "blocking"
MODE_REPORT = "report-only"


@dataclass
class BudgetDecision:
    """一次预算裁剪的结果。"""

    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    used_tokens: int = 0
    limit_tokens: int = 0
    over_budget: bool = False
    mode: str = MODE_REPORT

    @property
    def ok(self) -> bool:
        """是否允许继续（blocking 模式下超预算就是 False）。"""
        return not (self.over_budget and self.mode == MODE_BLOCKING)

    def explain(self) -> str:
        """人能读的说明 —— 出问题时直接进日志/UI。"""
        if not self.over_budget:
            return f"上下文预算正常：{len(self.included)} 项 / {self.used_tokens} token（上限 {self.limit_tokens}）"
        msg = (
            f"上下文超预算：{self.used_tokens} token > 上限 {self.limit_tokens}，"
            f"已排除 {len(self.excluded)} 项"
        )
        if self.excluded:
            msg += "（" + "、".join(self.excluded[:5]) + "）"
        if self.mode == MODE_BLOCKING:
            msg += "。当前是 blocking 模式，应拒绝加载。"
        return msg


@dataclass
class ContextProfile:
    """一份上下文选择配置。

    对应 ECC 的 context-profile JSON：

        {
          "schemaVersion": 1,
          "id": "default@1",
          "selection": {"eager": [...], "required": [...], "remainder": "routed"},
          "budget": {"tokens": 8000, "mode": "report-only"}
        }
    """

    id: str = "default@1"
    eager: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    remainder: str = "routed"          # routed | all
    limit_tokens: int = 8000
    mode: str = MODE_REPORT

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextProfile":
        sel = data.get("selection") or {}
        bud = data.get("budget") or {}
        if not isinstance(sel, dict):
            sel = {}
        if not isinstance(bud, dict):
            bud = {}
        return cls(
            id=str(data.get("id") or "default@1"),
            eager=tuple(sel.get("eager") or ()),
            required=tuple(sel.get("required") or ()),
            remainder=str(sel.get("remainder") or "routed"),
            limit_tokens=int(bud.get("tokens") or 8000),
            mode=str(bud.get("mode") or MODE_REPORT),
        )

    @classmethod
    def load(cls, path: Path) -> "ContextProfile":
        try:
            data = json.loads(Path(path).read_text("utf-8"))
        except Exception:
            return cls()
        return cls.from_dict(data if isinstance(data, dict) else {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "id": self.id,
            "selection": {
                "eager": list(self.eager),
                "required": list(self.required),
                "remainder": self.remainder,
            },
            "budget": {"tokens": self.limit_tokens, "mode": self.mode},
        }


def select(
    records: list[Any],
    profile: ContextProfile,
    *,
    query: str = "",
    estimate: Any = None,
) -> BudgetDecision:
    """按 profile 选出一批扩展，并检查预算。

    参数
    ----
    records
        `registry.ExtensionRecord` 列表。
    profile
        选择与预算配置。
    query
        本轮用户输入 —— 用来做 routed 档的触发词匹配。
    estimate
        估算函数，默认用 `context.estimate_tokens`。

    选择顺序（**优先级从高到低，先满足高优先级的**）：
      1. eager    —— 永远包含，哪怕超预算也要（它们是元技能）
      2. required —— 命中 query 的必须包含
      3. routed   —— 触发词命中的其余
      4. remainder=all 时，未命中的一律不收（避免全量注入）
    """
    from .registry import route

    est = estimate or estimate_tokens
    decision = BudgetDecision(limit_tokens=profile.limit_tokens, mode=profile.mode)

    by_id = {r.id: r for r in records}
    picked: dict[str, Any] = {}

    # 1) eager —— 无条件收
    for ext_id in profile.eager:
        rec = by_id.get(ext_id)
        if rec is not None:
            picked[ext_id] = rec

    # 2) required + routed —— 按 query 路由
    routed = route(records, query) if query else None
    matched_ids = [r.id for r in (routed.matched if routed else [])]

    for ext_id in profile.required:
        rec = by_id.get(ext_id)
        if rec is not None:
            picked[ext_id] = rec

    if profile.remainder == "all":
        # 全量模式：所有扩展都算候选（会撑爆预算，由预算层负责砍）
        for rec in records:
            picked.setdefault(rec.id, rec)
    else:
        for ext_id in matched_ids:
            rec = by_id.get(ext_id)
            if rec is not None and rec.id not in picked:
                picked[ext_id] = rec

    # 3) 预算核算 —— 按优先级砍：先砍 routed，再砍 required，eager 最后砍
    def cost(rec: Any) -> int:
        text = f"{rec.id}: {rec.description}"
        return int(est(text))

    eager_set = set(profile.eager)
    required_set = set(profile.required)

    # 按优先级排队：eager 最先占位（且**不受预算限制** —— 元技能不能少），
    # 然后 required，最后 routed。
    ordered = (
        [r for r in picked.values() if r.id in eager_set]
        + [r for r in picked.values() if r.id in required_set and r.id not in eager_set]
        + [r for r in picked.values() if r.id not in eager_set and r.id not in required_set]
    )

    used = 0
    overflow: list[str] = []
    for rec in ordered:
        c = cost(rec)
        if rec.id in eager_set:
            # eager 无条件保留
            used += c
            decision.included.append(rec.id)
            continue
        if used + c > profile.limit_tokens:
            overflow.append(rec.id)
            continue
        used += c
        decision.included.append(rec.id)

    decision.used_tokens = used
    decision.over_budget = used > profile.limit_tokens
    decision.excluded = sorted(overflow)
    return decision
