"""Agent 主循环：规划 → 调用工具 → 观察 → 继续，直到给出最终答复。

三个不可协商的约束（对齐 Marvis 的信任模型）：
1. 所有工具调用必须过 PolicyEngine；
2. L2 动作必须等用户点确认，未确认绝不执行；
3. 每次调用都写审计日志。
"""

from __future__ import annotations

import inspect
import json
import threading
import time
from pathlib import Path
from typing import Any

from .config import Config
from .checkpoint import CheckpointService
from .events import EventBus
from .agents import MAX_DEPTH, AgentLibrary, AgentSpec, SpawnBudget
from .experts import Expert, ExpertLibrary
from .failure import annotate as annotate_failure
from .hooks import HookBus, builtin_hooks
from .indexer import FileIndex
from .modes import ModePolicy, allowed_tools, get_policy
from .policy import PolicyEngine, Risk
from .projectdoc import ProjectDoc
from .team import MAX_MEMBERS, parse_members
from .toolsets import (
    AgentToolsMixin,
    AutomationToolsMixin,
    BrowserToolsMixin,
    DeferToolsMixin,
    DocToolsMixin,
    IndexToolsMixin,
    ManageToolsMixin,
    McpToolsMixin,
    MemoryToolsMixin,
    OfficeToolsMixin,
    PluginSkillsToolsMixin,
    PromptToolsMixin,
    RemoteToolsMixin,
    SandboxToolsMixin,
    SystemSettingsMixin,
    TerminalToolsMixin,
    ToolSearchToolsMixin,
    UseSkillToolsMixin,
    WorkflowToolsMixin,
)
from .prompts.loader import load_system_prompt, raw_system_prompt as system_prompt
from .providers import ProviderError, ToolCall, build_provider
from .skills import MAX_SKILL_CHARS, SkillLibrary
from .store import Store
from .styles import get_style
from .team import TeamRegistry
from .toolindex import ToolIndex, fuse
from .tooldefer import DeferPolicy
from .tools import ToolSpec, build_registry, tool_result_text

APPROVAL_TIMEOUT = 300  # 秒；超时视为拒绝
# 「还没被消费的中止请求」的有效期（秒）。
# 超过这个时间还没被任何一轮消费，就当作用户在旧任务上误点的停止，作废。
# 不设期限的话，那笔请求会一直挂着，把下一条消息也停掉。
CANCEL_PENDING_WINDOW = 5.0

# 「本线程从没设过这个属性」的哨兵。
# 用来区分「显式设为 None」与「没设过」—— 见 AgentRuntime._active_task_id。
_UNSET = object()


def _agent_from_snapshot(data: Any) -> AgentSpec | None:
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return AgentSpec(
        id=str(data["id"]), name=str(data.get("name") or data["id"]),
        description=str(data.get("description") or ""),
        tools=list(data.get("tools") or []), max_turns=int(data.get("max_turns") or 6),
        body=str(data.get("body") or ""), model=str(data.get("model") or "inherit"),
        permission_mode=str(data.get("permission_mode") or "inherit"),
        skills=list(data.get("skills") or []),
        disallowed_tools=list(data.get("disallowed_tools") or []),
        memory=str(data.get("memory") or ""),
        initial_prompt=str(data.get("initial_prompt") or ""),
        icon=str(data.get("icon") or ""), subtitle=str(data.get("subtitle") or ""),
        kind=str(data.get("kind") or "agent"),
    )


def _expert_from_snapshot(data: Any) -> Expert | None:
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return Expert(
        id=str(data["id"]), name=str(data.get("name") or data["id"]),
        description=str(data.get("description") or ""),
        scenarios=list(data.get("scenarios") or []), body=str(data.get("body") or ""),
        tags=list(data.get("tags") or []), personality=str(data.get("personality") or ""),
        speaking_style=str(data.get("speaking_style") or ""),
        use_cases=str(data.get("use_cases") or ""), self_intro=str(data.get("self_intro") or ""),
    )


class _StreamCancelled(RuntimeError):
    """模型流式输出途中被用户中止。

    为什么需要它：`provider.chat()` 是**同步阻塞**调用，
    而流式响应是一段一段吐回来的 —— 所以可以在**每个 chunk 到达时**
    查一次中止标志，命中就抛出来打断这次调用。

    以前只在主循环的轮首/轮末查标志，中间那次模型调用要等它**整个跑完**
    才有机会收手。本机挂的 Base 模型（不会终止生成）能连吐好几分钟 ——
    症状是「点了停止，界面一直转圈，任务状态永远停在 running」。

    带着已经吐出来的部分正文 —— 中止时它要留住，
    「半个答案比没答案有用」。
    """

    def __init__(self, partial: str = "") -> None:
        super().__init__("用户中止了这次模型调用")
        self.partial = partial


class _RunAbort:
    """**一轮运行自己的**中止信号（模块 11）。

    为什么以前要拆成「全局 Event + 全局代号」两个字段
    --------------------------------------------------
    Event 只有「置位 / 未置位」两态，分不清「上一轮遗留的置位」与
    「这一轮新点的停止」；代号（generation）解决了这个区分，
    但它是**全局一个计数器** —— 于是两轮并发时必然串扰：

        A、B 同时跑，两轮都记下代号 0
        用户停止 A → 全局代号 +1 → 变成 1
        B 查自己的代号（0）发现「变了」→ **B 也被当成被停止**

    实测确认过这个串扰（core/tests/probe_cancel_isolation.py）：
    停止 A 会把 B 一起停掉。

    现在每个运行实例持一份自己的信号：

    * `generation` —— 这一轮自己的代号，只有针对**这一轮**的取消才会 +1
    * `event` —— 这一轮自己的置位标志，审批等待靠它醒来

    别的运行完全不受影响 —— 一次停止只停自己那一轮。
    """

    __slots__ = ("generation", "event", "consumed", "at")

    def __init__(self) -> None:
        # 本轮自己的中止代号
        self.generation = 0
        # 本轮自己的置位标志（唤醒本轮的审批等待）
        self.event = threading.Event()
        # 本轮已经消费掉的代号 —— 用来实现「中止先于发起到达」的排队语义
        self.consumed = 0
        # 最近一次请求中止的时刻。用来判断「先于本轮到达的那笔」是不是已过期 ——
        # 用户在一个早就结束的任务上误点停止，不该把下一条消息也停掉。
        self.at = 0.0

    def cancel(self) -> None:
        """请求中止这一轮。"""
        self.generation += 1
        self.at = time.time()
        self.event.set()

    def is_cancelled(self, generation: int) -> bool:
        """这一轮要不要因为「用户点了停止」而收手。

        判据是「代号变了」而不是「Event 被置位」——
        后者分不清「上一轮遗留的置位」与「本轮新点的停止」。
        """
        if self.generation != generation:
            # 消费掉这一笔：下一轮不该再被它影响
            self.consumed = max(self.consumed, self.generation)
            return True
        return self.event.is_set()


class _RunSlot:
    """一个运行实例的登记（模块 11）。

    以前「正在跑的是哪个任务」靠一个**单槽**字段 `_active_task_id` ——
    两个运行并发时，后者会覆盖前者，于是：
      · `cancel(task_id=A)` 找不到 A（槽里已经是 B 了）→ 停不掉；
      · `is_task_running(A)` 说 A 没在跑 → 检查点回退的运行中保护失效。

    现在每个运行实例在 `_runs` 里占一格，带自己的 `(task_id, owner_id)`
    与自己的中止信号。定位靠这张表，而不是靠单槽。
    """

    __slots__ = ("rid", "task_id", "owner_id", "abort", "phase")

    def __init__(self, rid: int, task_id: str | None, owner_id: str | None) -> None:
        self.rid = rid
        self.task_id = task_id
        self.owner_id = owner_id
        self.abort = _RunAbort()
        # "pending" = 已登记正要开始跑；"running" = 正在跑
        self.phase = "pending"


# 主控系统提示词 —— 正文在 prompts/system/*.md，不在这里。
#
# 为什么外置：这段提示原先是本文件里一个 5950 字符的三引号常量。
# 改一句提示词要翻 Python 代码，改坏了编译期才发现 —— 跑起来才知道少了一句。
# 挪到 md 之后，改提示词不用碰 Python，段落边界也清楚（一个文件一个语义段）。
#
# 这里保留 SYSTEM_PROMPT 这个名字（模块级常量），是为了不破坏既有引用
# （tests/test_prompt_contract.py 等）。它的值由 loader 拼装。
#
# 硬约束：拼装结果必须与拆分前**逐字节相同**。
# 提示词差一个空格都可能让模型行为变样，所以有基准快照 + 逐字节对比的测试。
SYSTEM_PROMPT = system_prompt()


