"""事件总线：AG-UI 风格事件流 + 断点续传。

设计对齐 Marvis 的 openclaw_handler/buffer_listener：
  - 每条事件带单调递增 seq 与 trace_id，前端凭 seq 判断是否缺段
  - 事件先进环形缓冲再分发，前端重连时用 ?since=N 补齐断连期间的事件
  - 缓冲上限 BUFFER_LIMIT，溢出丢最旧的（保新不保旧）

事件族（与 Marvis 的 AG-UI 一致）：
  RUN_STARTED / RUN_FINISHED / RUN_ERROR
  REASONING_START / REASONING_CONTENT / REASONING_END
  TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT / TEXT_MESSAGE_END
  TOOL_CALL_START / TOOL_CALL_ARGS / TOOL_CALL_END / TOOL_CALL_RESULT
  以及 confirm / single_select / ack 等人机交互指令
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable

# ── AG-UI 事件族常量 ─────────────────────────────────────────────
RUN_STARTED = "RUN_STARTED"
RUN_FINISHED = "RUN_FINISHED"
RUN_ERROR = "RUN_ERROR"
REASONING_START = "REASONING_START"
REASONING_CONTENT = "REASONING_CONTENT"
REASONING_END = "REASONING_END"
TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
TOOL_CALL_END = "TOOL_CALL_END"
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
CUSTOM = "CUSTOM"


# 协议保留字段：调用方不能覆盖。
# seq 尤其关键 —— 前端拿它做单调校验，一旦被业务字段（哪怕叫 seq）盖掉，
# 后面所有事件都会被当成重复丢掉，整个界面就此卡死。
RESERVED_KEYS = frozenset({"seq", "trace_id", "type", "ts"})

BUFFER_LIMIT = 4096


class EventBus:
    """线程安全的事件总线。

    publish() 可能从 Agent 工作线程调用，SSE 生成器在事件循环里消费，
    因此分发走 call_soon_threadsafe；缓冲区本身用锁保护。
    """

    def __init__(self, buffer_limit: int = BUFFER_LIMIT) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sync_sinks: list[Callable[[dict[str, Any]], None]] = []
        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_limit)
        self._seq = 0
        self._trace_id = uuid.uuid4().hex[:16]

    # ── 生命周期 ─────────────────────────────────────────────
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def add_sync_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        """测试或 CLI 模式下的同步订阅。"""
        self._sync_sinks.append(sink)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    # ── 断点续传 ─────────────────────────────────────────────
    @property
    def current_seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def control(self, kind: str, **payload: Any) -> dict[str, Any]:
        """构造控制帧（ready / resync）。

        控制帧的 seq 等于当前水位，不占号 —— 它们不是业务事件，
        前端要放行后再做单调校验，否则会把水位值误判成重复。
        """
        with self._lock:
            return {"type": kind, "seq": self._seq, "trace_id": self._trace_id, **payload}

    def replay_since(self, since: int, limit: int = 2000) -> list[dict[str, Any]]:
        """取出 seq > since 的历史事件，供重连补发。

        缓冲区已滚动时返回空列表 —— 这时前端应走整页刷新，
        宁可少补也不能乱序（前端对 seq 做过单调校验）。
        """
        with self._lock:
            if since < 0:
                return []
            oldest = self._buffer[0]["seq"] if self._buffer else self._seq
            # 请求的起点已被挤出缓冲，无法连续补齐
            if self._buffer and since + 1 < oldest:
                return []
            return [e for e in self._buffer if e["seq"] > since][:limit]

    def has_gap_since(self, since: int) -> bool:
        """缓冲是否已经滚过 since，导致存在无法补齐的空洞。"""
        with self._lock:
            return bool(self._buffer) and since + 1 < self._buffer[0]["seq"]

    # ── 发布 ────────────────────────────────────────────────
    def publish(self, event_type: str, **payload: Any) -> dict[str, Any]:
        """发一条事件。返回带 seq 的完整事件，便于调用方按需使用。"""
        # 保留字段优先，业务 payload 同名时直接丢弃而不是覆盖
        for key in RESERVED_KEYS & payload.keys():
            del payload[key]
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "trace_id": self._trace_id,
                "type": event_type,
                "ts": time.time(),
                **payload,
            }
            self._buffer.append(event)
            subscribers = list(self._subscribers)

        for sink in self._sync_sinks:
            try:
                sink(event)
            except Exception:
                pass

        for queue in subscribers:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._safe_put, queue, event)
            else:
                self._safe_put(queue, event)
        return event

    # ── AG-UI 语义化快捷方法 ────────────────────────────────
    def run_started(self, task_id: str, **kw: Any) -> dict[str, Any]:
        return self.publish(RUN_STARTED, task_id=task_id, **kw)

    def run_finished(self, task_id: str, status: str = "done", **kw: Any) -> dict[str, Any]:
        return self.publish(RUN_FINISHED, task_id=task_id, status=status, **kw)

    def run_error(self, task_id: str, error: str, **kw: Any) -> dict[str, Any]:
        return self.publish(RUN_ERROR, task_id=task_id, error=error, **kw)

    def reasoning_start(self, task_id: str, **kw: Any) -> dict[str, Any]:
        return self.publish(REASONING_START, task_id=task_id, **kw)

    def reasoning_delta(self, task_id: str, delta: str, **kw: Any) -> dict[str, Any]:
        return self.publish(REASONING_CONTENT, task_id=task_id, delta=delta, **kw)

    def reasoning_end(self, task_id: str, **kw: Any) -> dict[str, Any]:
        return self.publish(REASONING_END, task_id=task_id, **kw)

    def text_start(self, task_id: str, message_id: str, role: str = "assistant", **kw: Any) -> dict[str, Any]:
        return self.publish(TEXT_MESSAGE_START, task_id=task_id, message_id=message_id, role=role, **kw)

    def text_delta(self, task_id: str, message_id: str, delta: str, **kw: Any) -> dict[str, Any]:
        return self.publish(TEXT_MESSAGE_CONTENT, task_id=task_id, message_id=message_id, delta=delta, **kw)

    def text_end(self, task_id: str, message_id: str, **kw: Any) -> dict[str, Any]:
        return self.publish(TEXT_MESSAGE_END, task_id=task_id, message_id=message_id, **kw)

    def tool_start(self, task_id: str, tool_call_id: str, tool: str, **kw: Any) -> dict[str, Any]:
        return self.publish(TOOL_CALL_START, task_id=task_id, tool_call_id=tool_call_id, tool=tool, **kw)

    def tool_args(self, task_id: str, tool_call_id: str, args: Any, **kw: Any) -> dict[str, Any]:
        return self.publish(TOOL_CALL_ARGS, task_id=task_id, tool_call_id=tool_call_id, args=args, **kw)

    def tool_end(self, task_id: str, tool_call_id: str, tool: str, **kw: Any) -> dict[str, Any]:
        return self.publish(TOOL_CALL_END, task_id=task_id, tool_call_id=tool_call_id, tool=tool, **kw)

    def tool_result(self, task_id: str, tool_call_id: str, tool: str, result: Any,
                    ok: bool = True, **kw: Any) -> dict[str, Any]:
        return self.publish(TOOL_CALL_RESULT, task_id=task_id, tool_call_id=tool_call_id,
                            tool=tool, result=result, ok=ok, **kw)

    def custom(self, name: str, task_id: str = "", **payload: Any) -> dict[str, Any]:
        return self.publish(CUSTOM, name=name, task_id=task_id, **payload)

    # ── 内部 ────────────────────────────────────────────────
    @staticmethod
    def _safe_put(queue: asyncio.Queue, event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass