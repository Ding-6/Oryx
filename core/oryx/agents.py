"""子 Agent 定义。

    agents/<name>/AGENT.md
    ---
    name: 文档管理
    description: 把需求与改动记录到项目文档
    tools: doc_read, doc_requirement_add, doc_change_log, fs_list, fs_read
    model: inherit
    permissionMode: ask
    skills: file-search
    max_turns: 6
    ---
    正文是这个 Agent 的职责边界与工作方式。

工具白名单是硬约束：子 Agent 拿不到名单外的工具，即使模型硬调也会被拒。

frontmatter 字段（已实现的部分）：

    name            显示名
    description     一句话说明，主 Agent 派活时据此判断找谁
    tools           工具白名单（硬边界）
    max_turns       最多跑几轮
    icon/subtitle   界面上显示的图标与副标题
    kind            butler（管家）还是 agent（普通子 Agent）
    model           用哪个模型服务；inherit = 跟随主 Agent
    permissionMode  权限基线；比主 Agent 更保守才生效（收紧，不是放宽）
    skills          要注入的技能（SKILL.md 正文会进提示词）
    initialPrompt   每次派活都带的开场提示
    memory          跨会话记住的事，写进记忆库
    disallowedTools 额外禁用的工具（在白名单之上再收紧）

解析用真 YAML 而不是简易 key:value —— 列表、嵌套、布尔都需要。

**没有实现的字段不要写进来**：以前这里声称「对齐 WorkBuddy 的 14 个字段」，
其中 effort / background / mcpServers 解析了却从不被使用 ——
声明了却不生效，比不声明更糟。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# frontmatter 解析 —— **全项目唯一一份**（oryx/frontmatter.py）。
# 以前这里先试严格 YAML、失败才回退宽松 —— 现在统一走宽松解析器，
# 因为严格 YAML 会把「有 # 号」的描述**静默截断**（# 前有空格就是注释）。
from .frontmatter import parse_frontmatter
from typing import Any


# 嵌套深度上限：主会话是第 0 层
MAX_DEPTH = 5
# 每会话 spawn 预算（只对 Agent 工具路径计数）
MAX_SPAWN_PER_SESSION = 200


@dataclass
class AgentSpec:
    id: str
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    max_turns: int = 6
    body: str = ""
    path: Path | None = None
    # --- 以下为扩展字段（都真的生效）---
    model: str = "inherit"           # 用哪个模型服务
    permission_mode: str = "inherit"  # 权限基线（只收紧）
    skills: list[str] = field(default_factory=list)      # 注入的技能
    disallowed_tools: list[str] = field(default_factory=list)  # 额外禁用
    memory: str = ""                 # 专属记忆的 tag
    initial_prompt: str = ""         # 每次派活都带的开场提示
    # 注意：effort / background / mcp_servers 三个字段以前解析了却从不被使用
    # —— 声明了「支持」但实际不生效，比不支持更误导人，所以已经移除。
    # effort（推理强度）要各家模型各自的参数（Anthropic 是 thinking.budget_tokens、
    # OpenAI 是 reasoning_effort），支持面参差不齐，硬做反而会出错。
    # --- 管家身份（第十一批）---
    # 管家是「有身份的角色」，不只是个 id。
    # 图标与职责短语在界面上一眼能认出是哪个管家，
    # 不用读完描述才知道。
    icon: str = ""
    subtitle: str = ""
    # kind=butler 的角色会在「管家团队」页里成排展示，
    # 普通的子 Agent（比如 reviewer）只在编队时出现。
    kind: str = "agent"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "max_turns": self.max_turns,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "skills": self.skills,
            "disallowed_tools": self.disallowed_tools,
            "memory": self.memory,
            "initial_prompt": self.initial_prompt,
            "icon": self.icon,
            "subtitle": self.subtitle,
            "kind": self.kind,
        }


def _split_list(value: Any) -> list[str]:
    """把 tools/skills 字段拆成列表。

    支持三种写法：逗号分隔字符串、真列表、None。
    """
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [t.strip() for t in re.split(r"[,，\s]+", str(value)) if t.strip()]


class AgentLibrary:
    def __init__(self, root: Path, registry: Any = None):
        self.root = root
        # registry 是可选的：有它就能拿到「这个 Agent 被停用了」。
        # 没有它（老调用方）行为与改造前完全一致。
        self._registry = registry
        self.agents: dict[str, AgentSpec] = {}
        self.reload()

    def reload(self) -> None:
        self.agents.clear()
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*/AGENT.md")):
            try:
                # utf-8-sig：吃掉 BOM。带 BOM 的文件用 utf-8 读出来
                # 开头是 U+FEFF，frontmatter 正则匹配不上 ——
                # 结果是整个 Agent 被当成「没有 frontmatter 的纯正文」，
                # 名字、工具白名单全丢，而且不报任何错。
                text = path.read_text("utf-8-sig")
            except Exception:
                continue
            meta, body = parse_frontmatter(text)
            agent_id = path.parent.name
            # **停用的 Agent 不加载**。
            #
            # 以前这个库自己扫目录、从不问注册表 ——
            # 界面上点「停用」，注册表里确实没了，
            # 但这个库照常加载，Agent 照样能被派活。
            if self._registry is not None and self._registry.is_disabled(
                    agent_id, path.parent.name):
                continue
            try:
                max_turns = int(str(meta.get("max_turns", 6) or 6))
            except (TypeError, ValueError):
                max_turns = 6
            self.agents[agent_id] = AgentSpec(
                id=agent_id,
                name=str(meta.get("name") or agent_id),
                description=str(meta.get("description", "") or ""),
                tools=_split_list(meta.get("tools")),
                max_turns=max_turns,
                body=body.strip(),
                path=path,
                model=str(meta.get("model", "inherit") or "inherit"),
                permission_mode=str(meta.get("permissionMode", "inherit") or "inherit"),
                skills=_split_list(meta.get("skills")),
                disallowed_tools=_split_list(meta.get("disallowedTools")),
                memory=str(meta.get("memory", "") or ""),
                initial_prompt=str(meta.get("initialPrompt", "") or ""),
                icon=str(meta.get("icon", "") or ""),
                subtitle=str(meta.get("subtitle", "") or ""),
                kind=str(meta.get("kind", "agent") or "agent"),
            )

    def get(self, agent_id: str | None) -> AgentSpec | None:
        if not agent_id:
            return None
        return self.agents.get(agent_id)

    def catalog(self) -> list[dict]:
        return [a.to_dict() for a in self.agents.values()]

    def prompt_block(self) -> str:
        """渲染成系统提示词里的一段：有哪些专职 Agent、各自管什么。

        为什么要有这段：主 Agent 提示词要求「L1 能闭环就不许拆成 L3/L4 的碎步」，
        但模型**不知道**有哪些 L1 可用 —— 只能靠 spawn_agent 的描述里那句
        "目前可用：doc-manager"（写死的）。管家一多就漏。

        这里按 kind 排：管家（butler）是日常会用到的那几类，排前面；
        普通子 Agent（reviewer / researcher 之类）排在后面 —— 它们主要
        在编队时按需调用，不该在主上下文里抢注意力。
        """
        if not self.agents:
            return "（当前没有可用的专职 Agent）"
        ordered = sorted(
            self.agents.values(),
            key=lambda a: (0 if a.kind == "butler" else 1, a.id),
        )
        lines = []
        for spec in ordered:
            head = f"- {spec.id}（{spec.name}"
            if spec.subtitle:
                head += f" / {spec.subtitle}"
            head += "）"
            lines.append(f"{head}：{spec.description}")
            # 工具白名单是子 Agent 的硬边界，写出来主 Agent才知道
            # 派给它什么活是合适的（它拿不到名单外的工具）。
            if spec.tools:
                lines.append(f"  可用工具：{'、'.join(spec.tools)}")
        return "\n".join(lines)


class SpawnBudget:
    """每会话的子 Agent 生成预算。

    嵌套 spawn 共享同一份预算。/clear 之后由调用方 reset。
    """

    def __init__(self, limit: int = MAX_SPAWN_PER_SESSION):
        self.limit = limit
        self.used = 0

    def consume(self) -> tuple[bool, str]:
        if self.used >= self.limit:
            return False, f"本会话已派生 {self.used} 个子 Agent，达到上限 {self.limit}"
        self.used += 1
        return True, ""

    def reset(self) -> None:
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)
