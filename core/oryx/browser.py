"""浏览器 Agent：接管网页、读取页面、点击输入、抓结构化数据。

三条设计原则：
1. **不把整页 DOM 塞进上下文**。页面被压缩成「编号元素清单 + 文本摘要」，
   模型看到的是几十行而不是几万字符——这是浏览器 Agent 能否跑得动的关键。
2. **浏览器跑在独立线程**。Playwright 的同步 API 不跨线程，用一个命令队列
   把所有操作串到同一个线程里，同时让浏览器会话在多次工具调用之间保持存活。
3. **优先用系统已装浏览器**。Windows 上直接用 Chrome / Edge，不额外下载 Chromium。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_TIMEOUT_MS = 15000
MAX_ELEMENTS = 120
MAX_TEXT_CHARS = 1500
MAX_TABLE_ROWS = 200

# 抓取可交互元素，并打上编号，后续按编号或选择器操作
SNAPSHOT_JS = """
() => {
  const out = [];
  const sel = 'a, button, input, textarea, select, [role=button], [role=link], [contenteditable="true"]';
  const nodes = Array.from(document.querySelectorAll(sel));
  let idx = 0;
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (rect.width === 0 || rect.height === 0) continue;
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
    idx += 1;
    el.setAttribute('data-oryx-idx', String(idx));
    const text = (el.innerText || el.value || el.getAttribute('aria-label') ||
                  el.getAttribute('title') || el.getAttribute('placeholder') || '').trim();
    out.push({
      idx: idx,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      text: text.slice(0, 80),
      href: el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '').slice(0, 200) : '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || ''
    });
    if (idx >= %d) break;
  }
  const body = document.body ? document.body.innerText : '';
  return {
    url: location.href,
    title: document.title,
    elements: out,
    html_length: document.documentElement.outerHTML.length,
    text: body.replace(/\\n{2,}/g, '\\n').trim().slice(0, %d)
  };
}
""" % (MAX_ELEMENTS, MAX_TEXT_CHARS)


class BrowserError(RuntimeError):
    pass


@dataclass
class _Job:
    op: str
    kwargs: dict[str, Any]
    result: dict[str, Any] | None = None
    error: Exception | None = None
    done: threading.Event = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.done = threading.Event()


class BrowserService:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._queue: queue.Queue[_Job] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self.channel: str | None = None

    # ---------------- 对外接口（任意线程可调） ----------------
    def call(self, op: str, **kwargs: Any) -> dict[str, Any]:
        self._ensure_thread()
        job = _Job(op=op, kwargs=kwargs)
        self._queue.put(job)
        if not job.done.wait(timeout=kwargs.pop("_timeout", 120)):
            raise BrowserError(f"浏览器操作超时：{op}")
        if job.error:
            raise job.error
        return job.result or {}

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="oryx-browser", daemon=True)
            self._thread.start()

    # ---------------- 工作线程 ----------------
    def _loop(self) -> None:
        try:
            while True:
                job = self._queue.get()
                if job.op == "__stop__":
                    break
                try:
                    job.result = self._dispatch(job.op, job.kwargs)
                except Exception as exc:  # 交给调用方
                    job.error = exc
                finally:
                    job.done.set()
        finally:
            self._teardown()

    def _dispatch(self, op: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        handler: Callable[..., dict[str, Any]] = getattr(self, f"_op_{op}", None)  # type: ignore[assignment]
        if handler is None:
            raise BrowserError(f"未知浏览器操作：{op}")
        return handler(**kwargs)

    # ---------------- 生命周期 ----------------
    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        errors: list[str] = []
        for channel in ("chrome", "msedge", None):
            try:
                browser = self._playwright.chromium.launch(
                    headless=self.headless, channel=channel
                )
            except Exception as exc:
                errors.append(f"{channel or 'bundled'}: {str(exc).splitlines()[0][:120]}")
                continue
            self._browser = browser
            self.channel = channel or "bundled"
            self._context = browser.new_context(
                locale="zh-CN",
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            return
        raise BrowserError(
            "无法启动浏览器。已尝试 Chrome / Edge / 内置 Chromium，均失败："
            + "；".join(errors)
        )

    def _teardown(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._playwright = None

    # ---------------- 具体操作 ----------------
    def _op_open(self, url: str, wait_ms: int = 0) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        if not url.startswith(("http://", "https://", "file://", "about:")):
            url = "https://" + url
        self._page.goto(url, wait_until="domcontentloaded")
        if wait_ms:
            self._page.wait_for_timeout(int(wait_ms))
        return self._op_snapshot()

    def _op_snapshot(self) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        data = self._page.evaluate(SNAPSHOT_JS)
        return {
            "url": data["url"],
            "title": data["title"],
            "elements": data["elements"],
            "text": data["text"],
            "html_length": data.get("html_length", 0),
            "count": len(data["elements"]),
        }

    def _op_click(self, target: str = "", index: int | None = None) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        selector = f'[data-oryx-idx="{index}"]' if index else target
        if not selector:
            raise BrowserError("需要提供 index 或 target 选择器")
        self._page.click(selector, timeout=DEFAULT_TIMEOUT_MS)
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        return self._op_snapshot()

    def _op_type(
        self, text: str, target: str = "", index: int | None = None, submit: bool = False
    ) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        selector = f'[data-oryx-idx="{index}"]' if index else target
        if not selector:
            raise BrowserError("需要提供 index 或 target 选择器")
        self._page.fill(selector, text, timeout=DEFAULT_TIMEOUT_MS)
        if submit:
            self._page.keyboard.press("Enter")
            try:
                self._page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
        return self._op_snapshot()

    def _op_extract(self, selector: str = "body", as_table: bool = False) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        if as_table:
            rows = self._page.eval_on_selector_all(
                f"{selector} tr",
                "rows => rows.map(r => Array.from(r.cells).map(c => c.innerText.trim()))",
            )
            return {"rows": rows[:MAX_TABLE_ROWS], "count": min(len(rows), MAX_TABLE_ROWS)}
        text = self._page.inner_text(selector)
        return {"text": text[:20000], "length": len(text)}

    def _op_links(self) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        links = self._page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0,200).map(e => ({text: (e.innerText||'').trim().slice(0,60), href: e.href}))",
        )
        return {"links": links, "count": len(links)}

    def _op_screenshot(self, path: str) -> dict[str, Any]:
        self._ensure_browser()
        assert self._page is not None
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(target), full_page=True)
        return {"path": str(target), "bytes": target.stat().st_size, "url": self._page.url}

    def _op_wait(self, selector: str = "", state: str = "visible",
                 timeout: int = 0) -> dict[str, Any]:
        """等一个元素达到某个状态。

        为什么需要
        ----------
        没有它，写浏览器自动化就只能 `sleep` 猜时间 ——
        页面慢一点就抓错元素，或者干脆抓不到。
        「等元素出现」是自动化的基本功，缺了它这套工具只能做手动浏览。

        `state`：
          · `visible` —— 出现且可见（默认）
          · `hidden` —— 消失或不可见
          · `attached` —— 在 DOM 里（哪怕不可见）
          · `detached` —— 从 DOM 里移除
        """
        self._ensure_browser()
        assert self._page is not None
        if not selector:
            raise BrowserError("需要提供 selector")
        if state not in ("visible", "hidden", "attached", "detached"):
            raise BrowserError(
                f"未知的等待状态：{state}"
                "（可用：visible / hidden / attached / detached）")
        timeout_ms = int(timeout) if timeout else DEFAULT_TIMEOUT_MS
        try:
            self._page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        except Exception as exc:
            raise BrowserError(
                f"等不到元素（{selector}，状态 {state}，超时 {timeout_ms}ms）："
                f"{str(exc).splitlines()[0][:160]}"
            ) from exc
        return {
            "waited": True,
            "selector": selector,
            "state": state,
            "url": self._page.url,
            "title": self._page.title(),
        }

    def _op_download(self, url: str = "", path: str = "", selector: str = "",
                     timeout: int = 0) -> dict[str, Any]:
        """下载文件到本地。

        两种触发方式：
          · `url` —— 直接访问下载地址（服务器返回附件）
          · `selector` —— 点页面上触发下载的元素（导出按钮之类）

        不给 `path` 就用服务器建议的文件名，落在当前工作目录。
        """
        self._ensure_browser()
        assert self._page is not None
        if not url and not selector:
            raise BrowserError(
                "需要提供 url（直接访问下载地址）或 selector（点页面上的下载入口）")
        timeout_ms = int(timeout) if timeout else DEFAULT_TIMEOUT_MS
        try:
            with self._page.expect_download(timeout=timeout_ms) as info:
                if url:
                    self._page.goto(url, wait_until="domcontentloaded")
                else:
                    self._page.click(selector, timeout=DEFAULT_TIMEOUT_MS)
            download = info.value
        except Exception as exc:
            raise BrowserError(
                f"下载失败：{str(exc).splitlines()[0][:160]}"
            ) from exc
        target = Path(path) if path else Path(download.suggested_filename)
        if str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(target))
        size = target.stat().st_size if target.exists() else 0
        return {
            "path": str(target.resolve()),
            "filename": download.suggested_filename,
            "bytes": size,
            "url": self._page.url,
        }

    def _op_close(self) -> dict[str, Any]:
        self._teardown()
        return {"status": "closed"}

    def _op_status(self) -> dict[str, Any]:
        running = self._thread is not None and self._thread.is_alive()
        return {
            "running": running,
            "channel": self.channel,
            "url": self._page.url if self._page else None,
            "headless": self.headless,
        }


_service: BrowserService | None = None
_service_lock = threading.Lock()


def get_service(headless: bool = True) -> BrowserService:
    global _service
    with _service_lock:
        if _service is None or _service.headless != headless:
            _service = BrowserService(headless=headless)
        return _service


def format_snapshot(snapshot: dict[str, Any], max_text: int = 800) -> str:
    """把快照压成给模型看的紧凑文本。

    页面内容是**外部来源** —— 里面可能有伪装成指令的文本。
    整段套上边界标记（见 taint.py），让模型知道这是资料不是指令。
    """
    from .taint import wrap

    lines = [f"URL: {snapshot.get('url')}", f"标题: {snapshot.get('title')}"]
    elements = snapshot.get("elements") or []
    if elements:
        lines.append(f"可交互元素（{len(elements)} 个）：")
        for el in elements:
            parts = [f"[{el['idx']}] {el['tag']}"]
            if el.get("type"):
                parts.append(f"type={el['type']}")
            if el.get("text"):
                parts.append(f"「{el['text']}」")
            if el.get("placeholder"):
                parts.append(f"placeholder={el['placeholder']}")
            if el.get("href"):
                parts.append(f"-> {el['href'][:120]}")
            lines.append("  " + " ".join(parts))
    text = (snapshot.get("text") or "")[:max_text]
    if text:
        lines.append("页面文本摘要：")
        lines.append(text)
    return wrap("\n".join(lines), str(snapshot.get("url") or "网页"))
