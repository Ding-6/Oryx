"""工具语义索引：embed_text 拼接、指纹、缓存、降级与融合。"""

from __future__ import annotations

from typing import Any

import pytest

from oryx.policy import Risk
from oryx.toolindex import MAX_EMBED_CHARS, ToolIndex, build_embed_text, fuse
from oryx.tools import ToolSpec


class FakeSpec:
    """最小 ToolSpec 替身，避免起整个 AgentRuntime。"""

    def __init__(self, name: str, description: str = "", embed_text: str = "",
                 risk: Risk = Risk.L0_READ):
        self.name = name
        self.description = description
        self.embed_text = embed_text
        self.risk = risk
        self.parameters = {}

    def semantic_text(self) -> str:
        if self.embed_text:
            return self.embed_text
        return build_embed_text(self.name, self.description, self.risk.label)


def fake_registry() -> dict[str, Any]:
    return {
        "fs_list": FakeSpec("fs_list", "列出目录下的文件与子目录。", "看看某个目录下面有哪些文件"),
        "fs_read": FakeSpec("fs_read", "读取文本文件内容。"),
        "system_info": FakeSpec("system_info", "读取本机系统信息。", "查电脑配置"),
    }


# ------------------------------------------------------------------ embed_text


def test_build_embed_text_includes_name_desc_and_risk():
    text = build_embed_text("fs_list", "列出目录", "只读")
    assert "fs list" in text          # 下划线转空格，让分词更稳
    assert "列出目录" in text
    assert "只读" in text


def test_build_embed_text_truncates():
    long_desc = "很长的描述" * 200
    assert len(build_embed_text("x", long_desc)) <= MAX_EMBED_CHARS


def test_semantic_text_prefers_explicit_embed_text():
    spec = FakeSpec("a", "原始描述", "人写的语义提示")
    assert spec.semantic_text() == "人写的语义提示"


def test_semantic_text_falls_back_to_name_and_desc():
    spec = FakeSpec("a", "原始描述")
    text = spec.semantic_text()
    assert "原始描述" in text and "a" in text


def test_toolspec_exposes_embed_text_field():
    spec = ToolSpec(name="t", description="d", parameters={}, risk=Risk.L0_READ,
                    fn=lambda: None, embed_text="提示")
    assert spec.embed_text == "提示"
    # schema 是给模型看的，不该把 embed_text 泄进去
    assert "embed_text" not in spec.schema()


# ------------------------------------------------------------------ 指纹


def test_fingerprint_changes_with_embed_text():
    """embed_text 变了必须重建，否则会一直用过期向量。"""
    a = fake_registry()
    b = fake_registry()
    b["fs_list"].embed_text = "改了"
    assert ToolIndex._fingerprint(a) != ToolIndex._fingerprint(b)


def test_fingerprint_stable_for_same_registry():
    assert ToolIndex._fingerprint(fake_registry()) == ToolIndex._fingerprint(fake_registry())


# ------------------------------------------------------------------ 降级


def test_ensure_returns_false_when_model_unavailable(monkeypatch):
    """向量模型不可用时不能抛异常，要返回 False 让上层退回关键词。"""
    from oryx import embeddings as emb

    monkeypatch.setattr(emb, "available", lambda *a, **k: False)
    idx = ToolIndex()
    assert idx.ensure(fake_registry()) is False
    assert idx.ready is False
    assert idx.search("查电脑配置") == []
    assert "降级" in idx.error


