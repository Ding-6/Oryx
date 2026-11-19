"""系统提示词外置的回归测试。

锁住的是**行为等价**，不是「文件存在」：

1. 拼装结果与拆分前**逐字节相同** —— 提示词差一个空格都可能让模型行为变样；
2. 占位符缺失要报错，不能把 `{agents}` 原样送到模型面前；
3. md 文件缺失要报错，不能降级成空字符串；
4. loader 有缓存，重复调用不反复读盘；
5. 段落顺序固定 —— 拼装顺序变了，提示词就变了。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oryx.prompts import loader
from oryx.prompts.loader import PromptError

CORE = Path(__file__).resolve().parents[1]
BASELINE = CORE / "tests" / "fixtures" / "system_prompt_baseline.txt"


# ---------------------------------------------------------------- 逐字节等价
def test_prompt_matches_baseline_byte_for_byte():
    """拼装结果必须与拆分前的常量逐字节相同。"""
    assert BASELINE.exists(), f"缺基准快照：{BASELINE}"
    baseline = BASELINE.read_text("utf-8")
    current = loader.raw_system_prompt()
    assert current == baseline, (
        f"提示词与基准不一致：基准 {len(baseline)} 字符，当前 {len(current)} 字符"
    )


def test_baseline_has_expected_shape():
    """基准快照本身的形状要合理 —— 防止有人拿空文件当基准糊弄过去。"""
    baseline = BASELINE.read_text("utf-8")
    assert len(baseline) > 5000, "基准快照太短，不像完整的主控提示"
    assert baseline.startswith("你是 Oryx"), "基准开头不是主控提示的第一句"
    assert "{agents}" in baseline, "基准里没有 {agents} 占位符"
    assert "{skills}" in baseline, "基准里没有 {skills} 占位符"


def test_module_level_constant_still_available():
    """loop.SYSTEM_PROMPT 这个名字要还在 —— 有别的代码和测试引用它。"""
    from oryx.loop import SYSTEM_PROMPT

    assert SYSTEM_PROMPT == BASELINE.read_text("utf-8")


def test_variable_substitution_matches_legacy_replace():
    """新版的变量替换与老版的 .replace() 结果一致。

    老版是 `SYSTEM_PROMPT.replace("{skills}", ...).replace("{agents}", ...)`。
    顺序不同理论上会有差异（如果一个变量的值里含另一个占位符），
    所以这里显式对比，防止将来改动引入细微差别。
    """
    baseline = BASELINE.read_text("utf-8")
    agents = "AGENTS_BLOCK"
    skills = "SKILLS_CATALOG"
    legacy = baseline.replace("{skills}", skills).replace("{agents}", agents)
    current = loader.load_system_prompt(agents=agents, skills=skills)
    assert current == legacy


# ---------------------------------------------------------------- 占位符
def test_missing_variable_raises():
    """少给变量必须报错 —— 不能把 `{agents}` 原样送到模型面前。"""
    with pytest.raises(PromptError) as exc:
        loader.load_system_prompt(agents="X")   # 故意不给 skills
    assert "skills" in str(exc.value)


def test_no_placeholder_survives_in_rendered_prompt():
    """渲染结果里不能残留任何占位符。"""
    out = loader.load_system_prompt(agents="A", skills="S")
    assert "{agents}" not in out
    assert "{skills}" not in out
    assert not loader.find_placeholders(out)


def test_find_placeholders_detects_both():
    found = loader.find_placeholders("前 {agents} 中 {skills} 后")
    assert found == {"agents", "skills"}


def test_find_placeholders_ignores_json_like_braces():
    """JSON 片段那种 { "a": 1 } 不该被当成占位符。"""
    found = loader.find_placeholders('{"key": "value"} 和 {name}')
    assert found == {"name"}


# ---------------------------------------------------------------- 文件缺失
def test_missing_section_file_raises(tmp_path, monkeypatch):
    """段落文件缺失要报错，不能降级成空字符串。"""
    fake = tmp_path / "system"
    fake.mkdir()
    for name in loader.SECTION_ORDER:
        (fake / f"{name}.md").write_text("占位", encoding="utf-8")
    (fake / f"{loader.SECTION_ORDER[3]}.md").unlink()

    monkeypatch.setattr(loader, "SYSTEM_DIR", fake)
    loader._read_section.cache_clear()
    try:
        with pytest.raises(PromptError) as exc:
            loader.load_system_prompt(agents="A", skills="S")
        assert "缺失" in str(exc.value)
    finally:
        loader._read_section.cache_clear()


def test_section_order_is_stable():
    """段落顺序固定 —— 顺序变了提示词就变了。"""
    names = loader.section_names()
    assert names[0] == "00-opening", "开篇引言必须在最前"
    assert names[-1] == "13-skills", "技能段必须在最后"
    assert list(names) == sorted(names), "段落顺序应与文件编号一致"
    assert len(set(names)) == len(names), "段落名不能重复"


def test_all_section_files_exist():
    """每个登记的段落都要有对应文件。"""
    for name in loader.SECTION_ORDER:
        path = loader.section_path(name)
        assert path.exists(), f"缺段落文件：{path}"


# ---------------------------------------------------------------- 缓存
def test_read_is_cached():
    """重复读同一个段落不应反复读盘。"""
    loader._read_section.cache_clear()
    loader.raw_system_prompt()
    after_first = loader.cache_info().misses
    loader.raw_system_prompt()
    after_second = loader.cache_info().misses
    assert after_first == after_second, "第二次拼装又去读盘了"


def test_cache_info_reports_hits():
    loader._read_section.cache_clear()
    loader.raw_system_prompt()
    loader.raw_system_prompt()
    info = loader.cache_info()
    assert info.hits > 0, "重复调用没有命中缓存"


# ---------------------------------------------------------------- 与拆分脚本的关系
def test_split_script_is_idempotent():
    """拆分脚本的 --check 必须通过：md 文件拼回去等于基准快照。

    这条测试的作用是：谁改了 md 文件但没同步基准，这里立刻红。
    """
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "scripts/extract_system_prompt.py", "--check"],
        cwd=str(CORE), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"拆分脚本校验失败：{r.stdout}\n{r.stderr}"
    assert "逐字节相同" in r.stdout


def test_no_system_prompt_literal_left_in_loop():
    """loop.py 里不该再有系统提示词的大段字面量。

    不查这个的话，以后有人「顺手」在 loop.py 里加一段提示，
    就会出现「两处提示词，改了一处」的情况 —— 很难查。
    """
    src = (CORE / "oryx" / "loop.py").read_text("utf-8")
    # 提示词正文搬走后，loop.py 里不该再有这些特征句
    for marker in ("# 一、任务闭环", "# 四、安全与确认", "# 七、失败处理"):
        assert marker not in src, f"loop.py 里还有提示词正文：{marker}"


# ---------------------------------------------------------------- 诊断开关
def test_legacy_switch_returns_identical_prompt(monkeypatch):
    """ORYX_PROMPT_LEGACY=1 必须返回**完全相同**的提示词。

    这个开关是给 A/B 对照用的（判断偶发问题是否与外置有关）。
    如果它返回的内容与正常路径不一致，对照实验就失去意义 ——
    差异会被误当成「外置的影响」。
    """
    normal = loader.raw_system_prompt()
    monkeypatch.setenv("ORYX_PROMPT_LEGACY", "1")
    legacy = loader.raw_system_prompt()
    assert legacy == normal, "对照开关返回的提示词与正常路径不一致"


def test_legacy_switch_is_off_by_default(monkeypatch):
    """默认必须走 md 文件路径 —— 不能被环境变量意外影响。"""
    monkeypatch.delenv("ORYX_PROMPT_LEGACY", raising=False)
    assert loader.raw_system_prompt() == BASELINE.read_text("utf-8")
