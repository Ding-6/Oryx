"""假 provider 服务：给契约测试提供"慢但成功"的模型端点。

为什么需要它：`/api/workflows/run` 独占事件循环这类 bug，只有在编排
**跑得够久**时才测得出来。临时库里没有真 provider，编排会在几毫秒内
失败 —— 探活采不到样本，"测不出问题"会被误当成"没问题"。

所以这里起一个最小的 OpenAI 兼容端点：每次 chat/completions 睡 1.5 秒
再回一个空 tool_calls 的完成帧。这样 step 会真的走完模型调用，
编排时长足够长，且完全确定（不依赖任何外部模型）。

用法：
    stub = StubProvider(delay=1.5)
    stub.start()
    ...
    stub.stop()
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubProvider:
    """一个只提供 /v1/chat/completions 的假 OpenAI 兼容端点。"""

    def __init__(self, delay: float = 1.5, port: int = 0) -> None:
        self.delay = delay
        self.port = port
        self.calls = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> str:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # 静音
                pass

            def do_POST(self) -> None:  # noqa: N802
                if not self.path.endswith("/chat/completions"):
                    self.send_error(404)
                    return
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                with stub._lock:
                    stub.calls += 1
                # 睡一下：让编排变成"跑得久"的状态，事件循环占用才测得出来
                time.sleep(stub.delay)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                frames = [
                    {"choices": [{"delta": {"role": "assistant"}, "index": 0, "finish_reason": None}]},
                    {"choices": [{"delta": {"content": "（假 provider 的固定回复）"},
                                  "index": 0, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
                    {"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                               "total_tokens": 15}},
                ]
                for f in frames:
                    self.wfile.write(f"data: {json.dumps(f, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def do_GET(self) -> None:  # noqa: N802
                self.send_error(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="stub-provider", daemon=True
        )
        self._thread.start()
        return self.base_url

    def configure(self, home) -> None:
        """把假 provider 写进指定 ORYX_HOME 的配置，并切到本地模式。"""
        from pathlib import Path

        cfg_path = Path(home) / "config.json"
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text("utf-8"))
            except Exception:
                cfg = {}
        cfg["providers"] = {
            "stub": {"type": "openai", "base_url": self.base_url, "model": "stub-1"},
        }
        cfg["active_provider"] = "stub"
        cfg["local_provider"] = "stub"
        cfg["mode"] = "local"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None