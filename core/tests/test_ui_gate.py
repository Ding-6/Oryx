"""前端质量门禁的**集成测试** —— 把它变成常规防线。

`tests/test_ui_audit.py` 测的是「检查器本身对不对」；
这里测的是「自家界面过不过得了自己的门禁」。

为什么要单独一个文件
------------------
门禁的价值在于**持续生效**。放进 pytest 之后，
每次 `pytest tests` 都会跑 —— 界面上新写了 `transition: all`，
CI 会直接红，而不是等到有人想起来手动跑一次。

分级策略
--------
· **block** —— 必须为 0。红了就是回归。
· **warn** —— 允许少量（当前基线），但不能无限增长。
  用「基线 + 容差」的方式卡住增长趋势。
"""
from __future__ import annotations

from pathlib import Path

from oryx.ui_audit import WEB_DIR, audit

CORE = Path(__file__).resolve().parents[1]


def test_no_block_level_ui_issues():
    """界面不允许有 block 级问题 —— 门禁的红线。"""
    result = audit(WEB_DIR)
    blocks = result.by_severity("block")
    assert not blocks, "界面出现阻断级问题：\n" + "\n".join(
        f"  {f.file}:{f.line} [{f.rule_id}] {f.description}\n"
        f"      {f.snippet}\n"
        f"      → {f.hint}"
        for f in blocks
    )


def test_warn_level_not_growing():
    """warn 级问题不许增长。

    用「基线 + 容差」而不是「必须为 0」：
    偶尔出现一个 warn（比如临时加了个动画忘了兜底）不该让 CI 全红，
    但**趋势增长**要能被发现。
    """
    result = audit(WEB_DIR)
    warns = len(result.by_severity("warn"))
    baseline = 5   # 当前基线：1 处开关滑块颜色 + 少量合理项
    assert warns <= baseline, (
        f"warn 级问题 {warns} 处，超过基线 {baseline} —— "
        f"新增的："
        + "、".join(
            f"{f.file}:{f.line}[{f.rule_id}]"
            for f in result.by_severity("warn")[:5]
        )
    )


def test_ui_audit_runs_as_module():
    """门禁要能当命令行用 —— 人工跑体检时用得上。"""
    from oryx.ui_audit import main

    code = main(["--web-dir", str(WEB_DIR), "--min-severity", "block"])
    assert code == 0, "有 block 级问题"
