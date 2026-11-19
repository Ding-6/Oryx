"""跨端链路端到端：中继 → PC 推流 → 手机收帧 / 回传操作。

用真实的中继进程和真实的 WebSocket，只把"注入键鼠"换成 dry-run，
避免测试真的去动用户的鼠标。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oryx.remote import protocol
from oryx.remote.host import RemoteHost

ROOT = Path(__file__).resolve().parents[2]
RELAY = ROOT / "relay" / "server.py"
CODE = "TESTCODE"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def relay():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(RELAY), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError("中继进程提前退出")
            time.sleep(0.2)
    yield f"ws://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_key_derivation_is_deterministic():
    key = protocol.derive_key("testcode")
    assert key == protocol.derive_key("TESTCODE")   # 大小写不敏感
    assert key == protocol.derive_key("TEST-CODE")  # 忽略分隔符
    assert len(key) == 32
    assert key != protocol.derive_key("OTHER")


def test_seal_roundtrip():
    key = protocol.derive_key(CODE)
    blob = protocol.pack_control(key, {"t": "hello", "role": "host"})
    kind, payload = protocol.unpack(key, blob)
    assert kind == protocol.KIND_CONTROL
    assert payload["t"] == "hello"

    frame = b"\xff\xd8" + b"x" * 100
    kind, payload = protocol.unpack(key, protocol.pack_frame(key, frame))
    assert kind == protocol.KIND_FRAME
    assert payload == frame


def test_wrong_key_cannot_decrypt():
    with pytest.raises(Exception):
        protocol.unpack(protocol.derive_key("WRONG"), protocol.pack_control(protocol.derive_key(CODE), {"t": "x"}))


def test_code_format():
    code = protocol.generate_code()
    assert len(code) == protocol.CODE_LENGTH
    assert all(ch in protocol.CODE_ALPHABET for ch in code)


@pytest.mark.asyncio
async def test_end_to_end_through_relay(relay, monkeypatch):
    import websockets

    monkeypatch.setenv("ORYX_REMOTE_DRY_RUN", "1")

    events: list[dict] = []
    host = RemoteHost(relay, code=CODE, fps=10, max_width=640)
    host.on_event = events.append
    host_task = asyncio.create_task(host.run())
    await asyncio.sleep(1.0)

    key = protocol.derive_key(CODE)
    url = f"{relay}/ws?role=client&room={CODE}"
    async with websockets.connect(url, max_size=4 * 1024 * 1024) as phone:
        await phone.send(protocol.pack_control(key, protocol.hello("client")))
        await phone.send(protocol.pack_control(key, {"t": "ready"}))

        frame = None
        deadline = time.time() + 15
        while time.time() < deadline and frame is None:
            try:
                blob = await asyncio.wait_for(phone.recv(), timeout=5)
            except asyncio.TimeoutError:
                break
            if isinstance(blob, str):
                continue
            kind, payload = protocol.unpack(key, blob)
            if kind == protocol.KIND_FRAME:
                frame = payload

        assert frame is not None, "应当收到屏幕帧"
        assert frame[:2] == b"\xff\xd8", "帧应当是 JPEG"
        assert len(frame) > 1000

        # 回传一次点击，PC 侧应当执行（dry-run 只算坐标）
        await phone.send(
            protocol.pack_control(key, protocol.input_event("tap", x=0.5, y=0.5))
        )
        await asyncio.sleep(1.0)

    host.stop()
    try:
        await asyncio.wait_for(host_task, timeout=10)
    except Exception:
        pass

    assert host.frames_sent > 0
    assert host.inputs_applied >= 1
    assert any(e["t"] == "peer_ready" for e in events)
