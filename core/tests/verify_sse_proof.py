"""自证字段的端到端验收：**直连后端读 SSE**。

为什么不能走代理：
    验收代理用 `resp.read()` 转发，会等流结束才返回 ——
    SSE 长连接被它堵死，页面收不到任何事件。
    要验 SSE 事件得直连后端，用 urllib 逐行读 `data:`。

这一份验的是**整条链路**：
    模型（假 provider）→ 工具执行 → 内核算自证字段 → agentd → server → SSE
    → 前端拿到的事件里有这些字段

为什么非验不可：
    「字段算了但出口丢掉」在这个仓里已经踩过三次。
    单测只能证明内核发的事件里有字段，证明不了事件**真的到了 SSE 上**。
    中间隔着 agentd 的 IPC、server 的 relay —— 每一层都可能把字段吃掉。

跑法：python -m tests.verify_sse_proof
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SERVER_PORT = 8795
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- 假 provider（会发工具调用）

class ToolCallingProvider:
    """一个只提供 /v1/chat/completions 的假 OpenAI 兼容端点。

    与 tests/stub_provider.py 的差别：**会真的发一个 tool_call**。
    不发工具调用就没有 step.done 事件，自证字段也就无从验起。
    """

    def __init__(self, port: int = 0) -> None:
        self.port = port
        self.calls = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.target_dir = ""

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> str:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                if not self.path.endswith("/chat/completions"):
                    self.send_error(404)
                    return
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                with stub._lock:
                    stub.calls += 1
                    nth = stub.calls
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                if nth == 1:
                    # 第一轮：要求调 fs_list（只读工具）
                    frames = [
                        {"choices": [{"index": 0, "delta": {"role": "assistant",
                                                           "tool_calls": [{
                                                               "index": 0,
                                                               "id": "call_acc_1",
                                                               "function": {
                                                                   "name": "fs_list",
                                                                   "arguments": json.dumps(
                                                                       {"path": stub.target_dir},
                                                                       ensure_ascii=False),
                                                               },
                                                           }]}, "finish_reason": None}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                    ]
                else:
                    # 第二轮：工具结果回来了，给个收尾
                    frames = [
                        {"choices": [{"index": 0, "delta": {"role": "assistant",
                                                           "content": "看完了"},
                                      "finish_reason": None}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    ]
                for f in frames:
                    self.wfile.write(f"data: {json.dumps(f, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def do_GET(self) -> None:
                self.send_error(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def configure(self, home: str) -> None:
        """把假 provider 写进指定 ORYX_HOME 的配置。"""
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


# ---------------------------------------------------------------- SSE 直连


def read_sse(port: int, token: str, on_event) -> threading.Thread:
    """后台线程直连 /api/events，逐行读 data: 帧。

    **必须直连**：走代理的话长连接被堵死，一个事件都收不到。
    """

    def run() -> None:
        url = f"http://127.0.0.1:{port}/api/events?t={token}"
        req = urllib.request.Request(url)
        req.add_header("X-Oryx-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    on_event(ev)
        except Exception:
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def main() -> int:
    home = tempfile.mkdtemp(prefix="oryx_sse_proof_")
    target = Path(home) / "sample"
    target.mkdir()
    (target / "a.txt").write_text("内容 A\n", encoding="utf-8")

    provider = ToolCallingProvider()
    provider.target_dir = str(target)
    provider.start()
    provider.configure(home)

    env = {**os.environ, "ORYX_HOME": home}
    proc = subprocess.Popen(
        [PYTHON, "-m", "oryx", "--no-browser", "--port", str(SERVER_PORT)],
        cwd=str(CORE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    token = ""
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print("后端起不来")
                return 1
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{SERVER_PORT}/", timeout=10
                ) as resp:
                    html = resp.read().decode("utf-8")
                import re

                m = re.search(r'const ORYX_TOKEN = "([^"]*)"', html)
                if m:
                    token = m.group(1)
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not token:
            print("拿不到令牌")
            return 1

        print(f"后端: http://127.0.0.1:{SERVER_PORT}  provider: {provider.base_url}")

        events: list[dict] = []
        read_sse(SERVER_PORT, token, events.append)
        time.sleep(1.5)  # 等 SSE 连上

        # 发起一轮对话 —— 模型会被要求调 fs_list
        req = urllib.request.Request(
            f"http://127.0.0.1:{SERVER_PORT}/api/chat",
            data=json.dumps({"goal": "列一下目录里的文件"}).encode("utf-8"),
            method="POST",
        )
        req.add_header("X-Oryx-Token", token)
        req.add_header("X-Oryx-Request", "1")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                chat = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            chat = {"http_error": exc.code, "body": exc.read().decode("utf-8", "replace")}
        except Exception as exc:
            chat = {"error": str(exc)}

        task_id = chat.get("task_id") or (chat.get("content") or {}).get("task_id") or ""
        print(f"  chat 返回: {str(chat)[:180]}")

        # 等事件流里出现 step.done
        deadline = time.monotonic() + 60
        done: list[dict] = []
        while time.monotonic() < deadline:
            done = [e for e in events if e.get("type") == "step.done"]
            if done:
                break
            time.sleep(0.4)

        # task_id 不在 chat 的返回里 —— chat 是「起个任务就返回」，
        # 真正的 task_id 在 RUN_STARTED 事件上。
        if not task_id:
            started = [e for e in events if e.get("type") == "RUN_STARTED"]
            if started:
                task_id = started[0].get("task_id") or ""
        print(f"  task_id: {task_id!r}")

        print("\n════ SSE 自证字段 ════")
        check("SSE 上收到了 step.done", bool(done),
              f"收到的事件类型：{[e.get('type') for e in events][:20]}")
        if done:
            ev = done[0]
            check("step.done 带 mutated 字段", "mutated" in ev, str(ev)[:200])
            check("只读工具的 mutated 是 False", ev.get("mutated") is False, str(ev.get("mutated")))
            check("step.done 带 preview 字段", ev.get("preview") is True, str(ev.get("preview")))
            check("step.done 带 deleted/modified/moved",
                  all(k in ev for k in ("deleted", "modified", "moved")), str(ev)[:200])

        # 审计记录
        print("\n════ 审计记录 ════")
        if task_id:
            areq = urllib.request.Request(
                f"http://127.0.0.1:{SERVER_PORT}/api/audit/{task_id}")
            areq.add_header("X-Oryx-Token", token)
            try:
                with urllib.request.urlopen(areq, timeout=30) as resp:
                    audit = json.loads(resp.read().decode("utf-8")).get("audit") or []
            except Exception as exc:
                audit = []
                print(f"  读审计失败: {exc}")
            check("审计里有条目", bool(audit), "没有审计记录")
            if audit:
                row = audit[-1]
                check("审计 outcome 不是 pending", row.get("outcome") != "pending",
                      f"outcome={row.get('outcome')!r}")
                check("审计 outcome 是 done", row.get("outcome") == "done",
                      f"outcome={row.get('outcome')!r}")
                check("审计 mutated 是 0（只读）", row.get("mutated") == 0,
                      f"mutated={row.get('mutated')!r}")
        else:
            check("拿到了 task_id", False, str(chat)[:200])

    finally:
        provider.stop()
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
