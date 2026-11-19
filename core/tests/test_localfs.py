"""本地目录浏览的护栏测试。

这个模块是「此电脑」视图的后端，它**不受 allowed_roots 限制**（浏览整盘
是用户手势），所以它自己的护栏就是最后一道防线 —— 测的全是"不该被看到
的东西能不能被看到"。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx import localfs


def _tmp_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """建一个目录树：root/sub/deep.txt + root/secret.txt + root/.hidden"""
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / "deep.txt").write_text("hello", encoding="utf-8")
    (root / "secret.txt").write_text("s", encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "x.txt").write_text("x", encoding="utf-8")
    return root, sub, root / ".hidden"


# ---------- 基本列举 ----------
def test_list_dir_returns_entries(tmp_path):
    root, sub, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root))
    names = {e["name"] for e in d["entries"]}
    assert "sub" in names and "secret.txt" in names
    assert d["path"].replace("\\", "/").endswith("root")
    assert d["count"] == len(d["entries"])


def test_entries_have_required_fields(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root))
    for e in d["entries"]:
        for field in ("name", "path", "is_dir", "size", "mtime", "ext", "hidden"):
            assert field in e, f"{field} 缺失"
    sub = next(e for e in d["entries"] if e["name"] == "sub")
    assert sub["is_dir"] is True
    txt = next(e for e in d["entries"] if e["name"] == "secret.txt")
    assert txt["is_dir"] is False
    assert txt["ext"] == "txt"
    assert txt["size"] == 1


def test_directories_come_first(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root))
    kinds = [e["is_dir"] for e in d["entries"]]
    assert kinds == sorted(kinds, reverse=True), "目录应排在文件前面"


def test_parent_points_up(tmp_path):
    root, sub, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(sub))
    assert d["parent"].replace("\\", "/").endswith("root")


def test_root_parent_is_empty(tmp_path):
    """盘符根的 parent 不能再往上 —— 否则会死循环。"""
    d = localfs.list_dir("C:\\")
    assert d["parent"] == ""


# ---------- 隐藏文件 ----------
def test_hidden_excluded_by_default(tmp_path):
    root, _, hidden = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root))
    assert ".hidden" not in {e["name"] for e in d["entries"]}


def test_hidden_included_when_asked(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root), show_hidden=True)
    assert ".hidden" in {e["name"] for e in d["entries"]}


# ---------- 越界与非法路径 ----------
def test_parent_traversal_is_normalized(tmp_path):
    """`..` 会被 resolve 掉 —— 落到真实路径上就算合法，不算越界。

    这不是漏洞：浏览整盘本来就是这个模块的职责。真正要挡的是
    落到盘外（网络路径、device path）的那种。
    """
    root, _, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root / "sub" / ".."))
    assert d["path"].replace("\\", "/").endswith("root")


@pytest.mark.parametrize("bad", [
    "\\\\?\\C:\\Windows",
    "\\\\.\\C:\\Windows",
    "\\\\server\\share",
])
def test_device_and_unc_paths_rejected(bad):
    with pytest.raises(localfs.BrowseError) as exc:
        localfs.safe_browse(bad)
    assert exc.value.code == 403


def test_empty_path_rejected():
    with pytest.raises(localfs.BrowseError):
        localfs.safe_browse("")


def test_nonexistent_path_404():
    with pytest.raises(localfs.BrowseError) as exc:
        localfs.safe_browse("C:\\definitely-not-here-12345")
    assert exc.value.code == 404


def test_file_path_is_not_a_directory(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    with pytest.raises(localfs.BrowseError) as exc:
        localfs.safe_browse(str(root / "secret.txt"))
    assert exc.value.code == 400


# ---------- 禁区 ----------
def test_forbidden_path_blocked(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    with pytest.raises(localfs.BrowseError) as exc:
        localfs.safe_browse(str(root), forbidden=[str(root)])
    assert exc.value.code == 403


def test_forbidden_path_blocks_child(tmp_path):
    root, sub, _ = _tmp_tree(tmp_path)
    with pytest.raises(localfs.BrowseError) as exc:
        localfs.safe_browse(str(sub), forbidden=[str(root)])
    assert exc.value.code == 403


def test_forbidden_path_allows_sibling(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    # 禁区只挡它自己及子目录，兄弟目录照常
    d = localfs.list_dir(str(other), forbidden=[str(root)])
    assert d["path"]


# ---------- 限流 ----------
def test_limit_and_offset(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(20):
        (root / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    d = localfs.list_dir(str(root), limit=5)
    assert len(d["entries"]) == 5
    assert d["total"] == 20
    d2 = localfs.list_dir(str(root), limit=5, offset=5)
    assert len(d2["entries"]) == 5
    assert {e["name"] for e in d["entries"]}.isdisjoint({e["name"] for e in d2["entries"]})


def test_limit_is_capped(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root), limit=999999)
    assert d["limit"] == localfs.MAX_LIMIT


def test_offset_beyond_end_returns_empty(tmp_path):
    root, _, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root), offset=9999)
    assert d["entries"] == []
    assert d["total"] > 0


def test_single_level_only(tmp_path):
    """只列一层，不递归 —— 否则扫整盘会把接口卡死。"""
    root, sub, _ = _tmp_tree(tmp_path)
    d = localfs.list_dir(str(root))
    assert all(e["name"] != "deep.txt" for e in d["entries"])
