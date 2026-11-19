"""交互模式：决定"这一轮允许 Agent 做什么"，以及它在提示词里的行为约定。

借鉴自 WorkBuddy 的多模式设计，但落到我们自己的风险分级上：

    ask       只读。可以用 L0 工具查证，任何写操作一律拦下并提示切换。
    plan      只读，且只产出方案。适合"先别动手，告诉我你打算怎么做"。
    craft     全权限（默认）。该确认的照样确认。
    bypass    跳过权限检查（开发者模式）。可以被显式关掉。
    dontAsk   把所有"问用户"变成"直接拒绝"。给无人值守场景用。
    auto      由分类器代替用户判断 —— fail-closed，判不准就拒。
    delegate  只调度不执行：主 Agent 只做协调，动手交给子 Agent。

模式是"能力闸门"，不是"提示词人设"——闸门必须由代码强制执行，
提示词只是让模型提前知道边界，避免它反复撞墙。

安全次序（对齐 WorkBuddy 的 9 Phase 权限链）：
    deny 规则 → 模式基线 → 兜底
也就是说 bypass 只能跳过"确认"，**永远绕不过 deny 规则**——
禁止操作在任何模式下都拦得住。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .policy import Risk


class InteractionMode(str, Enum):
    ASK = "ask"
    PLAN = "plan"
    CRAFT = "craft"
    BYPASS = "bypass"
    DONTASK = "dontAsk"
    AUTO = "auto"
    DELEGATE = "delegate"

    @property
    def label(self) -> str:
        return {
            "ask": "问答",
            "plan": "规划",
            "craft": "执行",
            "bypass": "开发者",
            "dontAsk": "无人值守",
            "auto": "自动判定",
            "delegate": "委派",
        }[self.value]


@dataclass(frozen=True)
class ModePolicy:
    name: str
    max_risk: Risk              # 允许的最高风险等级
    allow_write: bool
    prompt: str                 # 追加到系统提示的行为约定
    # 是否跳过 L2 的用户确认。deny 规则不受影响。
    skip_approval: bool = False
    # 遇到需要确认的操作时：True=直接拒绝，False=弹窗等用户
    deny_on_ask: bool = False
    # 是否由分类器代替用户判断
    use_classifier: bool = False
    # 委派模式下只保留协调类工具
    coordination_only: bool = False

    def allows(self, risk: Risk) -> bool:
        """该模式是否允许这个风险等级。"""
        return risk <= self.max_risk

    @property
    def label(self) -> str:
        """中文名，给界面与提示用。"""
        return InteractionMode(self.name).label


MODE_POLICIES: dict[str, ModePolicy] = {
    "ask": ModePolicy(
        name="ask",
        max_risk=Risk.L0_READ,
        allow_write=False,
        prompt=(
            "当前是【问答模式】。你只能使用只读工具（查看系统信息、检索文件、读取文件、"
            "网页只读操作）。\n"
            "- 不要尝试写入、删除、执行命令或修改任何东西。\n"
            "- 遇到需要动手的需求，先给出结论和步骤，然后明确告诉用户："
            "切换到「执行」模式我就来做。\n"
            "- 回答要让用户看懂，而不是急着完成。"
        ),
    ),
    "plan": ModePolicy(
        name="plan",
        max_risk=Risk.L0_READ,
        allow_write=False,
        prompt=(
            "当前是【规划模式】。你只能调研，不能动手。\n"
            "- 先用只读工具把情况弄清楚（文件在哪、结构如何、依赖什么）。\n"
            "- 然后输出一份可执行方案：分几步、每步做什么、动到哪些文件、有什么风险。\n"
            "- 方案末尾问一句「按这个执行吗」，不要自己开始。"
        ),
    ),
    "craft": ModePolicy(
        name="craft",
        max_risk=Risk.L3_FORBIDDEN,  # 具体拦截仍由 PolicyEngine 负责
        allow_write=True,
        prompt=(
            "当前是【执行模式】。你可以调用全部工具把事情做完。\n"
            "- 破坏性动作会由系统弹确认，你不需要自己再问一遍，但要把要做什么说清楚。\n"
            "- 多步任务请用 task_create / task_update 维护清单，让用户看到进度。"
        ),
    ),
    "bypass": ModePolicy(
        name="bypass",
        max_risk=Risk.L3_FORBIDDEN,
        allow_write=True,
        skip_approval=True,
        prompt=(
            "当前是【开发者模式】。破坏性操作不会再弹确认，直接执行。\n"
            "- 仍受禁止规则约束：支付、凭据、受保护文件这些永远做不了。\n"
            "- 动手前用一句话说明你要改什么。"
        ),
    ),
    "dontAsk": ModePolicy(
        name="dontAsk",
        max_risk=Risk.L2_DESTRUCTIVE,
        allow_write=True,
        deny_on_ask=True,
        prompt=(
            "当前是【无人值守模式】。没有人能点确认，所以需要确认的操作一律拒绝。\n"
            "- 只做不需要确认的事：读文件、查资料、跑只读命令。\n"
            "- 需要确认的操作不要反复尝试，换个不需要确认的做法，或者在最后说明卡在哪里。"
        ),
    ),
    "auto": ModePolicy(
        name="auto",
        max_risk=Risk.L3_FORBIDDEN,
        allow_write=True,
        use_classifier=True,
        prompt=(
            "当前是【自动判定模式】。需不需要确认由系统分类器判断，你不用等用户点。\n"
            "- 分类器是 fail-closed 的：判不准就照常弹确认卡，绝不会替你猜。\n"
            "- 有检查点快照兜底的动作（写/删/移文件、生成文档）会免打扰直接执行。\n"
            "- 涉及凭据文件、远程控制这类需要人拍板的事会被直接拒绝 —— "
            "被拒就换做法或如实告诉用户需要本人处理，不要重试。\n"
            "- 破坏性动作仍会在最终报告里说明。"
        ),
    ),
    "delegate": ModePolicy(
        name="delegate",
        max_risk=Risk.L0_READ,
        allow_write=False,
        coordination_only=True,
        prompt=(
            "当前是【委派模式】。你不亲自执行，只负责协调。\n"
            "- 用 spawn_agent 把具体工作派给子 Agent，用 team_* 让团队成员配合。\n"
            "- 你自己只做调研、拆解、汇总和汇报。\n"
            "- 动手的事交给子 Agent，你负责把结果讲清楚。"
        ),
    ),
}

DEFAULT_MODE = "craft"

# 委派模式下允许使用的工具 —— 只有协调类，没有执行类。
DELEGATE_TOOLS = frozenset({
    "spawn_agent", "list_agents", "team_create", "team_delete", "team_list",
    "team_add_member", "team_send_message", "team_dispatch", "team_inbox",
    "tool_search", "defer_execute",
})


def get_policy(mode: str | None) -> ModePolicy:
    # 模式名大小写不敏感：前端传的 "dontAsk" 与配置里的 "dontask" 都要认
    key = (mode or DEFAULT_MODE).lower()
    if key in MODE_POLICIES:
        return MODE_POLICIES[key]
    for name, policy in MODE_POLICIES.items():
        if name.lower() == key:
            return policy
    return MODE_POLICIES[DEFAULT_MODE]




def policy_label(mode: str) -> str:
    return get_policy(mode).name


def allowed_tools(mode: str | None) -> frozenset[str] | None:
    """该模式下允许使用的工具名集合。None 表示不限制。"""
    policy = get_policy(mode)
    if policy.coordination_only:
        return DELEGATE_TOOLS
    return None


def mode_cycle() -> list[str]:
    """Shift+Tab 循环顺序。dontAsk 不在里面 —— 它需要显式设置。"""
    return ["ask", "craft", "bypass", "auto", "plan", "delegate"]
