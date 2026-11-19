"""文档生成：Word / Excel / PowerPoint。

只做"生成"，不做"编辑已有复杂排版"——后者要解析用户文档的样式继承，
跟"快速交付一份能看的材料"是两件事。

三个函数都返回统一结构，便于工具层直接回给模型：
    {"ok": bool, "path": str, "bytes": int, "summary": str}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_SECTIONS = 200
MAX_ROWS = 5000
MAX_SLIDES = 200


def _prepare(path: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _result(target: Path, summary: str) -> dict[str, Any]:
    return {
        "ok": True,
        "path": str(target),
        "bytes": target.stat().st_size if target.exists() else 0,
        "summary": summary,
    }


# ---------------------------------------------------------------- Word
def write_docx(
    path: str,
    title: str,
    sections: list[dict[str, Any]] | None = None,
    subtitle: str = "",
) -> dict[str, Any]:
    """生成 .docx。sections 每项：{"heading": 标题, "paragraphs": [...], "bullets": [...]}"""
    import docx

    target = _prepare(path)
    document = docx.Document()

    if title:
        document.add_heading(title, level=0)
    if subtitle:
        document.add_paragraph(subtitle)

    count = 0
    for section in (sections or [])[:MAX_SECTIONS]:
        heading = section.get("heading")
        if heading:
            document.add_heading(str(heading), level=1)
        for paragraph in section.get("paragraphs") or []:
            document.add_paragraph(str(paragraph))
        for bullet in section.get("bullets") or []:
            document.add_paragraph(str(bullet), style="List Bullet")
        count += 1

    document.save(str(target))
    return _result(target, f"生成 Word 文档：{count} 个小节")


# ---------------------------------------------------------------- Excel
def write_xlsx(path: str, sheets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """生成 .xlsx。sheets 每项：{"name": 表名, "rows": [[单元格, ...], ...]}

    第一行默认当作表头加粗，并冻结首行。
    """
    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    target = _prepare(path)
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    total_rows = 0
    for sheet in (sheets or [{"name": "Sheet1", "rows": []}]):
        title = str(sheet.get("name") or f"Sheet{len(workbook.sheetnames) + 1}")
        # Excel 表名限制：31 字符 + 不允许 []:*?/\
        for ch in "[]:*?/\\":
            title = title.replace(ch, "_")
        worksheet = workbook.create_sheet(title[:31] or "Sheet")

        rows = (sheet.get("rows") or [])[:MAX_ROWS]
        widths: dict[int, int] = {}
        for row_index, row in enumerate(rows, start=1):
            for column_index, value in enumerate(row, start=1):
                worksheet.cell(row=row_index, column=column_index, value=value)
                width = len(str(value)) if value is not None else 0
                widths[column_index] = max(widths.get(column_index, 0), width)
        total_rows += len(rows)

        if rows:
            for cell in worksheet[1]:
                if cell.value is not None:
                    cell.font = Font(bold=True)
            worksheet.freeze_panes = "A2"
        for column_index, width in widths.items():
            worksheet.column_dimensions[get_column_letter(column_index)].width = min(
                max(width + 2, 8), 60
            )

    workbook.save(str(target))
    return _result(target, f"生成表格：{len(workbook.sheetnames)} 个工作表 / {total_rows} 行")


# ---------------------------------------------------------------- PowerPoint
def write_pptx(
    path: str, title: str, slides: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """生成 .pptx。slides 每项：{"title": 页标题, "bullets": [...], "notes": 备注}"""
    from pptx import Presentation
    from pptx.util import Pt

    target = _prepare(path)
    presentation = Presentation()

    # 封面
    cover = presentation.slides.add_slide(presentation.slide_layouts[0])
    cover.shapes.title.text = title or "未命名"
    if cover.placeholders and len(cover.placeholders) > 1:
        try:
            cover.placeholders[1].text = ""
        except Exception:
            pass

    count = 0
    for spec in (slides or [])[:MAX_SLIDES]:
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = str(spec.get("title") or "")
        body = slide.placeholders[1].text_frame
        bullets = [str(b) for b in (spec.get("bullets") or [])]
        body.clear()
        for index, bullet in enumerate(bullets):
            paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
            paragraph.text = bullet
            paragraph.font.size = Pt(20)
        notes = spec.get("notes")
        if notes:
            slide.notes_slide.notes_text_frame.text = str(notes)
        count += 1

    presentation.save(str(target))
    return _result(target, f"生成演示文稿：封面 + {count} 页")


# ---------------------------------------------------------------- 读取辅助
def read_tabular(path: str, sheet: str | None = None, limit: int = 200) -> dict[str, Any]:
    """读回表格前若干行，供生成后自检或用户追问时使用。"""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook[sheet] if sheet and sheet in workbook.sheetnames else workbook.worksheets[0]
        rows = []
        for row in worksheet.iter_rows(values_only=True):
            rows.append(["" if cell is None else str(cell) for cell in row])
            if len(rows) >= limit:
                break
        return {"ok": True, "sheet": worksheet.title, "rows": rows}
    finally:
        workbook.close()
