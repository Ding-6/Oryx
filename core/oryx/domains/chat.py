"""对话域：发起一轮对话、重跑、中止、剪贴板与粘贴导入、审批。

这个域有两个「有状态」的东西：

* `_DispatchTracker` —— 跟踪每一轮发起的落地状态
* `_dispatch` —— 它的单例

为什么要跟踪「落地」
------------------
「发送」和「停止」是**两条独立的 HTTP 请求**，可能并发到达。
用户点「发送」后立刻点「停止」时，停止请求可能**先到**：

* 先到内核 → 内核还没登记任务，那一下会被丢掉
* 先到控制面 → 控制面还不知道有发起在路上

两种情况都靠序号（前端给的 `seq`）对齐：

* 发起在途 → 等它落地再把中止转给内核
* 发起还没到 → 先记下这笔中止，等 chat 到了补上

模型调用没法从外面硬杀（正在等 HTTP 响应），
所以内核置一个标志位，主循环在每轮之间检查它，一轮结束就收手。
已经产出的内容保留 —— 半个答案比没答案有用。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ..api.response import ok
from ._common import json, rpc_failure

router = APIRouter(prefix="/api", tags=["对话"])


# ══════════ 请求模型 ══════════

class ChatRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=20_000)
    mode: str | None = None
    interaction: str | None = None
    style: str | None = None
    expert: str | None = Field(default=None, max_length=64)
    agent_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    seq: int = Field(default=0, ge=0)

    # 单次运行的偏好覆盖必须落在后端认识的枚举里。
    #
    # 以前不校验：前端手滑传个 "Ask" 或拼错，"get_policy" 会静默兜底成
    # craft —— 用户以为选了问答模式，实际却拿到了全权限，界面还照样
    # 把那个错值固化进会话快照。**权限相关的入参不能静默降级**。
    # 这里规范化成内核的 canonical 名（例如 dontask → dontAsk），
    # 未知值直接 422，让调用方立刻发现。
    @field_validator("interaction")
    @classmethod
    def _valid_interaction(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        from ..modes import MODE_POLICIES

        key = v.strip().lower()
        for name in MODE_POLICIES:
            if name.lower() == key:
                return name
        raise ValueError(f"未知的交互模式：{v}")

    @field_validator("style")
    @classmethod
    def _valid_style(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        from ..styles import STYLES

        key = v.strip().lower()
        if key in STYLES:
            return key
        raise ValueError(f"未知的回答风格：{v}")

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        key = v.strip().lower()
        if key in ("efficiency", "local"):
            return key
        raise ValueError(f"未知的计算模式：{v}")


class RetryRequest(BaseModel):
    """重新生成：把某个会话最后一轮的助手回复删掉，用同一提问重跑。"""

    task_id: str


class CancelRequest(BaseModel):
    """中止请求。

    ``seq`` 是发起这一轮时前端拿到的序号。控制面靠它判断
    「这次中止是冲哪一轮来的」—— 前端点发送后立刻点停止时，
    两条请求并发到达，序号是唯一能把它们对齐的东西。
    """

    seq: int = 0
    task_id: str | None = Field(default=None, max_length=64)


class ApprovalRequest(BaseModel):
    step_id: str
    approved: bool


# ══════════ 发起落地跟踪 ══════════

class DispatchTracker:
    """跟踪每一轮发起的落地状态，并记住「提前到达」的中止请求。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: int = 0        # 正在投递的序号（0 = 没有）
        self._landed: set[int] = set()  # 已经落地的序号（保留最近几个）
        self._pending_cancel: set[int] = set()  # 「提前到达」的中止请求
        self._max_seq: int = 0         # 见过的最大序号
        self._done = threading.Event()
        self._owner_id: str | None = None
        self._task_id: str | None = None
        self._active = False

    def begin(self, seq: int, owner_id: str | None = None, task_id: str | None = None) -> None:
        with self._lock:
            self._inflight = seq
            self._owner_id = owner_id
            self._task_id = task_id
            self._active = True
            if seq > self._max_seq:
                self._max_seq = seq
            self._done.clear()

    def end(self, seq: int, task_id: str | None = None) -> None:
        with self._lock:
            if self._inflight == seq:
                self._inflight = 0
                if task_id:
                    self._task_id = task_id
            self._landed.add(seq)
            # 只留最近 32 个落地序号 —— 免得这个集合无限涨
            if len(self._landed) > 32:
                for old in sorted(self._landed)[: len(self._landed) - 32]:
                    self._landed.discard(old)
        self._done.set()

    def authorized_cancel(self, owner_id: str | None, task_id: str | None = None) -> bool:
        with self._lock:
            if not self._active:
                return task_id is None
            return self._owner_id == owner_id and (task_id is None or self._task_id == task_id)

    def current_task_id(self) -> str | None:
        with self._lock:
            return self._task_id

    def complete(self) -> None:
        with self._lock:
            self._owner_id = None
            self._active = False

    def state(self, seq: int) -> str:
        """这个序号的发起现在处于哪一步。

        返回 "inflight" / "landed" / "unknown"。
        seq<=0（前端没带序号）一律算 unknown —— 没凭据就分不清是哪一轮。
        """
        if seq <= 0:
            return "unknown"
        with self._lock:
            if self._inflight == seq:
                return "inflight"
            if seq in self._landed:
                return "landed"
        return "unknown"

    def note_cancel(self, seq: int) -> bool:
        """记下「提前到达」的中止请求 —— 等这轮 chat 到了要补上。

        只有「还没见过更大的序号」时才记：
        序号比已知最大值还大 = 这一轮的 chat 请求可能还在路上（浏览器并发时
        两个请求的到达顺序不保证）。序号比已知最大值小 = 那是上一轮的迟到请求，
        记下来只会让下次发消息被莫名停掉。
        """
        if seq <= 0:
            return False
        with self._lock:
            if seq < self._max_seq:
                return False
            if seq > self._max_seq:
                self._max_seq = seq
            self._pending_cancel.add(seq)
            return True

    def take_cancel(self, seq: int) -> bool:
        """这轮 chat 到了 —— 该不该立刻补一次中止？取走标记。"""
        with self._lock:
            if seq not in self._pending_cancel:
                return False
            self._pending_cancel.discard(seq)
            return True

    async def wait_landed(self, timeout: float = 10.0) -> None:
        """等在途的发起落地。有超时，别把请求挂死。"""
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


