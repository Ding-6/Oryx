"""最小 MCP 客户端（stdio 传输，换行分隔的 JSON-RPC 2.0）。

为什么自己写而不是引第三方库：我们只需要 tools/list 和 tools/call 两件事，
一百多行就够，还能完全掌控超时、日志和进程回收。

安全约束：外部 MCP server 是可执行程序，**不会自动启动**，必须由配置显式启用。

大输出处理：MCP 服务器经常返回几十万字符的整份文件。全部塞进模型上下文
会挤爆窗口，而且模型也用不上。超过阈值的内容落盘，工具结果里只留一个引用，
模型需要细节时再用文件工具自己去读。对齐 WorkBuddy 的 MAX_MCP_OUTPUT_TOKENS 策略。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import app_dir

PROTOCOL_VERSION = "2025-06-18"


def _version() -> str:
    """产品版本 —— 从 `__init__` 取，那儿是唯一来源。

    为什么不在这里写死：写死一份就多一个会漂移的地方
    （之前这里写的是 "0.1.0"，而 CHANGELOG 已经到 1.9.0）。
    """
    try:
        from . import __version__

        return __version__
    except Exception:
        return "unknown"
DEFAULT_TIMEOUT = 30.0

# MCP 调用的重试策略。
#
# 只重试**瞬时**失败：连接不上、请求超时、5xx、429。
# 这些是「等一会儿再来一次就好」的故障。
# 参数错（400）、方法不存在（-32601）重试没有意义 —— 重试只是让用户多等几秒。
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5
RETRY_MAX_DELAY = 4.0

# 单次 MCP 结果的字符上限。超过就落盘，工具结果只留引用。
MAX_RESULT_CHARS = 80_000
# 落盘目录：~/.oryx/projects/<server>/tool-results/
RESULT_DIR = Path("projects") / "mcp" / "tool-results"


class MCPError(RuntimeError):
    pass


@dataclass
class MCPTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"mcp__{self.server}__{self.name}"

    def risk_level(self) -> int:
        """按 MCP 声明的注解推断风险：只读 → 0，破坏性 → 2，**没有声明 → 2**。

        默认从严是刻意的：``tools.py`` 顶部写明「不自带风险声明的工具一律按
        L2 处理」，README 也是这么承诺的。以前这里返回 1（L1 可逆写入），
        等于让一个没声明任何注解的第三方 MCP 工具不弹确认就能执行 ——
        而它完全可能删文件。文档与实现不一致时，以更安全的一侧为准。

        代价是无注解的 MCP 工具每次都要走确认；想要免打扰，请让 server
        正确声明 ``readOnlyHint``。
        """
        if self.annotations.get("readOnlyHint"):
            return 0
        if self.annotations.get("destructiveHint"):
            return 2
        return 2


class MCPClient:
    """stdio 传输的 MCP 客户端。"""

    transport = "stdio"

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr: list[str] = []
        self.tools: list[MCPTool] = []

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                cwd=self.cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise MCPError(f"无法启动 MCP server「{self.name}」：{exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, name=f"mcp-{self.name}", daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, name=f"mcp-err-{self.name}", daemon=True).start()

        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "oryx", "version": _version()},
            },
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized", {})
        self.refresh_tools()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ---------------- 协议 ----------------
    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and "id" in message:
                self._responses.put(message)
        self._responses.put({"__closed__": True})

    def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 200:
                del self._stderr[:100]

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError(f"MCP server「{self.name}」未运行")
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _next_request_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        request_id = self._next_request_id()
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                message = self._responses.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if message.get("__closed__"):
                raise MCPError(
                    f"MCP server「{self.name}」已退出。stderr：{' / '.join(self._stderr[-3:])}"
                )
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise MCPError(f"{method} 失败：{message['error']}")
            return message.get("result")
        raise MCPError(f"MCP 调用超时：{method}（{self.name}）")

    # ---------------- 能力 ----------------
    def refresh_tools(self) -> list[MCPTool]:
        result = self._request("tools/list", {})
        tools: list[MCPTool] = []
        for item in (result or {}).get("tools", []):
            tools.append(
                MCPTool(
                    server=self.name,
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    input_schema=item.get("inputSchema")
                    or {"type": "object", "properties": {}},
                    annotations=item.get("annotations") or {},
                )
            )
        self.tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._call_tool_with_retry(name, arguments)
        if not isinstance(result, dict):
            return {"ok": True, "content": str(result)}
        blocks = result.get("content") or []
        texts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            else:
                texts.append(json.dumps(block, ensure_ascii=False)[:2000])
        content = "\n".join(texts)
        content = self._offload_large(content, name)
        return {
            "ok": not result.get("isError", False),
            "content": content,
            "raw": result,
        }

    # ---------------- 重试 ----------------
    def _call_tool_with_retry(self, name: str, arguments: dict[str, Any]) -> Any:
        """调 MCP 工具，瞬时失败就退避重试。

        只重试「等一会儿就好」的故障：连接失败、超时、5xx、429。
        方法不存在、参数非法这类重试没有意义 —— 重试只是让用户多等几秒。

        **server 已经退出不重试**：进程都没了，再连也是连不上，
        重试三次纯属浪费用户的时间。
        """
        import random

        last: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                return self._request("tools/call", {"name": name, "arguments": arguments})
            except MCPError as exc:
                last = exc
                text = str(exc)
                # server 退出 —— 重试没有意义
                if "已退出" in text:
                    raise
                # 只重试能自愈的：超时、连接失败、5xx
                transient = ("超时" in text) or ("连接" in text) or (
                    any(str(code) in text for code in RETRYABLE_STATUS)
                )
                if not transient or attempt >= RETRY_MAX_ATTEMPTS:
                    raise
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                time.sleep(delay * random.uniform(0.8, 1.2))
        raise last or MCPError("MCP 调用失败")

    # ---------------- 大输出落盘 ----------------
    def _offload_large(self, content: str, tool_name: str) -> str:
        """超长的工具结果落盘，工具结果里只留一个可读的引用。

        落盘失败不阻断流程——截断后照常返回，只是内容不完整。
        """
        if len(content) <= MAX_RESULT_CHARS:
            return content
        try:
            directory = app_dir() / RESULT_DIR / self.name
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe = "".join(c for c in tool_name if c.isalnum() or c in "-_.")[:60] or "tool"
            path = directory / f"{safe}-{stamp}-{int(time.time() * 1000) % 100000}.txt"
            path.write_text(content, encoding="utf-8")
        except Exception:
            return content[:MAX_RESULT_CHARS] + "\n…（内容过长且落盘失败，已截断）"
        head = content[:2000]
        return (
            f"{head}\n\n"
            f"…（输出过长，共 {len(content)} 字符，已保存到文件）\n"
            f"完整内容：{path}\n"
            f"需要细节请用 fs_read 读取该文件。"
        )


class MCPHttpClient(MCPClient):
    """HTTP / SSE 传输的 MCP 客户端。

    与 stdio 版共用 tools/list、tools/call 两件事，差别只在怎么发请求：
        http   每次调用一个 POST，响应体就是 JSON-RPC 结果
        sse    POST 发请求，GET 订阅 SSE 拿通知

    安全约束（比 stdio 更严）：
    - URL 必须是 http/https，不接受 file:、javascript: 之类
    - 自定义头里的凭据不会回显到任何输出
    """

    def __init__(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
        transport: str = "http",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        super().__init__(name=name, command="", timeout=timeout)
        self.url = _validate_url(url)
        self.headers = dict(headers or {})
        self.transport = transport
        self._session = None
        self._sse_thread: threading.Thread | None = None
        self._sse_stop = threading.Event()
        self._next_id = 0
        self._id_lock = threading.Lock()

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        import httpx

        self._session = httpx.Client(timeout=self.timeout, headers=self.headers)
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "oryx", "version": _version()},
            },
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized", {})
        if self.transport == "sse":
            self._start_sse()
        self.refresh_tools()

    def close(self) -> None:
        self._sse_stop.set()
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def _start_sse(self) -> None:
        """订阅 SSE 通知流。断线不影响主流程，只是收不到服务端推送。"""
        import httpx

        def _run() -> None:
            try:
                with httpx.Client(timeout=None, headers=self.headers) as client:
                    with client.stream(
                        "GET", self.url, headers={"Accept": "text/event-stream"}
                    ) as resp:
                        for line in resp.iter_lines():
                            if self._sse_stop.is_set():
                                break
                            if not line or not line.startswith("data:"):
                                continue
                            try:
                                self._responses.put(json.loads(line[5:].strip()))
                            except json.JSONDecodeError:
                                continue
            except Exception:
                pass

        self._sse_thread = threading.Thread(target=_run, name=f"mcp-sse-{self.name}", daemon=True)
        self._sse_thread.start()

    # ---------------- 协议 ----------------
    def _send(self, payload: dict[str, Any]) -> None:
        if self._session is None:
            raise MCPError(f"MCP server「{self.name}」未运行")
        try:
            resp = self._session.post(self.url, json=payload)
        except Exception as exc:
            raise MCPError(f"MCP 请求失败：{exc}") from exc
        if resp.status_code >= 400:
            raise MCPError(f"MCP 返回 {resp.status_code}：{resp.text[:300]}")
        if not resp.content:
            return
        try:
            data = resp.json()
        except Exception:
            return
        if isinstance(data, dict) and "id" in data:
            self._responses.put(data)

    def _request(self, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        request_id = self._next_request_id()
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                message = self._responses.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise MCPError(f"{method} 失败：{message['error']}")
            return message.get("result")
        raise MCPError(f"MCP 调用超时：{method}（{self.name}）")


def _validate_url(url: str) -> str:
    """只允许 http/https。

    远程 MCP server 是外部可执行程序，URL 由用户提供 ——
    放开 file: / javascript: 会变成任意文件读取与脚本执行的口子。
    """
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise MCPError(f"MCP 地址必须是 http/https，收到：{parsed.scheme or '空'}")
    if not parsed.netloc:
        raise MCPError(f"MCP 地址不完整：{url}")
    return url.strip()


def build_client(spec: dict[str, Any], timeout: float = DEFAULT_TIMEOUT) -> MCPClient:
    """按配置构造客户端，自动选传输方式。

    spec 里没写 type 时：带 command → stdio，带 url → http。
    """
    kind = str(spec.get("type") or "").lower()
    if not kind:
        kind = "stdio" if spec.get("command") else "http"

    if kind == "stdio":
        command = spec.get("command")
        if not command:
            raise MCPError("stdio 传输必须写 command")
        return MCPClient(
            name=str(spec.get("name") or ""),
            command=str(command),
            args=[str(a) for a in (spec.get("args") or [])],
            env=spec.get("env") or {},
            cwd=spec.get("cwd"),
            timeout=timeout,
        )
    if kind in ("http", "sse"):
        url = spec.get("url")
        if not url:
            raise MCPError(f"{kind} 传输必须写 url")
        return MCPHttpClient(
            name=str(spec.get("name") or ""),
            url=str(url),
            headers=spec.get("headers") or {},
            transport=kind,
            timeout=timeout,
        )
    raise MCPError(f"不支持的 MCP 传输：{kind}")
