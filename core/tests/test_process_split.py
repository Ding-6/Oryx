"""Agent 内核进程化：真起子进程，验证 RPC 面与事件回流。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from oryx.agentlink import AgentLink
from oryx.ipc import IpcError

CORE = Path(__file__).resolve().parents[1]


@pytest.fixture
def link(tmp_path, monkeypatch):
    """真拉起一个 Agent 内核进程。ORYX_HOME 隔离，避免污染真实配置与库。"""
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["ORYX_HOME"] = str(home)
    env["PYTHONPATH"] = str(CORE)

    fd, port_file = tempfile.mkstemp(suffix=".port")
    os.close(fd)
    proc = subprocess.Popen(
        [sys.executable, "-m", "oryx.agentd", "--port", "0", "--port-file", port_file],
        cwd=str(CORE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 60
    port = 0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate()
            pytest.fail(f"内核进程提前退出：{err.decode('utf-8', 'replace')[-800:]}")
        try:
            text = Path(port_file).read_text("utf-8").strip()
            if text:
                port = int(text)
                break
        except OSError:
            pass
        time.sleep(0.1)
    assert port, "内核没有在超时前回报端口"

    from oryx.ipc import IpcClient

    client = IpcClient("127.0.0.1", port)
    try:
        yield client, proc
    finally:
        client.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.unlink(port_file)
        except OSError:
            pass


def test_kernel_process_answers_rpc(link):
    client, proc = link
    assert proc.poll() is None, "内核必须是独立存活的进程"

    skills = client.call("catalog.skills")
    assert isinstance(skills, list), "技能清单要能从内核取到"

    experts = client.call("catalog.experts")
    assert isinstance(experts, list) and experts, "人设清单不应为空"

    agents = client.call("catalog.agents")
    assert isinstance(agents, list) and agents, "子 Agent 清单不应为空"


def test_tool_catalog_groups_by_prefix(link):
    client, _proc = link
    payload = client.call("catalog.tools")
    assert payload["total"] > 40, "工具数量不该因为进程化而缩水"
    groups = {g["group"] for g in payload["groups"]}
    assert "fs" in groups and "browser" in groups
    for g in payload["groups"]:
        assert g["count"] == len(g["tools"])


def test_tool_search_over_ipc(link):
    client, _proc = link
    out = client.call("catalog.tools_search", query="browser", limit=5)
    assert out["count"] >= 1
    assert any("browser" in t["name"] for t in out["tools"])


def test_index_stats_over_ipc(link):
    client, _proc = link
    stats = client.call("index.stats")
    assert "files" in stats


def test_workspace_change_updates_policy(link):
    client, _proc = link
    tmp = tempfile.mkdtemp()
    out = client.call(
        "runtime.workspace_changed", allowed_roots=[tmp], forbidden_paths=[]
    )
    assert out["ok"] is True


def test_team_lifecycle_over_ipc(link):
    client, _proc = link
    agents = [a["id"] for a in client.call("catalog.agents")]
    created = client.call("team.create", name="t1", members=agents[:2], goal="验证")
    assert created["team"]["name"] == "t1"

    listed = client.call("team.list")
    assert any(t["name"] == "t1" for t in listed)

    detail = client.call("team.get", name="t1")
    assert detail is not None and len(detail["members"]) == 2

    assert client.call("team.delete", name="t1") is True
    assert client.call("team.get", name="t1") is None


def test_busy_flag_reflects_run(link):
    client, _proc = link
    assert client.call("chat.busy") is False


def test_events_flow_from_kernel(link):
    """内核事件必须能推到客户端（server 靠这个出 SSE）。"""
    client, _proc = link
    # chat.run 会真的去调模型，没有 key 会失败，但失败事件照样发出来
    got: list[dict] = []
    client.on_event = got.append
    client.call("chat.run", goal="打一句招呼")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not any(
        e.get("type", "").startswith("RUN_") for e in got
    ):
        time.sleep(0.1)
    assert got, "内核事件没有到达客户端"
    seqs = [e["seq"] for e in got if isinstance(e.get("seq"), int)]
    assert seqs == sorted(seqs), "内核侧事件序号必须单调"


def test_agent_link_starts_and_stops_a_real_kernel():
    """AgentLink 要能自己把内核拉起来、连上、再干净地收掉。"""
    home = tempfile.mkdtemp()
    env_backup = os.environ.get("ORYX_HOME")
    os.environ["ORYX_HOME"] = home
    seen: list[dict] = []
    agent = AgentLink(on_event=seen.append)
    try:
        agent.start()
        assert agent.alive, "内核进程应该活着"
        assert isinstance(agent.call("catalog.skills"), list)
    finally:
        agent.stop()
        if env_backup is None:
            os.environ.pop("ORYX_HOME", None)
        else:
            os.environ["ORYX_HOME"] = env_backup

    # stop 之后不该再能调通
    with pytest.raises(IpcError):
        agent.call("catalog.skills")


def test_agent_link_call_before_start_raises():
    agent = AgentLink(on_event=lambda e: None)
    with pytest.raises(IpcError, match="未就绪"):
        agent.call("catalog.skills")