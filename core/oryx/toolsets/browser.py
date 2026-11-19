"""浏览器 Agent：打开、快照、点击、输入、提取、等待、下载、截图。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class BrowserToolsMixin:
    def _register_browser_tools(self) -> None:
        from ..browser import format_snapshot, get_service

        def _svc():
            headless = bool(self.config.data.get("browser_headless", True))
            return get_service(headless=headless)

        def _open(url: str, wait_ms: int = 0) -> dict[str, Any]:
            snapshot = _svc().call("open", url=url, wait_ms=int(wait_ms))
            return {"ok": True, "content": format_snapshot(snapshot)}

        def _snapshot() -> dict[str, Any]:
            return {"ok": True, "content": format_snapshot(_svc().call("snapshot"))}

        def _click(index: int | None = None, target: str = "") -> dict[str, Any]:
            snapshot = _svc().call("click", index=index, target=target)
            return {"ok": True, "content": "已点击。\n" + format_snapshot(snapshot)}

        def _type(
            text: str, index: int | None = None, target: str = "", submit: bool = False
        ) -> dict[str, Any]:
            snapshot = _svc().call(
                "type", text=text, index=index, target=target, submit=bool(submit)
            )
            return {"ok": True, "content": "已输入。\n" + format_snapshot(snapshot)}

        def _extract(selector: str = "body", as_table: bool = False) -> dict[str, Any]:
            data = _svc().call("extract", selector=selector, as_table=bool(as_table))
            return {"ok": True, "content": data}

        def _links() -> dict[str, Any]:
            return {"ok": True, "content": _svc().call("links")}

        def _screenshot(path: str) -> dict[str, Any]:
            return {"ok": True, "content": _svc().call("screenshot", path=path)}

        def _close() -> dict[str, Any]:
            return {"ok": True, "content": _svc().call("close")}

        def _wait(selector: str, state: str = "visible",
                  timeout: int = 0) -> dict[str, Any]:
            data = _svc().call(
                "wait", selector=selector, state=state, timeout=int(timeout)
            )
            return {"ok": True, "content": data}

        def _download(url: str = "", path: str = "", selector: str = "",
                      timeout: int = 0) -> dict[str, Any]:
            data = _svc().call(
                "download", url=url, path=path, selector=selector,
                timeout=int(timeout),
            )
            return {"ok": True, "content": data}

        self.registry["browser_open"] = ToolSpec(
            name="browser_open",
            description="在浏览器中打开网址，返回页面标题、可交互元素清单（带编号）与文本摘要。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网址"},
                    "wait_ms": {"type": "integer", "description": "打开后额外等待毫秒"},
                },
                "required": ["url"],
            },
            risk=Risk.L0_READ,
            fn=_open,
        )
        self.registry["browser_snapshot"] = ToolSpec(
            name="browser_snapshot",
            description="重新读取当前页面。页面变化后想知道新状态时用，比重新 open 更快。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_snapshot,
        )
        self.registry["browser_click"] = ToolSpec(
            name="browser_click",
            description="点击页面元素。优先用快照里的编号 index，也可以传 CSS 选择器 target。",
            parameters={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "快照中的元素编号"},
                    "target": {"type": "string", "description": "CSS 选择器（index 不可用时）"},
                },
                "required": [],
            },
            risk=Risk.L1_WRITE,
            fn=_click,
        )
        self.registry["browser_type"] = ToolSpec(
            name="browser_type",
            description="在输入框中填写文本，可指定 submit=true 直接回车提交。",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "index": {"type": "integer"},
                    "target": {"type": "string"},
                    "submit": {"type": "boolean"},
                },
                "required": ["text"],
            },
            risk=Risk.L1_WRITE,
            fn=_type,
        )
        self.registry["browser_extract"] = ToolSpec(
            name="browser_extract",
            description="按 CSS 选择器提取页面内容；as_table=true 时把表格解析成行数组。",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器，默认 body"},
                    "as_table": {"type": "boolean", "description": "是否按表格解析"},
                },
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_extract,
        )
        self.registry["browser_links"] = ToolSpec(
            name="browser_links",
            description="列出当前页面所有链接（文本 + 地址），用于找入口或抓列表。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_links,
        )
        self.registry["browser_screenshot"] = ToolSpec(
            name="browser_screenshot",
            description="对当前页面整页截图并保存到指定路径。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            risk=Risk.L1_WRITE,
            fn=_screenshot,
        )
        self.registry["browser_wait"] = ToolSpec(
            name="browser_wait",
            description=(
                "等一个元素出现或消失。写浏览器自动化时用它代替 sleep 猜时间 —— "
                "页面慢一点也能等到。state 可取 visible（默认，出现）/ hidden（消失）/ "
                "attached（在 DOM 里）/ detached（已移除）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器"},
                    "state": {
                        "type": "string",
                        "description": "visible / hidden / attached / detached",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时毫秒数，默认 15000",
                    },
                },
                "required": ["selector"],
            },
            risk=Risk.L0_READ,
            fn=_wait,
        )
        self.registry["browser_download"] = ToolSpec(
            name="browser_download",
            description=(
                "下载文件到本地。给 url 直接访问下载地址，或给 selector 点页面上的"
                "下载入口（导出按钮之类）；不给 path 就用服务器建议的文件名。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "下载地址"},
                    "selector": {
                        "type": "string",
                        "description": "触发下载的元素（CSS 选择器）",
                    },
                    "path": {"type": "string", "description": "保存到哪个路径"},
                    "timeout": {
                        "type": "integer",
                        "description": "超时毫秒数，默认 15000",
                    },
                },
                "required": [],
            },
            risk=Risk.L1_WRITE,
            fn=_download,
        )
        self.registry["browser_close"] = ToolSpec(
            name="browser_close",
            description="关闭浏览器，释放资源。任务结束后建议调用。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_close,
        )
