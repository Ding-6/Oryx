"""自定义应用与启动的安全测试。

重点是 launch：**它只接受 id，不接受任意路径**。接受路径的话这个端点
就退化成了"运行任意程序"的通用原语。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx import manage


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把 ORYX_HOME 指到临时目录 —— 自定义应用存在那儿，不能碰用户真实数据。"""
    monkeypatch.setenv("ORYX_HOME", str(tmp_path))
    return tmp_path


def _exe(tmp_path: Path) -> Path:
    p = tmp_path / "myapp.exe"
    p.write_bytes(b"MZ")
    return p


# ---------- 存取 ----------
def test_custom_apps_empty_by_default(home):
    assert manage.load_custom_apps() == []


def test_save_and_load(home, tmp_path):
    exe = _exe(tmp_path)
    item = manage.save_custom_app({"name": "我的应用", "target": str(exe), "note": "测试"})
    assert item["name"] == "我的应用"
    assert item["target"] == str(exe)
    assert item["id"]
    loaded = manage.load_custom_apps()
    assert len(loaded) == 1
    assert loaded[0]["name"] == "我的应用"
    assert loaded[0]["note"] == "测试"


def test_save_requires_name(home, tmp_path):
    with pytest.raises(ValueError):
        manage.save_custom_app({"name": "  ", "target": str(_exe(tmp_path))})


def test_save_requires_target(home):
    with pytest.raises(ValueError):
        manage.save_custom_app({"name": "x", "target": ""})


def test_save_rejects_missing_target(home, tmp_path):
    with pytest.raises(ValueError):
        manage.save_custom_app({"name": "x", "target": str(tmp_path / "nope.exe")})


def test_update_same_id_does_not_duplicate(home, tmp_path):
    exe = _exe(tmp_path)
    first = manage.save_custom_app({"name": "应用", "target": str(exe)})
    second = manage.save_custom_app(
        {"id": first["id"], "name": "改名了", "target": str(exe)}
    )
    assert second["id"] == first["id"]
    loaded = manage.load_custom_apps()
    assert len(loaded) == 1
    assert loaded[0]["name"] == "改名了"


def test_created_at_preserved_on_update(home, tmp_path):
    exe = _exe(tmp_path)
    first = manage.save_custom_app({"name": "应用", "target": str(exe)})
    manage.save_custom_app({"id": first["id"], "name": "应用2", "target": str(exe)})
    loaded = manage.load_custom_apps()[0]
    assert loaded["created_at"] == first["created_at"]


def test_delete(home, tmp_path):
    item = manage.save_custom_app({"name": "应用", "target": str(_exe(tmp_path))})
    assert manage.delete_custom_app(item["id"]) is True
    assert manage.load_custom_apps() == []
    assert manage.delete_custom_app(item["id"]) is False


def test_corrupt_file_is_tolerated(home):
    """apps.json 坏了就当空的 —— 不能让一个手滑的编辑搞崩整个应用库。"""
    (home / "apps.json").write_text("{ 这不是 json", encoding="utf-8")
    assert manage.load_custom_apps() == []


def test_non_list_payload_is_ignored(home):
    (home / "apps.json").write_text('{"a": 1}', encoding="utf-8")
    assert manage.load_custom_apps() == []


def test_entries_without_target_are_dropped(home):
    (home / "apps.json").write_text(
        '[{"name": "无路径"}, {"target": "C:/x.exe"}]', encoding="utf-8"
    )
    assert manage.load_custom_apps() == []


# ---------- 启动 ----------
def test_launch_unknown_id_raises(home):
    with pytest.raises(ValueError):
        manage.launch_app("根本不存在的应用")


def test_launch_by_custom_id(home, tmp_path, monkeypatch):
    exe = _exe(tmp_path)
    item = manage.save_custom_app({"name": "我的应用", "target": str(exe)})
    called = {}

    def fake_startfile(path):
        called["path"] = path

    monkeypatch.setattr("os.startfile", fake_startfile)
    out = manage.launch_app(item["id"])
    assert out["ok"] is True
    assert out["launched"] == "我的应用"
    assert called["path"] == str(exe)


def test_launch_rejects_missing_target(home, tmp_path, monkeypatch):
    """登记之后目标被删了 —— 要报错，不能瞎启动别的东西。"""
    exe = _exe(tmp_path)
    item = manage.save_custom_app({"name": "我的应用", "target": str(exe)})
    exe.unlink()
    monkeypatch.setattr("os.startfile", lambda p: None)
    with pytest.raises(ValueError):
        manage.launch_app(item["id"])


def test_launch_by_installed_name(home, tmp_path, monkeypatch):
    """按已安装应用的名字也能启动（走 /api/apps 查表）。"""
    exe = _exe(tmp_path)
    monkeypatch.setattr(
        manage, "list_installed_apps",
        lambda limit=300: {
            "count": 1,
            "apps": [{
                "name": "我的工具", "version": "", "publisher": "",
                "source": "registry", "path": "",
                "target": str(exe),
            }],
        },
    )
    called = {}
    monkeypatch.setattr("os.startfile", lambda p: called.setdefault("path", p))
    out = manage.launch_app("我的工具")
    assert out["ok"] is True
    assert out["launched"] == "我的工具"
    assert called["path"] == str(exe)
