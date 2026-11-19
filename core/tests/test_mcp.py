"""MCP 客户端回归：用本地假 server 跑通 initialize / tools/list / tools/call。"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from oryx.mcp import MCPClient, MCPError

FAKE_SERVER = textwrap.dedent(
    '''
    import json, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "serverInfo": {"name": "fake", "version": "1.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "echo", "description": "回显文本",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                                 "required": ["text"]},
                 "annotations": {"readOnlyHint": True}},
                {"name": "destroy", "description": "删除东西",
                 "inputSchema": {"type": "object", "properties": {}},
                 "annotations": {"destructiveHint": True}}
            ]}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments") or {}
            if name == "echo":
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}]}})
            else:
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "isError": True, "content": [{"type": "text", "text": "拒绝"}]}})
    '''
)


@pytest.fixture()
def client(tmp_path: Path):
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    proc = MCPClient("fake", sys.executable, [str(script)], timeout=10)
    proc.start()
    yield proc
    proc.close()


def test_initialize_and_list_tools(client):
    names = [t.name for t in client.tools]
    assert names == ["echo", "destroy"]
    assert client.server_info.get("name") == "fake"


def test_risk_from_annotations(client):
    by_name = {t.name: t for t in client.tools}
    assert by_name["echo"].risk_level() == 0       # readOnlyHint
    assert by_name["destroy"].risk_level() == 2    # destructiveHint


def test_qualified_name(client):
    assert client.tools[0].qualified_name == "mcp__fake__echo"


def test_call_tool(client):
    result = client.call_tool("echo", {"text": "hi"})
    assert result["ok"] is True
    assert result["content"] == "echo:hi"


def test_call_tool_error_flagged(client):
    result = client.call_tool("destroy", {})
    assert result["ok"] is False
    assert "拒绝" in result["content"]


def test_missing_command_raises(tmp_path):
    bad = MCPClient("nope", str(tmp_path / "not-exist.exe"), timeout=2)
    with pytest.raises(MCPError):
        bad.start()
