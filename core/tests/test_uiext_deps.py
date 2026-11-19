"""界面扩展依赖（`requires`）的测试。

界面扩展此前**完全不支持依赖** —— 它能声明 `caps`、`baseCss`，
唯独不能声明「我需要什么」。一个面板缺了它依赖的东西，
照样挂上去（挂个空壳），用户只能看到「怎么是空的」。

要锁住的：

1. **界面扩展之间**的依赖（`ui`）
2. **界面扩展 → 后端扩展**的依赖（`backend` / 具体类型）
3. 传递闭包（A 依赖 B、B 缺依赖 → A 也缺）
4. 环检测
5. 版本约束
6. 没声明依赖的扩展**完全不受影响**
7. 依赖不满足的扩展**不挂载**，但**看得见**（带原因）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.uiext import (
    UiExtension,
    _parse_ui_requires,
    discover_ui_extensions,
    load_ui_extensions,
    resolve_ui_dependencies,
)


class BackendRec:
    """后端扩展记录的最小替身。"""

    def __init__(self, kind: str, ext_id: str, version: str = "1.0.0"):
        self.kind = kind
        self.id = ext_id
        self.version = version


def _ui(ext_id: str, *, requires=None, version: str = "1.0.0") -> UiExtension:
    return UiExtension(
        id=ext_id,
        name=ext_id,
        version=version,
        requires=requires or {},
    )


def _ext_dir(base: Path, ext_id: str, *, requires=None) -> Path:
    d = base / ext_id
    d.mkdir(parents=True)
    manifest = {"id": ext_id, "name": ext_id, "mount": "rail", "enabled": True}
    if requires:
        manifest["requires"] = requires
    (d / "oryx.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "index.js").write_text("export default { setup(ctx) {} };\n", encoding="utf-8")
    return d


# ── 解析 ──────────────────────────────────────────────────────────────
def test_parse_ui_requires_normalizes_aliases():
    """别名要归一 —— `panels` 与 `ui` 是同一个意思。"""
    out = _parse_ui_requires({"ui": ["a"], "backend": ["b"], "panels": ["c"]})
    assert out["ui"] == ["a", "c"], "panels 归到 ui 里，且**合并**而不是覆盖"
    assert out["backend"] == ["b"], "backend 保留"
    assert "panels" not in out, "别名应该被归一掉"


def test_parse_ui_requires_accepts_string():
    assert _parse_ui_requires("solo") == {"*": ["solo"]}


def test_parse_ui_requires_ignores_bad_shape():
    assert _parse_ui_requires(None) == {}
    assert _parse_ui_requires(123) == {}
    assert _parse_ui_requires({"ui": 123}) == {}


def test_manifest_requires_parsed(tmp_path):
    _ext_dir(tmp_path, "a", requires={"ui": ["b"], "backend": ["skill-x"]})
    found = discover_ui_extensions(tmp_path)
    assert len(found) == 1
    assert found[0].requires == {"ui": ["b"], "backend": ["skill-x"]}


# ── 界面扩展之间的依赖 ────────────────────────────────────────────────
def test_ui_dependency_missing_blocks():
    ok, blocked = resolve_ui_dependencies([_ui("a", requires={"ui": ["missing"]})])
    assert ok == [], "缺依赖的界面扩展不该挂载"
    assert blocked and "missing" in blocked[0]["reason"]


def test_ui_dependency_satisfied_passes():
    ok, blocked = resolve_ui_dependencies([
        _ui("a", requires={"ui": ["b"]}),
        _ui("b"),
    ])
    assert [e.id for e in ok] == ["a", "b"]
    assert blocked == []


def test_ui_dependency_disabled_blocks():
    """依赖被停用 → 依赖方被拦下，且**说清楚是停用不是没装**。"""
    ok, blocked = resolve_ui_dependencies(
        [_ui("a", requires={"ui": ["b"]}), _ui("b")],
        disabled_ids={"b"},
    )
    assert ok == [], "停用的扩展自己也不该挂载"
    assert blocked
    assert any(b["id"] == "a" and "停用" in b["reason"] for b in blocked)


def test_ui_dependency_version_mismatch_blocks():
    """版本不满足 → **依赖方**被拦下。

    被依赖的 `b` 自己没问题（它的版本就是 1.0.0），
    该挂的还是要挂 —— 拦的是「要求了更高版本却没满足」的那个。
    """
    ok, blocked = resolve_ui_dependencies([
        _ui("a", requires={"ui": ["b@>=2.0.0"]}),
        _ui("b", version="1.0.0"),
    ])
    assert [e.id for e in ok] == ["b"], "b 自己没问题，该挂还是要挂"
    assert blocked and blocked[0]["id"] == "a"
    assert "版本" in blocked[0]["reason"]


def test_ui_dependency_version_match_passes():
    ok, blocked = resolve_ui_dependencies([
        _ui("a", requires={"ui": ["b@>=1.0.0"]}),
        _ui("b", version="1.5.0"),
    ])
    assert len(ok) == 2
    assert blocked == []


def test_ui_dependency_transitive_closure():
    """A 依赖 B、B 依赖 C、C 缺 → B 与 A 都不该挂载。"""
    ok, blocked = resolve_ui_dependencies([
        _ui("a", requires={"ui": ["b"]}),
        _ui("b", requires={"ui": ["c"]}),
        _ui("c", requires={"ui": ["missing"]}),
    ])
    assert ok == []
    ids = {b["id"] for b in blocked}
    assert ids == {"a", "b", "c"}


def test_ui_dependency_cycle_blocks_both():
    ok, blocked = resolve_ui_dependencies([
        _ui("a", requires={"ui": ["b"]}),
        _ui("b", requires={"ui": ["a"]}),
    ])
    assert ok == []
    assert len(blocked) == 2


# ── 界面扩展 → 后端扩展 ───────────────────────────────────────────────
def test_backend_dependency_missing_blocks():
    ok, blocked = resolve_ui_dependencies(
        [_ui("a", requires={"backend": ["missing-skill"]})],
        [BackendRec("skill", "other")],
    )
    assert ok == []
    assert "missing-skill" in blocked[0]["reason"]


def test_backend_dependency_satisfied_passes():
    ok, blocked = resolve_ui_dependencies(
        [_ui("a", requires={"backend": ["web-research"]})],
        [BackendRec("skill", "web-research")],
    )
    assert len(ok) == 1
    assert blocked == []


def test_backend_dependency_specific_kind_must_match():
    """「依赖 skills/x」不该被一个同名的 tool 扩展顶替。"""
    ok, blocked = resolve_ui_dependencies(
        [_ui("a", requires={"skills": ["thing"]})],
        [BackendRec("tool", "thing")],
    )
    assert ok == [], "类型不对不该算满足"
    assert blocked and "类型" in blocked[0]["reason"]


def test_backend_dependency_kind_match_passes():
    ok, _blocked = resolve_ui_dependencies(
        [_ui("a", requires={"skills": ["thing"]})],
        [BackendRec("skill", "thing")],
    )
    assert len(ok) == 1


def test_backend_dependency_accepts_dict_records():
    """server 侧从内核拿到的是 dict 索引 —— 也得能判。"""
    ok, _ = resolve_ui_dependencies(
        [_ui("a", requires={"backend": ["web-research"]})],
        [{"kind": "skill", "id": "web-research", "version": "1.1.0"}],
    )
    assert len(ok) == 1


def test_backend_dependency_version_constraint():
    ok, blocked = resolve_ui_dependencies(
        [_ui("a", requires={"backend": ["web-research@>=2.0.0"]})],
        [BackendRec("skill", "web-research", "1.1.0")],
    )
    assert ok == []
    assert "版本" in blocked[0]["reason"]


def test_any_kind_dependency_finds_either_side():
    ok, _ = resolve_ui_dependencies(
        [_ui("a", requires={"extensions": ["b"]}), _ui("b")],
        [],
    )
    assert len(ok) == 2


# ── 不受影响 ──────────────────────────────────────────────────────────
def test_no_requires_is_never_blocked():
    ok, blocked = resolve_ui_dependencies([_ui("plain")])
    assert len(ok) == 1
    assert blocked == []


def test_blocked_extension_still_visible_with_reason():
    """被拦下的扩展**要看得见** —— 用户得知道「我装了个东西但它没跑」。"""
    ok, blocked = resolve_ui_dependencies([
        _ui("a", requires={"ui": ["missing"]}),
        _ui("fine"),
    ])
    assert [e.id for e in ok] == ["fine"]
    assert blocked[0]["id"] == "a"
    assert blocked[0]["reason"]


# ── 接进 load_ui_extensions ───────────────────────────────────────────
def test_load_ui_extensions_blocks_on_missing_dependency(tmp_path):
    home = tmp_path
    _ext_dir(home / "ui-extensions", "a", requires={"ui": ["missing"]})
    _ext_dir(home / "ui-extensions", "fine")

    loaded, skipped = load_ui_extensions(home)
    assert [e.id for e in loaded] == ["fine"]
    assert any(s["id"] == "a" and "missing" in s["reason"] for s in skipped)


def test_load_ui_extensions_backend_dependency(tmp_path):
    home = tmp_path
    _ext_dir(home / "ui-extensions", "a", requires={"backend": ["web-research"]})

    loaded, skipped = load_ui_extensions(home)
    assert loaded == [], "后端扩展不存在时不该挂载"
    assert skipped and "web-research" in skipped[0]["reason"]

    loaded2, skipped2 = load_ui_extensions(
        home, backend_records=[BackendRec("skill", "web-research")])
    assert [e.id for e in loaded2] == ["a"]
    assert skipped2 == []


def test_disabled_ui_extension_not_loaded(tmp_path):
    home = tmp_path
    _ext_dir(home / "ui-extensions", "a")
    loaded, skipped = load_ui_extensions(home, disabled_ids={"a"})
    assert loaded == []
    assert skipped and skipped[0]["reason"] == "已停用"
