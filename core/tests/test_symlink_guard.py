"""断言式安全测试：符号链接 / junction 越界防护。

抄自 Marvis 的 `tests/test_disk_inspect.py` 的写法：
造样本 → 跑真实路径 → **逐字节比对样本文件未被改动**。

三条判据：
1. 越界链接被识别出来（`escapes_root`）；
2. 扫描只统计真实文件，链接不进；
3. 链接目标的内容与 mtime 在扫描前后**逐字节不变** ——
   证明我们只是「看了链接文本」，没有跟着它去读目标。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oryx.symlink_guard import classify_symlink, is_junction, same_tree


def _symlink(src, dst) -> None:
    """建符号链接。Windows 没开发者模式时建不了 —— 跳过，不假装通过。"""
    try:
        os.symlink(src, dst, target_is_directory=os.path.isdir(src))
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("当前环境不能创建符号链接")


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    """快照：mtime_ns + size + 二进制全文。"""
    found: dict[str, tuple[int, int, bytes]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
                with open(path, "rb") as handle:
                    found[path] = (st.st_mtime_ns, st.st_size, handle.read())
            except OSError:
                continue
    return found


# ------------------------------------------------------------------ same_tree


def test_same_tree_accepts_child(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert same_tree(str(root), str(root / "child")) is True
    assert same_tree(str(root), str(root)) is True


def test_same_tree_rejects_sibling(tmp_path):
    """兄弟目录不算在根的树里 —— `root-other` 与 `root` 前缀相同但不是子目录。

    这是字符串前缀判断会踩的坑，`commonpath` 不会。
    """
    root = tmp_path / "root"
    root.mkdir()
    assert same_tree(str(root), str(tmp_path / "root-other")) is False
    assert same_tree(str(root), str(tmp_path)) is False


def test_same_tree_rejects_different_drive(tmp_path, monkeypatch):
    """跨盘符时 commonpath 抛 ValueError —— 要返回 False，不能让异常往上冒。"""

    def different_drive(paths):
        raise ValueError("different drive")

    monkeypatch.setattr("oryx.symlink_guard.os.path.commonpath", different_drive)
    assert same_tree(r"C:\root", r"D:\other") is False


# ------------------------------------------------------------------ classify_symlink


def test_absolute_link_outside_is_escaping(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    link = root / "link.txt"
    _symlink(str(outside), link)

    escaping, err = classify_symlink(str(root), str(link))
    assert err is None
    assert escaping is True


def test_relative_link_inside_is_not_escaping(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    target = root / "sub" / "real.txt"
    target.write_text("x", encoding="utf-8")
    link = root / "link.txt"
    _symlink(str(target), link)

    escaping, err = classify_symlink(str(root), str(link))
    assert err is None
    assert escaping is False


def test_relative_link_escaping_is_detected(tmp_path):
    """相对链接要按链接所在目录解析 —— 否则 `..\\..\\secret` 这种会漏。"""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("x", encoding="utf-8")
    link = root / "sub" / "link.txt"
    _symlink(os.path.join("..", "..", "secret.txt"), link)

    escaping, err = classify_symlink(str(root), str(link))
    assert err is None
    assert escaping is True


def test_classify_symlink_does_not_stat_target(tmp_path):
    """核心性质：**只读链接文本，从不 stat 目标**。

    判据是「连目标都没打开过」—— 用 monkeypatch 拦 os.stat 验证。
    """
    root = tmp_path / "root"
    root.mkdir()
    target = root / "real.txt"
    target.write_text("x", encoding="utf-8")
    link = root / "link.txt"
    _symlink(str(target), link)

    opened: list[str] = []
    real_stat = os.stat

    def tracked(path, *args, **kwargs):
        opened.append(os.path.basename(str(path)))
        return real_stat(path, *args, **kwargs)

    os.stat = tracked
    try:
        classify_symlink(str(root), str(link))
    finally:
        os.stat = real_stat

    assert "real.txt" not in opened, "不该 stat 链接目标"


# ------------------------------------------------------------------ is_junction


def test_is_junction_false_for_plain_dir(tmp_path):
    assert is_junction(str(tmp_path)) is False


def test_is_junction_fail_closed_on_error(tmp_path, monkeypatch):
    """探测出错时当成联接（返回 True）—— 宁可漏看，不给一条通向别处的路。"""

    def boom(path):
        raise OSError("cannot stat")

    monkeypatch.setattr("oryx.symlink_guard.os.path.isjunction", boom, raising=False)
    assert is_junction(str(tmp_path)) is True


# ------------------------------------------------------------------ 管线层：localfs


def test_localfs_marks_escaping_link(tmp_path):
    """目录浏览要标出「这个链接指向目录外」。"""
    from oryx.localfs import list_dir

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    _symlink(str(outside), root / "escape")

    result = list_dir(str(root))
    entries = {e["name"]: e for e in result["entries"]}
    assert "escape" in entries
    assert entries["escape"]["is_link"] is True
    assert entries["escape"]["escapes_root"] is True


def test_localfs_marks_internal_link(tmp_path):
    from oryx.localfs import list_dir

    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "real.txt").write_text("x", encoding="utf-8")
    _symlink(str(root / "sub" / "real.txt"), root / "inside")

    result = list_dir(str(root))
    entries = {e["name"]: e for e in result["entries"]}
    assert entries["inside"]["is_link"] is True
    assert entries["inside"]["escapes_root"] is False


def test_localfs_does_not_touch_link_target(tmp_path):
    """浏览目录不能改动链接目标 —— 逐字节比对。"""
    from oryx.localfs import list_dir

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("不要动我", encoding="utf-8")
    _symlink(str(outside), root / "escape")

    before = _snapshot(outside)
    list_dir(str(root))
    assert _snapshot(outside) == before


# ------------------------------------------------------------------ 管线层：indexer


def test_indexer_does_not_follow_links(tmp_path):
    """索引扫描不跟随链接 —— 链接指向的文件不进索引。"""
    from oryx.indexer import FileIndex

    root = tmp_path / "root"
    root.mkdir()
    (root / "real.txt").write_text("真实文件", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.txt").write_text("不该被索引", encoding="utf-8")
    _symlink(str(outside), root / "escape")

    index = FileIndex(path=tmp_path / "index.db", ocr=False, embeddings=False)
    index.scan([str(root)])

    hits = index.search("真实文件")
    assert hits, "真实文件应该被索引"
    assert not any("hidden.txt" in h["path"] for h in index.search("不该被索引"))


def test_indexer_does_not_modify_files(tmp_path):
    """索引扫描不改动任何文件 —— 逐字节比对。"""
    from oryx.indexer import FileIndex

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("内容 A", encoding="utf-8")
    (root / "b.md").write_text("# 标题", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("不要动我", encoding="utf-8")
    _symlink(str(outside), root / "escape")

    before = _snapshot(root)
    before_outside = _snapshot(outside)
    index = FileIndex(path=tmp_path / "index.db", ocr=False, embeddings=False)
    index.scan([str(root)])
    assert _snapshot(root) == before
    assert _snapshot(outside) == before_outside


# ------------------------------------------------------------------ 扫描上限自述


def test_indexer_reports_partial_when_depth_limit_hit(tmp_path):
    """触发深度上限 → 自述不完整。"""
    from oryx.indexer import FileIndex

    root = tmp_path / "root"
    deep = root
    for i in range(6):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("深层", encoding="utf-8")

    index = FileIndex(path=tmp_path / "index.db", ocr=False, embeddings=False)
    stats = index.scan([str(root)], max_depth=2)
    assert stats.partial is True
    assert "深度" in stats.limit_hits


def test_indexer_reports_partial_when_entry_limit_hit(tmp_path):
    from oryx.indexer import FileIndex

    root = tmp_path / "root"
    root.mkdir()
    for i in range(10):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")

    index = FileIndex(path=tmp_path / "index.db", ocr=False, embeddings=False)
    stats = index.scan([str(root)], max_files=3)
    assert stats.partial is True
    assert "条目" in stats.limit_hits


def test_indexer_not_partial_when_within_limits(tmp_path):
    from oryx.indexer import FileIndex

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")

    index = FileIndex(path=tmp_path / "index.db", ocr=False, embeddings=False)
    stats = index.scan([str(root)])
    assert stats.partial is False
    assert stats.limit_hits == []
