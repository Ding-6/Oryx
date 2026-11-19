"""内核进程间通信：行分隔 JSON over TCP。

对齐 Marvis 的「壳 → 网关 → 内核」三段式。这里只实现最后一段：
server.py（控制面）与 agentd.py（Agent 内核）之间的消息总线。

为什么是行分隔 JSON 而不是 nng/WebSocket：
- 只监听 127.0.0.1，且外面还套着令牌校验，通道本身不需要加密
- 行分隔 JSON 任何语言都能实现，将来换 Rust 网关时不用重写协议
- 出问题时用 netcat 就能看帧，排查成本低

三种帧：

    {"kind": "req", "id": 1, "method": "chat.run", "params": {...}}
    {"kind": "res", "id": 1, "ok": true, "result": {...}}
    {"kind": "evt", "event": {...}}

req/res 靠 id 配对，evt 是单向推送（Agent 内核的事件流）。
"""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import threading
from typing import Any, Callable

# 单帧上限。工具结果可能是整份文档，所以给到 32MB
MAX_FRAME = 32 * 1024 * 1024
CONNECT_TIMEOUT = 5.0
CALL_TIMEOUT = 600.0  # 索引扫描 / 长任务可能跑很久


class IpcError(Exception):
    """IPC 层错误（连接断了、帧坏了、方法不存在等）。"""


class ConnectionClosed(IpcError):
    """连接被对端关闭。"""


