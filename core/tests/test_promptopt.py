"""提示词优化：批评规则 + 补齐缺失项。"""

from __future__ import annotations

from oryx.promptopt import critique, optimize


def test_critique_detects_missing_parts():
    problems = critique("帮我弄一下")
    ids = {p.id for p in problems}
    assert "no-verb" in ids
    assert "no-scope" in ids
    assert "no-criteria" in ids


def test_critique_passes_complete_prompt():
    problems = critique("在 D:\\项目 下整理发票，输出成表格")
    assert {p.id for p in problems} == set()


def test_critique_flags_too_short():
    assert any(p.id == "too-short" for p in critique("改一下"))


def test_critique_empty():
    assert critique("")[0].id == "empty"


def test_optimize_keeps_original():
    result = optimize("找一下发票", skills={}, workspace="")
    assert result["ok"]
    assert result["content"]["original"] == "找一下发票"
    assert "找一下发票" in result["content"]["optimized"]


def test_optimize_adds_workspace():
    result = optimize("整理文件", skills={}, workspace="D:\\工作")
    assert "D:\\工作" in result["content"]["optimized"]
    assert any("工作目录" in n for n in result["content"]["notes"])


def test_optimize_binds_skill():
    class FakeSkill:
        description = "按内容找文件"

    result = optimize(
        "找一下发票", skills={"file-search": FakeSkill()}, bound=["file-search"], workspace=""
    )
    assert "file-search" in result["content"]["optimized"]
    assert any("file-search" in n for n in result["content"]["notes"])


def test_optimize_reports_missing_skill():
    result = optimize(
        "找一下发票", skills={}, bound=["nope"], workspace=""
    )
    assert any("没有这些技能" in n for n in result["content"]["notes"])


def test_optimize_without_skill_notes_it():
    result = optimize("找一下发票", skills={}, bound=[], workspace="")
    assert any("没有绑定技能" in n for n in result["content"]["notes"])


def test_optimize_empty_fails():
    result = optimize("   ", skills={}, workspace="")
    assert result["ok"] is False
