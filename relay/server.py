"""Oryx 中继服务器：把 PC 和手机配对，只转发密文。

设计原则：中继**不应该**需要信任，也不应该能看懂内容。
它只知道「房间号 + 两个端点」，转发原始字节。

部署：放一台有公网 IP 的机器，`python relay/server.py --port 8899`，
前面套一层 TLS（Caddy / Nginx），手机与 PC 都连 wss://。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oryx.relay")

MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_ROOM_CLIENTS = 2  # 一个 host + 一个 client
IDLE_TIMEOUT = 180


class Room:
    __slots__ = ("host", "client", "created")

    def __init__(self) -> None:
        self.host: Any = None
        self.client: Any = None
        self.created = time.time()

    def peer_of(self, role: str) -> Any:
        return self.client if role == "host" else self.host


rooms: dict[str, Room] = defaultdict(Room)
stats = {"connections": 0, "messages": 0, "bytes": 0}


async def handler(websocket) -> None:
    path = getattr(getattr(websocket, "request", None), "path", "") or ""
    query = parse_qs(urlparse(path).query)
    role = (query.get("role") or [""])[0]
    room_id = (query.get("room") or [""])[0].strip().upper()

    if role not in ("host", "client") or not room_id:
        await websocket.close(code=1008, reason="missing role or room")
        return

    room = rooms[room_id]
    if getattr(room, role) is not None:
        await websocket.close(code=1008, reason=f"room already has a {role}")
        return
    setattr(room, role, websocket)
    stats["connections"] += 1
    log.info("join room=%s role=%s (host=%s client=%s)",
             room_id, role, bool(room.host), bool(room.client))

    try:
        async for message in websocket:
            stats["messages"] += 1
            stats["bytes"] += len(message) if isinstance(message, (bytes, bytearray)) else 0
            peer = room.peer_of(role)
            if peer is None:
                continue  # 对端还没来，直接丢弃，不排队（实时画面排队没意义）
            try:
                await peer.send(message)
            except Exception:
                log.debug("forward failed room=%s", room_id)
    except Exception as exc:
        log.debug("connection error room=%s: %s", room_id, exc)
    finally:
        if getattr(room, role) is websocket:
            setattr(room, role, None)
        if room.host is None and room.client is None:
            rooms.pop(room_id, None)
        log.info("leave room=%s role=%s", room_id, role)


async def health(websocket) -> None:  # pragma: no cover - 仅用于探活
    await websocket.send("ok")


def _health_response(connection, request):
    """非 WebSocket 请求返回简单文本，方便云平台探活。"""
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    if request.path.startswith("/health"):
        body = f'{{"rooms":{len(rooms)},"messages":{stats["messages"]}}}'.encode()
        return Response(200, "OK", Headers([("Content-Type", "application/json")]), body)
    if request.path.startswith("/stats"):
        body = (
            f'{{"rooms":{len(rooms)},"connections":{stats["connections"]},'
            f'"messages":{stats["messages"]},"bytes":{stats["bytes"]}}}'
        ).encode()
        return Response(200, "OK", Headers([("Content-Type", "application/json")]), body)
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Oryx 中继服务器")
    parser.add_argument("--host", default=os.environ.get("ORYX_RELAY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8899")))
    args = parser.parse_args()

    try:
        from websockets.asyncio.server import serve
    except ImportError:  # 兼容旧版 websockets
        from websockets import serve  # type: ignore

    log.info("Oryx 中继启动：%s:%s", args.host, args.port)
    async with serve(
        handler,
        args.host,
        args.port,
        max_size=MAX_MESSAGE_BYTES,
        ping_interval=20,
        ping_timeout=40,
        process_request=_health_response,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
