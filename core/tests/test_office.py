"""文档生成回归：三种格式都要能落盘并被重新打开。"""

from __future__ import annotations

from pathlib import Path

from oryx.office import read_tabular, write_docx, write_pptx, write_xlsx


def test_write_docx(tmp_path: Path):
    target = tmp_path / "report.docx"
    result = write_docx(
        str(target),
        title="季度经营分析",
        subtitle="内部资料",
        sections=[
            {
                "heading": "一、整体情况",
                "paragraphs": ["本季度收入同比增长 12.3%。"],
                "bullets": ["华东区贡献最大", "华南区增速最快"],
            },
            {"heading": "二、问题", "paragraphs": ["人力成本上升"]},
        ],
    )
    assert result["ok"] and target.exists()

    import docx

    document = docx.Document(str(target))
    texts = [p.text for p in document.paragraphs]
    assert any("季度经营分析" in t for t in texts)
    assert any("华东区贡献最大" in t for t in texts)
    assert any("人力成本上升" in t for t in texts)


def test_write_xlsx(tmp_path: Path):
    target = tmp_path / "data.xlsx"
    result = write_xlsx(
        str(target),
        sheets=[
            {
                "name": "销售",
                "rows": [["产品", "数量", "金额"], ["耳机", 3, 897], ["键盘", 1, 499]],
            },
            {"name": "汇总", "rows": [["合计", 1396]]},
        ],
    )
    assert result["ok"] and target.exists()

    import openpyxl

    workbook = openpyxl.load_workbook(str(target))
    assert workbook.sheetnames == ["销售", "汇总"]
    sheet = workbook["销售"]
    assert sheet["A1"].value == "产品"
    assert sheet["B2"].value == 3
    assert sheet.freeze_panes == "A2"


def test_write_pptx(tmp_path: Path):
    target = tmp_path / "deck.pptx"
    result = write_pptx(
        str(target),
        title="产品路线图",
        slides=[
            {"title": "现状", "bullets": ["用户增长放缓", "留存稳定"], "notes": "重点讲留存"},
            {"title": "下一步", "bullets": ["重构引导流程"]},
        ],
    )
    assert result["ok"] and target.exists()

    from pptx import Presentation

    presentation = Presentation(str(target))
    assert len(presentation.slides) == 3  # 封面 + 2 页
    titles = [
        slide.shapes.title.text for slide in presentation.slides if slide.shapes.title
    ]
    assert "产品路线图" in titles
    assert "现状" in titles


def test_write_xlsx_sanitizes_sheet_name(tmp_path: Path):
    target = tmp_path / "weird.xlsx"
    write_xlsx(str(target), sheets=[{"name": "a/b:c*d", "rows": [["x"]]}])

    import openpyxl

    workbook = openpyxl.load_workbook(str(target))
    assert "[" not in workbook.sheetnames[0]
    assert "/" not in workbook.sheetnames[0]


def test_read_tabular(tmp_path: Path):
    target = tmp_path / "rt.xlsx"
    write_xlsx(str(target), sheets=[{"name": "S", "rows": [["a", "b"], [1, 2]]}])
    data = read_tabular(str(target), limit=10)
    assert data["ok"] and data["sheet"] == "S"
    assert data["rows"][0] == ["a", "b"]
    assert data["rows"][1] == ["1", "2"]


def test_docx_handles_empty_sections(tmp_path: Path):
    target = tmp_path / "empty.docx"
    result = write_docx(str(target), title="只有标题")
    assert result["ok"] and target.exists()
