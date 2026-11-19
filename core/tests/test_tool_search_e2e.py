"""tool_search 端到端：自然语言说法能不能召回正确的工具。

需要本地向量模型；缺失时整组跳过（语义层降级是设计内的行为）。
"""

from __future__ import annotations

import io
import contextlib
import tempfile
from pathlib import Path

import pytest

from oryx import embeddings as emb
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store

pytestmark = pytest.mark.skipif(
    not emb.available(), reason="本地向量模型缺失，语义检索已降级为关键词"
)

# 每条：(用户会怎么说, 应该召回哪个工具)
CASES = [
    ("把当前屏幕截图存下来", "browser_screenshot"),
    ("看看电脑什么配置", "system_info"),
    ("搜一下代码里的 TODO", "index_search"),
    ("做个汇报用的幻灯片", "pptx_write"),
    ("把这些发票上的金额汇总成表", "ocr_image"),
    ("别让我每次都重复告诉你这个约定", "memory_save"),
    ("桌面上有哪些文件", "fs_list"),
    ("看下这个配置文件写了啥", "fs_read"),
    ("把这个文件夹里的东西都删掉", "fs_delete"),
    ("生成一份周报文档", "docx_write"),
    ("看看我之前说过什么偏好", "memory_search"),
]


@pytest.fixture(scope="module")
def runtime():
    tmp = Path(tempfile.mkdtemp())
    data = dict(DEFAULT_CONFIG)
    data.update(allowed_roots=[str(tmp)], forbidden_paths=[], workspace=str(tmp))
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rt = AgentRuntime(Config(data), Store(tmp / "o.db"), EventBus())
    rt.tool_index.ensure(rt.registry)
    return rt


def test_index_is_ready(runtime):
    assert runtime.tool_index.ready, "模型可用时索引必须建成"
    assert len(runtime.tool_index._names) == len(runtime.registry)
    assert runtime.tool_index.error == ""


@pytest.mark.parametrize("query,expected", CASES)
def test_semantic_recall_finds_right_tool(runtime, query, expected):
    """这是P1-3 的核心价值：模型按意思找工具，而不是靠记工具名。"""
    hits = runtime.registry["tool_search"].fn(query=query, limit=3)
    names = [h["name"] for h in hits["content"]]
    assert expected in names, f"{query!r} 应召回 {expected}，实际 {names}"


def test_semantic_beats_keyword(runtime):
    """同样这些说法，纯关键词检索几乎全miss —— 这正是语义层的价值。"""
    keyword_miss = 0
    for query, expected in CASES:
        needle = query.strip().lower()
        scores = []
        for name, spec in runtime.registry.items():
            hay = f"{name} {spec.description} {spec.semantic_text()}".lower()
            score = 0
            if needle in name.lower():
                score += 10
            for token in needle.split():
                if token in name.lower():
                    score += 6
                elif token in hay:
                    score += 2
            if score:
                scores.append((score, name))
        scores.sort(key=lambda x: (-x[0], x[1]))
        if expected not in [n for _, n in scores[:3]]:
            keyword_miss += 1
    # 关键词路至少要漏掉一半，否则说明这批用例区分度不够
    assert keyword_miss >= len(CASES) // 2


def test_results_keep_full_schema(runtime):
    """召回结果要带完整参数说明，否则模型知道了工具名却用不了。"""
    hits = runtime.registry["tool_search"].fn(query="打开网页", limit=1)
    top = hits["content"][0]
    assert set(top) >= {"name", "description", "risk", "risk_label", "parameters"}
    assert top["parameters"], "参数 schema 不能为空"


def test_search_publishes_mode(runtime):
    """事件里要能看出走的是语义还是关键词，便于排查。"""
    seen: list[dict] = []
    runtime.bus.add_sync_sink(seen.append)
    runtime.registry["tool_search"].fn(query="把当前屏幕截图存下来", limit=3)
    ev = [e for e in seen if e["type"] == "tools.searched"]
    assert ev and ev[-1]["mode"] == "semantic"


def test_empty_query_lists_everything(runtime):
    """不带 query 时列出工具（老行为：每条都命中，截到 limit 上限）。"""
    hits = runtime.registry["tool_search"].fn(query="", limit=200)
    assert len(hits["content"]) == 30, "空 query 时按字典序取前 30"
    assert len(runtime.registry) > 30, "工具总数确实超过 30"


def test_limit_is_capped(runtime):
    hits = runtime.registry["tool_search"].fn(query="文件", limit=999)
    assert len(hits["content"]) <= 30