"""内嵌终端：流式输出、中断、通知。"""

from __future__ import annotations

import sys
import time

import pytest

from oryx.terminal import TerminalService, notify


def test_streaming_output():
    events: list[dict] = []
    service = TerminalService(on_event=events.append)
    session = service.run("echo hello && echo world")

    deadline = time.time() + 10
    while time.time() < deadline:
        if any(e["t"] == "term.finished" for e in events):
            break
        time.sleep(0.05)

    assert any(e["t"] == "term.started" for e in events)
    assert any(e["t"] == "term.finished" for e in events)
    outputs = [e["line"].strip() for e in events if e["t"] == "term.output"]
    assert "hello" in outputs
    assert "world" in outputs


def test_exit_code_captured():
    events: list[dict] = []
    service = TerminalService(on_event=events.append)
    service.run("exit 3")

    deadline = time.time() + 10
    while time.time() < deadline:
        if any(e["t"] == "term.finished" for e in events):
            break
        time.sleep(0.05)

    finished = [e for e in events if e["t"] == "term.finished"][0]
    assert finished["exit_code"] == 3


def test_session_retrievable():
    service = TerminalService()
    session = service.run("echo x")
    deadline = time.time() + 10
    while time.time() < deadline:
        if service.get(session["id"]) and not service.get(session["id"])["running"]:
            break
        time.sleep(0.05)
    assert service.get(session["id"]) is not None
    assert service.list()


def test_interrupt_long_running():
    service = TerminalService()
    # 不用 ping —— 部分精简环境（CI / 沙箱）里 PATH 里没有 ping.exe，
    # 命令会立刻失败，测不到"中断"这条路。用 Python 自己 sleep。
    session = service.run(
        f'"{sys.executable}" -c "import time; time.sleep(60)"'
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if service.get(session["id"]) and service.get(session["id"])["running"]:
            break
        time.sleep(0.05)
    assert service.get(session["id"])["running"] is True, "命令没起来就测中断"
    killed = service.interrupt(session["id"])
    assert killed is True


def test_interrupt_unknown_session():
    service = TerminalService()
    assert service.interrupt("nope") is False


def test_notify_requires_webhook():
    result = notify("标题", "内容", method="webhook", webhook="")
    assert result["ok"] is False
