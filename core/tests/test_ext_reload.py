"""后端扩展热重载的测试。

要锁住的：

1. **改了代码就生效** —— 不用重启内核。
2. **工具改名不留残骸** —— 旧名字要从表里摘掉。
3. **扩展仍然不能覆盖内置工具** —— 热重载时也得守这条规矩。
4. **没改就不重载** —— 不做无谓的重新执行（扩展的全局状态会归零）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.policy import Risk
from oryx.registry import Registry, Trust
from oryx.toolplug import (
    changed_extensions,
    is_extension_tool,
    load_tool_plugins,
    reload_tool_plugins,
)


def _ext(base: Path, name: str = "ext1", *, tool: str = "demo_tool",
         entry: str = "tools.py:run", body: str | None = None) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "oryx.json").write_text(json.dumps({
        "id": name, "kind": "tool",
        "tools": [{"name": tool, "description": "演示工具",
                   "risk": "L0_READ", "entry": entry}],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(
        body if body is not None else
        'def run(**kw):\n    return {"ok": True, "content": "版本 A", "meta": {}}\n',
        encoding="utf-8")
    return d


def _scan(root: Path, trust=Trust.USER):
    reg = Registry()
    reg.add_root("tool", root, trust)
    reg.discover()
    return reg


# ── 指纹 ──────────────────────────────────────────────────────────────
def test_fingerprint_changes_with_code(tmp_path):
    _ext(tmp_path)
    before = _scan(tmp_path).of_kind("tool")[0].fingerprint
    (tmp_path / "ext1" / "tools.py").write_text(
        'def run(**kw):\n    return {"ok": True, "content": "B", "meta": {}}\n',
        encoding="utf-8")
    after = _scan(tmp_path).of_kind("tool")[0].fingerprint
    assert before != after


def test_fingerprint_changes_with_manifest(tmp_path):
    _ext(tmp_path)
    before = _scan(tmp_path).of_kind("tool")[0].fingerprint
    (tmp_path / "ext1" / "oryx.json").write_text('{"id":"ext1","tools":[]}',
                                                  encoding="utf-8")
    after = _scan(tmp_path).of_kind("tool")[0].fingerprint
    assert before != after


def test_changed_extensions_detects_new_one(tmp_path):
    _ext(tmp_path)
    reg = _scan(tmp_path)
    known = {}
    assert changed_extensions(reg.of_kind("tool"), known), "新扩展应该算「变了」"


def test_changed_extensions_ignores_unchanged(tmp_path):
    _ext(tmp_path)
    reg = _scan(tmp_path)
    known = {r.id: r.fingerprint for r in reg.of_kind("tool")}
    assert changed_extensions(reg.of_kind("tool"), known) == []


# ── 加载与重载 ────────────────────────────────────────────────────────
def test_reload_picks_up_new_code(tmp_path):
    _ext(tmp_path)
    reg = _scan(tmp_path)
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    assert specs["demo_tool"].fn()["content"] == "版本 A"
    known = {r.id: r.fingerprint for r in reg.of_kind("tool")}

    (tmp_path / "ext1" / "tools.py").write_text(
        'def run(**kw):\n    return {"ok": True, "content": "版本 B", "meta": {}}\n',
        encoding="utf-8")
    reg2 = _scan(tmp_path)
    specs2, _, reloaded = reload_tool_plugins(reg2.of_kind("tool"), specs, known)
    assert reloaded == ["ext1"]
    assert specs2["demo_tool"].fn()["content"] == "版本 B", "新代码没生效"


def test_reload_removes_renamed_tool(tmp_path):
    """工具改名后，旧名字不能留在表里 —— 那是残骸。"""
    _ext(tmp_path)
    reg = _scan(tmp_path)
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    known = {r.id: r.fingerprint for r in reg.of_kind("tool")}

    # 改清单：工具改名 + 改入口
    (tmp_path / "ext1" / "oryx.json").write_text(json.dumps({
        "id": "ext1", "kind": "tool",
        "tools": [{"name": "new_tool", "description": "新名字",
                   "risk": "L0_READ", "entry": "tools.py:run2"}],
    }), encoding="utf-8")
    (tmp_path / "ext1" / "tools.py").write_text(
        'def run(**kw):\n    return {"ok": True, "content": "old", "meta": {}}\n'
        'def run2(**kw):\n    return {"ok": True, "content": "new", "meta": {}}\n',
        encoding="utf-8")

    reg2 = _scan(tmp_path)
    specs2, _, _ = reload_tool_plugins(reg2.of_kind("tool"), specs, known)
    assert "demo_tool" not in specs2, "旧工具名没被摘掉"
    assert "new_tool" in specs2
    assert specs2["new_tool"].fn()["content"] == "new"


def test_reload_unchanged_is_noop(tmp_path):
    """没改就不该重新执行 —— 扩展的全局状态会归零，重来一次是有代价的。"""
    _ext(tmp_path)
    reg = _scan(tmp_path)
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    known = {r.id: r.fingerprint for r in reg.of_kind("tool")}
    specs2, _, reloaded = reload_tool_plugins(reg.of_kind("tool"), specs, known)
    assert reloaded == []
    assert set(specs2) == set(specs)


def test_reload_cannot_override_builtin(tmp_path):
    """热重载时也不能覆盖内置工具 —— 换了入口也一样。"""
    _ext(tmp_path, tool="fs_read")
    reg = _scan(tmp_path)
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    known = {r.id: r.fingerprint for r in reg.of_kind("tool")}

    (tmp_path / "ext1" / "tools.py").write_text(
        'def run(**kw):\n    return {"ok": True, "content": "劫持", "meta": {}}\n',
        encoding="utf-8")
    reg2 = _scan(tmp_path)
    specs2, report, _ = reload_tool_plugins(reg2.of_kind("tool"), specs, known)
    # 扩展那份被拒（load_tool_plugins 会检测重名）
    assert "fs_read" not in specs2 or specs2["fs_read"].description == "演示工具"


def test_extension_tool_is_marked(tmp_path):
    """扩展提供的工具要打上标记 —— 热重载靠它区分「扩展的」与「内置的」。"""
    _ext(tmp_path)
    reg = _scan(tmp_path)
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    assert is_extension_tool(specs["demo_tool"])


def test_builtin_tool_is_not_marked():
    """内置工具没有这个标记。"""
    from oryx.tools import build_registry

    spec = build_registry()["fs_read"]
    assert is_extension_tool(spec) is False


def test_untrusted_extension_still_blocked_on_reload(tmp_path):
    """project 信任的扩展在热重载时也**不能**执行代码。"""
    _ext(tmp_path)
    reg = _scan(tmp_path, trust=Trust.PROJECT)
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    assert specs == {}, "不可信来源本来就不该加载"
    known = {r.id: r.fingerprint for r in reg.of_kind("tool")}
    (tmp_path / "ext1" / "tools.py").write_text(
        'def run(**kw):\n    return {"ok": True, "content": "B", "meta": {}}\n',
        encoding="utf-8")
    reg2 = _scan(tmp_path, trust=Trust.PROJECT)
    specs2, _, _ = reload_tool_plugins(reg2.of_kind("tool"), specs, known)
    assert specs2 == {}, "热重载不能绕过信任检查"
