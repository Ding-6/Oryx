"""工具检索与 mutates 声明复核的测试。

两个真问题：

1. **中文查询搜不到工具** —— 关键词路用 `needle.split()`，
   中文没有空格，「统计字数」整句成一个 token，
   去 `in` 描述里的「统计文本里的字数」当然匹配不上。
   这跟技能路由那个老毛病是同一类。
2. **mutates 漏声明 = 谎报** —— `result["mutated"] = bool(spec.mutates)`，
   工具结果里那句「我没动过任何东西」会变成假的。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx import embeddings
from oryx.registry import tokenize
from oryx.tooltable import (
    MUTATING_NAME_HINTS,
    READONLY_NAME_HINTS,
    ToolTable,
)


class _Spec:
    def __init__(self, name, description="d", parameters=None, risk=0,
                 mutates=False, embed_text=""):
        self.name = name
        self.description = description
        self.parameters = parameters if parameters is not None else {
            "type": "object", "properties": {}, "required": [],
        }
        self.risk = risk
        self.mutates = mutates
        self.embed_text = embed_text

    def semantic_text(self):
        return self.embed_text or self.description


# ── 分词 ──────────────────────────────────────────────────────────────
def test_tokenize_chinese_bigram():
    grams = tokenize("统计字数")
    assert "统计" in grams and "字数" in grams


def test_tokenize_english_word():
    assert "words" in tokenize("count words")


def test_tokenize_is_cached():
    """同一段文本反复切，不该重复算 —— 检索会对每个工具反复切。"""
    assert tokenize("统计文本里的字数") is tokenize("统计文本里的字数")


# ── mutates 复核 ──────────────────────────────────────────────────────
def _table_with(names):
    t = ToolTable()
    for n in names:
        t.add_builtin(_Spec(n))
    return t


def test_mutates_check_catches_write_tools():
    """名字里有写动词却没声明 mutates → 要报出来。"""
    t = _table_with(["fs_write"])
    sus = t.check_mutates()
    assert any("fs_write" in s for s in sus)


def test_mutates_check_no_false_positive_on_skill():
    """use_skill 含 "kill" 但是只读 —— 不能报。"""
    t = _table_with(["use_skill"])
    assert t.check_mutates() == []


def test_mutates_check_no_false_positive_on_settings():
    """sys_display_settings 含 "set" 但是「读取」—— 不能报。"""
    t = _table_with(["sys_display_settings", "sys_mouse_settings"])
    assert t.check_mutates() == []


def test_mutates_check_no_false_positive_on_input():
    """sys_input_methods 含 "put" 但是「列出」—— 不能报。"""
    t = _table_with(["sys_input_methods"])
    assert t.check_mutates() == []


def test_mutates_check_no_false_positive_on_runs():
    """automation_runs 含 "run" 但是列记录 —— 不能报。"""
    t = _table_with(["automation_runs"])
    assert t.check_mutates() == []


def test_mutates_check_skips_declared():
    """已声明 mutates 的不报。"""
    t = ToolTable()
    t.add_builtin(_Spec("fs_write", mutates=True))
    assert t.check_mutates() == []


def test_mutates_check_splits_on_underscore():
    """按完整词匹配，不按子串。"""
    t = _table_with(["skill_kill"])
    # "kill" 是完整词 → 应该报
    assert any("skill_kill" in s for s in t.check_mutates())


def test_mutates_verb_list_covers_common_writes():
    """动词表要覆盖常见的写操作。"""
    for word in ("write", "delete", "run", "click", "save", "dispatch"):
        assert word in MUTATING_NAME_HINTS


def test_readonly_hints_cover_common_reads():
    for word in ("status", "list", "read", "search"):
        assert word in READONLY_NAME_HINTS


# ── 工具检索（中文）───────────────────────────────────────────────────
@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    """一个最小 runtime，带上几个中文描述的工具。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    return AgentRuntime(Config(data), Store(tmp_path / "o.db"), EventBus())


def _search(rt, query, limit=6):
    out = rt.registry["tool_search"].fn(query=query, limit=limit)
    return [h["name"] for h in (out.get("content") or [])]


def test_chinese_query_finds_tool(runtime):
    """中文查询必须能找到中文描述的工具 —— 这是修掉的老毛病。"""
    hits = _search(runtime, "统计字数")
    assert hits, "中文查询一条都没命中"


def test_chinese_query_finds_specific_tool(runtime):
    hits = _search(runtime, "看电脑配置")
    assert "system_info" in hits, f"没找到 system_info：{hits}"


def test_chinese_query_finds_screenshot_tool(runtime):
    hits = _search(runtime, "截图")
    assert any("screenshot" in h or "ocr" in h for h in hits), f"没找到截图工具：{hits}"


@pytest.mark.skipif(
    not embeddings.available(),
    reason="英文查询靠语义召回 —— 没装向量依赖时这条测不了",
)
def test_english_query_still_works(runtime):
    hits = _search(runtime, "read file")
    assert "fs_read" in hits, f"英文查询坏了：{hits}"


def test_exact_tool_name_ranks_first(runtime):
    """模型直接报出工具名时，它必须排第一。"""
    hits = _search(runtime, "fs_read")
    assert hits and hits[0] == "fs_read", f"精确匹配没排第一：{hits}"


def test_search_result_has_scores(runtime):
    """召回分数要可见 —— 用户要能知道「为什么召回它」。"""
    out = runtime.registry["tool_search"].fn(query="截图", limit=3)
    items = out.get("content") or []
    assert items
    assert any("keyword_score" in i or "semantic_score" in i for i in items), \
        "结果里没有召回分数"


def test_unrelated_query_returns_nothing_or_low(runtime):
    """完全无关的查询不该乱召回一堆。"""
    hits = _search(runtime, "zzzqqqxyz")
    assert len(hits) <= 2, f"无关查询召回太多了：{hits}"
