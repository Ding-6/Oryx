"""PC 端：把屏幕推到中转，接收手机操作并注入。

运行模型：一个 asyncio 任务负责抓屏发送，另一个负责收事件。
两端都通过中转转发，PC 不需要公网 IP、不需要开端口。
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any, Callable

from . import protocol
from . import input_win

log = logging.getLogger("oryx.remote")

DEFAULT_FPS = 8
DEFAULT_MAX_WIDTH = 1280
DEFAULT_QUALITY = 55
MAX_FRAME_BYTES = 4 * 1024 * 1024

# 跨端安全策略等级（对齐 Marvis security_policy：按配对设备分级）
POLICY_READONLY = 1    # 只读：只能看屏幕，一切输入拒绝
POLICY_CONTROLLED = 2  # 可控：指针/文本/安全键放行，系统键（win/ctrl/alt/delete）拦截
POLICY_FULL = 3        # 完整：全部输入放行

POLICY_LABELS = {
    POLICY_READONLY: "只读",
    POLICY_CONTROLLED: "可控",
    POLICY_FULL: "完整",
}

# 可控等级下拦截的系统键——防止远程组合键（Win+R、Alt+F4、Ctrl+Alt+Del 类）逃逸
_SYSTEM_KEYS = {"win", "ctrl", "alt", "delete"}


def normalize_policy(value: int | str | None, default: int = POLICY_CONTROLLED) -> int:
    """把配置/参数收敛到合法等级；非法值回退 default。"""
    try:
        level = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return level if level in POLICY_LABELS else default


class RemoteHost:
    def __init__(
        self,
        relay_url: str,
        code: str | None = None,
        fps: int = DEFAULT_FPS,
        max_width: int = DEFAULT_MAX_WIDTH,
        quality: int = DEFAULT_QUALITY,
        policy: int = POLICY_CONTROLLED,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.relay_url = relay_url.rstrip("/")
        self.code = protocol.normalize_code(code) if code else protocol.generate_code()
        self.key = protocol.derive_key(self.code)
        self.fps = max(1, min(30, int(fps)))
        self.max_width = max(320, int(max_width))
        self.quality = max(20, min(90, int(quality)))
        self.policy = normalize_policy(policy)
        self.on_event = on_event
        self._stop = asyncio.Event()
        self._ws = None
        self.frames_sent = 0
        self.inputs_applied = 0
        self.inputs_blocked = 0
        self.connected = False
        self.peer_ready = False
        self.last_error: str | None = None

    # ---------------- 对外 ----------------
    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "relay": self.relay_url,
            "connected": self.connected,
            "peer_ready": self.peer_ready,
            "fps": self.fps,
            "frames_sent": self.frames_sent,
            "inputs_applied": self.inputs_applied,
            "inputs_blocked": self.inputs_blocked,
            "policy": self.policy,
            "policy_label": POLICY_LABELS[self.policy],
            "error": self.last_error,
        }

    def set_policy(self, level: int) -> None:
        self.policy = normalize_policy(level, default=self.policy)

    # ---------------- 策略门禁 ----------------
    def _input_allowed(self, event: dict[str, Any]) -> tuple[bool, str]:
        """按策略等级判定一条远程输入是否放行。返回 (放行?, 原因)。"""
        if self.policy >= POLICY_FULL:
            return True, ""
        if self.policy <= POLICY_READONLY:
            return False, "当前设备是只读策略，已拒绝输入"
        # 可控：系统键拦截
        if event.get("kind") == "key" and str(event.get("name", "")).lower() in _SYSTEM_KEYS:
            return False, f"可控策略下拦截系统键：{event.get('name')}"
        return True, ""

    def _emit(self, kind: str, **fields: Any) -> None:
        if self.on_event:
            try:
                self.on_event({"t": kind, **fields, "ts": time.time()})
            except Exception:
                pass

    # ---------------- 主循环 ----------------
    async def run(self) -> None:
        import websockets

        room = self.code  # 中转按 room 配对；room 之外看不到内容
        url = f"{self.relay_url}/ws?role=host&room={room}"
        backoff = 1.0

        while not self._stop.is_set():
            try:
                async with websockets.connect(url, max_size=MAX_FRAME_BYTES, ping_interval=20) as ws:
                    self._ws = ws
                    self.connected = True
                    self.peer_ready = False
                    self.last_error = None
                    backoff = 1.0
                    await ws.send(protocol.pack_control(self.key, protocol.hello("host")))
                    width, height = input_win.screen_size()
                    await ws.send(
                        protocol.pack_control(self.key, protocol.screen_meta(width, height))
                    )
                    self._emit("connected", code=self.code, relay=self.relay_url)

                    receiver = asyncio.create_task(self._receive(ws))
                    sender = asyncio.create_task(self._send_frames(ws))
                    done, pending = await asyncio.wait(
                        {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        exc = task.exception()
                        if exc:
                            raise exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._emit("error", error=self.last_error)
                log.warning("中继连接断开：%s", exc)
            finally:
                self.connected = False
                self.peer_ready = False
                self._ws = None

            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

        self._emit("stopped", code=self.code)

    # ---------------- 发帧 ----------------
    async def _send_frames(self, ws) -> None:
        interval = 1.0 / self.fps
        while not self._stop.is_set():
            started = time.time()
            payload = await asyncio.to_thread(self._capture)
            if payload is not None:
                await ws.send(protocol.pack_frame(self.key, payload))
                self.frames_sent += 1
            elapsed = time.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    def _capture(self) -> bytes | None:
        from PIL import ImageGrab

        try:
            image = ImageGrab.grab()
        except Exception as exc:
            log.debug("抓屏失败：%s", exc)
            return None
        if image.width > self.max_width:
            ratio = self.max_width / image.width
            image = image.resize((self.max_width, int(image.height * ratio)))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=self.quality, optimize=False)
        return buffer.getvalue()

    # ---------------- 收事件 ----------------
    async def _receive(self, ws) -> None:
        async for blob in ws:
            if isinstance(blob, str):
                continue
            try:
                kind, payload = protocol.unpack(self.key, blob)
            except Exception:
                continue  # 解不开的消息直接丢弃，不暴露细节
            if kind != protocol.KIND_CONTROL or not isinstance(payload, dict):
                continue

            event_type = payload.get("t")
            if event_type == "ready":
                self.peer_ready = True
                self._emit("peer_ready")
            elif event_type == "input":
                allowed, reason = self._input_allowed(payload)
                if not allowed:
                    self.inputs_blocked += 1
                    self._emit("input_blocked", event=payload, reason=reason)
                    continue
                result = await asyncio.to_thread(input_win.apply_event, payload)
                self.inputs_applied += 1
                self._emit("input", event=payload, result=result)
            elif event_type == "bye":
                self._emit("peer_left")
                raise ConnectionError("手机端已断开")