# 本域的单例 —— 只有对话相关的路由用它
_dispatch = DispatchTracker()


# ══════════ 业务 ══════════

async def run_chat(agent, config, dispatch, req: ChatRequest, store=None,
                   owner_id: str | None = None) -> dict:
    """发起一轮对话。"""
    from ..agentlink import IpcError

    goal = (req.goal or "").strip()
    if not goal:
        raise HTTPException(400, "goal 不能为空")

    session_id = (req.session_id or "").strip() or None
    task = store.get_task(session_id, owner_id) if session_id and store is not None else None
    if session_id and store is not None and task is None:
        raise HTTPException(404, "没有这个会话")

    snapshot = await _resolve_run_snapshot(agent, config, req, store, task, owner_id)
    frozen_agent_id = snapshot.get("agent_id") or None
    frozen_expert_id = snapshot.get("expert_id") or None
    if session_id and store is not None and not task.get("config_snapshot"):
        # Older sessions are assigned one immutable snapshot the first time they are resumed.
        store.set_task_config(session_id, frozen_agent_id, frozen_expert_id, snapshot)
        task = store.get_task(session_id, owner_id)
        if task and task.get("config_snapshot"):
            snapshot = task["config_snapshot"]
            frozen_agent_id = task.get("agent_id")
            frozen_expert_id = task.get("expert_id")

    # 记下这次调用：内核崩溃时靠它补跑（进程死了任务真的丢了，但用户意图还在）
    frozen_runtime = snapshot.get("runtime") or {}
    params = {
        "goal": goal,
        "mode": frozen_runtime.get("mode"),
        "interaction": frozen_runtime.get("interaction"),
        "style": frozen_runtime.get("style"),
        "expert": frozen_expert_id,
        "agent_id": frozen_agent_id,
        "config_snapshot": snapshot,
        # 对话续接：带上会话 id，内核就知道要把上一轮的历史带过来
        "session_id": session_id,
        "owner_id": owner_id,
    }
    agent.remember_run(**params)
    # 登记「有发起在途」：这段时间里到达的停止请求要排队等它落地，
    # 不能直接丢掉（详见 DispatchTracker 的注释）。
    dispatch.begin(req.seq, owner_id, session_id)
    try:
        result = await agent.call_async("chat.run", **params)
    except IpcError as exc:
        dispatch.end(req.seq, session_id)
        raise rpc_failure(exc) from None
    except Exception:
        dispatch.end(req.seq, session_id)
        raise
    result_task_id = str(result.get("task_id") or session_id or "") or None
    dispatch.end(req.seq, result_task_id)
    # 这轮 chat 落地了 —— 之前「提前到达」的中止请求要补上。
    # （用户在 chat 请求还在路上时就点了停止，那一下先前被记下来了。）
    if dispatch.take_cancel(req.seq):
        try:
            await agent.call_async(
                "chat.cancel", task_id=result_task_id, owner_id=owner_id,
            )
        except IpcError:
            pass
    return result


