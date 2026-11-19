"""特性开关（对齐 WorkBuddy 的 Disable* / 部署形态 product.*.json 做法）。

文件位置：%APPDATA%/Oryx/features.yaml
加载路径：先看 features.yaml，没有就用 DEFAULT_FEATURES。

每条开关遵循 WorkBuddy 的 Disable*-含义：
    - key 以 Disable 开头 → 真值表示关闭
    - key 以 Enable 开头  → 真值表示开启
    - 其余（命名空间型） → 真值表示使用该实现/平台

profile 用于切换部署形态：personal / team / selfhosted / cloud，
profile 把一组开关一次性打开。

**开关必须真的改变系统行为。**
本模块以前只提供「读出来给界面显示」，17 个开关在代码里零引用 ——
用户关掉「浏览器 Agent」，浏览器工具照旧可用。那是装饰，不是开关。

现在的做法：``FEATURE_GATES`` 把开关映射到具体的能力组，
``AgentRuntime`` 在注册工具时按它裁剪 —— 关掉的能力**连工具都不注册**，
模型看不到、也调不到。改开关立刻生效，不用重启。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_FEATURES: dict[str, bool] = {
    # 出厂预置（默认全开）
    "EnableOffice": True,
    "EnableOcr": True,
    "EnableBrowser": True,
    # 联网搜索：**默认关** —— 它要一个外部搜索服务的 key，
    # 而项目承诺纯本地可用。不能默认就依赖外网。
    "EnableWebSearch": False,
    "EnableIndex": True,
    "EnableRemote": True,
    "EnableAutomation": True,
    "EnableMemory": True,
    "EnableExperts": True,
    "EnableSkills": True,
    "EnableSubAgents": True,
    "EnableTerminal": True,
    "EnableDocs": True,
    "EnableSystemSettings": True,  # Marvis win_use_mcp toolkit 对齐
    # 通道/集成（默认关闭，需要时手动开）
    "DisableChannels": True,        # Disable* 语义：真 = 关
    "DisableA2A": True,            # 云端 A2A 代理
    "DisableWebSearch": True,      # 联网搜索（避免数据外发）
    "DisableImageGen": True,
    "DisableVideoGen": True,
    # 沙箱/安全增强
    "DisableSandbox": False,       # 命令沙箱已实现（sandbox.py），默认开
    # 产品形态开关
    "EnableFeatureFlags": True,
    # 运行时模型路由：默认**关闭**。开启后每轮会按复杂度/成本/网络重新选模型，
    # 属于改变执行行为的策略，用户要能明确关掉它。
    "EnableModelRouting": False,
    # 与 REA 的协作：开了之后 Oryx 会把 `rea` MCP server 自动拉起
    "EnableReaMcp": False,
}

# 部署形态预设：每个 profile 是一个覆盖规则，叠在 DEFAULT_FEATURES 上
PROFILES: dict[str, dict[str, bool]] = {
    "personal": {
        "DisableA2A": True,
        "DisableWebSearch": True,
        "DisableChannels": True,
        "DisableSandbox": False,
        "EnableReaMcp": False,
    },
    "team": {
        "DisableA2A": False,        # 团队内部允许 A2A
        "DisableWebSearch": False,
        "DisableChannels": False,   # Slack/Teams 等
        "DisableSandbox": False,
        "EnableReaMcp": True,       # 团队成员能调 REA
    },
    "selfhosted": {
        "DisableA2A": True,
        "DisableWebSearch": False,
        "DisableChannels": False,
        "DisableSandbox": False,    # 自托管有完整沙箱
        "EnableReaMcp": True,
    },
    "cloud": {
        "DisableA2A": False,
        "DisableWebSearch": False,
        "DisableChannels": False,
        "DisableSandbox": False,
        "EnableReaMcp": True,
    },
}


# ------------------------------------------------------------------ 能力组
#
# 每个能力组 = 一个开关 + 它管着哪些工具。
# 关掉开关 → 这组工具**根本不注册** → 模型看不见、调不到。
#
# 为什么按工具组切而不是按模块判断：模型只能通过工具来使用能力。
# 「关掉浏览器 Agent」的真实含义就是「别让模型碰浏览器」，
# 那最直接的实现是不给它 browser_* 工具。
#
# 空组 = 该能力由别处控制（不是工具级），在这里标注出来是为了让
# 「这个开关到底管什么」有唯一的事实来源。
FEATURE_GATES: dict[str, tuple[str, ...]] = {
    "EnableOffice": (
        "docx_write", "xlsx_write", "pptx_write", "xlsx_read",
        # 多模态交付：HTML 报告 / PDF / 图表图片（与 Office 三件套同一开关）
        "html_write", "pdf_write", "image_write",
    ),
    "EnableOcr": (
        "ocr_image",
    ),
    # 联网搜索：开关关掉 → web_search 工具被裁掉 → 模型看不见、调不到
    "EnableWebSearch": (
        "web_search",
    ),
    "EnableBrowser": (
        "browser_open", "browser_snapshot", "browser_click", "browser_type",
        "browser_extract", "browser_links", "browser_screenshot", "browser_close",
    ),
    "EnableIndex": (
        "index_scan", "index_search", "index_status",
    ),
    "EnableRemote": (
        "remote_start", "remote_status", "remote_stop",
    ),
    "EnableAutomation": (
        "automation_add", "automation_list", "automation_remove",
        "automation_toggle", "automation_runs",
    ),
    "EnableMemory": (
        "memory_save", "memory_search", "memory_forget",
    ),
    "EnableSkills": (
        "use_skill",
    ),
    "EnableSubAgents": (
        "spawn_agent", "list_agents",
        "team_create", "team_delete", "team_list", "team_add_member",
        "team_send_message", "team_dispatch", "team_inbox",
        "workflow_run", "workflow_status", "workflow_cancel",
        "workflow_draft",   # 意图 → 编排草案（只读，辅助层）
    ),
    "EnableTerminal": (
        "term_run", "term_status", "term_interrupt",
    ),
    "EnableDocs": (
        "doc_read", "doc_requirement_add", "doc_decision_add", "doc_change_log",
    ),
    "EnableSystemSettings": (
        "sys_power_status", "sys_lock_screen", "sys_set_screen_timeout",
        "sys_display_settings", "sys_set_dpi_scaling", "sys_set_wallpaper",
        "sys_mouse_settings", "sys_input_methods",
    ),
}

# 这些开关对应的能力**在本项目里根本不存在**（没有对应工具或模块）。
#
# 为什么单独列出来：它们出现在界面上的开关列表里，用户会以为
# 「关掉联网搜索」真的有意义 —— 但 Oryx 压根没有联网搜索功能。
# 留着是骗人，删掉是诚实。列在这里是为了让界面能标注「尚未实现」，
# 而不是默默消失让人以为被删了。
UNIMPLEMENTED_FLAGS: dict[str, str] = {
    "DisableWebSearch": "本项目暂无联网搜索能力",
    "DisableImageGen": "本项目暂无图像生成能力",
    "DisableVideoGen": "本项目暂无视频生成能力",
    "DisableChannels": "本项目暂无第三方聊天渠道",
    "DisableA2A": "本项目暂无云端 A2A 代理",
}


def gated_tools(flags: "FeatureFlags") -> set[str]:
    """当前配置下**应该被裁掉**的工具名集合。

    关掉的能力 → 工具被裁掉 → 模型看不见、调不到。
    """
    blocked: set[str] = set()
    for feature, tools in FEATURE_GATES.items():
        if not flags.is_enabled(feature):
            blocked.update(tools)
    # 沙箱是 Disable* 语义：真值 = 关掉沙箱工具
    if not flags.is_enabled("DisableSandbox"):
        blocked.add("shell_sandbox")
    return blocked



@dataclass
class FeatureFlags:
    flags: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_FEATURES))

    @classmethod
    def load(cls, path: Path | None = None) -> "FeatureFlags":
        """加载顺序：DEFAULT_FEATURES → profile 覆盖 → features.yaml。

        两处必须注意：
        1. **PyYAML 可能没装** —— 那就退回 JSON 解析。写成 JSON 的 YAML 是
           合法 YAML 的一部分，JSON 解析器能读；反过来不行。
           以前这里 `except: raw = None`，**整个文件被丢掉** ——
           用户手改的开关全部失效，而且没有任何提示。
        2. **profile 要读出来** —— 以前 profile 硬编码成 "personal"，
           用户在文件里写 `profile: team` 也不生效。
        """
        flags = dict(DEFAULT_FEATURES)
        # profile 优先于 yaml 是有意的：profile 是面向运维的批量开关
        flags.update(PROFILES.get("personal", {}))

        raw: Any = None
        if path is not None and path.exists():
            text = path.read_text("utf-8", errors="replace")
            try:
                import yaml  # type: ignore

                raw = yaml.safe_load(text)
            except ImportError:
                # 没装 PyYAML：JSON 是 YAML 的子集，用标准库读
                import json

                try:
                    raw = json.loads(text)
                except Exception:
                    raw = None
            except Exception:
                # 文件坏了：退回 JSON 再试一次，仍失败就用默认值
                import json

                try:
                    raw = json.loads(text)
                except Exception:
                    raw = None

        if isinstance(raw, dict):
            profile = raw.get("profile") if isinstance(raw.get("profile"), str) else None
            if profile and profile in PROFILES:
                flags.update(PROFILES[profile])
            overrides = raw.get("flags") if isinstance(raw.get("flags"), dict) else {}
            for k, v in overrides.items():
                if isinstance(v, bool):
                    flags[k] = v
        return cls(flags=flags)

    def is_enabled(self, name: str) -> bool:
        """统一命名判断：Disable* 看非，Enable* 看是，其余视为真。"""
        if name.startswith("Disable"):
            return not bool(self.flags.get(name, False))
        if name.startswith("Enable"):
            return bool(self.flags.get(name, False))
        return True

    def snapshot(self) -> dict[str, bool]:
        return dict(self.flags)


def feature_catalog(flags: FeatureFlags) -> list[dict[str, Any]]:
    """给 UI 用的卡片清单：name / enabled / description。

    ``implemented`` 字段说明这个开关**背后到底有没有能力**：
    - True  → 关掉它，系统行为真的会变（工具被裁掉）
    - False → 本项目压根没有这个能力，开关只是个占位，界面要如实标注

    以前没有这个字段，于是「关闭联网搜索」看起来能点，
    但 Oryx 根本没有联网搜索 —— 用户以为有意义，其实没有。
    """
    descriptions: dict[str, str] = {
        "EnableOffice": "生成 Word/Excel/PPT 文档",
        "EnableOcr": "图片/截图 OCR",
        "EnableBrowser": "浏览器 Agent",
        "EnableWebSearch": "联网搜索（需要配置 search.api_key）",
        "EnableIndex": "本地内容索引",
        "EnableRemote": "手机/电脑跨端远控",
        "EnableAutomation": "定时自动化任务",
        "EnableMemory": "跨会话长期记忆",
        "EnableExperts": "预设专家角色",
        "EnableSkills": "技能（SKILL.md）",
        "EnableSubAgents": "子 Agent 派发",
        "EnableTerminal": "内嵌终端",
        "EnableDocs": "项目文档生成",
        "EnableSystemSettings": "Windows 系统设置工具集（Marvis 风格）",
        "DisableSandbox": "关闭命令沙箱（shell_sandbox 工具）",
        "EnableFeatureFlags": "允许运行时调整特性开关",
        "EnableModelRouting": "按任务复杂度/成本/网络条件自动选择模型（执行期路由）",
        "EnableReaMcp": "把 rea 作为 MCP server 拉起，扩展反向分析能力",
    }
    out: list[dict[str, Any]] = []
    for name, desc in descriptions.items():
        out.append(
            {
                "key": name,
                "description": desc,
                "enabled": flags.is_enabled(name),
                "value": flags.flags.get(name, False),
                "implemented": True,
            }
        )
    # 尚未实现的能力：如实标出来，界面显示为不可切换。
    for name, reason in UNIMPLEMENTED_FLAGS.items():
        out.append(
            {
                "key": name,
                "description": reason,
                "enabled": flags.is_enabled(name),
                "value": flags.flags.get(name, False),
                "implemented": False,
            }
        )
    return out
