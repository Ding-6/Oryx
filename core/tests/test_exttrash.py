"""回收区自动清理的测试。

要锁住的：

1. **删了要记账** —— 知道什么时候删的，才知道该不该自动清。
2. **过期自动清、没过期不动** —— 保留期内的条目不能被误删。
3. **保留期为 0 = 永不自动清** —— 用户可以完全关掉这个行为。
4. **不知道删除时间的条目不自动清** —— 不知道它活多久，就不该替用户决定。
5. **恢复后账本要同步** —— 账本里不能留着一个已经不存在的条目。
6. **重名不丢扩展** —— 同名删两次，两份都在。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from oryx.extmanage import (
    DEFAULT_RETENTION_DAYS,
    ExtensionError,
    list_trash,
    move_to_trash,
    purge_expired,
    purge_from_trash,
    read_trash_meta,
    restore_from_trash,
    trash_stats,
)


def _make_ext(base: Path, name: str = "my-ext") -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "oryx.json").write_text(json.dumps({
        "id": name, "name": "我的扩展", "kind": "tool", "version": "1.0.0", "tools": [],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text("def run(**kw): pass\n", encoding="utf-8")
    return d


def _ext_root(tmp_path: Path) -> Path:
    root = tmp_path / "extensions"
    root.mkdir()
    return root


# ── 记账 ──────────────────────────────────────────────────────────────
def test_move_to_trash_records_metadata(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")

    meta = read_trash_meta(root)
    assert "a" in meta, "删除时要记一笔账"
    entry = meta["a"]
    assert entry["id"] == "a"
    assert entry["trashed_at"] > 0, "要记删除时间"
    assert entry["version"] == "1.0.0"


def test_list_trash_reports_age(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")

    items = list_trash(root)
    assert len(items) == 1
    assert "age_days" in items[0]
    assert items[0]["age_days"] < 1
    assert items[0]["original_id"] == "a"


def test_metadata_not_written_into_extension_dir(tmp_path):
    """账本放在回收区根部 —— 不往用户的扩展目录里塞我们的文件。"""
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    trash = root / ".trash"
    assert (trash / "_meta.json").exists()
    assert not (trash / "a" / "_meta.json").exists()
    assert sorted(p.name for p in (trash / "a").iterdir()) == ["oryx.json", "tools.py"]


# ── 自动清理 ──────────────────────────────────────────────────────────
def test_purge_expired_removes_old_items(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "old")
    move_to_trash(root, "old")
    # 假装它是 40 天前删的
    meta = read_trash_meta(root)
    meta["old"]["trashed_at"] = time.time() - 40 * 86400
    from oryx.extmanage import write_trash_meta
    write_trash_meta(root, meta)

    purged = purge_expired(root, 30)
    assert purged == ["old"]
    assert not list_trash(root)


def test_purge_expired_keeps_recent_items(tmp_path):
    """保留期内的条目不能被误删 —— 这是「误删能找回」的前提。"""
    root = _ext_root(tmp_path)
    _make_ext(root, "fresh")
    move_to_trash(root, "fresh")

    purged = purge_expired(root, 30)
    assert purged == []
    assert [i["id"] for i in list_trash(root)] == ["fresh"]


def test_purge_expired_zero_means_never(tmp_path):
    """保留期设为 0 = 永不自动清理。"""
    root = _ext_root(tmp_path)
    _make_ext(root, "old")
    move_to_trash(root, "old")
    meta = read_trash_meta(root)
    meta["old"]["trashed_at"] = time.time() - 999 * 86400
    from oryx.extmanage import write_trash_meta
    write_trash_meta(root, meta)

    assert purge_expired(root, 0) == []
    assert list_trash(root), "保留期为 0 时不该清任何东西"


def test_purge_expired_skips_items_without_timestamp(tmp_path):
    """老版本留下的条目没有删除时间 —— 不知道它活多久，不该替用户决定。"""
    root = _ext_root(tmp_path)
    _make_ext(root, "legacy")
    trash = root / ".trash"
    trash.mkdir()
    # 手工搬进去，不记账
    import shutil
    shutil.move(str(root / "legacy"), str(trash / "legacy"))

    purged = purge_expired(root, 30)
    assert purged == []
    assert [i["id"] for i in list_trash(root)] == ["legacy"]


def test_purge_expired_updates_ledger(tmp_path):
    """清掉的条目要从账本里去掉 —— 账本不能留着一个不存在的条目。"""
    root = _ext_root(tmp_path)
    _make_ext(root, "old")
    move_to_trash(root, "old")
    meta = read_trash_meta(root)
    meta["old"]["trashed_at"] = time.time() - 40 * 86400
    from oryx.extmanage import write_trash_meta
    write_trash_meta(root, meta)

    purge_expired(root, 30)
    assert "old" not in read_trash_meta(root)


# ── 恢复 / 彻底删除 ───────────────────────────────────────────────────
def test_restore_updates_ledger(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    restore_from_trash(root, "a")
    assert not read_trash_meta(root), "恢复后账本要同步"
    assert (root / "a").exists()


def test_purge_updates_ledger(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    purge_from_trash(root, "a")
    assert not read_trash_meta(root)


def test_restore_uses_original_id(tmp_path):
    """回收区里的目录名可能带 .1 后缀 —— 恢复要用**原 id**。"""
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    _make_ext(root, "a")          # 又装了一个同名的
    move_to_trash(root, "a")      # 又删了一次 → 回收区里叫 a.1

    items = list_trash(root)
    assert len(items) == 2
    assert items[1]["original_id"] == "a"

    restore_from_trash(root, "a.1")
    assert (root / "a").exists(), "恢复后应该回到原名"


# ── 统计 ──────────────────────────────────────────────────────────────
def test_trash_stats_counts_and_bytes(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    stats = trash_stats(root, 30)
    assert stats["count"] == 1
    assert stats["bytes"] > 0
    assert stats["retention_days"] == 30


def test_trash_stats_lists_expiring(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "old")
    move_to_trash(root, "old")
    meta = read_trash_meta(root)
    meta["old"]["trashed_at"] = time.time() - 40 * 86400
    from oryx.extmanage import write_trash_meta
    write_trash_meta(root, meta)

    stats = trash_stats(root, 30)
    assert stats["expiring"] == ["old"], "界面上要能提前告诉用户哪些会被清"


def test_trash_stats_expiring_empty_when_disabled(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "old")
    move_to_trash(root, "old")
    stats = trash_stats(root, 0)
    assert stats["expiring"] == []


def test_default_retention_is_thirty_days():
    """30 天与 Windows / macOS 回收站一致 —— 不用猜。"""
    assert DEFAULT_RETENTION_DAYS == 30


# ── 账本坏了不该让回收区打不开 ───────────────────────────────────────
def test_broken_ledger_does_not_break_trash(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    (root / ".trash" / "_meta.json").write_text("{不是 JSON", encoding="utf-8")

    items = list_trash(root)
    assert len(items) == 1, "账本坏了也要能列出回收区"


def test_missing_ledger_does_not_break_trash(tmp_path):
    root = _ext_root(tmp_path)
    _make_ext(root, "a")
    move_to_trash(root, "a")
    (root / ".trash" / "_meta.json").unlink()

    items = list_trash(root)
    assert len(items) == 1
    assert "age_days" not in items[0], "没账本就不知道多久了"
