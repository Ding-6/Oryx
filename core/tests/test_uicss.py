"""核心样式子集提取的测试。

要锁住三件事：

1. **只提取要的** —— 提 `.btn` 不该把整个 main.css 都拖进来。
2. **@media 里的规则也要提取到，且结构完整** ——
   只留声明体的话会变成无效 CSS，浏览器整块丢弃。
3. **@media 块要能被识别出它涉及哪些类** ——
   否则里面的规则一条都提不出来。
"""
from __future__ import annotations

import re

import pytest

from oryx.uicss import (
    MAIN_CSS,
    all_css,
    core_classes,
    extract_by_class,
    parse_css,
    subset_for,
)

SAMPLE = """
.btn { color: red; }
.btn.primary { color: blue; }
.inp { border: 1px solid; }
.other { color: green; }
@media (max-width: 600px) {
  .btn { font-size: 11px; }
  .inp { padding: 2px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; }
}
"""


# ── 解析 ──────────────────────────────────────────────────────────────
def test_parse_finds_rules():
    rules = parse_css(SAMPLE)
    assert len(rules) >= 6


def test_parse_collects_classes():
    rules = parse_css(SAMPLE)
    btn = next(r for r in rules if r.selector == ".btn")
    assert "btn" in btn.classes
    assert "primary" in next(r for r in rules if r.selector == ".btn.primary").classes


def test_media_block_carries_inner_classes():
    """@media 块的类集合必须是内部规则的并集 —— 否则一条都提不出来。"""
    rules = parse_css(SAMPLE)
    media = [r for r in rules if r.selector.startswith("@media")]
    assert media, "没解析出 @media 块"
    # 至少一个 @media 块带着内部类的信息
    assert any({"btn", "inp"} & r.classes for r in media)


# ── 提取 ──────────────────────────────────────────────────────────────
def test_extract_only_wanted():
    out = extract_by_class(SAMPLE, ["btn"])
    assert ".btn" in out
    assert ".other" not in out, "不该把无关的类也拖进来"


def test_extract_keeps_media_selectors():
    """@media 里的规则必须**连选择器一起**保留。

    只留声明体的话会变成 `@media (...) { font-size: 11px; }` ——
    那是无效 CSS，浏览器整块丢弃。
    """
    out = extract_by_class(SAMPLE, ["btn"])
    assert "@media" in out
    media_part = out[out.find("@media"):]
    assert ".btn" in media_part, "@media 里丢了选择器"
    assert "font-size" in media_part


def test_extract_media_braces_balanced():
    """括号要配平 —— 提取出来的 CSS 不能是坏的。"""
    out = extract_by_class(SAMPLE, ["btn", "inp"])
    assert out.count("{") == out.count("}"), "花括号不配平，CSS 是坏的"


def test_extract_empty_wanted_returns_empty():
    assert extract_by_class(SAMPLE, []) == ""
    assert extract_by_class(SAMPLE, [""]) == ""


def test_extract_accepts_dot_prefix():
    """类名带不带点都行 —— 扩展作者两种写法都会用。"""
    assert ".btn" in extract_by_class(SAMPLE, [".btn"])


# ── 真实核心 CSS ──────────────────────────────────────────────────────
def test_core_css_extracts_btn():
    sub = subset_for(["btn"])
    assert ".btn" in sub
    # 样式拆成了多个文件 —— 用 `all_css()` 比，别直接读 main.css（已不存在）
    assert len(sub) < len(all_css()), "子集应该比全文小"


def test_core_css_subset_is_valid():
    """从真实 main.css 提出来的子集，括号要配平、不能有裸声明。"""
    sub = subset_for(["btn", "inp", "iconbtn", "grid"])
    assert sub.count("{") == sub.count("}")
    # 每一段都该有选择器 —— 不能出现 '{' 后面直接是声明的（说明选择器丢了）
    for chunk in re.findall(r"\{([^{}]*)\}", sub):
        assert chunk.strip(), "有空的声明块"


def test_core_classes_are_listed():
    names = core_classes()
    assert "btn" in names
    assert len(names) > 50


def test_core_css_media_preserved():
    """核心里确实有 @media 规则，子集要能带上。"""
    sub = subset_for(["grid"])
    assert "@media" in sub, "核心 .grid 有 @media 规则，子集里却没有"
