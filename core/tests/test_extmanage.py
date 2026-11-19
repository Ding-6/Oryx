"""扩展管理（启停 / 安装 / 删除）的测试。

要锁住的：

1. **程序不改用户的扩展文件** —— 启停状态存在 Oryx 自己的配置里。
2. **安装后默认禁用** —— 「装」和「让它跑」是两个独立决定。
3. **删除是移进回收区，不是真删** —— 误删了能找回来。
4. **停用优先于 profile** —— 用户主动关掉的，profile 也救不回来。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.extmanage import (
    ExtensionError,
    disabled_ids,
    install_extension,
    list_trash,
    move_to_trash,
    purge_from_trash,
    restore_from_trash,
    set_disabled,
)
from oryx.registry import Registry, Trust


def _make_ext(base: Path, name: str = "my-ext") -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "oryx.json").write_text(json.dumps({
        "id": name, "name": "我的扩展", "kind": "tool", "tools": [],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text("def run(**kw): pass\n", encoding="utf-8")
    return d


# ── 启停 ──────────────────────────────────────────────────────────────
def test_disable_then_enable():
    cfg: dict = {}
    assert set_disabled(cfg, "a", True) is True
    assert disabled_ids(cfg) == {"a"}
    assert set_disabled(cfg, "a", False) is True
    assert disabled_ids(cfg) == set()


def test_disable_is_idempotent():
    """重复停用返回 False —— 界面上就不用提示「没变化」。"""
    cfg: dict = {}
    assert set_disabled(cfg, "a", True) is True
    assert set_disabled(cfg, "a", True) is False


def test_enable_when_not_disabled_returns_false():
    cfg: dict = {}
    assert set_disabled(cfg, "a", False) is False


def test_disabled_ids_ignores_bad_data():
    assert disabled_ids({}) == set()
    assert disabled_ids({"disabled_extensions": "不是数组"}) == set()
    assert disabled_ids({"disabled_extensions": ["a", "", "b"]}) == {"a", "b"}


def test_disabled_extension_not_in_registry(tmp_path):
    """停用的扩展不进注册表 —— 不被发现、不被加载。"""
    _make_ext(tmp_path, "keep")
    _make_ext(tmp_path, "gone")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.set_disabled({"gone"})
    reg.discover()
    assert reg.get("tool", "keep") is not None
    assert reg.get("tool", "gone") is None


def test_disabled_overrides_profile(tmp_path):
    """停用优先于 profile —— 用户主动关的，profile 也救不回来。"""
    from oryx.profiles import ProfileSet

    _make_ext(tmp_path, "x")
    ps = ProfileSet.from_dict({"profiles": {"p": {"extensions": ["x"]}}})
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.set_profiles(ps, "p")
    reg.set_disabled({"x"})
    reg.discover()
    assert reg.get("tool", "x") is None, "停用应该压过 profile"


# ── 安装 ──────────────────────────────────────────────────────────────
def test_install_copies_files(tmp_path):
    src = _make_ext(tmp_path / "src")
    target = tmp_path / "extensions"
    target.mkdir()
    res = install_extension(src, target)
    assert res.id == "my-ext"
    assert (target / "my-ext" / "oryx.json").exists()
    assert (target / "my-ext" / "tools.py").exists()
    assert res.enabled is False, "装完必须默认不启用"


def test_install_skips_junk_dirs(tmp_path):
    src = _make_ext(tmp_path / "src")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("junk", encoding="utf-8")
    (src / ".git").mkdir()
    target = tmp_path / "extensions"
    target.mkdir()
    res = install_extension(src, target)
    assert not (target / "my-ext" / "__pycache__").exists()
    assert not (target / "my-ext" / ".git").exists()


def test_install_rejects_duplicate(tmp_path):
    src = _make_ext(tmp_path / "src")
    target = tmp_path / "extensions"
    target.mkdir()
    install_extension(src, target)
    with pytest.raises(ExtensionError):
        install_extension(src, target)


def test_install_rejects_non_extension(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "readme.txt").write_text("不是扩展", encoding="utf-8")
    target = tmp_path / "extensions"
    target.mkdir()
    with pytest.raises(ExtensionError):
        install_extension(plain, target)


def test_install_rejects_missing_dir(tmp_path):
    with pytest.raises(ExtensionError):
        install_extension(tmp_path / "不存在", tmp_path / "extensions")


def test_install_does_not_execute_code(tmp_path):
    """安装只是复制文件 —— **不能执行扩展里的代码**。

    扩展是在你的机器上跑的东西，安装动作本身不该触发它。
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "oryx.json").write_text(json.dumps({"id": "evil", "tools": []}),
                                   encoding="utf-8")
    marker = tmp_path / "executed.txt"
    (src / "tools.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    target = tmp_path / "extensions"
    target.mkdir()
    install_extension(src, target)
    assert not marker.exists(), "安装时不该执行扩展的代码"


# ── 删除 / 回收区 ─────────────────────────────────────────────────────
def test_remove_moves_to_trash(tmp_path):
    _make_ext(tmp_path, "gone")
    target = tmp_path / "extensions"
    target.mkdir()
    _make_ext(target, "gone")
    path = move_to_trash(target, "gone")
    assert not (target / "gone").exists(), "原目录应该被移走"
    assert Path(path).exists(), "应该在回收区里"
    assert [i["id"] for i in list_trash(target)] == ["gone"]


def test_trash_name_conflict_keeps_both(tmp_path):
    target = tmp_path / "extensions"
    target.mkdir()
    _make_ext(target, "a")
    move_to_trash(target, "a")
    _make_ext(target, "a")
    move_to_trash(target, "a")     # 同名再来一次
    items = [i["id"] for i in list_trash(target)]
    assert len(items) == 2, f"回收区里应有两个，实际 {items}"


def test_restore_from_trash(tmp_path):
    target = tmp_path / "extensions"
    target.mkdir()
    _make_ext(target, "a")
    move_to_trash(target, "a")
    restore_from_trash(target, "a")
    assert (target / "a").exists()
    assert not list_trash(target)


def test_purge_from_trash(tmp_path):
    target = tmp_path / "extensions"
    target.mkdir()
    _make_ext(target, "a")
    move_to_trash(target, "a")
    purge_from_trash(target, "a")
    assert not list_trash(target)


def test_remove_missing_raises(tmp_path):
    target = tmp_path / "extensions"
    target.mkdir()
    with pytest.raises(ExtensionError):
        move_to_trash(target, "没有这个")


# ── 界面扩展的删除（独立回收区）──────────────────────────────────────
def test_ui_extension_has_own_trash(tmp_path):
    """后端扩展与界面扩展各有各的回收区。

    两者内容形态不同（Python 代码 vs JS 模块），混在一起会看不清。
    """
    from oryx.extmanage import list_trash, move_to_trash

    be_root = tmp_path / "extensions"
    ui_root = tmp_path / "ui-extensions"
    be_root.mkdir(parents=True)
    ui_root.mkdir(parents=True)

    _make_ext(be_root, "backend-one")
    _make_ext(ui_root, "ui-one")
    move_to_trash(be_root, "backend-one")
    move_to_trash(ui_root, "ui-one")

    assert [i["id"] for i in list_trash(be_root)] == ["backend-one"]
    assert [i["id"] for i in list_trash(ui_root)] == ["ui-one"]


def test_ui_extension_disable_blocks_loading(tmp_path):
    """界面扩展的启停走**同一个**停用名单 —— 用户在界面上关掉的，
    不管是后端还是界面扩展，都不加载。"""
    from oryx.uiext import load_ui_extensions

    home = tmp_path
    ui = home / "ui-extensions" / "one"
    ui.mkdir(parents=True)
    (ui / "oryx.json").write_text(json.dumps({
        "id": "one", "name": "甲", "mount": "rail", "enabled": True,
    }, ensure_ascii=False), encoding="utf-8")
    (ui / "index.js").write_text("export default { setup(ctx) {} };\n", encoding="utf-8")

    loaded, skipped = load_ui_extensions(home)
    assert [e.id for e in loaded] == ["one"]

    loaded2, skipped2 = load_ui_extensions(home, disabled_ids={"one"})
    assert loaded2 == [], "停用的界面扩展不该加载"
    assert any(s["reason"] == "已停用" for s in skipped2)


# ── 界面扩展的删除（独立回收区）──────────────────────────────────────
def test_ui_extension_has_own_trash(tmp_path):
    """后端扩展与界面扩展各有各的回收区。

    两者内容形态不同（Python 代码 vs JS 模块），混在一起会看不清。
    """
    from oryx.extmanage import list_trash, move_to_trash

    be_root = tmp_path / "extensions"
    ui_root = tmp_path / "ui-extensions"
    be_root.mkdir(parents=True)
    ui_root.mkdir(parents=True)

    _make_ext(be_root, "backend-one")
    _make_ext(ui_root, "ui-one")
    move_to_trash(be_root, "backend-one")
    move_to_trash(ui_root, "ui-one")

    assert [i["id"] for i in list_trash(be_root)] == ["backend-one"]
    assert [i["id"] for i in list_trash(ui_root)] == ["ui-one"]


def test_ui_extension_disable_blocks_loading(tmp_path):
    """界面扩展的启停走**同一个**停用名单 —— 用户在界面上关掉的，
    不管是后端还是界面扩展，都不加载。"""
    from oryx.uiext import load_ui_extensions

    home = tmp_path
    ui = home / "ui-extensions" / "one"
    ui.mkdir(parents=True)
    (ui / "oryx.json").write_text(json.dumps({
        "id": "one", "name": "甲", "mount": "rail", "enabled": True,
    }, ensure_ascii=False), encoding="utf-8")
    (ui / "index.js").write_text("export default { setup(ctx) {} };\n", encoding="utf-8")

    loaded, skipped = load_ui_extensions(home)
    assert [e.id for e in loaded] == ["one"]

    loaded2, skipped2 = load_ui_extensions(home, disabled_ids={"one"})
    assert loaded2 == [], "停用的界面扩展不该加载"
    assert any(s["reason"] == "已停用" for s in skipped2)