async def _resolve_run_snapshot(agent, config, req: ChatRequest, store, task, owner_id: str | None) -> dict:
    """Resolve selected definitions once and preserve the exact values on a task."""
    existing = (task or {}).get("config_snapshot")
    selected_agent = (req.agent_id or "").strip() or None
    if "expert" in req.model_fields_set:
        selected_expert = (req.expert or "").strip() or None
    else:
        selected_expert = (config.data.get("expert") or "").strip() or None

    if isinstance(existing, dict):
        requested_agent = selected_agent if "agent_id" in req.model_fields_set else task.get("agent_id")
        requested_expert = selected_expert if "expert" in req.model_fields_set else task.get("expert_id")
        if requested_agent != task.get("agent_id") or requested_expert != task.get("expert_id"):
            raise HTTPException(409, "此会话的 Agent 与专家配置已固定，请新建会话后切换")
        return existing

    from .persona import agent_detail, expert_detail

    agent_config = await agent_detail(agent, selected_agent) if selected_agent else None
    expert_config = await expert_detail(agent, selected_expert) if selected_expert else None
    if agent_config:
        agent_config = {k: v for k, v in agent_config.items() if k not in ("creator_id", "can_edit", "shared")}
    if expert_config:
        expert_config = {k: v for k, v in expert_config.items() if k not in ("creator_id", "can_edit", "shared")}

    return {
        "version": 1,
        "agent_id": selected_agent,
        "expert_id": selected_expert,
        "agent": agent_config,
        "expert": expert_config,
        "runtime": {
            "mode": req.mode or config.data.get("mode") or getattr(config, "mode", "efficiency"),
            "interaction": req.interaction or config.data.get("interaction") or "craft",
            "style": req.style or config.data.get("style") or "professional",
        },
    }


async def retry_chat(agent, config, store, req: RetryRequest,
                     owner_id: str | None = None) -> dict:
    """重新生成：删掉最后一轮助手回复，用同一提问重跑。

    对齐 Marvis 的「重新生成」：只重生成回答，用户说过的话一个字都不动。

    删消息的动作在控制面做（store 就在本进程），重跑交给内核 ——
    这样「删旧 + 跑新」和普通发消息走的是同一条内核路径。
    """
    from ..agentlink import IpcError

    task_id = (req.task_id or "").strip()
    if not task_id:
        raise HTTPException(400, "task_id 不能为空")
    task = store.get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(404, "没有这个会话")

    try:
        prep = store.prepare_retry(task_id)
    except Exception as exc:  # noqa: BLE001 — 别把内部错误直接抛给前端
        raise HTTPException(500, f"准备重跑失败：{exc}") from None
    if not prep.get("ok"):
        raise HTTPException(404, prep.get("reason") or "这个会话不能重跑")

    goal = prep["goal"]
    params = {
        "goal": goal,
        "mode": (task.get("config_snapshot") or {}).get("runtime", {}).get("mode") or config.data.get("mode"),
        "interaction": (task.get("config_snapshot") or {}).get("runtime", {}).get("interaction") or config.data.get("interaction"),
        "style": (task.get("config_snapshot") or {}).get("runtime", {}).get("style") or config.data.get("style"),
        "expert": task.get("expert_id") or None,
        "agent_id": task.get("agent_id") or None,
        "config_snapshot": task.get("config_snapshot"),
        # 复用同一个会话：内核知道要把前面的历史带过来
        "session_id": task_id,
        "owner_id": owner_id,
        # 那条提问还留在库里（prepare_retry 保留了它）—— 让内核别再写一遍
        "question_kept": True,
    }
    agent.remember_run(**params)
    try:
        result = await agent.call_async("chat.run", **params)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {**result, "ok": True, "task_id": task_id, "removed": prep["removed"]}


