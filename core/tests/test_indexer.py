"""本地索引回归：增量、中文检索、禁区跳过、类型过滤。"""

from __future__ import annotations

from pathlib import Path

import pytest

from oryx.indexer import FileIndex, chunk_text


@pytest.fixture()
def index(tmp_path: Path) -> FileIndex:
    return FileIndex(path=tmp_path / "index.db")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_indexes_text_files(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt", "这是一份关于年度预算的说明文档")
    write(root / "b.md", "# 会议纪要\n讨论了产品路线图")

    stats = index.scan([str(root)])
    assert stats.indexed == 2
    assert index.stats()["files"] == 2


def test_search_chinese_content(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "contract.txt", "甲方与乙方签订的服务合同，有效期一年")
    write(root / "notes.txt", "购物清单：牛奶、鸡蛋")
    index.scan([str(root)])

    hits = index.search("服务合同")
    assert len(hits) == 1
    assert hits[0]["path"].endswith("contract.txt")
    assert "服务合同" in hits[0]["snippet"]


def test_short_query_falls_back_to_like(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "invoice.txt", "本月发票合计 1280 元")
    index.scan([str(root)])

    hits = index.search("发票")  # 2 字，trigram 不命中，走 LIKE
    assert len(hits) == 1
    assert hits[0]["path"].endswith("invoice.txt")


def test_incremental_scan_skips_unchanged(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt", "内容不变")
    first = index.scan([str(root)])
    assert first.indexed == 1

    second = index.scan([str(root)])
    assert second.indexed == 0
    assert second.updated == 0
    assert second.skipped >= 1


def test_modified_file_is_reindexed(index, tmp_path):
    root = tmp_path / "docs"
    path = write(root / "a.txt", "旧内容")
    index.scan([str(root)])

    path.write_text("新内容：报销单据", encoding="utf-8")
    import os

    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))

    stats = index.scan([str(root)])
    assert stats.updated == 1
    assert index.search("报销")


def test_unsupported_extension_skipped(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "photo.jpg", "not really an image")
    write(root / "app.exe", "MZ")
    write(root / "ok.txt", "文本")
    stats = index.scan([str(root)])
    assert stats.indexed == 1
    assert stats.skipped >= 2


def test_forbidden_directory_skipped(tmp_path):
    root = tmp_path / "root"
    secret_dir = root / "secret"
    write(root / "public.txt", "公开资料")
    write(secret_dir / "private.txt", "机密内容")

    index = FileIndex(path=tmp_path / "index.db", forbidden=[str(secret_dir)])
    index.scan([str(root)])

    assert index.search("公开")
    assert not index.search("机密")


def test_docx_extraction(index, tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "docs" / "report.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    document.add_paragraph("第三季度经营分析报告，收入同比增长百分之十二")
    document.save(str(path))

    index.scan([str(tmp_path / "docs")])
    hits = index.search("经营分析报告")
    assert hits and hits[0]["path"].endswith("report.docx")


def test_clear_empties_index(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt", "一些内容")
    index.scan([str(root)])
    index.clear()
    stats = index.stats()
    assert stats["files"] == 0
    assert stats["chunks"] == 0


def test_numeric_query_ignores_thousand_separators(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "invoice.txt", "金额：￥12,800.00\n发票代码：044001900111")
    index.scan([str(root)])

    assert index.search("12800"), "带千分位的金额应能按纯数字命中"
    assert index.search("12,800")
    assert index.search("044001900111")


def test_digit_digest_strips_separators():
    from oryx.indexer import digit_digest

    digest = digit_digest("金额￥12,800.00，编号 A1024")
    assert "12800" in digest
    assert "1024" in digest
    assert "," not in digest


def test_chunk_text_splits_long_content():
    text = "甲" * 3000
    pieces = chunk_text(text)
    assert len(pieces) > 1
    assert all(len(p) <= 900 for p in pieces)
