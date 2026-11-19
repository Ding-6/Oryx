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

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ._common import json, rpc_failure

router = APIRouter(prefix="/api", tags=["对话"])


# ══════════ 请求模型 ══════════

class ChatRequest(BaseModel):
    goal: str
    mode: str | None = None
    interaction: str | None = None
    style: str | None = None
    expert: str | None = None
    session_id: str | None = None
    seq: int = 0


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

    def begin(self, seq: int) -> None:
        with self._lock:
            self._inflight = seq
            if seq > self._max_seq:
                self._max_seq = seq
            self._done.clear()

    def end(self, seq: int) -> None:
        with self._lock:
            if self._inflight == seq:
                self._inflight = 0
            self._landed.add(seq)
            # 只留最近 32 个落地序号 —— 免得这个集合无限涨
            if len(self._landed) > 32:
                for old in sorted(self._landed)[: len(self._landed) - 32]:
                    self._landed.discard(old)
        self._done.set()

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

async def run_chat(agent, config, dispatch, req: ChatRequest) -> dict:
    """发起一轮对话。"""
    from ..agentlink import IpcError

    goal = (req.goal or "").strip()
    if not goal:
        raise HTTPException(400, "goal 不能为空")

    # 记下这次调用：内核崩溃时靠它补跑（进程死了任务真的丢了，但用户意图还在）
    params = {
        "goal": goal,
        "mode": req.mode,
        "interaction": req.interaction,
        "style": req.style,
        "expert": req.expert,
        # 对话续接：带上会话 id，内核就知道要把上一轮的历史带过来
        "session_id": (req.session_id or "").strip() or None,
    }
    agent.remember_run(**params)
    # 登记「有发起在途」：这段时间里到达的停止请求要排队等它落地，
    # 不能直接丢掉（详见 DispatchTracker 的注释）。
    dispatch.begin(req.seq)
    try:
        result = await agent.call_async("chat.run", **params)
    except IpcError as exc:
        dispatch.end(req.seq)
        raise rpc_failure(exc) from None
    except Exception:
        dispatch.end(req.seq)
        raise
    dispatch.end(req.seq)
    # 这轮 chat 落地了 —— 之前「提前到达」的中止请求要补上。
    # （用户在 chat 请求还在路上时就点了停止，那一下先前被记下来了。）
    if dispatch.take_cancel(req.seq):
        try:
            await agent.call_async("chat.cancel")
        except IpcError:
            pass
    return result


async def retry_chat(agent, config, store, req: RetryRequest) -> dict:
    """重新生成：删掉最后一轮助手回复，用同一提问重跑。

    对齐 Marvis 的「重新生成」：只重生成回答，用户说过的话一个字都不动。

    删消息的动作在控制面做（store 就在本进程），重跑交给内核 ——
    这样「删旧 + 跑新」和普通发消息走的是同一条内核路径。
    """
    from ..agentlink import IpcError

    task_id = (req.task_id or "").strip()
    if not task_id:
        raise HTTPException(400, "task_id 不能为空")

    try:
        prep = store.prepare_retry(task_id)
    except Exception as exc:  # noqa: BLE001 — 别把内部错误直接抛给前端
        raise HTTPException(500, f"准备重跑失败：{exc}") from None
    if not prep.get("ok"):
        raise HTTPException(404, prep.get("reason") or "这个会话不能重跑")

    goal = prep["goal"]
    params = {
        "goal": goal,
        "mode": config.data.get("mode"),
        "interaction": config.data.get("interaction"),
        "style": config.data.get("style"),
        "expert": config.data.get("expert"),
        # 复用同一个会话：内核知道要把前面的历史带过来
        "session_id": task_id,
        # 那条提问还留在库里（prepare_retry 保留了它）—— 让内核别再写一遍
        "question_kept": True,
    }
    agent.remember_run(**params)
    try:
        result = await agent.call_async("chat.run", **params)
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {**result, "ok": True, "task_id": task_id, "removed": prep["removed"]}


async def cancel_chat(agent, bus, dispatch, seq: int) -> dict:
    """中止正在跑的任务。"""
    from ..agentlink import IpcError

    state = dispatch.state(seq)
    if state == "inflight":
        # 发起还在投递 —— 等它落地再停（有超时，别把请求挂死）
        await dispatch.wait_landed(timeout=10.0)
    elif state == "unknown" and dispatch.note_cancel(seq):
        # 这一轮的 chat 请求可能还在路上 —— 记下这笔中止，
        # 等 chat 到了会补一次（见 run_chat 里的 take_cancel）。
        return {"ok": True, "task_id": "", "pending": True}
    try:
        result = await agent.call_async("chat.cancel")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    if not result.get("ok"):
        raise HTTPException(409, str(result.get("error") or "现在没有正在跑的任务"))
    bus.publish("RUN_CANCELLING", task_id=result.get("task_id", ""))
    return {"ok": True, "task_id": result.get("task_id", "")}


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


def paste_item(req: dict) -> dict:
    """粘贴导入：把剪贴板里的图片 / 文本落盘，返回可交给对话的路径。

    文件不走这里 —— 文件路径是原路径，没有「落盘」可言，
    前端直接用 /api/clipboard 返回的 paths 即可。
    """
    from .. import paste as paste_mod

    kind = str(req.get("kind", "") or "").strip()
    if kind == "image":
        data_url = str(req.get("data_url", "") or "")
        if not data_url:
            raise HTTPException(400, "没有图片数据")
        try:
            item = paste_mod.save_image_data_url(data_url, str(req.get("name", "") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "item": item}
    if kind == "text":
        text = str(req.get("text", "") or "")
        if not text.strip():
            raise HTTPException(400, "没有文本内容")
        try:
            item = paste_mod.save_text(text, str(req.get("name", "") or "pasted.txt"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "item": item}
    raise HTTPException(400, "kind 只能是 image 或 text")


async def approve_step(agent, req: ApprovalRequest) -> dict:
    """审批一个待确认的步骤。"""
    from ..agentlink import IpcError

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
    async def _chat(req: ChatRequest):
        return json(await run_chat(agent, config, _dispatch, req))

    @router.post("/chat/retry")
    async def _retry(req: RetryRequest):
        return json(await retry_chat(agent, config, store, req))

    @router.post("/chat/cancel")
    async def _cancel(req: CancelRequest | None = None):
        seq = req.seq if req is not None else 0
        return json(await cancel_chat(agent, bus, _dispatch, seq))

    @router.get("/clipboard")
    async def _clipboard():
        return json(read_clipboard())

    @router.post("/paste")
    async def _paste(req: dict):
        return json(paste_item(req))

    @router.post("/approve")
    async def _approve(req: ApprovalRequest):
        return json(await approve_step(agent, req))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