async def cancel_chat(agent, bus, dispatch, seq: int,
                      owner_id: str | None = None,
                      task_id: str | None = None) -> dict:
    """中止正在跑的任务。"""
    from ..agentlink import IpcError

    state = dispatch.state(seq)
    # Explicit task cancellation is verified against the running worker in the
    # kernel. The dispatch tracker only represents the most recent HTTP launch
    # and must not reject a valid task because another request arrived later.
    if task_id is None and not dispatch.authorized_cancel(owner_id):
        raise HTTPException(404, "没有这个会话")
    if task_id is None and state == "inflight":
        # 发起还在投递 —— 等它落地再停（有超时，别把请求挂死）
        await dispatch.wait_landed(timeout=10.0)
    elif task_id is None and state == "unknown" and dispatch.note_cancel(seq):
        # 这一轮的 chat 请求可能还在路上 —— 记下这笔中止，
        # 等 chat 到了会补一次（见 run_chat 里的 take_cancel）。
        return {"ok": True, "task_id": "", "pending": True}
    target_task_id = task_id or dispatch.current_task_id()
    try:
        result = await agent.call_async(
            "chat.cancel", task_id=target_task_id, owner_id=owner_id,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise HTTPException(409, str(result.get("error") or "现在没有正在跑的任务"))
    bus.publish("RUN_CANCELLING", task_id=result.get("task_id", ""))
    return {"ok": True, "task_id": result.get("task_id", "")}


def cancel_run_payload(result: dict[str, Any]) -> dict:
    """把内核的中止结果整理成前端要的形状。

    2026-11-26（模块 11）：补上「停的是哪一个会话」。
    以前只回 `ok`，前端停完不知道到底停没停住 ——
    现在多回 `task_id`，界面可以据此判断「停的是当前这个会话」。
    """
    return {"ok": bool(result.get("ok")), "task_id": str(result.get("task_id") or "")}


def read_clipboard() -> dict:
    """读系统剪贴板。

    走本机后端而不是浏览器：剪贴板里的**文件**（CF_HDROP）
    是 Win32 的东西，Web 页面读不到 —— 前端 navigator.clipboard
    永远只能拿到文本与图片。
    """
    from .. import clipboard as clip

    try:
        return clip.read_clipboard()
    except Exception as exc:  # noqa: BLE001 — 剪贴板被占着之类的情况不该 500
        return {"kind": "empty", "detail": f"读剪贴板失败：{exc}"}


class PasteRequest(BaseModel):
    """粘贴导入请求 —— 以前是裸 dict 请求体，字段零校验。

    body 传成数组 / 字符串时，原来的 `req.get` 会抛 AttributeError → 500；
    现在契约层直接 422，而且字段不再被 `str()` 静默强转
    （传个对象进来以前会变成字面量 "{...}" 落盘）。
    """
    kind: str = ""
    data_url: str = ""
    text: str = ""
    name: str = ""


def paste_item(req: PasteRequest) -> dict:
    """粘贴导入：把剪贴板里的图片 / 文本落盘，返回可交给对话的路径。

    文件不走这里 —— 文件路径是原路径，没有「落盘」可言，
    前端直接用 /api/clipboard 返回的 paths 即可。
    """
    from .. import paste as paste_mod

    kind = req.kind.strip()
    if kind == "image":
        data_url = req.data_url
        if not data_url:
            raise HTTPException(400, "没有图片数据")
        try:
            item = paste_mod.save_image_data_url(data_url, req.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "item": item}
    if kind == "text":
        text = req.text
        if not text.strip():
            raise HTTPException(400, "没有文本内容")
        try:
            item = paste_mod.save_text(text, req.name or "pasted.txt")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "item": item}
    raise HTTPException(400, "kind 只能是 image 或 text")


async def approve_step(agent, req: ApprovalRequest, store=None,
                       owner_id: str | None = None) -> dict:
    """审批一个待确认的步骤。"""
    from ..agentlink import IpcError

    if store is not None:
        task_id = store.task_id_for_step(req.step_id)
        if not task_id or store.get_task(task_id, owner_id) is None:
            raise HTTPException(404, "没有这个待审批步骤")

    try:
        ok = await agent.call_async("chat.approve",
                                    step_id=req.step_id, approved=req.approved)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not ok:
        raise HTTPException(404, "该步骤不在等待确认状态")
    return {"step_id": req.step_id, "approved": req.approved}


def register(app, agent, store, config, bus) -> None:
    @router.post("/chat")
    async def _chat(req: ChatRequest, request: Request):
        return ok(await run_chat(
            agent, config, _dispatch, req, store, getattr(request.state, "user_id", None)
        ))

    @router.post("/chat/retry")
    async def _retry(req: RetryRequest, request: Request):
        return ok(await retry_chat(
            agent, config, store, req, getattr(request.state, "user_id", None)
        ))

    @router.post("/chat/cancel")
    async def _cancel(request: Request, req: CancelRequest | None = None):
        seq = req.seq if req is not None else 0
        return json(await cancel_chat(
            agent, bus, _dispatch, seq, getattr(request.state, "user_id", None),
            req.task_id if req is not None else None,
        ))

    @router.get("/clipboard")
    async def _clipboard():
        return json(read_clipboard())

    @router.post("/paste")
    async def _paste(req: PasteRequest):
        return json(paste_item(req))

    @router.post("/approve")
    async def _approve(req: ApprovalRequest, request: Request):
        return json(await approve_step(
            agent, req, store, getattr(request.state, "user_id", None)
        ))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
