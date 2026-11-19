"""OCR 与图片索引回归。

依赖 rapidocr-onnxruntime 与系统字体；缺失时跳过而不是失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oryx.indexer import FileIndex
from oryx.ocr import available, ocr_image

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def make_image(path: Path, text: str, size: int = 44) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
    if font_path is None:
        pytest.skip("没有可用的中文字体")
    image = Image.new("RGB", (760, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 70), text, fill="black", font=ImageFont.truetype(font_path, size))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


@pytest.mark.skipif(not available(), reason="未安装 OCR 引擎")
def test_ocr_reads_rendered_text(tmp_path):
    image = make_image(tmp_path / "invoice.png", "发票金额 1280 元")
    result = ocr_image(image)
    assert result["ok"], result.get("error")
    assert "1280" in result["text"]


@pytest.mark.skipif(not available(), reason="未安装 OCR 引擎")
def test_image_indexed_and_searchable(tmp_path):
    root = tmp_path / "pics"
    make_image(root / "receipt.png", "报销单据 编号 A1024")

    index = FileIndex(path=tmp_path / "index.db", ocr=True)
    stats = index.scan([str(root)])
    assert stats.indexed == 1

    hits = index.search("A1024")
    assert hits and hits[0]["path"].endswith("receipt.png")


def test_image_skipped_when_ocr_disabled(tmp_path):
    root = tmp_path / "pics"
    make_image(root / "receipt.png", "报销单据 编号 A1024")

    index = FileIndex(path=tmp_path / "index.db", ocr=False)
    stats = index.scan([str(root)])
    assert stats.indexed == 0
    assert stats.skipped >= 1
    assert not index.search("A1024")


def test_missing_image_reports_error(tmp_path):
    result = ocr_image(tmp_path / "nope.png")
    assert result["ok"] is False
    assert "不存在" in result["error"]
