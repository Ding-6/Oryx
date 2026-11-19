"""Office 三件套（Word / Excel / PPT）+ HTML / PDF / 图片生成。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class OfficeToolsMixin:
    def _register_office_tools(self) -> None:
        from .. import office

        def _docx(
            path: str,
            title: str,
            sections: list[dict[str, Any]] | None = None,
            subtitle: str = "",
        ) -> dict[str, Any]:
            return {"ok": True, "content": office.write_docx(path, title, sections, subtitle)}

        def _xlsx(path: str, sheets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            return {"ok": True, "content": office.write_xlsx(path, sheets)}

        def _pptx(
            path: str, title: str, slides: list[dict[str, Any]] | None = None
        ) -> dict[str, Any]:
            return {"ok": True, "content": office.write_pptx(path, title, slides)}

        def _xlsx_read(path: str, sheet: str = "", limit: int = 100) -> dict[str, Any]:
            return {
                "ok": True,
                "content": office.read_tabular(path, sheet or None, int(limit)),
            }

        self.registry["docx_write"] = ToolSpec(
            name="docx_write",
            description=(
                "生成 Word 文档（.docx）。按「标题 + 若干小节」组织："
                "每个小节可以有 heading、paragraphs（正文段落）、bullets（要点列表）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "输出文件路径，以 .docx 结尾"},
                    "title": {"type": "string", "description": "文档主标题"},
                    "subtitle": {"type": "string", "description": "副标题或说明"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "paragraphs": {"type": "array", "items": {"type": "string"}},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
                "required": ["path", "title"],
            },
            risk=Risk.L1_WRITE,
            fn=_docx,
        )
        self.registry["xlsx_write"] = ToolSpec(
            name="xlsx_write",
            description=(
                "生成 Excel 表格（.xlsx）。sheets 是工作表数组，每个工作表用 rows 二维数组表示，"
                "第一行会被当作表头加粗并冻结。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "输出文件路径，以 .xlsx 结尾"},
                    "sheets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "rows": {
                                    "type": "array",
                                    "items": {"type": "array", "items": {}},
                                },
                            },
                            "required": ["name", "rows"],
                        },
                    },
                },
                "required": ["path", "sheets"],
            },
            risk=Risk.L1_WRITE,
            fn=_xlsx,
        )
        self.registry["pptx_write"] = ToolSpec(
            name="pptx_write",
            description=(
                "生成演示文稿（.pptx）。先自动生成封面，再按 slides 逐页输出；"
                "每页一个标题加若干要点，可选 notes 写备注。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "输出文件路径，以 .pptx 结尾"},
                    "title": {"type": "string", "description": "演示文稿标题（封面）"},
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "notes": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["path", "title"],
            },
            risk=Risk.L1_WRITE,
            fn=_pptx,
        )
        self.registry["xlsx_read"] = ToolSpec(
            name="xlsx_read",
            description="读取 Excel 的前若干行，用于核对生成结果或分析已有表格。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "sheet": {"type": "string", "description": "工作表名，默认第一个"},
                    "limit": {"type": "integer", "description": "最多读取行数"},
                },
                "required": ["path"],
            },
            risk=Risk.L0_READ,
            fn=_xlsx_read,
        )

        # ------------------------------------------------ 多模态交付（补齐 Marvis 差距）
        # 三种新格式与 Office 三件套同属「直接产出可用文件」，
        # 走 Python 库而不是 shell —— shell 是 L2，交付一份报告不该每次弹审批。
        from .. import artifacts

        def _as_result(out: dict[str, Any]) -> dict[str, Any]:
            """统一成内核约定的 {"ok", "content"} 结构。

            成功时 content 保留产物字典（里面有 path / bytes —— 产物面板靠它判定），
            失败时 content 是错误文本，且 ok=False 让主循环标红而不是假装成功。
            """
            if out.get("ok"):
                return {"ok": True, "content": out}
            return {"ok": False, "content": str(out.get("error") or "生成失败")}

        def _html(
            path: str,
            title: str,
            sections: list[dict[str, Any]] | None = None,
            subtitle: str = "",
        ) -> dict[str, Any]:
            return _as_result(artifacts.write_html(path, title, sections, subtitle))

        def _pdf(
            path: str,
            title: str,
            sections: list[dict[str, Any]] | None = None,
            subtitle: str = "",
        ) -> dict[str, Any]:
            return _as_result(artifacts.write_pdf(path, title, sections, subtitle))

        def _image(
            path: str,
            chart: dict[str, Any] | None = None,
            title: str = "",
            width: int = 1200,
            height: int = 700,
        ) -> dict[str, Any]:
            return _as_result(artifacts.write_image(path, chart, title, int(width), int(height)))

        _sections_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string", "description": "小节标题"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string", "description": "提示/备注，会渲染成高亮块"},
                    "table": {
                        "type": "object",
                        "properties": {
                            "headers": {"type": "array", "items": {"type": "string"}},
                            "rows": {"type": "array", "items": {"type": "array", "items": {}}},
                        },
                    },
                    "chart": {
                        "type": "object",
                        "description": "可选图表：bar 柱状 / line 折线 / pie 饼图",
                        "properties": {
                            "type": {"type": "string", "enum": ["bar", "line", "pie"]},
                            "title": {"type": "string"},
                            "labels": {"type": "array", "items": {"type": "string"}},
                            "values": {"type": "array", "items": {"type": "number"}},
                        },
                    },
                },
            },
        }

        for tool_name, description, fn, required in (
            (
                "html_write",
                "生成一份自包含的 HTML 网页/可视化看板（离线可打开，内嵌 CSS 与 SVG 图表，不依赖 CDN）。"
                "适合交付报告、看板、对比页。sections 支持标题、段落、要点、表格、图表、备注。",
                _html,
                ["path", "title"],
            ),
            (
                "pdf_write",
                "生成 PDF 文档（支持中文、表格与图表）。适合交付需要打印/转发的正式报告。"
                "sections 结构与 html_write 相同。",
                _pdf,
                ["path", "title"],
            ),
            (
                "image_write",
                "生成一张数据图表图片。path 以 .svg 结尾输出矢量 SVG，其余输出 PNG。"
                "chart 指定 type(bar/line/pie)、labels、values；也支持 [{label,value}] 形式。",
                _image,
                ["path", "chart"],
            ),
        ):
            props: dict[str, Any] = {
                "path": {"type": "string", "description": "输出文件绝对路径"},
            }
            if tool_name == "image_write":
                props["chart"] = _sections_schema["items"]["properties"]["chart"]
                props["title"] = {"type": "string", "description": "图表标题"}
                props["width"] = {"type": "integer", "description": "PNG 宽度，默认 1200"}
                props["height"] = {"type": "integer", "description": "PNG 高度，默认 700"}
            else:
                props["title"] = {"type": "string", "description": "文档主标题"}
                props["subtitle"] = {"type": "string", "description": "副标题"}
                props["sections"] = _sections_schema
            self.registry[tool_name] = ToolSpec(
                name=tool_name,
                description=description,
                parameters={"type": "object", "properties": props, "required": required},
                risk=Risk.L1_WRITE,
                fn=fn,
            )