def test_ensure_survives_encode_failure(monkeypatch):
    from oryx import embeddings as emb

    monkeypatch.setattr(emb, "available", lambda *a, **k: True)
    monkeypatch.setattr(emb, "encode", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    idx = ToolIndex()
    assert idx.ensure(fake_registry()) is False
    assert idx.search("x") == []


def test_search_on_empty_query_returns_nothing():
    assert ToolIndex().search("   ") == []


# ------------------------------------------------------------------ 缓存


def test_cache_roundtrip(tmp_path, monkeypatch):
    """首次编码并落盘，第二个实例应当直接吃缓存。"""
    from oryx import embeddings as emb
    from oryx import toolindex as tmod

    import numpy as np

    monkeypatch.setattr(tmod, "cache_path", lambda: tmp_path / "tool_index.json")
    calls = {"n": 0}

    def fake_encode(texts, **kw):
        calls["n"] += 1
        return np.eye(len(texts), dtype="float32")

    monkeypatch.setattr(emb, "available", lambda *a, **k: True)
    monkeypatch.setattr(emb, "encode", fake_encode)
    monkeypatch.setattr(emb, "dim", lambda: 3)

    registry = fake_registry()
    idx = ToolIndex()
    assert idx.ensure(registry) is True
    assert calls["n"] == 1
    assert (tmp_path / "tool_index.json").exists()

    # 第二个实例应当直接吃缓存，不再调 encode
    idx2 = ToolIndex()
    assert idx2.ensure(registry) is True
    assert calls["n"] == 1, "命中缓存时不该重新编码"


def test_corrupted_cache_triggers_rebuild(tmp_path, monkeypatch):
    from oryx import embeddings as emb
    from oryx import toolindex as tmod

    monkeypatch.setattr(tmod, "cache_path", lambda: tmp_path / "tool_index.json")
    (tmp_path / "tool_index.json").write_text("{ not json", "utf-8")

    import numpy as np

    monkeypatch.setattr(emb, "available", lambda *a, **k: True)
    monkeypatch.setattr(emb, "encode", lambda texts, **kw: np.eye(len(texts), dtype="float32"))
    monkeypatch.setattr(emb, "dim", lambda: 3)

    idx = ToolIndex()
    assert idx.ensure(fake_registry()) is True, "缓存损坏要能自愈重建"
    assert idx.status()["count"] == 3


def test_clear_cache_resets_state(tmp_path, monkeypatch):
    from oryx import embeddings as emb
    from oryx import toolindex as tmod

    monkeypatch.setattr(tmod, "cache_path", lambda: tmp_path / "tool_index.json")
    import numpy as np

    monkeypatch.setattr(emb, "available", lambda *a, **k: True)
    monkeypatch.setattr(emb, "encode", lambda texts, **kw: np.eye(len(texts), dtype="float32"))
    monkeypatch.setattr(emb, "dim", lambda: 3)

    idx = ToolIndex()
    idx.ensure(fake_registry())
    assert idx.ready
    assert idx.clear_cache() is True
    assert not idx.ready


# ------------------------------------------------------------------ 融合


def test_fuse_prefers_union_of_both_lists():
    out = fuse(
        keyword=[{"name": "a", "score": 10.0}, {"name": "b", "score": 5.0}],
        semantic=[{"name": "c", "score": 0.9}, {"name": "a", "score": 0.8}],
        limit=3,
    )
    names = [o["name"] for o in out]
    assert set(names) == {"a", "b", "c"}


def test_fuse_weighs_semantic_higher():
    """语义分与关键词分打平时，语义那侧应该排前。"""
    out = fuse(
        keyword=[{"name": "kw", "score": 1.0}],
        semantic=[{"name": "sem", "score": 1.0}],
        limit=2,
        semantic_weight=0.6,
    )
    assert out[0]["name"] == "sem"


def test_fuse_respects_limit():
    out = fuse(
        keyword=[{"name": f"k{i}", "score": float(i)} for i in range(5)],
        semantic=[],
        limit=2,
    )
    assert len(out) == 2


def test_fuse_handles_empty_inputs():
    assert fuse([], [], 5) == []
    assert len(fuse([{"name": "a", "score": 1.0}], [], 5)) == 1


def test_fuse_ranks_exact_name_match_first():
    """查询词就是工具名时，它必须排第一。

    模型自己报出 fs_read 却召回不到它，语义层就成了负优化。
    """
    out = fuse(
        keyword=[{"name": "fs_read", "score": 10.0, "exact": True},
                 {"name": "xlsx_read", "score": 2.0}],
        semantic=[{"name": "xlsx_read", "score": 0.95},
                  {"name": "docx_write", "score": 0.94},
                  {"name": "fs_read", "score": 0.60}],
        limit=3,
    )
    assert out[0]["name"] == "fs_read"


def test_exact_bonus_is_large_enough():
    """精确命中的加分要压得住语义噪声。"""
    from oryx.toolindex import EXACT_BONUS

    assert EXACT_BONUS >= 1.0, "归一化后两侧分都在 0..1，bonus 至少要 1 才稳"