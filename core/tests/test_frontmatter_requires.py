"""frontmatter 里的 `requires` —— 声明了就得生效。

解析层面的行为在 `test_frontmatter.py`（统一解析器）；
这里只守**端到端**：声明的依赖得真的进注册表、真的能把扩展阻塞住。

以前是什么样
------------
`parse_frontmatter` 是**宽松**的（只认单行 `key: value`）——
这是故意的：描述里带冒号的老技能不能解析失败。

但 `requires` 是**嵌套**结构，宽松解析器**认不出来** ——
用户写了依赖、注册表里 `requires` 是空的，**静默失效**。
依赖保护形同虚设，比不写更坏：它让人以为有防护。

现在：统一解析器（`oryx/frontmatter.py`）认得嵌套块了。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.registry import Registry, Trust


def _registry_with_skill(home: Path, text: str, manifest: dict | None = None):
    """装一个技能（可选带 oryx.json），返回注册表。"""
    d = home / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    if manifest is not None:
        (d / "oryx.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    reg = Registry(roots=[])
    reg.add_root("skill", home / "skills", Trust.USER)
    reg.discover()
    return reg


def test_skill_requires_in_frontmatter_is_registered(tmp_path):
    """技能在 frontmatter 里声明的依赖，得进注册表。"""
    home = tmp_path / "home"
    home.mkdir()
    reg = _registry_with_skill(home, """---
name: my-skill
description: 一个技能
requires:
  skills: other-skill
---

正文
""")
    rec = reg.get("skill", "my-skill")
    assert rec is not None, "技能没被发现"
    assert rec.requires == {"skills": ["other-skill"]}, rec.requires


def test_manifest_requires_wins_over_frontmatter(tmp_path):
    """清单（oryx.json）优先 —— 它是显式配置。"""
    home = tmp_path / "home"
    home.mkdir()
    reg = _registry_with_skill(
        home,
        """---
name: my-skill
description: 一个技能
requires:
  skills: from-frontmatter
---

正文
""",
        manifest={"id": "my-skill", "requires": {"skills": ["from-manifest"]}},
    )
    rec = reg.get("skill", "my-skill")
    assert rec.requires == {"skills": ["from-manifest"]}, rec.requires


def test_skill_without_manifest_still_works(tmp_path):
    """只有 SKILL.md 的老技能照常加载 —— 不许回归。"""
    home = tmp_path / "home"
    home.mkdir()
    reg = _registry_with_skill(home, """---
name: my-skill
description: 老式技能
triggers: 触发词
---

正文
""")
    rec = reg.get("skill", "my-skill")
    assert rec is not None
    assert rec.triggers == ("触发词",), rec.triggers
    assert rec.requires == {}, rec.requires


def test_frontmatter_requires_actually_blocks(tmp_path):
    """声明了依赖，依赖没满足 → 真的被阻塞。

    这是重点：以前声明被静默忽略，扩展照常加载 ——
    用户以为有依赖保护，其实没有。
    """
    home = tmp_path / "home"
    home.mkdir()
    reg = _registry_with_skill(home, """---
name: my-skill
description: 一个技能
requires:
  skills: definitely-not-installed
---

正文
""")
    reg.discover()
    assert reg.dep_report is not None
    assert reg.dep_report.is_blocked("skill", "my-skill"), (
        "依赖没满足却没被阻塞 —— 声明等于没用"
    )
    reason = reg.dep_report.reason("skill", "my-skill")
    assert "definitely-not-installed" in reason, reason
