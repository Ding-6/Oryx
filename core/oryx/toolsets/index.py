"""本地内容索引：扫描、检索、状态。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class IndexToolsMixin:
    def _register_index_tools(self) -> None:
        def _index_status() -> dict[str, Any]:
            return {"ok": True, "content": self.index.stats()}

        def _index_search(query: str, limit: int = 20, mode: str = "hybrid") -> dict[str, Any]:
            # 降级可见化：hybrid/semantic 模式在向量不可用时其实只跑了关键词，
            # 以前用户完全看不出来。这里先检查再发事件。
            if mode in ("hybrid", "semantic"):
                try:
                    state = self.index.stats()
                    if not state.get("embedding_ready"):
                        self._announce_degradation(
                            "语义检索", "端侧向量模型未安装，本次只用关键词检索",
                            "安装 bge-small-zh-v1.5 模型后可启用语义召回",
                        )
                    elif int(state.get("vectors") or 0) == 0 and int(state.get("files") or 0) > 0:
                        self._announce_degradation(
                            "语义检索", "索引里还没有向量，本次只用关键词检索",
                            "重新执行 index_scan 会为已索引文件补建向量",
                        )
                except Exception:
                    pass
            hits = self.index.search(query, int(limit), mode=mode)
            if not hits:
                stats = self.index.stats()
                if stats["files"] == 0:
                    return {
                        "ok": True,
                        "content": "索引为空。请先调用 index_scan 建立索引后再检索。",
                        "meta": stats,
                    }
                return {"ok": True, "content": f"没有找到匹配「{query}」的内容。", "meta": {"hits": 0}}
            return {"ok": True, "content": hits, "meta": {"hits": len(hits), "mode": mode}}

        def _index_scan(roots: list[str] | None = None) -> dict[str, Any]:
            targets = roots or self.config.allowed_roots
            if self.index.scanning:
                return {"ok": False, "content": "已有扫描在进行中，请等待完成。"}

            def progress(payload: dict[str, Any]) -> None:
                if payload.get("done"):
                    self.bus.publish("index.done", **payload)
                else:
                    self.bus.publish("index.progress", **payload)

            self.bus.publish("index.started", roots=targets)
            stats = self.index.scan(targets, on_progress=progress)
            return {"ok": True, "content": stats.to_dict()}

        self.registry["index_status"] = ToolSpec(
            name="index_status",
            description="查看本地内容索引的状态：已索引文件数、片段数、上次扫描结果。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_index_status,
        )
        self.registry["index_search"] = ToolSpec(
            name="index_search",
            description="在本地索引中检索文件。默认混合检索（关键词 + 语义），返回路径与命中片段。适合“记得意思但记不住词”的场景。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要搜索的内容或描述"},
                    "limit": {"type": "integer", "description": "最多返回条数"},
                    "mode": {
                        "type": "string",
                        "enum": ["hybrid", "keyword", "semantic"],
                        "description": "hybrid=关键词+语义；keyword=精确匹配；semantic=纯语义",
                    },
                },
                "required": ["query"],
            },
            risk=Risk.L0_READ,
            fn=_index_search,
        )
        self.registry["index_scan"] = ToolSpec(
            name="index_scan",
            description="扫描并建立/更新本地文件内容索引。不传 roots 时扫描配置里的允许目录。耗时可能较长。",
            parameters={
                "type": "object",
                "properties": {
                    "roots": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要索引的目录列表",
                    }
                },
                "required": [],
            },
            risk=Risk.L1_WRITE,
            fn=_index_scan,
        )

        def _ocr_image(path: str) -> dict[str, Any]:
            from .. import ocr as _ocr
            from ..ocr import ocr_image

            if not _ocr.available():
                self._announce_degradation(
                    "图片 OCR", "未安装 rapidocr-onnxruntime，本次无法识别图片文字",
                    "pip install rapidocr-onnxruntime 后重启即可使用",
                )
            result = ocr_image(path)
            if not result.get("ok"):
                return {"ok": False, "content": result.get("error", "OCR 失败")}
            return {
                "ok": True,
                "content": {
                    "text": result.get("text", ""),
                    "lines": (result.get("lines") or [])[:50],
                    "elapsed_ms": result.get("elapsed_ms"),
                },
            }

        self.registry["ocr_image"] = ToolSpec(
            name="ocr_image",
            description="识别图片中的文字（截图、发票、白板、证照）。返回按行排列的文本。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "图片路径"}},
                "required": ["path"],
            },
            risk=Risk.L0_READ,
            fn=_ocr_image,
        )
