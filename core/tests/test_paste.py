"""粘贴导入：图片 / 文本落盘。

落盘位置跟着 ORYX_HOME 走 —— 测试必须隔离，
否则会往用户真实的 %APPDATA%\\Oryx 里写文件。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from oryx import paste as paste_mod


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """把 ORYX_HOME 指到临时目录 —— 别往用户真实配置目录里写。"""
    monkeypatch.setenv("ORYX_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"


def png_data_url(color: bytes = b"\x89PNG\r\n\x1a\n") -> str:
    """造一个最小 PNG 的 data URL。"""
    return "data:image/png;base64," + base64.b64encode(color).decode("ascii")


def test_save_image_data_url(isolated_home):
    item = paste_mod.save_image_data_url(png_data_url(), "shot.png")
    path = Path(item["path"])
    assert path.exists()
    assert path.suffix == ".png"
    assert item["kind"] == "image"
    assert item["mime"] == "image/png"
    assert path.read_bytes().startswith(b"\x89PNG")


def test_save_image_uses_real_mime_extension(isolated_home):
    """扩展名按真实 MIME 猜，不信客户端给的。"""
    url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff").decode("ascii")
    item = paste_mod.save_image_data_url(url, "x")
    assert Path(item["path"]).suffix == ".jpg"


def test_save_image_rejects_non_image(isolated_home):
    with pytest.raises(ValueError):
        paste_mod.save_image_data_url("data:text/plain;base64,aGk=", "x")


def test_save_image_rejects_garbage(isolated_home):
    with pytest.raises(ValueError):
        paste_mod.save_image_data_url("not-a-data-url", "x")


def test_save_image_rejects_empty(isolated_home):
    with pytest.raises(ValueError):
        paste_mod.save_image_data_url("data:image/png;base64,", "x")


def test_save_image_sanitizes_name(isolated_home):
    """文件名里的非法字符要洗掉 —— Windows 上直接写会报错。"""
    item = paste_mod.save_image_data_url(png_data_url(), "a/b:c*d.png")
    name = item["name"]
    assert "/" not in name and ":" not in name and "*" not in name


def test_save_image_does_not_overwrite(isolated_home):
    """同名不覆盖 —— 加序号。"""
    a = paste_mod.save_image_data_url(png_data_url(), "shot.png")
    b = paste_mod.save_image_data_url(png_data_url(), "shot.png")
    assert a["path"] != b["path"]
    assert Path(a["path"]).exists() and Path(b["path"]).exists()


def test_save_text(isolated_home):
    item = paste_mod.save_text("你好，世界", "note.txt")
    path = Path(item["path"])
    assert path.exists()
    assert path.read_text("utf-8") == "你好，世界"
    assert item["kind"] == "text"


def test_save_text_default_name(isolated_home):
    item = paste_mod.save_text("x")
    assert item["name"].endswith(".txt")


def test_save_text_rejects_too_long(isolated_home):
    with pytest.raises(ValueError):
        paste_mod.save_text("x" * (paste_mod.MAX_TEXT_CHARS + 1))


def test_paste_dir_under_app_home(isolated_home):
    """粘贴的东西要落在应用目录下，不是用户的工作区里。

    粘贴是随手行为，用户没指定放哪 ——
    放工作区会污染用户自己的项目目录。
    """
    item = paste_mod.save_text("x")
    assert Path(item["path"]).is_relative_to(isolated_home)
