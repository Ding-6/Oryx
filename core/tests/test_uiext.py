"""前端扩展的测试。

要锁住的核心：

1. **只从用户目录加载** —— 仓库里自带的（clone 来的）不自动注入界面。
   clone 一个仓库不该等于让它往你的界面里插代码。
2. **扩展拿不到令牌** —— 后端服务扩展资源时不注入 __ORYX_TOKEN__，
   扩展只能通过 ctx.api 调后端。
3. **核心保留名不能被占用** —— 顶掉核心模块会让功能「莫名其妙没了」。
4. **坏的扩展要说清楚** —— 不静默跳过（静默会让用户以为装上了）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.uiext import (
    MOUNT_POINTS,
    RESERVED_IDS,
    UiExtension,
    discover_ui_extensions,
    load_ui_extensions,
    ui_extension_roots,
)


def _ext(root: Path, name: str, **manifest) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": name, "name": name, "mount": "rail"}
    data.update(manifest)
    (d / "oryx.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (d / "index.js").write_text(
        "export default { setup(ctx) {} };\n", encoding="utf-8"
    )
    return d


def _home(tmp_path: Path) -> Path:
    """造一个「应用数据目录」，扩展住在它的 ui-extensions/ 下。

    `load_ui_extensions` 收的是 app_dir（不是扩展目录本身）——
    这一点容易搞错，所以这里包一层。
    """
    h = tmp_path / "home"
    (h / "ui-extensions").mkdir(parents=True, exist_ok=True)
    return h


# ── 发现 ──────────────────────────────────────────────────────────────
def test_discovers_extension(tmp_path):
    _ext(tmp_path, "a")
    found = discover_ui_extensions(tmp_path)
    assert [e.id for e in found] == ["a"]
    # URL 带**内容指纹** —— 扩展改了 URL 就变，浏览器才会重新加载。
    assert found[0].url.startswith("/web/ui-extensions/a/index.js?v=")


def test_url_fingerprint_changes_with_content(tmp_path):
    """内容变了指纹就要变 —— 这是热重载的基础。"""
    _ext(tmp_path, "a")
    before = discover_ui_extensions(tmp_path)[0].url
    (tmp_path / "a" / "index.js").write_text(
        "export default { setup(ctx) { /* 改了 */ } };\n", encoding="utf-8"
    )
    after = discover_ui_extensions(tmp_path)[0].url
    assert before != after, "内容变了指纹必须变"


def test_url_fingerprint_changes_with_manifest(tmp_path):
    """清单改了指纹也要变 —— 改了 mount / baseCss 之类也要重新加载。"""
    _ext(tmp_path, "a")
    before = discover_ui_extensions(tmp_path)[0].url
    (tmp_path / "a" / "oryx.json").write_text(
        '{"id":"a","mount":"side"}', encoding="utf-8"
    )
    after = discover_ui_extensions(tmp_path)[0].url
    assert before != after


def test_missing_manifest_is_ignored(tmp_path):
    """没有 oryx.json 的目录不算扩展。"""
    (tmp_path / "junk").mkdir()
    assert discover_ui_extensions(tmp_path) == []


def test_missing_index_js_is_problem(tmp_path):
    """缺 index.js 要记问题，不能当成正常扩展。"""
    d = tmp_path / "a"
    d.mkdir()
    (d / "oryx.json").write_text('{"id":"a"}', encoding="utf-8")
    found = discover_ui_extensions(tmp_path)
    assert found and found[0].problems


def test_bad_mount_is_problem(tmp_path):
    """mount 不在允许范围要记问题。"""
    _ext(tmp_path, "a", mount="不存在的挂点")
    found = discover_ui_extensions(tmp_path)
    assert found and any("mount" in p for p in found[0].problems)


def test_reserved_id_is_problem(tmp_path):
    """核心保留名不能被占用。"""
    _ext(tmp_path, "core")
    found = discover_ui_extensions(tmp_path)
    assert found and any("保留" in p for p in found[0].problems)


def test_core_module_ids_are_reserved():
    """核心模块名都在保留表里。"""
    for name in ("core", "boot", "agui", "layout", "models", "theme"):
        assert name in RESERVED_IDS


def test_broken_manifest_does_not_crash(tmp_path):
    """坏掉的清单不能让整个发现崩掉。"""
    d = tmp_path / "a"
    d.mkdir()
    (d / "oryx.json").write_text("{ 这不是 JSON", encoding="utf-8")
    (d / "index.js").write_text("export default {};", encoding="utf-8")
    found = discover_ui_extensions(tmp_path)
    assert len(found) == 1      # 没崩，且仍然能列出来


def test_order_is_respected(tmp_path):
    _ext(tmp_path, "b", order=10)
    _ext(tmp_path, "a", order=1)
    found = discover_ui_extensions(tmp_path)
    assert [e.id for e in found] == ["a", "b"]


def test_base_css_is_read(tmp_path):
    """扩展声明要复用哪些核心类 —— 后端据此挑样式。"""
    _ext(tmp_path, "a", baseCss=["btn", ".inp"])
    found = discover_ui_extensions(tmp_path)
    assert found[0].base_css == ("btn", "inp"), "类名要统一去掉点"


def test_base_css_defaults_empty(tmp_path):
    _ext(tmp_path, "a")
    assert discover_ui_extensions(tmp_path)[0].base_css == ()


# ── 加载 ──────────────────────────────────────────────────────────────
def test_disabled_extension_not_loaded(tmp_path):
    home = _home(tmp_path)
    _ext(home / "ui-extensions", "a", enabled=False)
    loaded, skipped = load_ui_extensions(home)
    assert loaded == []
    assert any("enabled" in s["reason"] for s in skipped)


def test_problematic_extension_not_loaded(tmp_path):
    home = _home(tmp_path)
    d = _ext(home / "ui-extensions", "a")
    (d / "oryx.json").write_text('{"id":"a","mount":"乱写"}', encoding="utf-8")
    loaded, skipped = load_ui_extensions(home)
    assert loaded == []
    assert skipped


def test_allowlist_filters(tmp_path):
    home = _home(tmp_path)
    _ext(home / "ui-extensions", "a")
    _ext(home / "ui-extensions", "b")
    loaded, skipped = load_ui_extensions(home, allow_ids=["a"])
    assert [e.id for e in loaded] == ["a"]
    assert len(skipped) == 1


def test_allowlist_unknown_means_none_loaded(tmp_path):
    home = _home(tmp_path)
    _ext(home / "ui-extensions", "a")
    loaded, _ = load_ui_extensions(home, allow_ids=["别的"])
    assert loaded == []


# ── 只从用户目录加载 ──────────────────────────────────────────────────
def test_only_user_dir_is_searched(tmp_path):
    """仓库里的扩展目录**不在**搜索路径里。"""
    roots = ui_extension_roots(tmp_path)
    assert len(roots) == 1
    assert roots[0].name == "ui-extensions"
    assert roots[0].parent == tmp_path


def test_repo_extensions_dir_not_in_roots(tmp_path):
    """不能把 core/web/ui-extensions 加进搜索路径 —— 那是仓库自带的。"""
    roots = ui_extension_roots(tmp_path)
    for r in roots:
        assert "web" not in r.parts


# ── 契约 ──────────────────────────────────────────────────────────────
def test_mount_points_are_declared():
    assert set(MOUNT_POINTS) == {"rail", "railfoot", "topbar", "side"}


def test_to_dict_has_no_token():
    """清单里不能有令牌。"""
    ext = UiExtension(id="a", name="甲")
    d = ext.to_dict()
    assert "token" not in json.dumps(d).lower()


def test_to_dict_fields():
    ext = UiExtension(id="a", name="甲", mount="side", url="/x.js", order=3)
    d = ext.to_dict()
    assert d["id"] == "a"
    assert d["name"] == "甲"
    assert d["mount"] == "side"
    assert d["url"] == "/x.js"
    assert d["order"] == 3
