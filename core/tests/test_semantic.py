"""向量检索回归：语义召回 + 混合排序。

模型缺失时自动跳过，不阻塞 CI。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oryx import embeddings
from oryx.indexer import FileIndex

pytestmark = pytest.mark.skipif(not embeddings.available(), reason="未安装向量模型")


@pytest.fixture()
def index(tmp_path: Path) -> FileIndex:
    return FileIndex(path=tmp_path / "index.db", ocr=False, embeddings=True)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_encode_is_normalized():
    import numpy as np

    vectors = embeddings.encode(["测试句子", "另一句话"])
    assert vectors.shape[0] == 2
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)


def test_vectors_are_persisted(index, tmp_path):
    write(tmp_path / "docs" / "a.txt", "一些文字内容")
    index.scan([str(tmp_path / "docs")])
    assert index.stats()["vectors"] >= 1


def test_semantic_recall_without_keyword_overlap(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "finance.txt", "本季度财务报表：营业收入、成本与利润分析，毛利率环比提升")
    write(root / "ops.txt", "服务器 CPU 使用率监控告警阈值配置说明，包含内存与磁盘指标")
    index.scan([str(root)])

    # 查询里没有任何一个词直接出现在财务文档中
    hits = index.search("公司这季度赚了多少钱", mode="semantic")
    assert hits, "语义检索应当有结果"
    assert hits[0]["path"].endswith("finance.txt")


def test_keyword_mode_still_exact(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "invoice.txt", "发票代码：044001900111")
    write(root / "other.txt", "完全无关的运维手册")
    index.scan([str(root)])

    hits = index.search("044001900111", mode="keyword")
    assert hits and hits[0]["path"].endswith("invoice.txt")


def test_hybrid_merges_both_signals(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "finance.txt", "本季度财务报表：营业收入、成本与利润分析")
    write(root / "meeting.txt", "会议纪要：讨论了新的考勤制度")
    index.scan([str(root)])

    hits = index.search("利润", mode="hybrid")
    assert hits and hits[0]["path"].endswith("finance.txt")


def test_clear_removes_vectors(index, tmp_path):
    write(tmp_path / "docs" / "a.txt", "一些文字内容")
    index.scan([str(tmp_path / "docs")])
    index.clear()
    assert index.stats()["vectors"] == 0


def test_unchanged_chunks_reuse_vectors(index, tmp_path):
    """文件改动后，只有变化的片段需要重新 embedding。"""
    import os

    root = tmp_path / "docs"
    base = "".join(f"第{i}段：这是用于测试增量索引的正文内容，包含一些说明文字。\n" for i in range(80))
    path = write(root / "long.txt", base)

    first = index.scan([str(root)])
    assert first.embedded >= 2, "长文件应当被切成多个片段"

    path.write_text(base + "\n新增尾部：报销单据编号 A1024\n", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))

    second = index.scan([str(root)])
    assert second.updated == 1
    assert second.reused >= 1, "未变化的前缀片段应复用向量"
    assert second.embedded < first.embedded, "复用过半后不该全量重算"


def test_vectors_survive_reopen(index, tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt", "持久化测试内容")
    index.scan([str(root)])
    count = index.stats()["vectors"]

    reopened = FileIndex(path=tmp_path / "index.db", ocr=False)
    assert reopened.stats()["vectors"] == count
