"""IPC 层：行分隔 JSON over TCP 的请求-响应与事件推送。"""

from __future__ import annotations

import threading
import time

import pytest

from oryx.ipc import FrameReader, IpcClient, IpcError, IpcServer


@pytest.fixture
def pair():
    """起一个 IPC 服务端，返回 (server, host, port)。"""
    events: list[dict] = []
    methods = {
        "echo": lambda **kw: kw,
        "add": lambda a, b: a + b,
        "boom": lambda: (_ for _ in ()).throw(ValueError("炸了")),
        "slow": lambda: (time.sleep(0.3), "done")[1],
    }
    server = IpcServer(methods, on_event=events.append)
    port = server.start("127.0.0.1", 0)
    yield server, events, port
    server.stop()


def test_frame_reader_handles_multiple_frames_in_one_recv():
    """一次 recv 带回多帧时，每一帧都要拿到。

    缓冲要是函数内的局部变量，同一批里的后续帧会被直接吃掉。
    """
    import socket as _socket
    import threading as _threading

    left, right = _socket.socketpair()
    reader = FrameReader()

    def feed() -> None:
        right.sendall(b'{"kind":"res","id":1}\n{"kind":"res","id":2}\n{"kind":"res","id":3}\n')
        right.close()

    t = _threading.Thread(target=feed, daemon=True)
    t.start()
    try:
        ids = [reader.next_frame(left)["id"] for _ in range(3)]
        assert ids == [1, 2, 3]
    finally:
        left.close()
        t.join(timeout=2)


def test_frame_reader_reassembles_split_frame():
    """一帧被拆成多次送达，也要能拼回来。"""
    import socket as _socket
    import threading as _threading

    left, right = _socket.socketpair()
    reader = FrameReader()

    def feed() -> None:
        right.sendall(b'{"kind":"re')
        right.sendall(b's","id":7}')
        right.sendall(b"\n")
        right.close()

    t = _threading.Thread(target=feed, daemon=True)
    t.start()
    try:
        assert reader.next_frame(left)["id"] == 7
    finally:
        left.close()
        t.join(timeout=2)


def test_sync_call_roundtrip(pair):
    _server, _events, port = pair
    client = IpcClient("127.0.0.1", port)
    try:
        assert client.call("echo", name="oryx", n=3) == {"name": "oryx", "n": 3}
        assert client.call("add", a=2, b=5) == 7
    finally:
        client.close()


def test_unknown_method_returns_error(pair):
    _server, _events, port = pair
    client = IpcClient("127.0.0.1", port)
    try:
        with pytest.raises(IpcError, match="没有这个方法"):
            client.call("nope")
    finally:
        client.close()


def test_handler_exception_is_reported_not_raised(pair):
    """内核侧抛的错要带回调用方，而不是把连接搞崩。"""
    _server, _events, port = pair
    client = IpcClient("127.0.0.1", port)
    try:
        with pytest.raises(IpcError, match="炸了"):
            client.call("boom")
        # 出错之后连接还能继续用
        assert client.call("add", a=1, b=1) == 2
    finally:
        client.close()


def test_events_reach_client(pair):
    server, events, port = pair
    got: list[dict] = []
    client = IpcClient("127.0.0.1", port, on_event=got.append)
    try:
        for i in range(3):
            server.emit({"type": "X", "i": i})
        deadline = time.monotonic() + 3
        while len(got) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert [e["i"] for e in got] == [0, 1, 2]
        assert len(events) == 0, "客户端不该往服务端回灌事件"
    finally:
        client.close()


def test_concurrent_calls_do_not_cross_wires(pair):
    """id 配对必须准确：高并发下每个调用要拿回自己的结果。"""
    _server, _events, port = pair
    client = IpcClient("127.0.0.1", port)
    results: dict[int, int] = {}
    lock = threading.Lock()

    def worker(i: int) -> None:
        got = client.call("add", a=i, b=1000)
        with lock:
            results[i] = got

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(results) == 20
        assert all(results[i] == i + 1000 for i in range(20))
    finally:
        client.close()


def test_async_call(pair):
    import asyncio

    _server, _events, port = pair

    async def go() -> int:
        client = IpcClient("127.0.0.1", port)
        try:
            return await client.call_async("add", a=7, b=8)
        finally:
            client.close()

    assert asyncio.run(go()) == 15


def test_timeout_raises_rather_than_hangs(pair):
    _server, _events, port = pair
    client = IpcClient("127.0.0.1", port)
    try:
        with pytest.raises(IpcError, match="超时"):
            client.call("slow", timeout=0.05)
    finally:
        client.close()


def test_pending_calls_fail_fast_when_server_stops(pair):
    """服务端没了，等待中的调用要立刻拿到失败，不能挂到超时。"""
    server, _events, port = pair
    client = IpcClient("127.0.0.1", port)
    errors: list[str] = []

    def worker() -> None:
        try:
            client.call("slow", timeout=30)
        except IpcError as exc:
            errors.append(str(exc))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.15)
    server.stop()
    t.join(timeout=5)

    assert errors, "连接断开后等待方必须被唤醒"
    assert not t.is_alive(), "不能挂死"


def test_connect_refused_raises():
    with pytest.raises(OSError):
        IpcClient("127.0.0.1", 1, connect_timeout=0.5)