def _send(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(data) > MAX_FRAME:
        raise IpcError(f"帧太大：{len(data)} 字节")
    sock.sendall(data)


class FrameReader:
    """把字节流切成帧。

    一次 recv 可能带回多帧（甚至半帧），所以缓冲必须是连接级持久的 ——
    用函数里的局部变量会把同一批里后面的帧直接吃掉。
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def next_frame(self, sock: socket.socket) -> dict[str, Any]:
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                frame = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                return self._decode(frame)
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionClosed("对端关闭了连接")
            self._buf += chunk
            if len(self._buf) > MAX_FRAME:
                raise IpcError("帧超过上限")

    @staticmethod
    def _decode(frame: bytes) -> dict[str, Any]:
        try:
            return json.loads(frame.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IpcError(f"帧不是合法 JSON：{exc}") from exc


# ---------------------------------------------------------------- 服务端


class IpcServer:
    """请求-响应服务端。一条连接上并发处理多个请求。"""

    def __init__(
        self,
        methods: dict[str, Callable[..., Any]],
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.methods = methods
        self.on_event = on_event
        self._sock: socket.socket | None = None
        self._clients: set[socket.socket] = set()
        self._write_locks: dict[int, threading.Lock] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def serve_forever(self, host: str, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(16)
        sock.settimeout(0.5)  # 便于定期检查停止标志
        self._sock = sock
        while not self._stop_evt.is_set():
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(None)
            with self._lock:
                self._clients.add(conn)
            threading.Thread(
                target=self._handle, args=(conn,), name="oryx-ipc-conn", daemon=True
            ).start()

    def start(self, host: str, port: int) -> int:
        """后台起服务，返回实际监听的端口（port=0 时由系统分配）。"""
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self.serve_forever, args=(host, port),
            name="oryx-ipc-accept", daemon=True,
        )
        self._thread.start()
        for _ in range(100):
            if self._sock is not None:
                return self._sock.getsockname()[1]
            threading.Event().wait(0.02)
        raise IpcError("IPC 服务未能启动")

    def stop(self) -> None:
        self._stop_evt.set()
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
            self._write_locks.clear()
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    # ---- 内部 ----
    def _write(self, conn: socket.socket, payload: dict[str, Any]) -> bool:
        """往一条连接写一帧。

        一个连接上可能有多个请求线程同时回包（sendall 不是线程安全的，
        并发写会把两帧的字节交错成乱码），所以按连接维度串行化。
        """
        with self._lock:
            guard = self._write_locks.get(id(conn))
            if guard is None:
                guard = threading.Lock()
                self._write_locks[id(conn)] = guard
        with guard:
            try:
                _send(conn, payload)
                return True
            except (OSError, IpcError):
                return False

    def _handle(self, conn: socket.socket) -> None:
        reader = FrameReader()
        try:
            while not self._stop_evt.is_set():
                try:
                    frame = reader.next_frame(conn)
                except (socket.timeout, ConnectionClosed, IpcError, OSError):
                    return
                kind = frame.get("kind")
                if kind == "req":
                    threading.Thread(
                        target=self._serve_req, args=(conn, frame),
                        name="oryx-ipc-req", daemon=True,
                    ).start()
                elif kind == "evt":
                    if self.on_event is not None:
                        try:
                            self.on_event(frame.get("event") or {})
                        except Exception:
                            pass
        finally:
            with self._lock:
                self._clients.discard(conn)
                self._write_locks.pop(id(conn), None)
            try:
                conn.close()
            except OSError:
                pass

    def _serve_req(self, conn: socket.socket, frame: dict[str, Any]) -> None:
        rid = frame.get("id")
        method = frame.get("method", "")
        params = frame.get("params") or {}
        try:
            fn = self.methods.get(method)
            if fn is None:
                raise IpcError(f"没有这个方法：{method}")
            result = fn(**params) if isinstance(params, dict) else fn(params)
            reply: dict[str, Any] = {"kind": "res", "id": rid, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001 — 错误要带回调用方而不是打断连接
            reply = {
                "kind": "res", "id": rid, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        self._write(conn, reply)

    def emit(self, event: dict[str, Any]) -> None:
        """把事件推给所有已连客户端。"""
        frame = {"kind": "evt", "event": event}
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            self._write(c, frame)


# ---------------------------------------------------------------- 客户端


class IpcClient:
    """请求-响应客户端。一个后台读线程把帧分派给等待中的调用方或事件回调。

    对外同时提供同步（call）与异步（call_async）两套入口：
    FastAPI 端点是 async 用后者，Agent 工作线程用前者。
    """

    def __init__(self, host: str, port: int, on_event: Callable[[dict[str, Any]], None] | None = None,
                 connect_timeout: float = CONNECT_TIMEOUT) -> None:
        self.on_event = on_event
        self._pending: dict[int, queue.Queue] = {}
        self._asyncs: dict[int, tuple] = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self._closed = False

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(connect_timeout)
        sock.connect((host, port))
        sock.settimeout(None)
        self._sock = sock
        # reader 线程一起来就能收帧，不需要额外的握手或就绪等待
        threading.Thread(target=self._read_loop, name="oryx-ipc-reader", daemon=True).start()

    def _read_loop(self) -> None:
        reader = FrameReader()
        try:
            while not self._closed:
                frame = reader.next_frame(self._sock)
                kind = frame.get("kind")
                if kind == "res":
                    self._settle(frame.get("id"), frame)
                elif kind == "evt":
                    if self.on_event is not None:
                        try:
                            self.on_event(frame.get("event") or {})
                        except Exception:
                            pass
        except (ConnectionClosed, IpcError, OSError):
            # 连接断了：所有等待方都要立刻拿到失败，不能挂到超时
            self._fail_all("与 Agent 内核的连接已断开")

    def _settle(self, rid: Any, frame: dict[str, Any]) -> None:
        """把一帧应答交给对应的等待方（同步队列或 asyncio future）。"""
        with self._lock:
            waiter = self._pending.pop(rid, None)
            pending_async = self._asyncs.pop(rid, None)
        if waiter is not None:
            waiter.put(frame)
        if pending_async is not None:
            loop, fut = pending_async
            if not frame.get("ok"):
                loop.call_soon_threadsafe(
                    _set_exception, fut, IpcError(str(frame.get("error", "未知错误")))
                )
            else:
                loop.call_soon_threadsafe(fut.set_result, frame.get("result"))

    def _forget(self, rid: int) -> None:
        with self._lock:
            self._pending.pop(rid, None)
            self._asyncs.pop(rid, None)

    def _fail_all(self, msg: str) -> None:
        with self._lock:
            waiters = list(self._pending.values())
            asyncs = list(self._asyncs.values())
            self._pending.clear()
            self._asyncs.clear()
        frame = {"ok": False, "error": msg}
        for w in waiters:
            w.put(frame)
        for loop, fut in asyncs:
            loop.call_soon_threadsafe(_set_exception, fut, IpcError(msg))

    def _register(self, method: str, params: dict[str, Any]) -> tuple[int, queue.Queue]:
        with self._lock:
            if self._closed:
                raise IpcError("IPC 客户端已关闭")
            self._next_id += 1
            rid = self._next_id
            waiter: queue.Queue = queue.Queue(maxsize=1)
            self._pending[rid] = waiter
        try:
            _send(self._sock, {"kind": "req", "id": rid, "method": method, "params": params})
        except (OSError, IpcError):
            self._forget(rid)
            raise IpcError("无法向 Agent 内核发送请求") from None
        return rid, waiter

    def call(self, method: str, timeout: float = CALL_TIMEOUT, **params: Any) -> Any:
        """同步调用。阻塞当前线程。"""
        rid, waiter = self._register(method, params)
        try:
            frame = waiter.get(timeout=timeout)
        except queue.Empty:
            self._forget(rid)
            raise IpcError(f"调用 {method} 超时（{timeout}s）") from None
        if not frame.get("ok"):
            raise IpcError(str(frame.get("error", "未知错误")))
        return frame.get("result")

    async def call_async(self, method: str, timeout: float = CALL_TIMEOUT, **params: Any) -> Any:
        """异步调用。不阻塞事件循环线程，也不额外起线程。"""
        loop = asyncio.get_running_loop()
        rid, waiter = self._register(method, params)
        fut: asyncio.Future = loop.create_future()
        with self._lock:
            self._asyncs[rid] = (loop, fut)

        # 应答可能正好落在上面那个空档里，先自取一次再交给 asyncio 通知
        try:
            frame = waiter.get_nowait()
        except queue.Empty:
            frame = None
        if frame is not None:
            self._forget(rid)
            if not frame.get("ok"):
                raise IpcError(str(frame.get("error", "未知错误")))
            return frame.get("result")

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._forget(rid)
            raise IpcError(f"调用 {method} 超时（{timeout}s）") from None

    def close(self) -> None:
        self._closed = True
        self._fail_all("IPC 客户端已关闭")
        try:
            self._sock.close()
        except OSError:
            pass


def _set_exception(fut: asyncio.Future, exc: BaseException) -> None:
    """给 future 塞异常。已取消/已超时的 future 会抛 InvalidStateError，吞掉。"""
    if not fut.done():
        fut.set_exception(exc)