class AgentRuntime(
    AgentToolsMixin,
    AutomationToolsMixin,
    BrowserToolsMixin,
    DeferToolsMixin,
    DocToolsMixin,
    IndexToolsMixin,
    ManageToolsMixin,
    McpToolsMixin,
    MemoryToolsMixin,
    OfficeToolsMixin,
    PluginSkillsToolsMixin,
    PromptToolsMixin,
    RemoteToolsMixin,
    SandboxToolsMixin,
    SystemSettingsMixin,
    TerminalToolsMixin,
    ToolSearchToolsMixin,
    UseSkillToolsMixin,
    WorkflowToolsMixin,
):
    def __init__(
        self,
        config: Config,
        store: Store,
        bus: EventBus,
        skill_root: Path | None = None,
        autostart_scheduler: bool = True,
    ):
        self.config = config
        self.store = store
        self.bus = bus
        # 待命内核（预热）不自动启动定时任务：它还没被激活，
        # 不该抢跑用户的定时任务。激活时再补启动。
        self._autostart_scheduler = autostart_scheduler
        self.policy = PolicyEngine(config.allowed_roots, config.forbidden_paths, config)
        self.registry: dict[str, ToolSpec] = build_registry()
        root = skill_root or (Path(__file__).resolve().parents[2] / "skills")
        # ── 扩展注册表（统一的扩展发现入口）──────────────────────────
        # 以前 skills / experts / agents 各写一遍「扫目录 + 解析 frontmatter」，
        # 加一种扩展类型就复制一遍。现在统一走 registry：
        #   · 它给三套 library 补充声明式元数据（触发词 / 常驻 / 能力集 / 契约）；
        #   · 老目录结构**一行不用改**照样能加载（oryx.json 是可选增强）。
        #
        # 信任分级：用户目录（app_dir 下的 skills）算 USER（可信），
        # 产品自带目录算 BUILTIN，仓库里的项目目录算 PROJECT（不可信，
        # 不能声明 proc.* 这类高危能力）。
        from .registry import Registry, Trust

        from .config import app_dir as _app_dir

        user_skills = _app_dir() / "skills"
        user_ext = _app_dir() / "extensions"
        repo_ext = root.parent / "extensions"
        self.extension_registry = Registry(
            roots=[
                ("expert", root.parent / "experts"),
                ("agent", root.parent / "agents"),
            ],
            trust_map={
                "expert": Trust.BUILTIN,
                "agent": Trust.BUILTIN,
            },
        )
        # 技能目录 —— **按根目录**给信任级别：
        #   用户目录（app_dir()/skills）→ USER（用户自己放的）
        #   仓库目录（产品自带的 skills/）→ BUILTIN
        #
        # 为什么不用 trust_map：它是**按 kind**给的，
        # 同一个 kind 挂两个根时两个根会拿到同一个级别 ——
        # 仓库自带的技能会被误标成「用户信任」。
        # 信任看的是「东西从哪来」，不是「它是什么类型」。
        self.extension_registry.add_root("skill", user_skills, Trust.USER)
        self.extension_registry.add_root("skill", root, Trust.BUILTIN)
        # 扩展提供的工具 —— 同样**按根目录**给信任级别：
        #   用户目录（app_dir()/extensions）→ USER，可执行代码
        #   仓库目录（项目里的 extensions/）→ PROJECT，**只能声明元数据**
        #
        # 「git clone 一个仓库」不该等于「授权它的代码在你机器上跑」。
        self.extension_registry.add_root("tool", user_ext, Trust.USER)
        self.extension_registry.add_root("tool", repo_ext, Trust.PROJECT)

        # ── 停用名单 ──────────────────────────────────────────────────
        # 用户在界面上关掉的扩展，不进入注册表 —— 不被发现、不被加载。
        # 存在 Oryx 自己的配置里（不改用户的扩展文件）。
        from .extmanage import disabled_ids

        self._disabled_extensions = disabled_ids(self.config.data)
        self.extension_registry.set_disabled(self._disabled_extensions)

        # ── profile：一组扩展的组合 ───────────────────────────────────
        # profile 只是**收窄**加载范围，不影响安全判定。
        # 没配 profile 就不排除任何东西 —— profile 是可选增强，不是必需项。
        from .profiles import ProfileSet

        profile_id = str(self.config.data.get("extension_profile") or "").strip()
        profile_set = ProfileSet.load(root.parent / "profiles.json")
        if not profile_set.profiles:
            # 用户目录下的 profile 也认（用户自己写的优先）
            user_profiles = _app_dir() / "profiles.json"
            if user_profiles.exists():
                profile_set = ProfileSet.load(user_profiles)
        self.extension_registry.set_profiles(profile_set, profile_id)
        self.extension_registry.discover()
        self.extension_profiles = profile_set
        # 三个 library **都以注册表为准** ——
        # 目录从注册表拿（用户目录的技能也算），停用与依赖从注册表查。
        # 以前 experts/agents 各自扫目录、从不问注册表，
        # 于是界面上「停用」对它们完全无效。
        self.skills = SkillLibrary(root, registry=self.extension_registry)
        self.experts = ExpertLibrary(
            root.parent / "experts", registry=self.extension_registry)
        self.agents = AgentLibrary(
            root.parent / "agents", registry=self.extension_registry)

        # 插件技能要在**加载工具扩展之前**进注册表 ——
        # 技能也能声明工具，晚一步注册，那部分工具就永远加载不到。
        self._register_plugin_skills()

        # ── 扩展提供的工具 ────────────────────────────────────────────
        # 「加工具不用改 Python」的落点。扩展在 oryx.json 里声明工具的
        # schema 与风险等级（声明层），代码放在扩展目录的 tools.py（实现层）。
        #
        # 安全边界在 toolplug 里：**只有 builtin / user 信任级别能执行扩展代码**。
        # 仓库自带（git clone 来的）扩展只能声明元数据，不会被执行。
        from .toolplug import load_tool_plugins
        from .tooltable import Conflict, ToolTable

        # ── 统一工具表 ────────────────────────────────────────────────
        # 内置、扩展、MCP 三种来源合成一张表。
        # 冲突检测在这里做 —— **扩展永远不能覆盖内置或 MCP 的工具**。
        # 以前是 `self.registry[name] = spec` 直接覆盖，
        # 一个扩展能把 fs_read 换成返回任意内容的实现，而模型毫无察觉。
        #
        # 表在**所有工具登记完之后**重建（见 refresh_tool_table）——
        # 工具是分散注册的，建早了会漏。
        self.tool_table = ToolTable()

        self.tool_plugins, self.tool_plugin_report = load_tool_plugins(
            self.extension_registry.of_kind("tool")
            + self.extension_registry.of_kind("skill")
        )
        # 加载报告里已经有「哪个扩展、什么信任级别」——
        # 不用再回头翻注册表找一遍。
        _loaded_meta = {
            t.spec.name: (t.extension_id, t.trust)
            for t in self.tool_plugin_report.loaded
        }
        for _name, _spec in sorted(self.tool_plugins.items()):
            _ext_id, _trust = _loaded_meta.get(_name, (_name, "user"))
            if _name in self.registry:
                # 与内置/MCP 同名 —— **拒绝**，保留内置实现。
                # 冲突要记下来：用户得知道有扩展试图覆盖内置工具。
                self.tool_table.conflicts.append(Conflict(
                    name=_name,
                    kept="builtin",
                    rejected=f"extension/{_ext_id}",
                ))
                continue
            self.registry[_name] = _spec
        # 记下扩展的指纹 —— 热重载靠它判断「要不要重新加载」
        self._ext_fingerprints = {
            r.id: r.fingerprint
            for r in (self.extension_registry.of_kind("tool")
                      + self.extension_registry.of_kind("skill"))
        }
        self.project_doc = ProjectDoc(self.config.data.get("workspace") or Path.cwd())
        # 分层记忆：用户偏好 / 项目约定 / 条件规则（按文件类型精准注入）
        from .config import app_dir
        from .memory import MemoryService

        self.memory = MemoryService(
            store=self.store,
            home=app_dir(),
            workspace=self.config.data.get("workspace") or None,
        )
        # 索引器的能力也跟着特性开关走：
        # 关掉 EnableOcr → 索引不再做 OCR；关掉 EnableIndex → 不建索引。
        # 读盘一次，开关改动会在下一轮 run 时通过 _gated_tools 生效。
        try:
            from .features import FeatureFlags as _FF

            _flags = _FF.load(config.features_path())
        except Exception:
            _flags = None
        self.index = FileIndex(
            forbidden=config.forbidden_paths,
            ocr=bool(config.data.get("ocr", True))
            and (_flags is None or _flags.is_enabled("EnableOcr")),
            embeddings=bool(config.data.get("embeddings", True)),
        )
        # 工具语义索引。懒加载：只有真的调tool_search 才会去建向量，
        # 免得内核启动时被模型加载拖慢。
        self.tool_index = ToolIndex()
        self._approvals: dict[str, threading.Event] = {}
        self._approval_results: dict[str, bool] = {}
        # 每个审批等待隶属于哪个任务、哪个账号：step_id -> (task_id, owner_id)。
        # cancel() 靠它判断「这笔停止是不是冲着这个等待来的」——
        # 不能只看 _active_task_id：那是单个槽位，嵌套起跑（定时任务、派发）
        # 会把它顶成别的任务，外层任务在等确认时就停不动了。
        self._approval_context: dict[str, tuple[str | None, str | None]] = {}
        # 审批卡要展示的内容（工具、计划、风险、所属任务）。SSE 事件可能错过
        # （刷新页面、重连时缓冲已滚过），所以内核里留一份可查询的快照。
        self._approval_details: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._active_registry: dict[str, ToolSpec] = {}
        self._active_interaction: str = config.data.get("interaction", "craft")
        # **线程局部**的「当前运行上下文」（模块 11）。
        #
        # 为什么不能是普通实例属性：两个会话并发跑时，B 会把 A 的
        # task_id / owner_id 顶掉 —— 于是 A 的审批等待被记成 B 的，
        # 停止 A 时按 owner 匹配不上（停不掉），或者停止 B 却叫醒了 A 的等待。
        #
        # 每个运行在自己的线程里（`_launch` 起的是 `threading.Thread`），
        # 所以「当前是谁」用线程局部存最自然：互不干扰，嵌套也能各归各位。
        self._tls = threading.local()
        self._active_task_id: str | None = None
        # 当前这一轮归属的账号。run() 进出时切换 / 还原。
        # 必须在这里给初值：审批等待在没跑过任务时也会读它。
        self._active_owner_id: str | None = None
        # 线程局部的「当前运行上下文」。见下面两个 property 的说明。
        # 初值用 _UNSET —— 「本线程从没设过」与「显式设为 None」要分得开。
        self._tls.task_id = _UNSET
        self._tls.owner_id = _UNSET
        self._tls.abort: _RunAbort | None = None
        # 全局兜底值：没有运行上下文时（比如测试直接赋值）用它。
        self._fallback_task_id: str | None = None
        self._fallback_owner_id: str | None = None
        self._seq: int = 0

        # 哪些 task 的思考通道还开着（task_id → True）。
        # 用来保证 REASONING_START 只发一次、结束时补一个 END。
        self._reasoning_open: dict[str, bool] = {}
        # 子 Agent 派生预算（每会话）
        self.spawn_budget = SpawnBudget()
        # 会话级禁用工具（与 Agent 自己的 disallowedTools 取并集）
        self._session_disallowed: set[str] = set()
        # 当前子 Agent 嵌套深度（主会话为 0）
        self._agent_depth: int = 0
        # 工作流编排：活动 run（中止用），由 workflow tool 维护
        self._workflow: Any = None
        # ── 中止信号：每个运行实例一份（模块 11） ──────────────────
        #
        # 以前这里是「一个全局 Event + 一个全局代号计数器」。
        # 那个设计在**单轮**下是对的，但两轮并发时必然串扰：
        # A、B 同时跑都记下代号 0，用户停止 A 让全局代号 +1，
        # B 查自己的代号发现「变了」→ B 也被当成被停止（实测确认过）。
        #
        # 现在改成：每个运行实例占 `_runs` 一格，带自己的 `(task_id, owner_id)`
        # 与自己的中止信号。`cancel(task_id=…, owner_id=…)` 按
        # (task_id, owner_id) 精确找到目标实例，只动它那一份。
        self._runs: dict[int, _RunSlot] = {}
        # 下一个实例 id
        self._run_seq = 0
        # 「还没被消费的中止请求」的时刻表：task_id → 时刻。
        # 用于「中止请求比任务起步还早到」的排队语义
        # （用户点发送后立刻点停止，两条请求并发到达）。
        self._pending_aborts: dict[str, float] = {}
        # 「有任务在跑或正要开始跑」—— 这才是真正的忙碌标志。
        #
        # 为什么不用 _active_task_id：那个字段跑完**不会清**（它是「当前/最近一个任务」，
        # 好几处测试与代码拿它当句柄）。拿它当忙碌标志的后果是：
        #   · 定时任务永远排不上队（is_busy 恒为真）
        #   · 用户在旧任务上误点停止，那一下会一直挂着，把下一条消息也停掉
        #
        # 分成两个计数：
        #   _run_pending   —— 已发起、还没开跑（内核收到 chat.run 到线程进 run() 之间）
        #   _run_in_flight —— 正在跑（run() 入口置位，finally 清位）
        # 分开是因为「用户在这个窗口里点停止」也得算数 —— 那时前者为真、后者为假。
        #
        # 用计数而不是布尔：定时任务触发时会**嵌套**调 run()（调度线程里同步跑），
        # 这时用户又发消息 —— 布尔标志会被内层那次的收尾提前清掉，
        # 外层还在跑却显示成「空闲」。
        self._run_pending = 0
        self._run_in_flight = 0
        # 待交接的运行登记：(task_id, owner_id, 运行实例 id)
        self._pending_runs: list[tuple[str, str | None, int]] = []
        # 特性开关裁剪的缓存。stamp = (mtime, size)，
        # 变了就重新读盘 —— 用户在界面改开关后下一轮立即生效。
        self._gated_cache: set[str] | None = None
        self._gated_cache_stamp: tuple[float, int] | None = None
        self._register_use_skill()
        self._register_index_tools()
        self._register_browser_tools()
        self._register_search_tools()
        self._register_office_tools()
        self._register_remote_tools()
        self._register_doc_tools()
        self._register_agent_tools()
        self._register_workflow_tools()
        self._register_memory_tools()
        self._register_manage_tools()
        self._register_prompt_tools()
        self._register_automation_tools()
        self._register_terminal_tools()
        self._register_tool_search()
        self._register_system_settings()
        self._register_sandbox_tool()
        self.plugins: list[Any] = []
        self.mcp_clients: dict[str, Any] = {}
        self.teams = TeamRegistry(store=self.store)
        # 检查点：每次用户提示打一个，写操作前存快照，支持回退
        self.checkpoints = CheckpointService(self.store, config.allowed_roots)

        # 两段式工具加载：延迟暴露的工具不进模型上下文，
        # 模型用 tool_search 找到后经 defer_execute 调用。
        self.defer = DeferPolicy()
        # .mcp.json 里的 defer_loading 规则（_init_plugins 时填充）
        self.mcp_defer: dict[str, list[str]] = {}

        # 生命周期 hook：主循环只在切点 emit，具体做什么由注册方决定。
        # 内置耗时统计通过 hook.metrics 事件外发，前端可以看每一步/每轮花了多久。
        self.hooks = HookBus()
        builtin_hooks(self.hooks, on_metrics=lambda m: self.bus.publish("hook.metrics", **m))

        self._init_plugins()
        # MCP 工具已就位，再补语义标注 —— 否则 plugin 带的工具拿不到提示词
        self._annotate_semantics()
        self._apply_defer_config()
        self._register_defer_tools()
        # 所有工具登记完毕 → 重建统一工具表。
        # （前面只登记了 build_registry() 的那批；各 _register_* 是分散注册的。）
        self.refresh_tool_table()
        self._active_registry = dict(self.registry)

    # ── 「当前运行上下文」：线程局部的两个字段 ──────────────────────
    #
    # 模块 11：以前这是两个普通实例属性（全局单槽），
    # 两个会话并发跑时后者会把前者顶掉 —— 审批归属、停止匹配都跟着错。
    # 现在读写走线程局部：每个运行线程看自己的，互不干扰。
    #
    # 兜底：没有线程局部值时用 `_fallback_*`
    #（测试直接赋值 `runtime._active_task_id = ...` 时走这条路）。
    #
    # 哨兵 `_UNSET` 的作用：区分「本线程**显式**设为 None」与
    # 「本线程从没设过」。前者要返回 None（这一轮不属于任何账号），
    # 后者才回退到全局兜底 —— 否则一个线程跑完后把兜底值清成 None，
    # 另一个线程就再也读不到自己的账号了。
    @property
    def _active_task_id(self) -> str | None:
        tid = getattr(self._tls, "task_id", _UNSET)
        if tid is not _UNSET:
            return tid
        return self._fallback_task_id

    @_active_task_id.setter
    def _active_task_id(self, value: str | None) -> None:
        self._tls.task_id = value
        self._fallback_task_id = value

    @property
    def _active_owner_id(self) -> str | None:
        owner = getattr(self._tls, "owner_id", _UNSET)
        if owner is not _UNSET:
            return owner
        return self._fallback_owner_id

    @_active_owner_id.setter
    def _active_owner_id(self, value: str | None) -> None:
        self._tls.owner_id = value
        self._fallback_owner_id = value

    @property
    def _current_abort(self) -> _RunAbort | None:
        """本线程当前运行实例的中止信号（没有就 None）。"""
        return getattr(self._tls, "abort", None)

    def _only_run_abort(self) -> _RunAbort:
        """当前**唯一**那个运行实例的中止信号。

        只给「此刻应该只有一个运行」的场合用（测试断言、排障）。
        有多个实例时取最近登记的那个 —— 与旧接口「当前/最近一个任务」一致。
        """
        with self._lock:
            if not self._runs:
                raise RuntimeError("当前没有运行实例")
            slot = max(self._runs.values(), key=lambda s: s.rid)
            return slot.abort

    def _recent_abort(self) -> _RunAbort | None:
        """最近登记的那个运行实例的中止信号。

        给「不一定在运行上下文里、但属于某次运行」的场合用 ——
        比如子 Agent：它跑在父运行的线程里，正常路径直接拿
        `_current_abort`；被工具链直接调用时（测试、工作流编排）
        退到这里，保证「父运行被停 → 子 Agent 也停」。
        """
        current = self._current_abort
        if current is not None:
            return current
        with self._lock:
            if not self._runs:
                return None
            slot = max(self._runs.values(), key=lambda s: s.rid)
            return slot.abort

    # ------------------------------------------------------------------ 技能工具
    def reload_extensions(self) -> dict[str, Any]:
        """重新加载改了的后端扩展 —— **不用重启内核**。

        流程：
          1. 重新扫描扩展目录（拿到新的指纹）
          2. 找出指纹变了的扩展
          3. 重新执行它们的 Python 模块（模块缓存里的旧版本清掉）
          4. 重建 ToolSpec 并更新 registry

        注意扩展代码在**你的机器上**执行 —— 所以只对**可信来源**生效。
        project 信任的扩展本来就不执行代码，热重载对它们无意义。

        返回
        ----
        {"reloaded": [...], "errors": [...], "tools": N}
        """
        from .toolplug import is_extension_tool, reload_tool_plugins

        # 1) 重新扫描（指纹会更新）
        self.extension_registry.discover()
        records = (self.extension_registry.of_kind("tool")
                   + self.extension_registry.of_kind("skill"))

        # 2+3+4) 重载变化的扩展
        specs, report, reloaded = reload_tool_plugins(
            records, self.tool_plugins, self._ext_fingerprints
        )
        if not reloaded:
            return {"reloaded": [], "errors": list(report.errors), "tools": len(self.tool_plugins)}

        # 更新工具表：摘掉旧的、装上新的
        old_names = set(self.tool_plugins)
        for name in old_names - set(specs):
            self.registry.pop(name, None)
        self.tool_plugins = specs
        for name, spec in specs.items():
            old = self.registry.get(name)
            if old is not None and not is_extension_tool(old):
                # registry 里已有的是**内置或 MCP** 的工具 ——
                # 扩展不能覆盖它（与初始加载同一套规矩）。
                continue
            self.registry[name] = spec

        # 4) 统一工具表重建
        self.refresh_tool_table()
        # 工具表重建后要同步「活动注册表」—— 那是给模型看的那份
        self._active_registry = dict(self.registry)

        self._ext_fingerprints = {r.id: r.fingerprint for r in records}
        self.tool_plugin_report = report
        return {
            "reloaded": reloaded,
            "errors": list(report.errors),
            "tools": len(specs),
        }

    def refresh_tool_table(self) -> None:
        """重建统一工具表。

        为什么要有这个方法：工具是**分散注册**的 ——
        `build_registry()` 给 10 个，各 `_register_*` 方法再陆续加 60 多个，
        MCP 的最后到。统一表要在**全部登记完之后**才准。

        三种来源的归位：
          · 内置       —— 名字里没有 `mcp__` 前缀的
          · MCP        —— `mcp__<server>__<name>`
          · 扩展       —— `tool_plugins` 里那些（按名字对上）
        """
        from .tooltable import ToolTable

        table = ToolTable()
        # 之前记下的冲突（扩展试图覆盖内置）要保留 ——
        # refresh 是重建，不能把已经发现的问题冲掉。
        table.conflicts.extend(self.tool_table.conflicts)
        ext_names = set(self.tool_plugins)
        loaded_meta = {
            t.spec.name: (t.extension_id, t.trust)
            for t in self.tool_plugin_report.loaded
        }
        for name, spec in sorted(self.registry.items()):
            if name in ext_names:
                ext_id, trust = loaded_meta.get(name, (name, "user"))
                table.add_extension(spec, ext_id=ext_id, trust=trust)
            elif name.startswith("mcp__"):
                parts = name.split("__")
                table.add_mcp(spec, server=parts[1] if len(parts) > 1 else "")
            else:
                table.add_builtin(spec)
        table.validate()
        self.tool_table = table


    # ------------------------------------------------------------------ 索引工具
    def _announce_degradation(self, capability: str, reason: str, action: str = "") -> None:
        """能力静默降级时发一条事件，让界面显式告诉用户。

        向量检索退回纯关键词、OCR 引擎缺失，以前都是**悄悄发生**的：
        用户以为自己在用语义检索，其实只跑了关键词，结果差了也不知道为什么。
        去重按 (能力, 原因) —— 同一次运行里不反复刷同一条提示。
        """
        if not hasattr(self, "_degraded_announced"):
            self._degraded_announced: set[tuple[str, str]] = set()
        key = (capability, reason)
        if key in self._degraded_announced:
            return
        self._degraded_announced.add(key)
        self.bus.publish(
            "capability.degraded",
            capability=capability, reason=reason, action=action,
        )


    # ------------------------------------------------------------------ 浏览器工具

    # ------------------------------------------------------------------ 文档生成
    def _register_search_tools(self) -> None:
        """联网搜索。

        **默认关**（`EnableWebSearch`）—— 它要一个外部搜索服务的 key，
        而项目承诺纯本地可用。关掉时工具会被 `gated_tools` 裁掉，
        模型看不见也调不到。

        开了但没配 key → **明确报错**。静默失败最难查。
        """
        from .search import SearchError, SearchProvider, format_results

        def _web_search(query: str, count: int = 5) -> dict[str, Any]:
            cfg = dict(self.config.data.get("search") or {})
            if not cfg.get("api_key"):
                # 明文可能被搬进凭据库了 —— 那时配置里是个标记不是字符串
                cfg["api_key"] = self.config.search_api_key()
            provider = SearchProvider(cfg)
            try:
                results = provider.search(query, int(count))
            except SearchError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "content": format_results(query, results)}

        self.registry["web_search"] = ToolSpec(
            name="web_search",
            description=(
                "联网搜索。查当前信息（新闻、政策、版本号、价格）时用它 —— "
                "本地的文件和索引里没有这些。返回标题、地址、摘要。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词"},
                    "count": {
                        "type": "integer",
                        "description": "返回几条，1-20，默认 5",
                    },
                },
                "required": ["query"],
            },
            risk=Risk.L0_READ,
            fn=_web_search,
        )


    # ------------------------------------------------------------------ 命令沙箱

    # ------------------------------------------------------------------ 跨端远控

    # ------------------------------------------------------------------ 自动化
    def _notify_automation_failure(self, item: Any, exc: "Exception | str") -> None:
        """定时任务失败时发通知。

        为什么必须发：定时任务是**没人盯着**跑的。失败只发事件的话，
        用户不打开界面就完全错过 —— 任务看起来「在跑」，实际早就挂了。

        走两条路：
        1. 配置了 webhook → POST 过去（钉钉/飞书/Slack 自建机器人都行）；
        2. 没配置 → Windows 系统通知（toast）。

        通知本身失败**不能抛** —— 通知失败不该让调度线程出问题。
        """
        from .terminal import notify

        detail = f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc)
        # 去重：_fire 与调度器 on_error 是两条可能都到达的路径，
        # 30 秒内同一个任务只发一次系统通知，别把通知栏刷屏。
        if not hasattr(self, "_last_failure_notify"):
            self._last_failure_notify: dict[str, float] = {}
        key = str(getattr(item, "id", "") or getattr(item, "name", ""))
        now = time.time()
        if now - float(self._last_failure_notify.get(key, 0)) < 30:
            return
        self._last_failure_notify[key] = now

        message = f"定时任务「{item.name}」执行失败：{detail}"
        title = "Oryx 定时任务失败"
        webhook = str(self.config.data.get("notify_webhook") or "").strip()
        try:
            if webhook:
                notify(title, message, method="webhook", webhook=webhook)
            else:
                notify(title, message, method="toast")
        except Exception:
            pass


    # ------------------------------------------------------------------ 工具检索

    # ------------------------------------------------------------------ 延迟工具
    def _apply_defer_config(self) -> None:
        """按配置决定哪些工具延迟暴露。

        三处来源，优先级从高到低（NoDefer 恒定压过一切）：
            配置 defer_tools / defer_spec —— 显式规则
            MCP 服务器级 defer_loading
            MCP 工具级 defer_loading —— 覆盖服务器级
        """
        cfg = self.config.data
        spec = str(cfg.get("defer_spec") or "")
        if spec:
            self.defer.add_spec(spec)
        for pattern in cfg.get("defer_tools") or []:
            self.defer.defer_patterns([str(pattern)])

        for server, conf in (cfg.get("mcp_servers") or {}).items():
            if not isinstance(conf, dict):
                continue
            if conf.get("defer_loading"):
                self.defer.defer_server(server, [])
            # 工具级显式关闭 —— 服务器级延迟但这个工具不延迟
            for tool_name in (conf.get("defer_except") or []):
                self.defer.undefer_server(server, [str(tool_name)])

        # .mcp.json 里的 defer_loading 规则（服务器级 + 工具级）
        for server, rules in (getattr(self, "mcp_defer", None) or {}).items():
            deferred = rules.get("defer") or []
            exempt = rules.get("nodefer") or []
            if deferred:
                self.defer.defer_server(server, deferred)
            if exempt:
                self.defer.undefer_server(server, exempt)


    def _exposed_registry(self) -> dict[str, ToolSpec]:
        """本次 run 暴露给模型的工具集：延迟的先扣掉，开关关掉的也扣掉。

        tool_search 与 defer_execute 永远在 —— 它们是"发现通道"，
        少了任何一个延迟工具就等于永远调不到。

        **特性开关在这里生效**：关掉的能力，它的工具根本不进上下文，
        模型看不见也调不到。用户点「关闭浏览器 Agent」，浏览器工具真的消失。

        每次 run 重新算一遍 —— 所以界面上切换开关后**下一轮立即生效**，
        不用重启内核。
        """
        exposed = {}
        blocked = self._gated_tools()
        for name, spec in self.registry.items():
            if name in ("tool_search", "defer_execute"):
                exposed[name] = spec
                continue
            if name in blocked:
                continue
            if self.defer.should_defer(name):
                continue
            exposed[name] = spec
        return exposed

    def _gated_tools(self) -> set[str]:
        """当前开关配置下应当被裁掉的工具名。

        读盘有开销（features.yaml 可能不存在），所以缓存一下，
        但**每轮至少检查一次文件有没有变** —— 用户在界面上改了开关，
        下一次调用要能看到。
        """
        try:
            from .features import FeatureFlags, gated_tools

            path = self.config.features_path()
            try:
                stat = path.stat()
                stamp = (stat.st_mtime, stat.st_size)
            except OSError:
                stamp = None
            if self._gated_cache_stamp != stamp or self._gated_cache is None:
                flags = FeatureFlags.load(path)
                self._gated_cache = gated_tools(flags)
                self._gated_cache_stamp = stamp
            return self._gated_cache
        except Exception:
            # 读开关失败时**不裁剪任何工具** —— 宁可能力全开，
            # 也不能因为一个配置问题让 Agent 半个工具都用不了。
            return set()

    def _annotate_semantics(self) -> None:
        """给工具补 embed_text —— 写「什么情况下该用它」，而不是重复 description。

        只补关键词检索最容易漏掉的那些：同义说法、口语化表达、以及名字
        与用途不完全一致的工具。其余的由 ToolSpec.semantic_text 自动拼。
        """
        hints: dict[str, str] = {
            "browser_screenshot": "把当前网页整屏截屏保存成图片，也叫截屏、屏幕快照、留个图",
            "browser_open": "打开某个网址、网页、链接，比如打开百度或某个文档页面",
            "browser_click": "点网页上的按钮、链接、菜单，按编号或元素选择器点击",
            "browser_type": "在网页的输入框、搜索框里打字、填表单、输入文字并回车",
            "browser_extract": "把网页里的正文或表格内容抓下来存成数据",
            "browser_links": "列出网页里所有链接和入口地址",
            "fs_read": "打开并读取一个文件的内容，比如看代码、看文档、看配置、看看写了什么",
            "fs_list": "看看某个目录下面有哪些文件和子文件夹，比如桌面、下载、文档里都有啥",
            "fs_write": "新建一个文件或整体覆盖写入内容",
            "fs_move": "重命名文件、移动文件、挪到别的目录",
            "fs_delete": "删除文件或者文件夹，删掉不再需要的资料",
            "fs_glob": "按文件名模式批量查找文件，比如找出所有 .py 文件",
            "fs_grep": "在文件里按正则搜某段文字，返回命中行和行号",
            "search_files": "按文件名找文件、按内容找文件，类似 everything 的快速搜索",
            "shell_sandbox": "执行一条命令行指令，跑脚本、装依赖、查环境变量",
            "shell": "执行 shell 命令，原始的系统命令入口",
            "term_run": "在终端里运行命令并实时看输出，适合耗时长的任务",
            "ocr_image": "识别图片里的文字，比如从截图、发票、扫描件里提取内容，"
                         "把报销单上的金额日期编号汇总成表格",
            "docx_write": "生成一份 Word 文档，交付报告、方案、说明书",
            "xlsx_write": "生成 Excel 表格，做统计、列清单、导出数据",
            "pptx_write": "生成 PPT 演示文稿，做汇报幻灯片、会议演示",
            "html_write": "生成 HTML 网页、可视化看板、离线报告，内嵌图表不用联网",
            "pdf_write": "生成 PDF 文档，做可打印、可转发的正式报告，支持中文表格和图表",
            "image_write": "生成图表图片，柱状图折线图饼图，PNG 或 SVG，也叫画个图、出张图",
            "index_search": "按内容搜索本机文件，搜正文里的关键词或一段话的大意，"
                            "比如找代码里的 TODO、找提到某个函数的文件",
            "index_scan": "给本地文件建索引，搜之前先建，扫完就能全文检索",
            "memory_save": "记住一条长期事实，比如用户偏好、项目约定，"
                           "别让我每次都重复告诉你同一个约定",
            "memory_search": "查一下之前记住的东西，看我以前说过什么偏好",
            "system_info": "查电脑配置，问 CPU 内存显卡是什么型号、这机器什么配置",
            "sys_display_settings": "看屏幕分辨率和缩放比例",
            "list_apps": "看本机装了哪些软件，问我电脑里有什么程序",
            "workspace_info": "看当前工作目录是哪个、能访问哪些路径",
            "workspace_switch": "切换工作目录到别的项目文件夹",
            "use_skill": "读取某个技能的完整操作步骤，按流程办事",
            "spawn_agent": "把一件专门的活交给子 Agent 去做，比如让它专门记录文档",
            "team_create": "组建多 Agent 团队，把活分给不同专长的角色",
            "team_send_message": "给团队成员投递任务说明，只投递不执行",
            "team_dispatch": "让团队成员真正开工干活并交回结论",
            "notify": "发系统通知或 webhook，任务跑完提醒用户",
            "doc_change_log": "记录这次改了什么、动了哪些文件",
            "doc_requirement_add": "把新需求记进项目文档",
            "tool_search": "按需检索可用工具及其参数说明",
        }
        for name, hint in hints.items():
            spec = self.registry.get(name)
            if spec is not None and not spec.embed_text:
                spec.embed_text = hint

    # ------------------------------------------------------------------ 内嵌终端与通知

    # ------------------------------------------------------------------ 提示词优化

    # ------------------------------------------------------------------ 管理与系统

    # ------------------------------------------------------------------ 长期记忆

    def _skill_catalog_budget(self) -> int:
        """技能目录这一段能占多少 token。

        从配置读 `skill_catalog_budget`，默认 600 —— 够放十几条技能摘要，
        又不会挤掉系统提示的其他部分。

        设成 0 表示不限制（全量列出），保留老行为。
        """
        try:
            value = int(self.config.data.get("skill_catalog_budget") or 600)
        except (TypeError, ValueError):
            value = 600
        return max(0, value)

    def _memory_block(self, limit: int = 15) -> str:
        """常驻记忆块：用户偏好 + 项目约定 + 自动沉淀的经验。"""
        try:
            return self.memory.static_block(limit, self._active_owner_id)
        except Exception:
            return ""

    def _memory_rules_for(self, file_path: str) -> str:
        """文件操作前取出该路径适用的项目规范（注入过一次就不再给）。"""
        try:
            return self.memory.remember(file_path)
        except Exception:
            return ""

    # ------------------------------------------------------------------ 项目文档

    # ------------------------------------------------------------------ 子 Agent

    # ------------------------------------------------------------------ 工作流

    def run_workflow(self, plan: dict[str, Any], args: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行一份编排。同步跑 —— 调用方在 Agent 工作线程里，不会卡住事件循环。"""
        from .workflow import WorkflowError, WorkflowPlan, WorkflowRun

        try:
            parsed = WorkflowPlan.parse(plan)
        except WorkflowError as exc:
            return {"ok": False, "content": f"编排定义有问题：{exc}"}

        # 子 Agent 必须在 agents/ 里已定义 —— step 不允许自带工具白名单，
        # 否则就绕过了子 Agent 的权限边界
        known = set(self.agents.agents)
        for stage in parsed.stages:
            for step in stage.steps:
                if step.agent not in known:
                    available = "、".join(sorted(known)) or "无"
                    return {
                        "ok": False,
                        "content": f"没有这个子 Agent：{step.agent}。可用：{available}",
                    }

        run = WorkflowRun(parsed, store=self.store, bus=self.bus, args=args or {})
        self._workflow = run
        try:
            run.run(self._workflow_dispatch)
        finally:
            if self._workflow is run:
                self._workflow = None

        meta = {
            "run_id": run.id,
            "status": run.status,
            "steps": len(run.results),
            "failed": run.count_by_status("failed"),
        }
        if run.status == "done":
            report = self._write_workflow_report(run)
            if report:
                meta["report_path"] = report
            return {"ok": True, "content": run.final_answer(), "meta": meta}
        if run.status == "cancelled":
            # 中止不是错误：已完成的部分仍然有用，要交给模型看
            report = self._write_workflow_report(run)
            if report:
                meta["report_path"] = report
            return {
                "ok": False,
                "content": f"工作流已中止：{run.error or '用户中止'}。"
                           f"以下是中止前已产出的部分——\n\n{run.final_answer()}",
                "meta": meta,
            }
        return {
            "ok": False,
            "content": f"工作流失败：{run.error or '部分 step 未完成'}",
            "meta": meta,
        }

    def _write_workflow_report(self, run: Any) -> str:
        """把这次编排的产出落成一份报告文件。

        落盘位置：应用数据目录下的 reports/。不落工作区 ——
        报告是 Oryx 自己的产出，不该混进用户自己的项目目录。

        用**独占创建**：已存在就跳过，不覆盖。
        覆盖掉上一份 = 用户再也找不到上次那份了。

        写失败不算错误 —— 报告是额外的，编排本身已经成功了。
        """
        from .config import app_dir
        from .workflow import write_new_report

        try:
            directory = app_dir() / "reports"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{run.id}.md"
            return write_new_report(str(path), run.final_answer())
        except Exception:
            return ""

    def _workflow_dispatch(
        self, agent: str, goal: str, deps: dict[str, str]
    ) -> dict[str, Any]:
        """把一个 step 交给子 Agent。

        子 Agent 的白名单、深度闸门、spawn 预算都在 run_subagent 里，
        这里不重复判断 —— 工作流只是"谁来派活"的另一个入口，
        不该成为绕过权限链的后门。
        """
        prefix = "".join(f"{k}: {v}\n\n" for k, v in deps.items())
        return self.run_subagent(agent, (prefix + goal).strip())

    def run_workflow_template(
        self, template_id: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """按一份存好的模板跑编排。

        模板只是"另一条入口" —— 取到 plan 之后原样交给 run_workflow，
        白名单校验、审计、权限全在那边，不在这里重写一遍（重写就一定会漏）。
        """
        tpl = self.store.get_workflow_template(template_id)
        if tpl is None:
            return {"ok": False, "content": f"没有这个模板：{template_id}"}
        plan = tpl.get("plan") or {}
        # 模板存的是用户手写的定义，可能已经过期（子 Agent 被删了）。
        # run_workflow 里的白名单检查会挡住并说清楚缺哪个 —— 那是用户该看到的。
        merged = dict(args or {})
        merged.update(tpl.get("args") or {})
        result = self.run_workflow(plan, merged)
        # 记一次触发：用户要看得见"这份模板我用过几次"。
        # 用模板**真 id** 记，不是传进来的字符串 —— 用户可以用别名跑，
        # 传别名的话这条 UPDATE 匹配不到行，次数就永远停在 0。
        try:
            self.store.mark_workflow_template_run(str(tpl.get("id") or template_id))
        except Exception:
            pass
        return result

    def save_workflow_template(self, item: dict[str, Any]) -> dict[str, Any]:
        """存一份模板。定义先过一遍校验 —— 坏模板不留到运行时才炸。"""
        from .workflow import WorkflowError, WorkflowPlan, plan_read_only

        plan = item.get("plan") or {}
        try:
            WorkflowPlan.parse(plan)
        except WorkflowError as exc:
            return {"ok": False, "content": f"编排定义有问题：{exc}"}
        # 只读分级：从子 Agent 的工具白名单算出来，用户改不了这个结论。
        # 存下来之后，界面上就能区分「这份模板只读」与「这份模板会改东西」。
        item = dict(item)
        item["read_only"] = plan_read_only(plan, self._agent_tool_map())
        # 别名去重 + 去掉空的（空别名会让按别名查模板时匹配到任何东西）
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        item["aliases"] = list(dict.fromkeys(aliases))
        return {"ok": True, "content": self.store.save_workflow_template(item)}

    def _agent_tool_map(self) -> dict[str, list[str]]:
        """{agent_id: 工具白名单}。只读分级与报告判断都要用。"""
        return {
            agent_id: list(spec.tools)
            for agent_id, spec in (self.agents.agents or {}).items()
        }

    def list_workflow_templates(self) -> list[dict[str, Any]]:
        """模板列表。只读标记在这里**重算**，不读存下来的那份。

        为什么重算：子 Agent 的工具白名单可能事后变了 ——
        给 reviewer 加了 fs_write 之后，之前存成「只读」的模板
        就不再只读了。存下来的标记会过期，重算的不会。
        """
        from .workflow import plan_read_only

        templates = self.store.list_workflow_templates()
        tool_map = self._agent_tool_map()
        for item in templates:
            plan = self.store.get_workflow_template(item["id"]) or {}
            item["read_only"] = plan_read_only(plan.get("plan") or {}, tool_map)
        return templates

    def get_workflow_template(self, template_id: str) -> dict[str, Any] | None:
        """模板详情。只读标记同样重算（理由同 list_workflow_templates）。

        id、名字、别名都能当键用 —— store 那边已经按这个顺序查了。
        """
        from .workflow import plan_read_only

        item = self.store.get_workflow_template(template_id)
        if item is None:
            return None
        item["read_only"] = plan_read_only(item.get("plan") or {}, self._agent_tool_map())
        return item

    def workflow_run_plan(self, run_id: str) -> dict[str, Any]:
        """取某次 run 用的 plan —— "把这次运行存成模板"的数据来源。"""
        plan = self.store.get_workflow_run_plan(run_id)
        if plan is None:
            return {"ok": False, "content": f"没有这次运行的编排：{run_id}"}
        return {"ok": True, "content": plan}

    @property
    def active_workflow(self) -> Any:
        """当前正在跑的编排（没有则 None）。

        对外暴露而不是让调用方碰 _workflow —— agentd 要用它拦住
        "删除正在跑的 run" 这种操作。
        """
        return self._workflow

    def workflow_status(self, run_id: str = "") -> dict[str, Any]:
        """查一次运行的进度。step 的完整产出也从这儿取。"""
        active = self._workflow
        if not run_id and active is not None:
            d = active.to_dict()
            # 前端据此决定给不给「存为模板」按钮
            d["has_plan"] = self.store.get_workflow_run_plan(active.id) is not None
            return {"ok": True, "content": d}
        target = run_id
        if not target:
            rows = self.store.workflow_runs(limit=1)
            if not rows:
                return {"ok": True, "content": []}
            target = rows[0]["id"]
        item = self.store.get_workflow_run(target)
        if item is None:
            return {"ok": False, "content": f"没有这次运行：{run_id}"}
        item["steps"] = self.store.workflow_steps(target)
        return {"ok": True, "content": item}

    def workflow_cancel(self, run_id: str = "") -> dict[str, Any]:
        active = self._workflow
        if active is not None and (not run_id or active.id == run_id):
            active.cancel()
            return {"ok": True, "content": f"已请求中止 {active.id}"}
        return {"ok": False, "content": "没有正在跑的工作流"}

    def _record_usage(self, task_id: str, provider_cfg: dict[str, Any], reply: Any) -> None:
        usage = getattr(reply, "usage", None) or {}
        # Anthropic 兼容协议里，缓存命中的 token 单列在 cache_read / cache_creation，
        # 不加进 input_tokens 的话统计会严重偏低（MiniMax 就是这样）。
        input_tokens = (
            int(usage.get("input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0))
        )
        output_tokens = int(usage.get("output_tokens", 0))
        if not input_tokens and not output_tokens:
            return  # 供应商没给用量就不瞎估
        self.store.record_usage(
            task_id,
            provider_cfg.get("name", "?"),
            provider_cfg.get("model", "?"),
            input_tokens,
            output_tokens,
        )
        self.bus.publish(
            "usage.updated",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=provider_cfg.get("model"),
        )

    # ---------------------------------------------------------------- 团队编辑
    @property
    def is_busy(self) -> bool:
        """现在有没有任务在跑（或正要开始跑）。

        这是**唯一**该拿来判断「忙不忙」的地方。
        之前调度器用 `self._active_task_id is not None` ——
        那个字段是「当前/最近一个任务」的句柄，跑完**从不清**，
        于是 is_busy 恒为真，定时任务永远排不上队。
        """
        with self._lock:
            return self._run_in_flight > 0 or self._run_pending > 0

    def is_task_running(self, task_id: str) -> bool:
        """**这个会话**是不是有任务在跑（含已发起、还没开跑的那段窗口）。

        回退要改文件、删消息 —— 如果这一轮的工具调用正在写文件，
        回退和它撞在一起会把内容写成一团。所以回退前要先问这个。

        为什么不直接用 is_busy：那是全局的。别的会话在跑时，
        当前会话的历史回退不该被拦住 —— 回退只影响自己这一份数据。

        2026-11-26（模块 11）：改查 `_runs` 实例表。
        以前靠单槽 `_active_task_id` —— 两个会话并发时，
        后启动的那个会把槽位顶掉，于是「A 在跑」被判成 false，
        A 的检查点回退保护就失效了（能在运行中回退，文件会被写花）。
        """
        tid = str(task_id or "")
        if not tid:
            return False
        with self._lock:
            return any(slot.task_id == tid for slot in self._runs.values())

    def _match_runs(
        self, task_id: str | None, owner_id: str | None,
    ) -> list[_RunSlot]:
        """找出匹配 (task_id, owner_id) 的运行实例。

        匹配口径与老逻辑一致：
          · 给了 task_id → 只认同一个任务的实例
          · 再给了 owner_id → 还得同账号
          · 只给 owner_id → 认同账号的任意实例
          · 都没给 → 全部（老前端按 seq 停，不带 task_id）

        调用方要持有 self._lock。
        """
        out: list[_RunSlot] = []
        for slot in self._runs.values():
            if task_id and slot.task_id != task_id:
                continue
            if owner_id is not None and slot.owner_id != owner_id:
                continue
            out.append(slot)
        return out

    def cancel(
        self, task_id: str | None = None, owner_id: str | None = None,
    ) -> dict[str, Any]:
        """中止正在跑的任务。

        用户点「停止」时调它。做法是给目标运行实例的**专属**中止信号置位：
        模型调用没法从外面硬杀（正在等 HTTP 响应），
        但可以在**每轮之间**检查标志，一轮结束就收手。
        已经跑出来的内容保留 —— 半个答案比没答案有用。

        **只停匹配的那一轮**（模块 11）。
        以前中止标志是全局共享的（一个 Event + 一个全局代号），
        两轮并发时停止 A 会把 B 一起停掉 —— 现在每个运行实例持一份
        自己的信号，按 (task_id, owner_id) 精确定位，互不波及。

        审批等待和两次模型调用之间都仍处于运行态，因此会被及时唤醒；
        过期或不匹配的 task_id 不能触碰任何运行实例的中止标志。

        中止**代号**只在真有任务在跑时才 +1：
        它是「这一轮有人点了停止」的凭据，用来区分
        「上一轮遗留的置位」与「这一轮新点的停止」——
        之前只靠 Event 的置位/未置位，run() 里那句 clear()
        把新点的停止吞掉了（实测 0~20ms 的停止全部失效）。

        没有任务在跑时，这笔中止直接作废 ——
        用户在一个早就结束的任务上误点停止，不该把下一条消息也停掉。
        """
        with self._lock:
            targets = self._match_runs(task_id, owner_id)
            # 等确认的审批也算「有东西在跑」—— 它最长要等 5 分钟，
            # 不算进来的话，点停止会被判成「现在没有正在跑的任务」而直接丢掉，
            # 任务一直杵在等确认上。
            has_approval = False
            if not targets:
                has_approval = self._approval_waits_match(task_id, owner_id)
            in_flight = bool(targets) or has_approval
            # 代号只在真有任务在跑时 +1：代号是「这一轮有人点了停止」的凭据，
            # 没有运行可消费它的话会一直挂着，把下一条消息也停掉。
            if targets:
                for slot in targets:
                    slot.abort.cancel()
        if not in_flight:
            return {"ok": False, "error": "这个会话当前没有正在运行的任务" if task_id else "现在没有正在跑的任务"}
        # 审批等待要靠它立刻醒来；只有完成了 task / owner 匹配才置位。
        #
        # **必须在锁外调**：`_wake_matching_approvals` 自己要拿 `self._lock`，
        # 而那是普通 `threading.Lock`（不可重入）—— 在锁里再拿一次就死锁。
        self._wake_matching_approvals(task_id, owner_id)
        return {"ok": True, "task_id": task_id or self._active_task_id}

    def _wake_matching_approvals(
        self, task_id: str | None, owner_id: str | None,
    ) -> None:
        """唤醒属于 (task_id, owner_id) 的审批等待，让它立刻醒来。

        审批等待最长 5 分钟 —— 不唤醒的话，用户点了停止，
        任务会一直杵在「等确认」上，界面看着像没反应。
        """
        with self._lock:
            steps = [
                step_id for step_id, (wait_task, wait_owner)
                in self._approval_context.items()
                if self._approval_matches(wait_task, wait_owner, task_id, owner_id)
            ]
            events = [self._approvals.get(step_id) for step_id in steps]
        for event in events:
            if event is not None:
                event.set()

    @staticmethod
    def _approval_matches(
        wait_task: str | None, wait_owner: str | None,
        task_id: str | None, owner_id: str | None,
    ) -> bool:
        """一个审批等待是否属于这次中止的目标。

        给了 task_id 就只认同一个任务的等待，再给了 owner_id 还得同账号。
        """
        if task_id:
            if wait_task != task_id:
                return False
            if owner_id is not None and wait_owner != owner_id:
                return False
            return True
        if owner_id is not None:
            return wait_owner == owner_id
        return True

    def mark_run_pending(
        self, task_id: str | None = None, owner_id: str | None = None,
    ) -> None:
        """登记「有任务在排队 / 正要开始跑」。

        内核收到 chat.run 时立刻调它 —— 从 chat.run 到 run() 真正开跑
        之间有个窗口（建 task、查配置、拼提示词、连模型），
        用户在这个窗口点「停止」也得算数。

        为什么不能靠 run() 自己置位：那个窗口里 run() 还没进去，
        cancel() 看不到「有任务在跑」，那笔停止就被丢了。

        2026-11-26（模块 11）：登记的不只是计数，而是一个**运行实例**
        （含自己的中止信号）。停止请求在窗口期到达时，作用在这一个实例上；
        run() 接手的就是同一个实例，不会误伤别的并发运行。
        """
        with self._lock:
            self._run_pending += 1
            self._run_seq += 1
            slot = _RunSlot(self._run_seq, str(task_id or "") or None, owner_id)
            self._runs[slot.rid] = slot
            # 第三个元素是实例 id —— 交接时按它精确取回同一个实例，
            # 中止信号才不会在「登记 → 开跑」之间丢掉。
            self._pending_runs.append((str(task_id or ""), owner_id, slot.rid))

    def team_save(
        self,
        team: str,
        new_name: str = "",
        goal: Any = None,
        members: Any = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """一次性整体保存团队：名字、目标、成员名单一起换。

        前端原来把「改名字 / 删成员 / 加成员 / 改分工」拆成 5+ 次串行请求，
        中途任何一次失败都会留下半保存的团队；单成员团队换人更必败 ——
        旧的先删后加会撞上「至少要留一个成员」。

        这里的关键不是「少发一次请求」，而是**先全部校验、再一次落地**：
        校验没过就一个字节都不写（内存与 SQLite 都不动）。成员整体替换而不
        是逐个增删，所以单成员换人也是合法操作。信箱按 team_id 关联，
        不随成员名单变化而丢失。

        `reason` 给控制面用来精确映射 HTTP 状态码（404/403/409/400），
        不要再靠「error 里有没有『创建者』」这种子串猜。
        """
        obj = self.teams.get(team)
        if obj is None:
            return {"ok": False, "error": f"没有叫「{team}」的团队", "reason": "not_found"}
        if not self.teams.can_edit(team, owner_id):
            return {"ok": False, "error": "只有创建者可以修改这个共享团队", "reason": "forbidden"}

        new_name = (new_name or "").strip()
        if new_name and new_name != team:
            if not new_name or len(new_name) > 80 or "/" in new_name or "\\" in new_name:
                return {"ok": False, "error": "团队名称需为 1–80 个字符，且不能包含斜杠",
                        "reason": "invalid"}
            if self.teams.get(new_name) is not None:
                return {"ok": False, "error": f"已经有叫「{new_name}」的团队了",
                        "reason": "conflict"}

        parsed_members: dict[str, str] | None = None
        if members is not None:
            parsed = parse_members(members)
            if not parsed:
                return {"ok": False, "error": "团队至少要有一个成员", "reason": "invalid"}
            if len(parsed) > MAX_MEMBERS:
                return {"ok": False, "error": f"成员最多 {MAX_MEMBERS} 个，当前 {len(parsed)} 个",
                        "reason": "invalid"}
            ids = [aid for aid, _ in parsed]
            if len(set(ids)) != len(ids):
                return {"ok": False, "error": "成员不能重复", "reason": "invalid"}
            # 只校验「新加入」的成员必须在子 Agent 目录里。
            # 已经保存但目录里查不到的成员标红保留，绝不因为一次改名就自动清掉 ——
            # 与模块 6 的候选选择器同一条原则。
            known = set(self.agents.agents)
            unknown = [aid for aid in ids if aid not in obj.members and aid not in known]
            if unknown:
                return {"ok": False,
                        "error": f"这些子 Agent 不存在：{'、'.join(unknown)}。请先到 Agent 管理里创建",
                        "reason": "invalid"}
            parsed_members = dict(parsed)

        # ── 校验全过，从这里开始才落地 ──────────────────────────────
        if new_name and new_name != team:
            if self.teams.store is not None:
                try:
                    if not self.teams.store.rename_team(team, new_name):
                        return {"ok": False, "error": "改名失败：同名团队已存在",
                                "reason": "conflict"}
                except Exception as exc:
                    return {"ok": False, "error": f"改名失败：{exc}", "reason": "conflict"}
            self.teams._teams.pop(team, None)
            self.teams._teams[new_name] = obj
            obj.name = new_name

        if goal is not None:
            obj.goal = str(goal).strip()
        if parsed_members is not None:
            obj.members = parsed_members

        self.teams._persist_team(obj)
        self.bus.publish("team.updated", team=obj.to_dict(owner_id), _owner_id=owner_id)
        return {"ok": True, "team": obj.to_dict(owner_id), "name": obj.name}

    def team_update(
        self,
        team: str,
        new_name: str = "",
        goal: Any = None,
        member: str = "",
        role: Any = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """改团队的名称、目标或某个成员的分工。

        改名要同时挪字典的键 —— 团队在内存里是按名字索引的，
        只改 Team.name 会让 list() 里出现两个同名团队。
        """
        obj = self.teams.get(team)
        if obj is None:
            return {"ok": False, "error": f"没有叫「{team}」的团队"}
        if not self.teams.can_edit(team, owner_id):
            return {"ok": False, "error": "只有创建者可以修改这个共享团队"}

        if new_name and new_name != team:
            if self.teams.get(new_name) is not None:
                return {"ok": False, "error": f"已经有叫「{new_name}」的团队了"}
            # 落库用 rename —— 团队 id 不变，信箱消息按 team_id 关联，跟着一起活着。
            # 先删后写的话消息全成孤儿。
            if self.teams.store is not None:
                try:
                    if not self.teams.store.rename_team(team, new_name):
                        return {"ok": False, "error": "改名失败：同名团队已存在"}
                except Exception as exc:
                    return {"ok": False, "error": f"改名失败：{exc}"}
            # 内存里换键。只有数据库更新成功后才改内存，避免两边名称不一致。
            self.teams._teams.pop(team, None)
            self.teams._teams[new_name] = obj
            obj.name = new_name

        if goal is not None:
            obj.goal = str(goal).strip()
        if member:
            if member not in obj.members:
                return {"ok": False, "error": f"{member} 不在团队里"}
            obj.members[member] = str(role or "").strip() or "成员"

        self.teams._persist_team(obj)
        self.bus.publish("team.updated", team=obj.to_dict(owner_id), _owner_id=owner_id)
        return {"ok": True, "team": obj.to_dict(owner_id), "name": obj.name}

    def team_remove_member(
        self, team: str, agent: str, owner_id: str | None = None
    ) -> dict[str, Any]:
        """把成员移出团队。队伍至少留一个人 —— 空队伍没有意义。"""
        obj = self.teams.get(team)
        if obj is None:
            return {"ok": False, "error": f"没有叫「{team}」的团队"}
        if agent not in obj.members:
            return {"ok": False, "error": f"{agent} 不在团队里"}
        if len(obj.members) <= 1:
            return {"ok": False, "error": "团队至少要留一个成员"}
        if not self.teams.can_edit(team, owner_id):
            return {"ok": False, "error": "只有创建者可以修改这个共享团队"}
        ok, err = self.teams.remove_member(team, agent, owner_id)
        if not ok:
            return {"ok": False, "error": err}
        obj = self.teams.get(team)
        self.bus.publish("team.updated", team=obj.to_dict(owner_id) if obj else {}, _owner_id=owner_id)
        return {"ok": True, "team": obj.to_dict(owner_id) if obj else {}}

    def _resolve_agent_model(self, wanted: str, fallback: dict[str, Any]) -> dict[str, Any] | None:
        """解析子 Agent 要用的模型。

        两种写法都认：
          · provider 名  —— "deepseek"（用那个服务当前配的模型）
          · 模型名      —— "qwen3:8b"（用提供这个模型的服务）

        找不到返回 None —— 调用方要给出可操作的提示，
        不能静默退回默认模型（那样用户以为用的是 A，实际跑的是 B）。
        """
        wanted = (wanted or "").strip()
        if not wanted:
            return fallback
        providers = self.config.data.get("providers") or {}
        # 先按 provider 名找
        if wanted in providers:
            spec = providers[wanted]
            return {"name": wanted, **spec, "api_key": self.config.provider_api_key(wanted)}
        # 再按「哪个 provider 提供这个模型」找
        for name, spec in providers.items():
            if isinstance(spec, dict) and str(spec.get("model") or "") == wanted:
                return {"name": name, **spec, "api_key": self.config.provider_api_key(name)}
        return None

    def run_subagent(
        self,
        agent_id: str,
        goal: str,
        max_depth: int = 0,
        acceptance: str = "",
    ) -> dict[str, Any]:
        """跑一个受限的子 Agent：专属提示词 + 工具白名单 + 独立上下文。

        ``acceptance`` 是派发契约里的验收判据 —— 「3 个文件均已落盘且返回绝对路径」
        这种可验证的条件。给了判据，子 Agent 才知道什么时候算做完；
        不给的话它可能写一半就收工，主 Agent 只能自己再去查一遍。
        """
        spec = self.agents.get(agent_id)
        if spec is None:
            available = "、".join(a.id for a in self.agents.agents.values()) or "无"
            return {"ok": False, "content": f"没有这个子 Agent：{agent_id}。可用：{available}"}
        # 嵌套深度闸门：防止子 Agent 无限套娃
        depth = max_depth if max_depth > 0 else self._agent_depth + 1
        if depth >= MAX_DEPTH:
            return {
                "ok": False,
                "content": f"子 Agent 嵌套不能超过 {MAX_DEPTH - 1} 层，请自己完成剩余工作",
            }
        # spawn 预算闸门：防止失控扩散
        ok, why = self.spawn_budget.consume()
        if not ok:
            return {"ok": False, "content": why}

        # 工具白名单 ∩ 会话禁用名单 − Agent 自己的禁用名单
        denied = set(spec.disallowed_tools) | set(self._session_disallowed)
        allowed = {
            name: tool for name, tool in self._active_registry.items()
            if name in spec.tools and name not in denied
        }
        if not allowed:
            return {"ok": False, "content": f"子 Agent「{spec.name}」没有可用的工具"}

        # provider 没配好时要给出可操作的提示，不能把 KeyError 抛给用户 ——
        # 工作流里这会让每个 step 都显示成一串看不懂的 "未知 provider：xxx"
        try:
            provider_cfg = self.config.provider_config()
        except Exception as exc:  # noqa: BLE001 — 配置问题要给 human 可读的说法
            return {
                "ok": False,
                "content": f"模型服务未就绪（{exc}）。请先在设置里配置并启用一个 provider。",
            }
        # 子 Agent 可以指定自己的模型。
        #
        # 为什么要有这个：审校、跑批这类活用便宜的小模型就够，
        # 复杂规划才值得上大模型。全部用同一个模型就是浪费。
        #
        # model: inherit 表示跟随主 Agent。写 provider 名或模型名都行：
        # 先按 provider 名找，找不到就按「哪个 provider 提供这个模型」找。
        if spec.model and spec.model.lower() not in ("inherit", ""):
            provider_cfg = self._resolve_agent_model(spec.model, provider_cfg)
            if provider_cfg is None:
                return {
                    "ok": False,
                    "content": (
                        f"子 Agent「{spec.name}」指定了模型「{spec.model}」，但找不到这个模型服务。"
                        "请检查设置里的模型服务，或把 AGENT.md 里的 model 改成 inherit。"
                    ),
                }
        provider = build_provider(provider_cfg)
        system_parts = [
            f"你是子 Agent「{spec.name}」。{spec.description}",
            spec.body,
            "你只能使用被授权的工具。完成后用一段话回报做了什么，不要复述工具输出。",
        ]
        # 派发契约里的验收判据 —— 给了它才知道「做到什么程度算完」。
        if acceptance:
            system_parts.insert(
                2, f"本次任务的完成判据：{acceptance}\n回报里要说明是否满足该判据。"
            )
        # 技能：把 SKILL.md 的正文直接塞进提示词。
        #
        # 以前 skills 字段解析了但从不用 —— 声明了「这个 Agent 会某项技能」，
        # 实际那个 Agent 根本不知道技能内容是什么。
        # 现在它真的把步骤喂进去。
        if spec.skills:
            blocks = []
            for skill_name in spec.skills:
                skill = self.skills.get(skill_name)
                if skill is None:
                    blocks.append(f"（技能「{skill_name}」未找到，请说明并按自己的方式处理）")
                    continue
                # 依赖不满足的技能不注入正文 —— 塞一段走不通的步骤进去，
                # 比不塞更糟：Agent 会以为流程能走完。
                if skill.blocked_reason:
                    blocks.append(
                        f"（技能「{skill_name}」当前不可用：{skill.blocked_reason}。"
                        f"请说明并按自己的方式处理）"
                    )
                    continue
                blocks.append(f"### 技能：{skill.name}\n{skill.body[:MAX_SKILL_CHARS]}")
            if blocks:
                system_parts.append(
                    "你掌握了以下技能，按它们的步骤做事：\n\n" + "\n\n".join(blocks)
                )
        # initialPrompt：每次派活都带的开场提示。
        # 用来固定子 Agent 的行为基调（"先说结论"、"每次改动都要给路径"之类）。
        if spec.initial_prompt:
            system_parts.append(spec.initial_prompt)
        # memory：这个 Agent 专属的长期记忆。
        #
        # 用途：某些角色有"自己的经验"（比如「审校」要记住用户的写作偏好）。
        # 记忆按 tag 过滤，取不到就跳过 —— 不能因为记忆为空就让 Agent 起不来。
        if spec.memory:
            try:
                rows = self.store.search_memories("", limit=20)
                picked = [
                    r for r in rows
                    if spec.memory in str(r.get("tags") or "") or not r.get("tags")
                ]
                if picked:
                    system_parts.append(
                        "你记住的这些事（跨会话有效）：\n"
                        + "\n".join(f"- {r.get('text')}" for r in picked[:10])
                    )
            except Exception:
                pass
        # permissionMode：子 Agent 自己的权限基线。
        #
        # 这是一个**收紧**闸门，不是放宽 —— 子 Agent 只能比主 Agent 更保守。
        # 写 ask 的子 Agent 遇到 L2 动作会挂起回传，等主 Agent 处理；
        # 写 craft 的子 Agent 与主 Agent 同级（但仍受 L3 硬拒约束）。
        if spec.permission_mode and spec.permission_mode.lower() not in ("inherit", ""):
            mode = get_policy(spec.permission_mode)
            if not mode.allows(Risk.L2_DESTRUCTIVE):
                system_parts.append(
                    f"【权限基线：{mode.label}】遇到需要确认的操作时**不要自己动手**，"
                    "把要做什么说清楚，由主 Agent 决定。"
                )
        system = "\n\n".join(system_parts)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": goal}]}
        ]
        schemas = [tool.schema() for tool in allowed.values()]
        final = ""
        # 子 Agent 的权限基线。inherit 表示跟随主 Agent（不做额外限制）。
        # 只有「比主 Agent 更保守」才生效 —— 这是收紧，不是放宽。
        sub_mode: ModePolicy | None = None
        if spec.permission_mode and spec.permission_mode.lower() not in ("inherit", ""):
            candidate = get_policy(spec.permission_mode)
            if not candidate.allows(Risk.L2_DESTRUCTIVE):
                sub_mode = candidate

        self.bus.publish(
            "subagent.started",
            agent=spec.id,
            agent_name=spec.name,
            agent_icon=spec.icon,
            goal=goal,
        )
        self.hooks.handoff(
            kind="subagent", target=spec.id, task_id=self._active_task_id or "",
            goal=goal, depth=depth,
        )
        # 记录当前深度，让子 Agent 里再派子 Agent 时能接着往下数
        outer_depth = self._agent_depth
        self._agent_depth = depth
        try:
            # 子 Agent 也走同一套中止检查 ——
            # 它调的是同一个 provider.chat()，同样可能一直不返回。
            # 不查的话，用户点了停止，主 Agent 收手了，
            # 子 Agent 还在后台接着跑（还会继续发工具调用）。
            #
            # 这里**不消费**中止计数 —— 那件事归主循环的收尾管。
            # 子 Agent 只需要知道「代号变了 = 有人点了停止」。
            #
            # 继承**父运行**的中止信号（模块 11）：子 Agent 是父运行的一部分，
            # 停父运行就该连它一起停；停别的运行则与它无关。
            sub_abort = self._recent_abort()
            sub_cancel_gen = sub_abort.consumed if sub_abort is not None else 0

            def _sub_on_text(chunk: str) -> None:
                # 子 Agent 的正文不往主对话流灌（它有自己的输出通道），
                # 但**必须**查中止 —— 否则这一轮模型调用停不下来。
                if self._is_cancelled(sub_cancel_gen, sub_abort):
                    raise _StreamCancelled("")

            for _turn in range(max(1, spec.max_turns)):
                if sub_abort is not None and sub_abort.is_cancelled(sub_cancel_gen):
                    break
                reply = provider.chat(
                    system, messages, schemas,
                    **self._provider_kwargs(provider, _sub_on_text),
                )
                self._record_usage(self._active_task_id or "", provider_cfg, reply)
                self.hooks.llm_end(
                    task_id=self._active_task_id or "", turn=_turn,
                    model=provider_cfg.get("model"), agent=spec.id,
                )
                if reply.text:
                    final = reply.text
                blocks: list[dict[str, Any]] = []
                if reply.text:
                    blocks.append({"type": "text", "text": reply.text})
                for call in reply.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                    )
                if not blocks:
                    break
                messages.append({"role": "assistant", "content": blocks})
                if not reply.tool_calls:
                    break

                results = []
                for call in reply.tool_calls:
                    if call.name not in allowed:
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call.id,
                                "content": f"工具 {call.name} 不在你的授权范围内",
                                "is_error": True,
                            }
                        )
                        continue
                    # 权限基线闸门：子 Agent 的 permissionMode 只能**收紧**。
                    #
                    # 写 ask / plan 的子 Agent 碰到 L2 动作时不能自己动手 ——
                    # 它没有替用户点头的权限。这不是提示词约定，是这里真的拦住。
                    #
                    # 收紧方向是单向的：主 Agent 在 craft 模式时，
                    # 子 Agent 声明 ask 仍然生效（子 Agent 更保守是允许的）。
                    if sub_mode is not None and not sub_mode.allows(Risk.L2_DESTRUCTIVE):
                        tool_spec = allowed.get(call.name)
                        if tool_spec is not None and tool_spec.risk >= Risk.L2_DESTRUCTIVE:
                            results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": call.id,
                                    "content": (
                                        f"「{call.name}」是需要确认的操作，你的权限基线是"
                                        f"「{sub_mode.label}」，不能自己做。"
                                        "请说明要做什么、影响哪些文件，由主 Agent 决定。"
                                    ),
                                    "is_error": True,
                                }
                            )
                            continue
                    self._seq += 1
                    results.append(self._handle_tool_call(self._active_task_id or "", self._seq, call))
                messages.append({"role": "user", "content": results})
        except _StreamCancelled:
            # 用户点了停止，子 Agent 收手。
            # 不往上抛 —— 抛出去会连主循环一起带走，
            # 而主循环自己也会查到中止标志并收手（那才是该发 RUN_CANCELLED 的地方）。
            # 这里让它安静退出，产出已有一部分就保留。
            pass
        finally:
            self._agent_depth = outer_depth

        # 子代理输出去毒：它可能读过网页/文件里的注入内容，
        # 伪装成系统标签或对话轮次的内容在这里被改写（不删除）。
        from .sanitize import sanitize

        final, poisoned = sanitize(final)

        # 子代理可能 cat 过 .env 或把密钥抄进结论 —— 并入主上下文前打码。
        from .redact import redact

        final = redact(final)

        # 空产出要单独标出来 —— 界面上「没产出」和「一条正常回复」
        # 长得一样的话，用户会以为它说完了但其实什么都没说。
        empty = not final.strip()
        self.bus.publish(
            "subagent.finished",
            agent=spec.id,
            agent_name=spec.name,
            agent_icon=spec.icon,
            text=final,
            empty=empty,
            sanitized=poisoned,
        )
        return {"ok": True, "content": final or "（子 Agent 没有产出文本）"}

    # ------------------------------------------------------------------ 插件与 MCP

    def _init_plugins(self) -> None:
        """扫描 plugins/ 目录，挂载技能，启动被显式授权的 MCP server。

        外部 MCP server 是可执行程序，未在配置里 enable 的一律不启动。
        """
        from .mcp import MCPClient

        # 插件技能已在 __init__ 早期挂进注册表（见 _register_plugin_skills）——
        # 这里只负责 MCP server 的启动。
        if not hasattr(self, "plugins"):
            self._register_plugin_skills()

        # 内置 REA preset：把 rea-agents 作为 MCP server 拉起（特性开关控制）
        from .features import FeatureFlags as _FF
        rea_enabled = True
        rea_cfg = (self.config.data.get("mcp_servers") or {}).get("rea") or {}
        if isinstance(rea_cfg, dict) and "enabled" in rea_cfg:
            rea_enabled = bool(rea_cfg.get("enabled"))
        else:
            try:
                rea_enabled = _FF.load(self.config.features_path()).is_enabled("EnableReaMcp")
            except Exception:
                rea_enabled = False
        if rea_enabled and "rea" not in self.mcp_clients:
            rea_client = MCPClient(
                name="rea",
                command="npx",
                args=["-y", "rea-agents", "mcp"],
                timeout=90.0,  # REA 启动较慢，给足时间
            )
            try:
                rea_client.start()
                self.mcp_clients["rea"] = rea_client
            except Exception as exc:
                self.plugin_errors.append(
                    "rea preset: 启动失败（请确保已安装 Node.js ≥22；运行 npx -y rea-agents setup）: "
                    f"{type(exc).__name__}: {exc}"
                )

        enabled = self.config.data.get("mcp_servers") or {}
        for plugin in self.plugins:
            for name, spec in plugin.mcp_servers.items():
                conf = enabled.get(name) or {}
                if not conf.get("enabled"):
                    continue
                if not spec.get("command"):
                    self.plugin_errors.append(f"{name}: 清单里没有 command")
                    continue
                client = MCPClient(
                    name=name,
                    command=spec["command"],
                    args=spec.get("args") or [],
                    env=spec.get("env") or {},
                    cwd=spec.get("cwd"),
                )
                try:
                    client.start()
                    self.mcp_clients[name] = client
                except Exception as exc:
                    self.plugin_errors.append(f"{name}: {exc}")

        self._load_mcp_config_servers()
        self._register_mcp_tools()

    def _load_mcp_config_servers(self) -> None:
        """从 .mcp.json 三个作用域加载 MCP 服务器。

        外部 MCP server 是可执行程序，**不会自动启动**，必须显式启用：
            config.json 的 mcp_servers[name].enabled = true
        未启用的只登记不启动。
        """
        from .mcp import build_client
        from .mcpconfig import load_servers, tool_defer_map

        workspace = self.config.data.get("workspace") or ""
        warnings: list[str] = []
        try:
            servers = load_servers(workspace, warnings)
        except Exception as exc:
            self.plugin_errors.append(f"读取 .mcp.json 失败：{exc}")
            return
        for warning in warnings:
            self.plugin_errors.append(warning)

        enabled = self.config.data.get("mcp_servers") or {}
        self.mcp_defer = tool_defer_map(servers)

        for name, spec in servers.items():
            conf = enabled.get(name) or {}
            if not conf.get("enabled"):
                continue
            if name in self.mcp_clients:
                continue
            try:
                client = build_client(spec)
                client.start()
                self.mcp_clients[name] = client
            except Exception as exc:
                self.plugin_errors.append(f"{name}: {exc}")


    def mcp_status(self) -> dict[str, Any]:
        """MCP 服务器清单 —— 给界面看的。

        以前这些信息**只能从配置文件里看**：summarize() 写好了却没人调用，
        界面上也没有 MCP 页。用户装了 MCP server 之后，
        根本不知道「它到底启用没有、提供了哪些工具」。

        现在它是一个接口 —— 用户能看见、能启停、能看工具数。
        """
        from .mcpconfig import load_servers, summarize

        workspace = self.config.data.get("workspace") or ""
        warnings: list[str] = []
        try:
            servers = load_servers(workspace, warnings)
        except Exception as exc:
            return {"servers": [], "warnings": [f"读取 .mcp.json 失败：{exc}"], "errors": list(self.plugin_errors)}

        enabled = self.config.data.get("mcp_servers") or {}
        items = summarize(servers)
        for item in items:
            name = item["name"]
            conf = enabled.get(name) or {}
            item["enabled"] = bool(conf.get("enabled"))
            item["running"] = name in self.mcp_clients
            client = self.mcp_clients.get(name)
            if client is not None:
                item["live_tools"] = len(getattr(client, "tools", []) or [])
                item["server_info"] = getattr(client, "server_info", {}) or {}
            else:
                item["live_tools"] = 0
                item["server_info"] = {}
        return {
            "servers": items,
            "warnings": warnings,
            "errors": list(self.plugin_errors),
        }

    def mcp_set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        """启用或停用一个 MCP 服务器。

        启用后会**立刻重载** MCP —— 以前 MCP 只在内核启动时加载一次，
        用户在配置里打开 enabled 也不起作用，得重启内核。
        """
        name = (name or "").strip()
        if not name:
            return {"ok": False, "content": "服务器名不能为空"}

        servers = self.config.data.setdefault("mcp_servers", {})
        conf = servers.setdefault(name, {})
        conf["enabled"] = bool(enabled)
        try:
            self.config.save()
        except Exception as exc:
            return {"ok": False, "content": f"保存配置失败：{exc}"}

        self.reload_mcp()
        return {"ok": True, "content": f"{'已启用' if enabled else '已停用'} {name}"}

    def reload_mcp(self) -> None:
        """重载 MCP：关掉旧的、启动新启用的、重新注册工具。

        停用的服务器要**真的关掉** —— 否则它还在跑，白占资源，
        而且工具还留在注册表里，用户以为停用了其实没停。
        """
        # 先摘掉旧的 MCP 工具
        for name in list(self.registry):
            if name.startswith("mcp__"):
                self.registry.pop(name, None)

        # 关掉不再启用的 client
        enabled = self.config.data.get("mcp_servers") or {}
        for name in list(self.mcp_clients):
            if not (enabled.get(name) or {}).get("enabled"):
                try:
                    self.mcp_clients.pop(name).close()
                except Exception:
                    self.mcp_clients.pop(name, None)

        self.plugin_errors = [e for e in self.plugin_errors if not e.startswith("mcp")]
        self._load_mcp_config_servers()
        self._register_mcp_tools()
        # 工具变了，语义标注也要重做一遍
        self._annotate_semantics()

    def shutdown(self) -> None:
        # 先收终端子进程：不收的话它们会脱离内核继续跑，变成看不见的孤儿
        try:
            self.terminal.shutdown()
        except Exception:
            pass
        for client in list(self.mcp_clients.values()):
            try:
                client.close()
            except Exception:
                pass
        self.mcp_clients.clear()

    # ------------------------------------------------------------------ 任务清单
    def _publish_todos(self, task_id: str) -> None:
        self.bus.publish(
            "todos.updated", task_id=task_id, todos=self.store.list_todos(task_id)
        )

    # ------------------------------------------------------------------ 思考过程
    @staticmethod
    def _provider_kwargs(provider: Any, on_text=None, on_reasoning=None) -> dict[str, Any]:
        """构造 provider.chat() 的关键字参数。

        这里必须**探测** provider 支不支持 on_reasoning —— 自定义 provider
        （插件、测试替身）可能还是旧的 chat(system, messages, tools, on_text)
        签名，直接传 on_reasoning 会 TypeError，整个任务就废了。
        """
        kwargs: dict[str, Any] = {}
        if on_text is not None:
            kwargs["on_text"] = on_text
        try:
            params = inspect.signature(provider.chat).parameters
            supports_reasoning = "on_reasoning" in params or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            supports_reasoning = False
        if on_reasoning is not None and supports_reasoning:
            kwargs["on_reasoning"] = on_reasoning
        return kwargs

    def _call_provider(
        self,
        provider: Any,
        system: str,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        task_id: str,
        message_id: str,
        cancel_gen: int = 0,
        abort: "_RunAbort | None" = None,
    ) -> Any:
        """调模型，把正文与思考过程分别接到事件流上。

        **中止检查放在流式回调里** —— 每来一个 chunk 就查一次，
        命中就抛 _StreamCancelled 把这次模型调用打断。
        只能在轮首/轮末查的话，中止要等整个模型调用跑完才生效；
        本机挂的 Base 模型不会终止生成，一次能连吐好几分钟 ——
        症状是「点了停止，界面一直转圈，状态永远停在 running」。
        """
        partial: list[str] = []

        def _on_text(chunk: str) -> None:
            if not chunk:
                return
            partial.append(chunk)
            # 先把这段推进事件流再查中止 ——
            # 顺序反了的话，界面上看到的正文与库里存的正文会差最后一段。
            self.bus.text_delta(task_id, message_id, chunk)
            if self._is_cancelled(cancel_gen, abort):
                raise _StreamCancelled("".join(partial))

        def _on_reasoning_chunk(chunk: str) -> None:
            # 思考通道也要能打断 —— 本机挂的 qwen3 系列先吐一大段 reasoning，
            # 只看正文的话，那段思考期间点停止照样停不下来。
            self._on_reasoning(task_id, chunk)
            if self._is_cancelled(cancel_gen, abort):
                raise _StreamCancelled("".join(partial))

        kwargs = self._provider_kwargs(provider, _on_text, _on_reasoning_chunk)
        return provider.chat(system, messages, schemas, **kwargs)

    def _on_reasoning(self, task_id: str, chunk: str) -> None:
        """把模型的思考过程转成事件流。

        对齐 Marvis 的做法：思考是一个**独立于正文**的通道，前端拿它渲染
        一个可折叠的「正在思考中… / 已思考完成」面板。开始和结束各发一次
        标记，中间是增量文本。

        事件名用 REASONING_START / REASONING_CONTENT / REASONING_END ——
        events.py 里已经定义好了，只是内核一直没调用。
        """
        if not chunk:
            return
        if not self._reasoning_open.get(task_id):
            self._reasoning_open[task_id] = True
            self.bus.reasoning_start(task_id)
        self.bus.reasoning_delta(task_id, chunk)

    def _end_reasoning(self, task_id: str) -> None:
        """关闭思考通道（这一轮模型调用结束）。"""
        if self._reasoning_open.pop(task_id, None):
            self.bus.reasoning_end(task_id)

    def _task_tools(self, task_id: str) -> dict[str, ToolSpec]:
        """多步任务的可视化清单。只在允许动手的模式下注册。"""

        def _create(title: str) -> dict[str, Any]:
            todo = self.store.add_todo(task_id, title)
            self._publish_todos(task_id)
            return {"ok": True, "content": todo}

        def _update(id: str, status: str) -> dict[str, Any]:
            if status not in ("pending", "in_progress", "done"):
                return {
                    "ok": False,
                    "content": "status 只能是 pending / in_progress / done",
                }
            todo = self.store.update_todo(task_id, id, status)
            if todo is None:
                return {"ok": False, "content": f"没有找到任务项：{id}"}
            self._publish_todos(task_id)
            return {"ok": True, "content": todo}

        def _list() -> dict[str, Any]:
            return {"ok": True, "content": self.store.list_todos(task_id)}

        def _rewind(checkpoint: str, scope: str = "CodeAndConversation") -> dict[str, Any]:
            result = self.checkpoints.rewind(checkpoint, scope)
            if result.get("ok"):
                self.bus.publish("checkpoint.rewound", task_id=task_id,
                                 checkpoint=checkpoint, scope=scope,
                                 content=result["content"])
            return result

        def _ckpt_status() -> dict[str, Any]:
            return {"ok": True, "content": self.checkpoints.status(task_id)}

        return {
            "task_create": ToolSpec(
                name="task_create",
                description="为多步任务新建一个待办项，用户能在界面上看到进度。",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string", "description": "这一步要做什么"}},
                    "required": ["title"],
                },
                risk=Risk.L0_READ,
                fn=_create,
            ),
            "task_update": ToolSpec(
                name="task_update",
                description="更新待办项状态。开始做时标 in_progress，做完立刻标 done。",
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done"],
                        },
                    },
                    "required": ["id", "status"],
                },
                risk=Risk.L0_READ,
                fn=_update,
            ),
            "task_list": ToolSpec(
                name="task_list",
                description="列出当前任务的待办清单和状态。",
                parameters={"type": "object", "properties": {}, "required": []},
                risk=Risk.L0_READ,
                fn=_list,
            ),
            "checkpoint_rewind": ToolSpec(
                name="checkpoint_rewind",
                description=(
                    "回退到某个检查点，把 Agent 改错的东西撤回去。"
                    "先用 checkpoint_status 看有哪些检查点，再指定 id 回退。"
                    "scope=Code 只还原文件，Conversation 只删后续对话，"
                    "CodeAndConversation 两者都退。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "checkpoint": {"type": "string", "description": "检查点 id"},
                        "scope": {
                            "type": "string",
                            "enum": ["Code", "Conversation", "CodeAndConversation"],
                            "description": "回退范围，默认两者都退",
                        },
                    },
                    "required": ["checkpoint"],
                },
                risk=Risk.L2_DESTRUCTIVE,
                fn=_rewind,
            ),
            "checkpoint_status": ToolSpec(
                name="checkpoint_status",
                description="列出当前任务有哪些检查点，可以回退到哪一个。",
                parameters={"type": "object", "properties": {}, "required": []},
                risk=Risk.L0_READ,
                fn=_ckpt_status,
            ),
        }

    # ------------------------------------------------------------------ 审批
    def wait_approval(
        self, step_id: str, timeout: float | None = None,
        task_id: str | None = None, owner_id: str | None = None,
        tool: str = "", plan: dict[str, Any] | None = None,
        risk: int = 0,
        abort: "_RunAbort | None" = None,
    ) -> bool:
        timeout = APPROVAL_TIMEOUT if timeout is None else timeout
        event = threading.Event()
        # 归属账号优先取本轮运行实例的账号 —— 不能用全局单槽
        # （并发时另一个会话会把它顶掉，审批就记到别人名下了）。
        run_owner = owner_id or self._active_owner_id
        with self._lock:
            self._approvals[step_id] = event
            self._approval_results[step_id] = False
            # 记下这个等待隶属于谁 —— cancel(task_id=…, owner_id=…) 只唤醒匹配的等待。
            self._approval_context[step_id] = (task_id or None, run_owner)
            self._approval_details[step_id] = {
                "step_id": step_id,
                "task_id": task_id or "",
                "owner_id": run_owner,
                "tool": tool,
                "plan": plan or {},
                "risk": int(risk),
                "started_at": time.time(),
            }
        # 审批是最长的一处等待（默认 5 分钟）。用户点「停止」要能立刻打断它，
        # 否则点了没反应 —— 任务还杵在等确认上。
        # 轮询而不是等中止 Event：那个 Event 是本轮专属的，
        # 在这里 clear/set 会跟主循环抢。
        #
        # 判据用**本轮专属**的中止信号（模块 11）：
        # 以前查的是全局 event，停止 A 会把 B 的审批等待也一起打断。
        target = abort if abort is not None else self._current_abort
        deadline = time.time() + timeout
        triggered = False
        while time.time() < deadline:
            if event.wait(0.2):
                triggered = True
                break
            if target is not None and target.event.is_set():
                break
        with self._lock:
            approved = self._approval_results.pop(step_id, False)
            self._approvals.pop(step_id, None)
            self._approval_context.pop(step_id, None)
            self._approval_details.pop(step_id, None)
        return bool(triggered and approved)

    def pending_approvals(
        self, task_id: str | None = None, owner_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """当前正在等确认的审批 —— 页面刷新 / SSE 断线后的兜底数据源。

        只返回展示所需字段：内部的 Event 与结果标志不外泄。
        过滤口径与 cancel() 一致：给了 task_id 就只看这个会话，
        再给了 owner_id 还得是同账号（敏感的工具参数不能跨账号看到）。
        """
        with self._lock:
            items = [dict(item) for item in self._approval_details.values()]
        out = []
        for item in items:
            if task_id and item.get("task_id") != task_id:
                continue
            if owner_id is not None and item.get("owner_id") != owner_id:
                continue
            out.append(item)
        return out

    def _approval_waits_match(
        self, task_id: str | None, owner_id: str | None,
    ) -> bool:
        """有没有正在等确认的审批，隶属于这次中止要停的那个任务。

        task_id / owner_id 的匹配口径与 cancel() 里对运行实例的一致：
        给了 task_id 就只认同一个任务的等待，再给了 owner_id 还得同账号。
        调用方要持有 self._lock。
        """
        for wait_task, wait_owner in self._approval_context.values():
            if task_id:
                if wait_task != task_id:
                    continue
                if owner_id is not None and wait_owner != owner_id:
                    continue
            elif owner_id is not None and wait_owner != owner_id:
                continue
            return True
        return False

    def resolve_approval(self, step_id: str, approved: bool) -> bool:
        with self._lock:
            event = self._approvals.get(step_id)
            if not event:
                return False
            self._approval_results[step_id] = approved
            detail = dict(self._approval_details.get(step_id) or {})
        event.set()
        # 批准/拒绝可能来自另一个页面或另一个人 —— 广播一次，
        # 所有还亮着审批卡的界面都能立刻撤掉，不必等 5 分钟超时。
        self.bus.publish(
            "step.approval_resolved",
            task_id=str(detail.get("task_id") or ""),
            step_id=step_id,
            approved=bool(approved),
            _owner_id=detail.get("owner_id"),
        )
        return True

    # ------------------------------------------------------------------ 主循环
    def run(
        self,
        goal: str,
        mode: str | None = None,
        interaction: str | None = None,
        style: str | None = None,
        expert: str | None = None,
        session_id: str | None = None,
        question_kept: bool = False,
        pending_slot: bool = False,
        resume_brief: str = "",
        resume_task_id: str = "",
        owner_id: str | None = None,
        agent_id: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> str:
        """跑一轮 Agent。

        这一层是**薄包装**：只管「忙碌计数」和「中止信号」的进出，
        真正跑起来的事交给 _run_loop。
        拆开是为了保证计数绝对平衡 —— 之前计数写在 run() 的中段，
        搭环境那段（建 task、连模型、拼提示词）抛异常就会漏掉收尾，
        忙碌标志永远挂着，定时任务从此再也没排上队。

        **中止信号是本轮专属的**（模块 11）：本轮从 `_runs` 实例表里
        领一个 `_RunSlot`，用它的 `_RunAbort` 做判定。
        别的并发运行有各自的一份，停止谁都不波及别人。
        """
        # **先**记下本轮的中止代号，再开始搭这一轮的环境。
        #
        # 顺序很重要：中止判定如果只靠 Event 的置位/未置位，
        # 就分不清「上一轮遗留的置位」与「这一轮新点的停止」。
        # 原来 run() 在搭环境的中段才 clear() 抹掉遗留置位 ——
        # 而 clear() 和「用户刚好在这个窗口里点停止」是竞态的：
        # cancel() 置上的标志位被随后的 clear() 抹掉，这一轮照跑到底。
        # 实测延迟 0~20ms 的 stop 全部丢失，只有 80ms 之后才生效。
        #
        # 改用代号就没有这个竞态。
        #
        # 本轮的起始代号 = 上一轮消费到的位置。
        # 之后只要代号变了（cancel() 每次 +1），就是「这轮有人点了停止」。
        #
        # 还有一种更早的情况：**中止请求比任务起步还先到**
        # （用户点发送后立刻点停止，cancel 先到内核，chat.run 后到）。
        # 那笔请求要冲这一轮来 —— 所以起始代号取「已被消费的最大值」，
        # 未被消费的那笔自然落在本轮区间里。
        #
        # 但「还没被消费」不等于「是冲这一轮来的」：用户在一个早就结束的任务上
        # 误点停止，那笔请求会一直挂着，把下一条消息也停掉。
        # 所以要看时间 —— 超过 CANCEL_PENDING_WINDOW 的旧请求直接作废。
        with self._lock:
            previous_owner_id = self._active_owner_id
            active_task_id = session_id or resume_task_id
            # 「正要开始跑」那个槽位交接给「正在跑」——
            # 同一个锁里做完，中间不留「两边都是 0」的缝，
            # 不然用户恰好在这会儿点停止，那一下会被当成「没有任务在跑」。
            slot = self._claim_run_slot(active_task_id, owner_id, pending_slot)
            abort = slot.abort
            cancel_gen = abort.consumed
            pending_cancel = False
            if abort.generation > abort.consumed:
                if (time.time() - abort.at) <= CANCEL_PENDING_WINDOW:
                    pending_cancel = True
                else:
                    # 旧请求作废：把它算进本轮的起始代号，别让本轮被它停掉
                    cancel_gen = abort.generation
                # 无论新不旧，这一笔都算本轮消费掉了
                abort.consumed = abort.generation
            self._run_in_flight += 1
        # 运行上下文切到**本线程**（模块 11）。
        # 嵌套起跑（定时任务在调度线程里同步跑）时，退出时要还原回外层的值。
        self._active_owner_id = owner_id
        if active_task_id:
            self._active_task_id = active_task_id
        self._tls.abort = abort
        try:
            return self._run_loop(
                goal, mode, interaction, style, expert, session_id,
                question_kept, cancel_gen, pending_cancel,
                resume_brief, resume_task_id, owner_id, agent_id, config_snapshot,
                abort=abort,
            )
        finally:
            # 这一轮收尾：
            #   · 中止请求都算已消费 —— 不清的话，用户在上一轮点的那一下
            #     会一直挂着，把下一条消息也停掉（症状是「第二条消息秒停」）
            #   · 忙碌计数还回去 —— 不还的话定时任务永远排不上队，
            #     而且用户在旧任务上点停止会被当成「有任务在跑」
            #   · **注销本轮的运行实例** —— 它的中止信号随实例一起消失，
            #     不会漏到别的运行上（模块 11）
            with self._lock:
                self._runs.pop(slot.rid, None)
                if self._run_in_flight > 0:
                    self._run_in_flight -= 1
            # 运行上下文还原：这一轮结束后，本线程回到「没有运行」的状态。
            # 嵌套起跑时还原回外层那一轮的值。
            self._active_owner_id = previous_owner_id
            self._tls.abort = None

    def _claim_run_slot(
        self, task_id: str | None, owner_id: str | None, pending_slot: bool,
    ) -> _RunSlot:
        """领走本轮该用的运行实例。

        两种来源：
          · `pending_slot=True` —— 内核收到 chat.run 时已经登记过，
            这里把那个实例从「正要开始跑」交接成「正在跑」；
            中止信号是同一个，所以窗口期点的停止照样算数。
          · 没有登记过 —— 自己新建一个（内部调用路径）。

        交接必须在同一个锁里做完，中间不留「两边都是 0」的缝，
        不然用户恰好在这会儿点停止，那一下会被当成「没有任务在跑」。

        调用方要持有 self._lock。
        """
        if pending_slot and self._run_pending > 0:
            self._run_pending -= 1
            pending_index = next((
                i for i, (pending_id, pending_owner, _rid) in enumerate(self._pending_runs)
                if pending_id == str(task_id or "") and pending_owner == owner_id
            ), None)
            if pending_index is None:
                # Older/internal callers register a pending slot without an id.
                pending_index = next((
                    i for i, (pending_id, pending_owner, _rid) in enumerate(self._pending_runs)
                    if not pending_id and pending_owner is None
                ), None)
            if pending_index is not None:
                rid = self._pending_runs.pop(pending_index)[2]
                slot = self._runs.get(rid)
                if slot is not None:
                    slot.phase = "running"
                    return slot
        # 没有预先登记（内部调用路径）—— 自己建一个。
        self._run_seq += 1
        slot = _RunSlot(self._run_seq, str(task_id or "") or None, owner_id)
        self._runs[slot.rid] = slot
        slot.phase = "running"
        return slot

    def _is_cancelled(self, cancel_gen: int, abort: "_RunAbort | None" = None) -> bool:
        """这一轮要不要因为「用户点了停止」而收手。

        判据是「中止代号变了」，不是「Event 被置位」——
        后者分不清上一轮遗留的置位与本轮新点的停止（见 run() 开头）。

        `abort` 是**本轮专属**的中止信号（模块 11）。不传就用当前线程的 ——
        传错的话会查到别的运行的中止状态（并发时就是误伤）。
        """
        target = abort if abort is not None else self._current_abort
        if target is None:
            return False
        return target.is_cancelled(cancel_gen)

    def _finish_cancelled(self, task_id: str, final_text: str) -> str:
        """任务被用户停掉：标状态、发事件、收尾。

        抽出来是因为「轮首」和「轮末」两个地方都要做同一套动作 ——
        少做一处就会出现「按了停止，任务还是跑完了」。
        """
        self.store.set_task_status(task_id, "cancelled")
        # 事件名要跟 AG-UI 家族一致（大写 RUN_*）——
        # 前端是按大小写精确匹配的，写成小写 run.cancelled
        # 前端的 case 'RUN_CANCELLED' 永远匹配不上，
        # 停止功能的事件接线等于没接。
        self.bus.publish("RUN_CANCELLED", task_id=task_id, text=final_text)
        self.hooks.end(task_id=task_id, status="cancelled", text=final_text)
        return final_text or "（已停止）"

    def _run_loop(
        self,
        goal: str,
        mode: str | None,
        interaction: str | None,
        style: str | None,
        expert: str | None,
        session_id: str | None,
        question_kept: bool,
        cancel_gen: int,
        pending_cancel: bool,
        resume_brief: str = "",
        resume_task_id: str = "",
        owner_id: str | None = None,
        agent_id: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        abort: "_RunAbort | None" = None,
    ) -> str:
        """run() 的主体：搭环境 + 跑主循环。

        拆出来是为了让 run() 那层薄包装能兜住**所有**异常路径 ——
        这里的任何一处抛出，run() 的 finally 都会把忙碌计数还回去。

        ``resume_task_id`` 非空表示这是崩溃恢复：复用同一个 task，
        把已完成步骤的 brief 注入系统提示，从断点继续而不是整体重跑。
        """
        # 断点续跑：崩溃前那个 task 的 status 还停在 running，
        # 它的消息、步骤、检查点都还在库里，直接续上。
        if resume_task_id:
            session_id = session_id or resume_task_id
        mode = mode or self.config.mode
        interaction = (interaction or self.config.data.get("interaction") or "craft").lower()
        style_name = (style or self.config.data.get("style") or "professional").lower()
        if isinstance(config_snapshot, dict):
            agent_id = config_snapshot.get("agent_id") or None
            expert_id = config_snapshot.get("expert_id") or None
        else:
            expert_id = expert if expert is not None else self.config.data.get("expert", "")
        mode_policy = get_policy(interaction)
        style_obj = get_style(style_name)
        agent_obj = _agent_from_snapshot((config_snapshot or {}).get("agent"))
        if agent_obj is None:
            agent_obj = self.agents.get(agent_id)
        expert_obj = _expert_from_snapshot((config_snapshot or {}).get("expert"))
        if expert_obj is None:
            expert_obj = self.experts.get(expert_id)
        if agent_obj and agent_obj.permission_mode and agent_obj.permission_mode.lower() not in ("inherit", ""):
            try:
                candidate_policy = get_policy(agent_obj.permission_mode)
                if not candidate_policy.allows(Risk.L2_DESTRUCTIVE):
                    mode_policy = candidate_policy
            except ValueError:
                raise ValueError(f"子 Agent「{agent_obj.name}」的权限模式无效：{agent_obj.permission_mode}") from None

        provider_cfg = self.config.provider_config()
        if agent_obj and agent_obj.model and agent_obj.model.lower() not in ("inherit", ""):
            provider_cfg = self._resolve_agent_model(agent_obj.model, provider_cfg)
            if provider_cfg is None:
                raise ValueError(f"子 Agent「{agent_obj.name}」指定的模型服务不存在：{agent_obj.model}")
        # 运行时模型路由：默认关闭；开启后按本轮需求的复杂度 / 成本 / 网络条件
        # 从**配置里真实存在**的 provider 里选一个，并发事件让界面看得见。
        # 任何异常都退回当前 provider —— 路由不能变成新的失败点。
        try:
            from .routing import route

            decision = route(self.config, goal, tools_count=len(self.registry))
            if decision.get("switched"):
                provider_cfg = self.config.provider_config_for(decision["name"])
                self.bus.publish("model.routed", **decision)
        except Exception:
            pass
        provider = build_provider(provider_cfg)

        # 对话续接：用户没点「新建对话」就一直在同一个会话里。
        # 实现是**复用同一个 task** —— 消息自然累积在一条会话下，
        # 会话列表里也只出现一条，而不是每说一句多一个条目。
        task_id = ""
        if session_id:
            try:
                if self.store.get_task(session_id, owner_id) is not None:
                    task_id = session_id
            except Exception:
                task_id = ""
        fresh = not task_id
        if fresh:
            task_id = self.store.create_task(
                goal, mode, provider_cfg.get("name", "?"), owner_id=owner_id,
                agent_id=agent_id, expert_id=expert_id,
                config_snapshot=config_snapshot,
            )
        else:
            self.store.set_task_status(task_id, "running")
            if config_snapshot:
                self.store.set_task_config(task_id, agent_id, expert_id, config_snapshot)
        self.store.set_task_provider(task_id, str(provider_cfg.get("name") or "?"))

        # 本次运行的上下文：工具口径 + 交互模式 + 任务 id
        registry = self._exposed_registry()
        if mode_policy.allow_write:
            registry.update(self._task_tools(task_id))
        # 委派模式：只暴露协调类工具，执行类不进模型上下文
        mode_tools = allowed_tools(interaction)
        if mode_tools is not None:
            registry = {n: s for n, s in registry.items() if n in mode_tools}
        if agent_obj is not None:
            # A selected definition is the run profile: its tool allowlist is a hard bound.
            allowed = set(agent_obj.tools)
            denied = set(agent_obj.disallowed_tools)
            registry = {n: spec for n, spec in registry.items() if n in allowed and n not in denied}
        self._active_registry = registry
        self._active_interaction = interaction
        self._active_task_id = task_id
        # 中止标志不能在这里清 —— 清除会与「用户此刻点停止」竞态。
        # 判定改用中止代号（见 run() 开头的注释）。

        # 检查点打在用户消息之前：这样回退到它 = 这一轮完全没发生。
        # seq 传 0 = 让 store 自己数这个会话的第几个点 —— 调用方自己数会错，
        # 错一位整个回退范围就错位（会把更早那轮改的文件也退掉）。
        checkpoint_id = self.checkpoints.mark(task_id, 0, goal[:60])

        # 对话续接：把本会话**已有**的历史接在当前提问之前。
        # 必须在 add_message 之前取 —— 否则会把自己这条提问也带进去。
        # 只取 user/assistant 两种角色（工具结果不进上下文 —— 它们属于
        # 上一轮的执行细节，重新走一遍工具比把旧结果塞回去更准）。
        history: list[dict[str, Any]] = []
        if not fresh:
            try:
                for row in self.store.messages_for_task(task_id):
                    role = row.get("role")
                    content = (row.get("content") or "").strip()
                    if role not in ("user", "assistant") or not content:
                        continue
                    history.append({"role": role, "content": content})
            except Exception:
                history = []

        # 崩溃恢复时那条提问已经在库里了（add_message 在崩溃前就写完了）。
        # 不能重写一遍，否则库里出现 user, user, assistant 三条，
        # 界面上会看到用户的话被复读。
        if (
            resume_task_id
            and history
            and history[-1].get("role") == "user"
            and str(history[-1].get("content") or "").strip() == goal.strip()
        ):
            question_kept = True

        # 「重新生成」时，那条提问还留在库里（prepare_retry 刻意保留了它）。
        # 这时不能再写一遍 —— 库里就会出现 user, user, assistant 三条，
        # 用户看到自己的话被复读了一次。
        #
        # 顺带把那条提问从**历史**里摘掉：它是"当前这一轮"，
        # 后面会单独作为本轮输入接上，留在历史里等于说了两遍。
        #
        # 只摘**最后一条**匹配的 —— 用户可能问过一模一样的话，
        # 把早先那条也删掉就丢上下文了。
        if question_kept:
            for i in range(len(history) - 1, -1, -1):
                if history[i]["role"] == "user" and history[i]["content"] == goal.strip():
                    history.pop(i)
                    break
        else:
            self.store.add_message(task_id, "user", goal)

        # ---- 长会话上下文预算 ----
        # 以前这里全量取历史、不截断，几百轮下来直接撑爆上下文窗口。
        # 裁掉的老历史会变成一段摘要进系统提示，而不是无声消失。
        history_summary = ""
        try:
            from .context import resolve_budget, trim_history, trim_report

            budget = resolve_budget(self.config, provider_cfg)
            keep_recent = int(self.config.data.get("context_keep_recent") or 8)
            kept, history_summary = trim_history(history, budget, keep_recent)
            if len(kept) != len(history):
                self.bus.publish(
                    "context.trimmed", task_id=task_id,
                    budget=budget, **trim_report(history, kept),
                )
            history = kept
        except Exception:
            # 裁剪失败宁可用原始历史，也不能把这一轮搞挂
            pass

        self.bus.run_started(
            task_id,
            goal=goal,
            mode=mode,
            interaction=interaction,
            style=style_name,
            expert=expert_obj.id if expert_obj else None,
            provider=provider_cfg.get("name"),
            model=provider_cfg.get("model"),
            checkpoint=checkpoint_id,
        )
        self.hooks.start(
            task_id=task_id, goal=goal, mode=mode, interaction=interaction,
            style=style_name, expert=expert_obj.id if expert_obj else None,
            provider=provider_cfg.get("name"), model=provider_cfg.get("model"),
        )

        memory_block = self._memory_block()
        system_parts = [
            # 系统提示正文来自 prompts/system/*.md（loader 负责拼装）。
            # 变量在这里填 —— Agent 清单与技能目录是运行时才知道的。
            #
            # 技能目录按**本轮 query + 上下文预算**裁剪：
            # 技能装多了，全量列出来就是几千 token 的纯浪费。
            # 裁剪后会写明「还有多少个未列出」，模型才知道能用 use_skill 去取。
            load_system_prompt(
                agents=self.agents.prompt_block(),
                skills=self.skills.catalog(
                    query=goal,
                    limit_tokens=self._skill_catalog_budget(),
                ),
            ),
            mode_policy.prompt,
            style_obj.prompt,
        ]
        if memory_block:
            system_parts.insert(1, memory_block)
        try:
            always_rules = self.memory.always_rules()
        except Exception:
            always_rules = ""
        if always_rules:
            system_parts.insert(1, always_rules)
        if expert_obj is not None:
            system_parts.insert(1, self.experts.prompt_block(expert_obj))
        if agent_obj is not None:
            selected_parts = [
                f"你现在以子 Agent「{agent_obj.name}」的定义执行本轮任务。{agent_obj.description}",
                agent_obj.body,
                "只能使用该定义授权的工具；完成后直接回应用户。",
            ]
            if agent_obj.initial_prompt:
                selected_parts.append(agent_obj.initial_prompt)
            if agent_obj.skills:
                selected_skills = []
                for skill_name in agent_obj.skills:
                    skill = self.skills.get(skill_name)
                    if skill and not skill.blocked_reason:
                        selected_skills.append(f"### 技能：{skill.name}\n{skill.body[:MAX_SKILL_CHARS]}")
                if selected_skills:
                    selected_parts.append("按以下技能步骤工作：\n\n" + "\n\n".join(selected_skills))
            if agent_obj.memory:
                try:
                    rows = self.store.search_memories("", limit=20, owner_id=owner_id)
                    memories = [
                        row for row in rows
                        if agent_obj.memory in str(row.get("tags") or "") or not row.get("tags")
                    ]
                    if memories:
                        selected_parts.append(
                            "与此 Agent 相关的长期记忆：\n" +
                            "\n".join(f"- {row.get('text')}" for row in memories[:10])
                        )
                except Exception:
                    pass
            system_parts.insert(1, "\n\n".join(part for part in selected_parts if part))
        if history_summary:
            system_parts.insert(1, history_summary)
        if resume_brief:
            # 崩溃恢复的续跑说明要排在最前：模型先看到「哪些步骤别重做」
            system_parts.insert(1, resume_brief)
        system = "\n\n".join(system_parts)
        messages: list[dict[str, Any]] = [
            {"role": turn["role"], "content": turn["content"]} for turn in history
        ] + [{"role": "user", "content": [{"type": "text", "text": goal}]}]
        schemas = [spec.schema() for spec in registry.values()]
        self._seq = 0
        final_text = ""
        # 本轮进度累积：给「总览」面板和主动建议用。
        # 放在 _run_loop 里初始化而不是 __init__ —— 每轮都要从零开始，
        # 跨轮残留会让进度条越跑越大。
        self._run_artifacts = []
        self._run_completed = []
        self._suggested_labels = set()
        # 本轮 assistant 消息 id：AG-UI 用 message_id 把 start/content/end 串成一条消息
        message_id = f"msg_{task_id}_{int(time.time() * 1000)}"

        try:
            max_turns = self.config.max_turns
            if agent_obj is not None:
                max_turns = max(1, min(max_turns, int(agent_obj.max_turns or max_turns)))
            for _turn in range(max_turns):
                # 用户点了「停止」→ 这一轮就到此为止。
                # 检查点放在每轮开头：模型调用没法从外面硬杀，
                # 但一轮跑完就能收手，已经产出的内容保留。
                #
                # 判据是「中止代号变了」，不是「Event 被置位」——
                # 后者分不清上一轮遗留的置位与本轮新点的停止（见 run() 开头）。
                # 查的是**本轮专属**的信号（模块 11），别的运行的中止不关本轮的事。
                cancelled = (
                    abort.is_cancelled(cancel_gen) if abort is not None else False
                )
                if cancelled or pending_cancel:
                    return self._finish_cancelled(task_id, final_text)
                # 轮末也要查 —— 见下面 break 处的注释。
                # （判据统一走 _is_cancelled，免得两处逻辑漂移。）
                # 工具清单每轮重算：上一轮 defer_execute 展开的工具要露出来
                schemas = [spec.schema() for spec in self._active_registry.values()]
                self.bus.text_start(task_id, message_id)
                llm_gate = self.hooks.llm_start(
                    task_id=task_id, turn=_turn, provider=provider_cfg.get("name"),
                    model=provider_cfg.get("model"), reason="",
                )
                if llm_gate["veto"]:
                    raise RuntimeError(llm_gate["reason"] or "hook 拒绝了这次模型调用")
                reply = self._call_provider(
                    provider, system, messages, schemas, task_id, message_id, cancel_gen,
                    abort=abort,
                )
                self.bus.text_end(task_id, message_id)
                self._end_reasoning(task_id)
                self._record_usage(task_id, provider_cfg, reply)
                self.hooks.llm_end(
                    task_id=task_id, turn=_turn, model=provider_cfg.get("model"),
                    input_tokens=int(getattr(reply, "usage", {}).get("input_tokens", 0))
                    if isinstance(getattr(reply, "usage", None), dict) else 0,
                    output_tokens=int(getattr(reply, "usage", {}).get("output_tokens", 0))
                    if isinstance(getattr(reply, "usage", None), dict) else 0,
                )
                assistant_blocks: list[dict[str, Any]] = []
                if reply.text:
                    assistant_blocks.append({"type": "text", "text": reply.text})
                    final_text = reply.text
                    self.store.add_message(task_id, "assistant", reply.text)

                for call in reply.tool_calls:
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                if not assistant_blocks:
                    # 模型这一轮没说话也没调工具 —— 要结束了。
                    # 但用户可能恰好在这时候点了停止：**这里也要查一次**。
                    # 只在轮首查会漏掉这种 —— 症状是「按了停止，任务还是跑完了」，
                    # 而且模型秒回的场景下必现（用户点停止的时机正好落在这一轮里）。
                    if self._is_cancelled(cancel_gen, abort):
                        return self._finish_cancelled(task_id, final_text)
                    break
                messages.append({"role": "assistant", "content": assistant_blocks})

                if not reply.tool_calls:
                    # 同上：这一轮要收尾了，也得看一眼停止标志。
                    if self._is_cancelled(cancel_gen, abort):
                        return self._finish_cancelled(task_id, final_text)
                    break

                tool_results: list[dict[str, Any]] = []
                for call in reply.tool_calls:
                    self._seq += 1
                    seq = self._seq
                    tool_results.append(self._handle_tool_call(task_id, seq, call))
                messages.append({"role": "user", "content": tool_results})

                # 结构化进度汇报（给总览面板，不是给模型当旁白）。
                # 系统提示词禁止「正在处理中」这类无信息量的播报，
                # 所以这里只给能核对的数字。
                try:
                    from .suggestions import progress_snapshot

                    self.bus.publish(
                        "run.progress", task_id=task_id,
                        **progress_snapshot(
                            turn=_turn,
                            completed=self._run_completed,
                            artifacts=list(dict.fromkeys(self._run_artifacts)),
                            current=reply.tool_calls[-1].name if reply.tool_calls else "",
                        ),
                    )
                except Exception:
                    pass

                if reply.stop_reason in ("end_turn", "stop"):
                    # 模型已经收尾，但仍有工具调用时继续一轮让模型消化结果
                    continue

            # 收尾再给一组「下一步」建议：基于这一轮真正产出的文件，
            # 而不是凭模型想象。这里**故意不做跨阶段去重** ——
            # 前端会把上一组卡替换掉，而「预览刚生成的产物」恰恰是
            # 跑完之后最该给的动作；中途那组是给仍在跑的用户看的。
            try:
                from .suggestions import suggest

                paths = list(dict.fromkeys(self._run_artifacts))
                final_s = suggest(
                    tool="", args={"path": paths[-1]} if paths else {},
                    result={}, ok=True, artifacts=paths,
                )
                if final_s:
                    self.bus.publish(
                        "run.suggestion", task_id=task_id, phase="final",
                        suggestions=final_s, tool="",
                    )
            except Exception:
                pass
            self.store.set_task_status(task_id, "done")
            self.bus.run_finished(task_id, status="done", text=final_text)
            self.hooks.end(task_id=task_id, status="done", text=final_text)
            return final_text
        except _StreamCancelled as exc:
            # 模型流式输出途中被用户点停 —— 不是错误，是用户主动中止。
            # 必须在 except Exception **之前**接住它：它是 RuntimeError 的子类，
            # 落到兜底分支会被标成 failed，界面上就变成「任务失败」而不是「已停止」。
            #
            # 已经吐出来的那部分正文要留住 ——「半个答案比没答案有用」。
            # 它已经进了事件流（前端能看到），这里补落库。
            partial = exc.partial or final_text
            if partial and partial != final_text:
                final_text = partial
                self.store.add_message(task_id, "assistant", partial)
            # 文本通道与思考通道都要收尾 ——
            # 不发 TEXT_MESSAGE_END，前端那条消息就停在「流式中」，一直转圈。
            self.bus.text_end(task_id, message_id)
            self._end_reasoning(task_id)
            return self._finish_cancelled(task_id, final_text)
        except ProviderError as exc:
            self.store.set_task_status(task_id, "failed")
            self.bus.run_error(task_id, str(exc))
            self.hooks.end(task_id=task_id, status="failed", error=str(exc))
            return f"模型调用失败：{exc}"
        except Exception as exc:  # 兜底，保证 UI 不会一直转圈
            self.store.set_task_status(task_id, "failed")
            self.bus.run_error(task_id, f"{type(exc).__name__}: {exc}")
            self.hooks.end(task_id=task_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            return f"运行时错误：{exc}"

    # ------------------------------------------------------------------ 系统设置（Marvis win_use_mcp toolkit 风格）

    # ------------------------------------------------------------------ 单次调用
    def _snapshot_before_write(self, task_id: str, call: ToolCall) -> None:
        """写/删/移文件前存一份"改之前"的内容，供 rewind 还原。

        只覆盖能直接改文件的工具。shell 造成的改动不跟踪 —— shell 能改的东西
        太杂（装包、改注册表、写数据库），快照不下来的硬凑只会给出错误的安全感。
        """
        if not task_id:
            return
        try:
            # fs_move 要记两个路径（源与目标），单独走一条路
            if call.name == "fs_move":
                src = call.arguments.get("src")
                dst = call.arguments.get("dst")
                if isinstance(src, str) and src and isinstance(dst, str) and dst:
                    self.checkpoints.snapshot_move(task_id, src, dst)
                return
            path = call.arguments.get("path")
            if not isinstance(path, str) or not path:
                return
            if call.name in ("fs_write", "fs_delete"):
                new_content = None if call.name == "fs_delete" else call.arguments.get("content")
                self.checkpoints.snapshot_write(task_id, path, new_content)
            elif call.name in (
                "docx_write", "xlsx_write", "pptx_write",
                # 新交付格式同样只产出新文件，纳入检查点才能被 rewind 撤回来
                "html_write", "pdf_write", "image_write",
            ):
                # 文档类工具只产出新文件，记下"本来不存在"即可
                self.checkpoints.snapshot_write(task_id, path, "")
        except Exception:
            # 快照失败不该拦住正事
            pass

    def _rules_hint(self, call: ToolCall) -> str:
        """取这个工具目标路径适用的项目规范。取不到返回空串。"""
        if not self._active_task_id:
            return ""
        try:
            path = (
                call.arguments.get("path")
                or call.arguments.get("src")
                or call.arguments.get("root")
                or ""
            )
            if not isinstance(path, str) or not path:
                return ""
            # 只对会碰文件的工具有意义
            if call.name not in (
                "fs_write", "fs_read", "fs_delete", "fs_list", "search_files",
                "fs_move", "fs_glob", "fs_grep",
                "docx_write", "xlsx_write", "pptx_write", "xlsx_read",
                "html_write", "pdf_write", "image_write",
            ):
                return ""
            return self._memory_rules_for(path)
        except Exception:
            return ""

    def _credential_paths_in(self, args: dict[str, Any]) -> list[str]:
        """这次调用涉及的路径里，有哪些是凭据类文件。

        给自动判定模式用：凭据类文件需要「用户明确授权」，
        而自动判定的前提恰恰是没人能授权 —— 所以这类必须直接拒，
        不能让分类器自己拍板放行。

        判定复用 policy.credential_path —— 与策略引擎同一套规则，
        不另起一份（两份规则迟早会漂移）。
        """
        hits: list[str] = []
        for key in ("path", "src", "dst", "root"):
            value = args.get(key)
            if not isinstance(value, str) or not value:
                continue
            hit = self.policy.credential_path(value)
            if hit:
                hits.append(value)
        return hits

    def _escalate_dynamic_risk(self, call: ToolCall, decision: Any) -> None:
        """按参数内容把风险等级往上抬。

        为什么需要：ToolSpec.risk 是**静态**声明的，但有些工具的真实风险
        取决于调用时带了什么参数。

        ``workflow_run`` 与 ``spawn_agent`` 是两个例子：它们自己不碰文件，
        但派出去的子 Agent 可能持有 ``fs_write`` / ``shell`` 这类工具。
        声明成 L0 只读的话，问答模式与规划模式都拦不住它们 ——
        用户选了「只读」，Agent 照样能通过子 Agent 写文件。

        判据用**子 Agent 的工具白名单**（agent 身份的一部分，硬边界），
        而不是模板自己声明什么。
        """
        if call.name not in ("workflow_run", "spawn_agent"):
            return
        if decision.risk >= Risk.L2_DESTRUCTIVE:
            return  # 已经是 L2，不用抬

        # 找出这次会用到哪些子 Agent
        agent_ids: list[str] = []
        if call.name == "spawn_agent":
            aid = call.arguments.get("agent")
            if isinstance(aid, str) and aid:
                agent_ids.append(aid)
        else:
            plan = call.arguments.get("plan")
            if isinstance(plan, dict):
                for stage in plan.get("stages") or []:
                    if not isinstance(stage, dict):
                        continue
                    for step in stage.get("steps") or []:
                        if isinstance(step, dict):
                            aid = step.get("agent")
                            if isinstance(aid, str) and aid:
                                agent_ids.append(aid)

        if not agent_ids:
            return

        from .tools import MUTATING_TOOLS

        tool_map = self._agent_tool_map()
        for aid in agent_ids:
            tools = tool_map.get(aid)
            if tools is None:
                # 查不到的 Agent 按「不可知即不安全」处理
                decision.risk = Risk.L2_DESTRUCTIVE
                decision.reason = (
                    f"子 Agent「{aid}」不存在或状态不明，无法确认它的权限边界"
                )
                return
            if any(t in MUTATING_TOOLS for t in tools):
                decision.risk = Risk.L2_DESTRUCTIVE
                decision.reason = (
                    f"子 Agent「{aid}」持有会改动系统的工具，需要确认后才能执行"
                )
                # L2 需要一份执行计划给用户看
                if decision.plan is None:
                    try:
                        decision.plan = self.policy.build_plan(
                            self._active_registry.get(call.name),
                            call.arguments,
                            decision.findings,
                        )
                    except Exception:
                        pass
                return

    def _handle_tool_call(self, task_id: str, seq: int, call: ToolCall) -> dict[str, Any]:
        # 本轮累积的进度与产物。正常由 _run_loop 初始化；测试若直接调用
        # 本方法也别炸 —— 用 getattr 兜住。
        if not hasattr(self, "_run_artifacts"):
            self._run_artifacts = []
            self._run_completed = []
            self._suggested_labels = set()
        spec = self._active_registry.get(call.name)
        if spec is None:
            result = {"ok": False, "content": f"未知工具：{call.name}"}
            self.bus.publish(
                "step.failed", task_id=task_id, tool=call.name, error=result["content"]
            )
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result["content"],
                "is_error": True,
            }

        decision = self.policy.evaluate(spec, call.arguments)
        # 动态风险：有些工具的真实风险取决于**参数内容**，不是工具本身。
        #
        # 最典型的两个：
        #   workflow_run  —— 编排里派给子 Agent 的活可能改文件、跑命令
        #   spawn_agent   —— 子 Agent 的工具白名单里可能有写工具
        #
        # 它们声明成 L0 只读的话，问答/规划模式下就**拦不住** ——
        # 用户选了「只读」，Agent 照样能通过子 Agent 写文件。
        # 那是权限缺口，必须按真实能力定级。
        self._escalate_dynamic_risk(call, decision)
        step_id = self.store.create_step(
            task_id, seq, call.name, call.arguments, int(decision.risk)
        )
        self.bus.tool_start(task_id, call.id, call.name)
        self.bus.tool_args(task_id, call.id, call.arguments)
        self.bus.publish(
            "step.created",
            task_id=task_id,
            step_id=step_id,
            step_seq=seq,
            tool=call.name,
            args=call.arguments,
            risk=int(decision.risk),
            risk_label=decision.risk.label,
        )

        # L3：硬拒绝。这一层在模式闸门之前——禁止操作不能被任何模式放行。
        # （对齐 WorkBuddy 权限链：deny 规则位于第 1 位，模式基线在第 7 位。）
        if decision.forbidden:
            reason = decision.reason or "命中禁止规则"
            self.store.finish_step(step_id, "denied", error=reason)
            self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk), "denied", reason
            )
            self.bus.publish(
                "step.denied", task_id=task_id, step_id=step_id, tool=call.name, reason=reason
            )
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": f"该操作被安全策略禁止：{reason}。请告知用户需要本人手动完成。",
                "is_error": True,
            }

        # 交互模式闸门：模式决定这一轮最高能碰到什么风险等级
        mode_policy = get_policy(self._active_interaction)
        # 委派模式：只保留协调类工具，执行类一律不暴露给模型
        mode_tools = allowed_tools(self._active_interaction)
        if mode_tools is not None and call.name not in mode_tools:
            reason = f"当前是「{mode_policy.name}」模式，不能直接调用 {call.name}"
            self.store.finish_step(step_id, "denied", error=reason)
            self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk), "mode_denied", reason
            )
            self.bus.publish(
                "step.denied",
                task_id=task_id,
                step_id=step_id,
                tool=call.name,
                reason=reason,
            )
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": (
                    f"该工具在当前模式下不可用：{reason}。"
                    "请改用 spawn_agent 或 team_dispatch 把工作派出去。"
                ),
                "is_error": True,
            }
        if not mode_policy.allows(decision.risk):
            reason = (
                f"当前是「{mode_policy.name}」模式，只能做只读操作；"
                f"{call.name} 属于{decision.risk.label}"
            )
            self.store.finish_step(step_id, "denied", error=reason)
            self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk), "mode_denied", reason
            )
            self.bus.publish(
                "step.denied",
                task_id=task_id,
                step_id=step_id,
                tool=call.name,
                reason=reason,
            )
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": (
                    f"该工具在当前模式下不可用：{reason}。"
                    "请告诉用户切换到「执行」模式后再做，不要重试。"
                ),
                "is_error": True,
            }

        # hook 闸门：策略引擎之外的额外闸门（限流、黑名单、灰度开关都挂这里）
        gate = self.hooks.tool_start(
            task_id=task_id, step_id=step_id, tool=call.name, args=call.arguments,
            risk=int(decision.risk), risk_label=decision.risk.label, reason="",
        )
        if gate["veto"]:
            reason = gate["reason"] or f"hook 拒绝了 {call.name}"
            self.store.finish_step(step_id, "denied", error=reason)
            self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk), "hook_denied", reason
            )
            self.bus.publish(
                "step.denied", task_id=task_id, step_id=step_id, tool=call.name, reason=reason
            )
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": f"该操作被拦截：{reason}。请告知用户，不要重试。",
                "is_error": True,
            }

        # hook 改写入参：在权限检查之前替换参数，然后按新参数重新判定。
        # 这等于给了 hook 改写工具入参的能力 —— 属于高权限，只在内置/受信 hook 上用。
        hook_decision = gate.get("decision") or ""
        if gate.get("modified_args"):
            patched = {**call.arguments, **gate["modified_args"]}
            call.arguments = patched
            self.store.audit(
                task_id, step_id, call.name, patched, int(decision.risk),
                "hook_modified", "hook 改写了调用参数",
            )
            decision = self.policy.evaluate(spec, patched)

        # hook 说 allow：跳过 L2 等确认（deny 已经在上面被拦了）
        skip_approval = hook_decision == "allow"
        # hook 说 ask：即使策略引擎判定可以自动执行，也要问用户
        force_approval = hook_decision == "ask"

        # L2：等确认。hook 说 allow 就跳过，说 ask 就强制问。
        need_confirm = decision.needs_approval and decision.plan is not None
        if force_approval and not need_confirm:
            need_confirm = True
            if decision.plan is None:
                decision.plan = self.policy.build_plan(spec, call.arguments, decision.findings)

        # 自动判定模式：分类器替用户决定这一档要不要确认。
        #
        # 放在 hook 之后 —— hook 是「业务额外闸门」，它的 allow/ask 优先于分类器。
        # 放在模式开关之前 —— bypass/dontAsk 是模式层的硬开关，不该被分类器改写。
        if mode_policy.use_classifier:
            from .autoclass import ALLOW, ASK, DENY, classify

            verdict, why = classify(
                call.name,
                call.arguments,
                risk=int(decision.risk),
                credential_paths=self._credential_paths_in(call.arguments),
            )
            self.bus.publish(
                "step.autoclass",
                task_id=task_id,
                step_id=step_id,
                tool=call.name,
                verdict=verdict,
                reason=why,
            )
            if verdict == DENY:
                self.store.finish_step(step_id, "denied", error=why)
                self.store.audit(
                    task_id, step_id, call.name, call.arguments, int(decision.risk),
                    "auto_denied", why,
                )
                self.bus.publish(
                    "step.denied", task_id=task_id, step_id=step_id, tool=call.name, reason=why
                )
                return {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": (
                        f"该操作被自动判定拒绝：{why}。"
                        "请告知用户需要本人手动完成，不要重试。"
                    ),
                    "is_error": True,
                }
            if verdict == ALLOW:
                skip_approval = True
                need_confirm = False
            elif verdict == ASK:
                # 拿不准就照常确认 —— fail-closed
                skip_approval = False
                if not need_confirm:
                    need_confirm = True
                    if decision.plan is None:
                        decision.plan = self.policy.build_plan(
                            spec, call.arguments, decision.findings
                        )

        # 模式层的两把开关：bypass 跳过确认，dontAsk 直接拒
        if mode_policy.skip_approval:
            skip_approval = True
        if mode_policy.deny_on_ask and need_confirm:
            reason = gate.get("reason") or "当前是无人值守模式，没有人可以确认"
            self.store.finish_step(step_id, "denied", error=reason)
            self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk),
                "denied", reason,
            )
            self.bus.publish(
                "step.denied", task_id=task_id, step_id=step_id, tool=call.name, reason=reason
            )
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": (
                    f"该操作需要人工确认，当前模式（{mode_policy.label}）无人值守，已拒绝。"
                    "请改用不需要确认的做法，或在最后说明卡在哪里。"
                ),
                "is_error": True,
            }
        if need_confirm and not skip_approval and decision.plan is not None:
            # 计划只序列化一次 —— SSE 与页面刷新后的兜底查询必须是同一份内容，
            # 否则两张卡片会显示不一样的路径 / 风险。
            plan_payload = decision.plan.to_dict()
            risk_level = int(decision.risk)
            self.bus.publish(
                "step.awaiting_approval",
                task_id=task_id,
                step_id=step_id,
                tool=call.name,
                plan=plan_payload,
                risk=risk_level,
                risk_label=decision.risk.label,
                # 多账号下 SSE 按 task_id / _owner_id 过滤；少了它这张卡可能
                # 被服务端判定为「不属于当前账号」而丢掉。
                _owner_id=self._active_owner_id,
            )
            approved = self.wait_approval(
                step_id, task_id=task_id, owner_id=self._active_owner_id,
                tool=call.name, plan=plan_payload, risk=risk_level,
                abort=self._current_abort,
            )
            if not approved:
                self.store.finish_step(step_id, "denied", error="用户未确认")
                self.store.audit(
                    task_id, step_id, call.name, call.arguments, int(decision.risk), "denied", "用户未确认"
                )
                self.bus.publish(
                    "step.denied", task_id=task_id, step_id=step_id, tool=call.name, reason="用户未确认"
                )
                return {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": "用户没有确认该操作，已取消。不要重试，改为向用户说明情况。",
                    "is_error": True,
                }
            audit_id = self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk), "approved", "pending"
            )
        else:
            outcome = "hook_allowed" if skip_approval else "auto"
            audit_id = self.store.audit(
                task_id, step_id, call.name, call.arguments, int(decision.risk), outcome, "pending"
            )

        # 执行
        self.bus.publish("step.running", task_id=task_id, step_id=step_id, tool=call.name)
        started = time.time()
        # 写/删类工具动手前先存快照，回退时才还原得回去
        self._snapshot_before_write(task_id, call)
        # 该路径适用的项目规范拼到结果里：模型动手前就该知道约定，
        # 而不是改完了才被 review 打回来
        rules_hint = self._rules_hint(call)
        try:
            result = spec.fn(**call.arguments)
        except _StreamCancelled:
            # 用户点了停止 —— 这是中止，不是工具失败。
            # 不能吞成 {"ok": false}：那样主循环会把它当成一次普通的
            # 工具报错，接着又把下一轮模型调用跑起来（模型看到"工具失败"就重试）。
            # 直接往上抛，让主循环收手。
            raise
        except TypeError as exc:
            result = {"ok": False, "content": f"参数不合法：{exc}"}
        except Exception as exc:
            result = {"ok": False, "content": f"{type(exc).__name__}: {exc}"}
        # 工具返回值统一过打码层：任何工具都可能把密钥带回来
        # （fs_read 读 .env、shell 的 cat、search_files 的命中内容）。
        # 在这里统一处理一次，下游的落库、事件推送、进上下文全都是打码后的版本。
        from .redact import redact_result

        result = redact_result(result)
        # 失败分类：给模型的不只是「失败了」，还有「这类失败该怎么办」。
        # 提示词要求先分类再决策，分类判据必须由代码给 ——
        # 让模型自己判断，同一个错误两次调用可能给出两个分类。
        result = annotate_failure(result, call.name)
        # 只读自证：工具结果统一带上「我没动过」的标志。
        # 只读工具恒为真；有副作用的工具明确标 false。
        # 这样「这个操作改了什么」不用靠猜工具名。
        result["mutated"] = bool(spec.mutates)
        if not spec.mutates:
            result["preview"] = True
            result["deleted"] = False
            result["modified"] = False
            result["moved"] = False
        elapsed_ms = int((time.time() - started) * 1000)
        # 自证字段要**真的能被人看见**，光写在 result 里没用 ——
        # 下面三处只传 content，字段在这一步就丢了。
        # 抄 Marvis 的做法：每个只读结果都带这四个字段，
        # 界面与审计都能据此回答「这个操作改了什么」。
        proof = {
            "mutated": bool(result.get("mutated")),
            "preview": bool(result.get("preview", False)),
            "deleted": bool(result.get("deleted", False)),
            "modified": bool(result.get("modified", False)),
            "moved": bool(result.get("moved", False)),
        }

        # 产物判定放在**后端**：按结果元数据（真的写了字节 + 带路径），
        # 不再让前端硬编码 FILE_TOOLS。新增任何会落盘的工具都自动被收进产物面板，
        # 只读工具（xlsx_read / fs_read）也进不来。
        artifact_path = ""
        if result.get("ok"):
            try:
                from .artifacts import extract_artifact_path

                artifact_path = extract_artifact_path(call.name, result)
            except Exception:
                artifact_path = ""

        state = "done" if result.get("ok") else "failed"
        # 审计补记结果：这一行先前写的是 'pending'（打算做什么、谁批的），
        # 现在工具跑完了，把「实际成没成、有没有改动东西」补上。
        # 不补的话，审计翻出来永远停在「待执行」，看不出结果。
        self.store.finish_audit(
            audit_id, "done" if result.get("ok") else "failed", bool(proof["mutated"])
        )
        # 失败分类 / 只读自证 / 产物路径原来只发在 step.done 事件里，
        # 页面刷新后就查不到了。既然内核已经算出来，一并落库 ——
        # 执行步骤面板刷新前后显示一致，也不必再靠 SSE 补记忆。
        self.store.finish_step(
            step_id,
            state,
            result=result.get("content"),
            error=None if result.get("ok") else result.get("content"),
            error_class=result.get("error_class"),
            artifact_path=artifact_path or None,
            mutated=bool(proof["mutated"]),
        )
        self.bus.tool_end(task_id, call.id, call.name)
        self.bus.tool_result(
            task_id,
            call.id,
            call.name,
            result.get("content") if result.get("ok") else None,
            ok=bool(result.get("ok")),
        )
        self.bus.publish(
            "step.done" if result.get("ok") else "step.failed",
            task_id=task_id,
            step_id=step_id,
            tool=call.name,
            elapsed_ms=elapsed_ms,
            result=result.get("content") if result.get("ok") else None,
            error=None if result.get("ok") else result.get("content"),
            # 失败分类一起发出去 —— 界面上能显示「这是权限问题」
            # 而不是笼统的「失败」。成功时这个字段不存在。
            **({"error_class": result["error_class"]} if result.get("error_class") else {}),
            **({"artifact_path": artifact_path} if artifact_path else {}),
            # 旧工具（fs_write / browser_screenshot 等）的结果只有一句文本，
            # 没有 bytes/path 元数据。把调用参数一起带上，前端才能按
            # FILE_TOOLS 兜底把产物路径收进面板。step.created 本来就发 args，
            # 这里不是新增泄露。
            args=call.arguments,
            **proof,
        )
        self.hooks.tool_end(
            task_id=task_id, step_id=step_id, tool=call.name,
            ok=bool(result.get("ok")), elapsed_ms=elapsed_ms,
            result=result.get("content"), error=None if result.get("ok") else result.get("content"),
        )

        # ---- 运行中主动建议（对齐 Marvis）----
        # 只在「有实质变化」时发：一步跑完 / 一步失败 / 产出了新文件。
        # 建议只展示，绝不自动执行；去重避免同一工具反复刷同一张卡。
        try:
            from .suggestions import suggest

            if artifact_path:
                self._run_artifacts.append(artifact_path)
            self._run_completed.append(call.name)
            candidates = suggest(
                tool=call.name,
                args=call.arguments,
                result=result,
                ok=bool(result.get("ok")),
                artifacts=self._run_artifacts,
            )
            fresh = [s for s in candidates if s.get("label") not in self._suggested_labels]
            if fresh:
                self._suggested_labels.update(s.get("label") for s in fresh)
                self.bus.publish(
                    "run.suggestion", task_id=task_id, phase="step",
                    suggestions=fresh, step_id=step_id, tool=call.name,
                )
        except Exception:
            # 建议是锦上添花，不能因为它失败把主链路拖垮
            pass

        text = tool_result_text(result, call.name)
        if rules_hint:
            # 规范放在结果前面：模型读到的是"先看约定，再看结果"
            text = f"{rules_hint}\n\n{text}"
        return {
            "type": "tool_result",
            "tool_use_id": call.id,
            "content": text,
            "is_error": not result.get("ok"),
        }
