"""生命周期 hook：对齐 Marvis 的 7 个 hook。

Marvis 在网关层（xiaobao_gateway）留了 7 个生命周期切点，业务只往里挂回调，
不直接改主流程 —— 这样「加一个观测/审计/限流」不需要动 Agent 主循环一行代码。

7 个 hook 与各自的语义：

    on_start       一次 run 开始（拿到 task_id / goal / 交互模式）
    on_end         一次 run 结束（拿到 status / 产出文本）
    on_handoff     活交给别人：派给子 Agent 或团队成员时触发
    on_tool_start  工具执行前（拿到工具名、参数、风险等级）
    on_tool_end    工具执行后（拿到 ok / 结果 / 耗时）
    on_llm_start   调模型前（拿到 provider / model / 轮次）
    on_llm_end     调模型后（拿到 token 用量）

三条硬规则：
1. hook 是**旁路观察者**，抛异常绝不能影响主流程，所以内部吞掉并记一条日志。
2. hook 可以改变执行：`on_tool_start` 返回 False 直接否决这次调用（策略引擎之外的
   额外闸门，`on_llm_start` 同理）。
3. 注册是 O(1) 的 hook 名 -> 回调列表；一个 hook 可以挂多个回调，按注册顺序跑。

三种决策（对齐 WorkBuddy 的 permissionDecision）：
    allow   放行，跳过 L2 等确认
    deny    拒绝
    ask     转人工确认（策略引擎本来要确认的，现在问用户）
    （不返回 / True）  按策略引擎的判定走

回调可以返回：
    False                      → deny
    (False, "原因")             → deny + 原因
    "allow" / ("allow", "原因")  → allow
    "ask"   / ("ask", "原因")    → ask
    {"decision": "...", "reason": "...", "modified_args": {...}}
                                 → 结构化写法，还能改写入参

内置回调（默认生效，不需要配置）：
    - 耗时统计：llm/tool/run 三个维度各自计时，结果发 `hook.metrics` 事件
    - 审计留痕：tool 维度把每一次调用写进 store.audit（主循环已写，这里补子 Agent 的）
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable

log = logging.getLogger("oryx.hooks")

# 7 个 hook 名，顺序与 Marvis 一致
ON_START = "on_start"
ON_END = "on_end"
ON_HANDOFF = "on_handoff"
ON_TOOL_START = "on_tool_start"
ON_TOOL_END = "on_tool_end"
ON_LLM_START = "on_llm_start"
ON_LLM_END = "on_llm_end"

HOOK_NAMES: tuple[str, ...] = (
    ON_START,
    ON_END,
    ON_HANDOFF,
    ON_TOOL_START,
    ON_TOOL_END,
    ON_LLM_START,
    ON_LLM_END,
)

# 可以否决执行的 hook：返回 False 就拦住
VETOABLE = frozenset({ON_TOOL_START, ON_LLM_START})

# 三种决策
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ASK = "ask"
DECISIONS = (DECISION_ALLOW, DECISION_DENY, DECISION_ASK)




def _parse_decision(out: Any) -> tuple[str, str, dict | None]:
    """把 hook 的返回值归一成 (decision, reason, modified_args)。

    decision 为空串表示"没表态"。支持这些写法：
        None / True                → 不表态
        False                      → deny
        "allow" / "deny" / "ask"   → 对应决策
        (False, "原因")             → deny + 原因
        ("ask", "原因")              → ask + 原因
        {"decision": ..., "reason": ..., "modified_args": {...}}
    """
    if out is None or out is True:
        return "", "", None
    if isinstance(out, dict):
        decision = str(out.get("decision") or "").lower()
        if decision not in DECISIONS:
            return "", "", None
        reason = str(out.get("reason") or "")
        modified = out.get("modified_args")
        return decision, reason, (modified if isinstance(modified, dict) else None)
    if isinstance(out, tuple):
        if not out:
            return "", "", None
        head = out[0]
        reason = ""
        if len(out) >= 2 and isinstance(out[1], str):
            reason = out[1]
        if head is False:
            return DECISION_DENY, reason, None
        if head is True:
            return "", reason, None
        if isinstance(head, str) and head.lower() in DECISIONS:
            return head.lower(), reason, None
        return "", reason, None
    if out is False:
        return DECISION_DENY, "", None
    if isinstance(out, str) and out.lower() in DECISIONS:
        return out.lower(), "", None
    return "", "", None


class HookBus:
    """hook 注册与分发。

    线程安全：Agent 主循环在 worker 线程里触发 hook，HTTP 处理器在事件循环线程里注册。

    **三级开关**（借鉴 ECC 的 hook profile）：
      1. 总闸   —— `ORYX_HOOKS_ENABLED=0` 全关
      2. 档位   —— `ORYX_HOOK_PROFILE=minimal|standard|strict`
      3. 单点   —— `ORYX_DISABLED_HOOKS="pre:tool:net-guard,post:llm:usage"`

    为什么要有：以前钩子只能全开全关，用户遇到某个钩子误伤时
    只能去改代码。有了**稳定 id**，就能精确禁用那一个。
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[Callable[..., Any], str]]] = {n: [] for n in HOOK_NAMES}
        self._hook_ids: dict[str, list[str]] = {n: [] for n in HOOK_NAMES}
        self._lock = threading.Lock()
        # 三级开关的当前状态（每次 emit 时读环境变量，改了立即生效）
        self._enabled = True
        self._profile = "standard"
        self._disabled: set[str] = set()

    # ── 开关 ────────────────────────────────────────────────
    def _refresh_switches(self) -> None:
        """从环境变量刷新开关状态。

        每次 emit 都读 —— 这样用户在运行中改环境变量也不必重启
        （虽然实际场景多半是改配置后重启）。
        """
        import os

        raw = (os.environ.get("ORYX_HOOKS_ENABLED") or "").strip().lower()
        self._enabled = raw not in ("0", "false", "no", "off")
        prof = (os.environ.get("ORYX_HOOK_PROFILE") or "").strip().lower()
        if prof in ("minimal", "standard", "strict"):
            self._profile = prof
        disabled = os.environ.get("ORYX_DISABLED_HOOKS") or ""
        self._disabled = {
            part.strip() for part in disabled.replace(";", ",").split(",")
            if part.strip()
        }

    def is_disabled(self, hook_id: str) -> bool:
        """某个 hook id 当前是否被禁用。"""
        self._refresh_switches()
        if not self._enabled:
            return True
        return hook_id in self._disabled

    def hook_id_for(self, name: str, owner: str) -> str:
        """把 (事件名, owner) 组成稳定 id。

        形如 `pre:tool:net-guard` —— 事件前缀 + 事件名 + owner。
        稳定 id 是「能精确禁用某个钩子」的前提。
        """
        prefix = {
            ON_START: "pre", ON_END: "post",
            ON_HANDOFF: "handoff", ON_TOOL_START: "pre:tool",
            ON_TOOL_END: "post:tool", ON_LLM_START: "pre:llm",
            ON_LLM_END: "post:llm",
        }.get(name, name)
        return f"{prefix}:{owner}" if owner else prefix

    # ── 注册 ────────────────────────────────────────────────
    def on(self, name: str, fn: Callable[..., Any], *, owner: str = "") -> Callable[..., Any]:
        """注册一个回调。owner 用于同名校验时替换/摘除，避免闭包身份对不上。"""
        if name not in self._hooks:
            raise ValueError(f"没有这个 hook：{name}。可用：{'、'.join(HOOK_NAMES)}")
        with self._lock:
            self._hooks[name].append((fn, owner))
            self._hook_ids[name].append(self.hook_id_for(name, owner))
        return fn

    def off(self, name: str, owner: str) -> bool:
        """摘掉某个 owner 注册的全部回调。"""
        with self._lock:
            before = len(self._hooks.get(name, []))
            self._hooks[name] = [(fn, o) for fn, o in self._hooks.get(name, []) if o != owner]
            # id 列表按同样规则重建（id 是从 (name, owner) 算出来的，不是独立存的）
            self._hook_ids[name] = [
                self.hook_id_for(name, o) for _fn, o in self._hooks.get(name, [])
            ]
            return len(self._hooks[name]) != before

    def clear(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                for n in self._hooks:
                    self._hooks[n].clear()
                    self._hook_ids[n].clear()
            else:
                self._hooks.get(name, []).clear()
                self._hook_ids.get(name, []).clear()

    def count(self, name: str) -> int:
        with self._lock:
            return len(self._hooks.get(name, []))

    # ── 分发 ────────────────────────────────────────────────
    def emit(
        self,
        name: str,
        *args: Any,
        veto: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """触发一个 hook。

        返回 {"veto": bool, "reason": str, "errors": [...], "decision": str, "modified_args": dict | None}。

        decision 取值：
            ""       没hook表态，按策略引擎的判定走
            "allow"  放行
            "deny"   拒绝（等价于 veto=True）
            "ask"    转人工确认

        否决时想带上原因，两种写法都行：

            def deny(**kw): return False
            def deny(**kw): return (False, "本轮禁止访问文件系统")

        裸 False 拿不到原因时，用调用方传进来的 kwargs["reason"] 兜底。

        改写入参用结构化写法（只能改调用参数，不改工具本身）：

            def patch(**kw):
                return {"decision": "allow", "modified_args": {"path": "/tmp/x"}}
        """
        with self._lock:
            callbacks = list(self._hooks.get(name, []))

        result: dict[str, Any] = {
            "veto": False,
            "reason": "",
            "errors": [],
            "decision": "",
            "modified_args": None,
            "skipped": [],
        }
        for fn, owner in callbacks:
            # 三级开关：被禁用的钩子直接跳过（但要在结果里说明跳过了谁）
            hook_id = self.hook_id_for(name, owner)
            if self.is_disabled(hook_id):
                result["skipped"].append(hook_id)
                continue
            try:
                out = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — hook 坏了不能拖垮主流程
                log.warning("hook %s（%s）抛异常，已忽略：%s: %s", name, owner or "-",
                            type(exc).__name__, exc)
                result["errors"].append(f"{owner or fn.__name__}: {exc}")
                continue

            decision, reason, modified = _parse_decision(out)
            # 非否决型 hook（veto=False）不参与决策，返回值一律忽略
            if not veto or decision == "":
                continue
            if reason:
                result["reason"] = reason
            if modified is not None:
                result["modified_args"] = modified
            if decision == DECISION_DENY:
                result["decision"] = DECISION_DENY
                result["veto"] = True
                fallback = kwargs.get("reason") or "hook 拒绝"
                result["reason"] = result["reason"] or (
                    fallback if isinstance(fallback, str) else str(fallback)
                )
                break
            if decision == DECISION_ALLOW:
                # allow 不终止分发 —— 后续 hook 仍可否决
                result["decision"] = DECISION_ALLOW
                continue
            # ask：交给上层弹确认
            result["decision"] = DECISION_ASK
            break
        return result

    # ── 语义化快捷方法 ──────────────────────────────────────
    def start(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_START, **kw)

    def end(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_END, **kw)

    def handoff(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_HANDOFF, **kw)

    def tool_start(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_TOOL_START, veto=True, **kw)

    def tool_end(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_TOOL_END, **kw)

    def llm_start(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_LLM_START, veto=True, **kw)

    def llm_end(self, **kw: Any) -> dict[str, Any]:
        return self.emit(ON_LLM_END, **kw)


def builtin_hooks(bus: HookBus, on_metrics: Callable[[dict[str, Any]], None] | None = None) -> None:
    """挂上内置回调：耗时统计。

    on_metrics 收到的是 {"kind": "run|llm|tool", "name": ..., "elapsed_ms": int, ...}。
    不传 on_metrics 就只做内部计数，不外发。

    配对靠业务标识（task_id / step_id / turn），不能靠 id(kwargs) ——
    start 和 end 拿到的是两个不同的 dict，对象地址对不上，耗时会恒为 0。
    """
    timers: dict[str, float] = {}
    lock = threading.Lock()

    def _mark(key: str) -> None:
        with lock:
            timers[key] = time.time()

    def _elapsed(key: str) -> int:
        with lock:
            start = timers.pop(key, 0.0)
        return int((time.time() - start) * 1000) if start else 0

    def _on_start(**kw: Any) -> None:
        _mark(f"run:{kw.get('task_id', '')}")

    def _on_end(**kw: Any) -> None:
        task_id = kw.get("task_id", "")
        _report(on_metrics, kind="run", name="run", task_id=task_id,
                 status=kw.get("status", ""), elapsed_ms=_elapsed(f"run:{task_id}"))

    def _on_llm_start(**kw: Any) -> None:
        _mark(_llm_key(kw))

    def _on_llm_end(**kw: Any) -> None:
        _report(on_metrics, kind="llm", name=kw.get("model", "?"),
                 task_id=kw.get("task_id", ""), agent=kw.get("agent", ""),
                 turn=kw.get("turn", 0),
                 elapsed_ms=_elapsed(_llm_key(kw)),
                 input_tokens=kw.get("input_tokens", 0),
                 output_tokens=kw.get("output_tokens", 0))

    def _on_tool_start(**kw: Any) -> None:
        _mark(f"tool:{kw.get('step_id', '')}")

    def _on_tool_end(**kw: Any) -> None:
        step_id = kw.get("step_id", "")
        _report(on_metrics, kind="tool", name=kw.get("tool", "?"),
                 task_id=kw.get("task_id", ""), step_id=step_id,
                 ok=bool(kw.get("ok")), elapsed_ms=_elapsed(f"tool:{step_id}"))

    bus.on(ON_START, _on_start, owner="builtin.metrics")
    bus.on(ON_END, _on_end, owner="builtin.metrics")
    bus.on(ON_LLM_START, _on_llm_start, owner="builtin.metrics")
    bus.on(ON_LLM_END, _on_llm_end, owner="builtin.metrics")
    bus.on(ON_TOOL_START, _on_tool_start, owner="builtin.metrics")
    bus.on(ON_TOOL_END, _on_tool_end, owner="builtin.metrics")


def _llm_key(kw: dict[str, Any]) -> str:
    """一次模型调用在一次 run 内唯一：task_id + 谁调的 + 第几轮。"""
    return f"llm:{kw.get('task_id', '')}:{kw.get('agent', '')}:{kw.get('turn', 0)}"


def _report(sink: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if sink is None:
        return
    try:
        sink(payload)
    except Exception:  # noqa: BLE001
        pass