"""frontmatter 解析 —— 全项目唯一一份。

这个模块合并了原先散在五处的解析器（memory / registry / skills / experts / agents），
行为统一。这里锁住两件事：

1. **该支持的写法都支持**（嵌套、列表、布尔）
2. **宽松语义不变** —— 老格式不该因为合并而解析失败或丢内容

第 2 点更重要：当年就是因为「描述里带冒号」不敢用严格 YAML，
才各写各的宽松版本。合并后这条性质不能丢。
"""
from __future__ import annotations

import pytest

from oryx.frontmatter import as_list, parse_frontmatter


# ── 基本 ──────────────────────────────────────────────────────────────
def test_no_frontmatter_returns_whole_text():
    meta, body = parse_frontmatter("就是正文\n")
    assert meta == {}
    assert body == "就是正文\n"


def test_simple_keys():
    meta, body = parse_frontmatter("---\nname: x\ndescription: y\n---\n正文")
    assert meta == {"name": "x", "description": "y"}
    assert body == "正文"


def test_body_keeps_inner_blank_lines():
    meta, body = parse_frontmatter("---\nname: x\n---\n第一段\n\n第二段\n")
    assert meta == {"name": "x"}
    assert "第一段" in body and "第二段" in body


# ── 宽松语义：合并后不能丢 ────────────────────────────────────────────
def test_description_with_hash_is_kept_whole():
    """`#` 前有空格就是 YAML 注释 —— 严格 YAML 会把后半截吃掉。

    这就是**不用 YAML** 的原因：失败会报错，截断只是悄悄丢内容。
    """
    meta, _ = parse_frontmatter("---\ndescription: 有 # 号 的文字\n---\n")
    assert meta["description"] == "有 # 号 的文字"


def test_description_with_colon_is_kept_whole():
    """`12:30` 的冒号后面没空格 —— 值的一部分，不该被切开。"""
    meta, _ = parse_frontmatter("---\ndescription: 时间 12:30 开会\n---\n")
    assert meta["description"] == "时间 12:30 开会"


def test_description_with_url_is_kept_whole():
    meta, _ = parse_frontmatter("---\ndescription: 见 https://x.com/a:b\n---\n")
    assert meta["description"] == "见 https://x.com/a:b"


def test_value_keeps_colons_after_first():
    """值里多个冒号 —— 只按第一个切。"""
    meta, _ = parse_frontmatter("---\ndescription: a: b: c\n---\n")
    assert meta["description"] == "a: b: c"


def test_quotes_are_stripped():
    meta, _ = parse_frontmatter("---\ndescription: \"带引号\"\n---\n")
    assert meta["description"] == "带引号"


def test_whole_line_comment_is_skipped():
    meta, _ = parse_frontmatter("---\n# 这行是注释\nname: x\n---\n")
    assert meta == {"name": "x"}


# ── 列表与布尔 ────────────────────────────────────────────────────────
def test_inline_list():
    meta, _ = parse_frontmatter("---\ntriggers: [a, b]\n---\n")
    assert meta["triggers"] == ["a", "b"]


def test_inline_list_with_quotes():
    meta, _ = parse_frontmatter("---\ntriggers: ['a', \"b\"]\n---\n")
    assert meta["triggers"] == ["a", "b"]


def test_boolean_values():
    meta, _ = parse_frontmatter("---\neager: true\nalwaysApply: false\n---\n")
    assert meta["eager"] is True
    assert meta["alwaysApply"] is False


def test_numbers_stay_strings():
    """数字故意保持字符串 —— 消费方一直是 `int(str(v))`。"""
    meta, _ = parse_frontmatter("---\nmax_turns: 8\n---\n")
    assert meta["max_turns"] == "8"


# ── 嵌套 ──────────────────────────────────────────────────────────────
def test_nested_block():
    """嵌套块 —— requires 就靠它。"""
    meta, _ = parse_frontmatter(
        "---\nrequires:\n  skills: web-research\n  ui: chart-panel\n---\n")
    assert meta["requires"] == {"skills": "web-research", "ui": "chart-panel"}


def test_multiline_list():
    meta, _ = parse_frontmatter("---\ntriggers:\n  - a\n  - b\n---\n")
    assert meta["triggers"] == ["a", "b"]


def test_nested_list_under_key():
    meta, _ = parse_frontmatter("---\nrequires:\n  skills:\n    - a\n    - b\n---\n")
    assert meta["requires"] == {"skills": ["a", "b"]}


def test_block_ends_at_dedent():
    """缩进块结束后就是别的字段 —— 别把后面的内容吃进来。"""
    meta, _ = parse_frontmatter(
        "---\nrequires:\n  skills: a\ndescription: 不该算进依赖\n---\n")
    assert meta["requires"] == {"skills": "a"}
    assert meta["description"] == "不该算进依赖"


def test_mixed_nested_and_flat():
    meta, _ = parse_frontmatter(
        "---\nname: x\ntriggers:\n  - a\nrequires:\n  skills: y\n---\n")
    assert meta["name"] == "x"
    assert meta["triggers"] == ["a"]
    assert meta["requires"] == {"skills": "y"}


# ── as_list ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    (None, []),
    ("", []),
    ("a", ["a"]),
    ("a, b", ["a", "b"]),
    ("a，b", ["a", "b"]),
    ("a、b", ["a", "b"]),
    ("[a, b]", ["a", "b"]),
    (["a", "b"], ["a", "b"]),
    ("a,b,c", ["a", "b", "c"]),
])
def test_as_list_variants(value, expected):
    assert as_list(value) == expected


def test_as_list_on_nested_block():
    """嵌套块也能摊成列表。"""
    assert as_list({"skills": ["a", "b"]}) == ["a", "b"]
    assert as_list({"skills": "a"}) == ["a"]


def test_as_list_keeps_numbers_as_text():
    assert as_list([1, 2]) == ["1", "2"]
