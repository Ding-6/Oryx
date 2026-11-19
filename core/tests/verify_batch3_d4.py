"""第三批 · D4：检查点 / rewind 的验证。

覆盖：打点、写前快照、还原已存在文件、删除新建文件、三种 scope、
      对话裁剪、越界路径拒绝。

运行：python -m tests.verify_batch3_d4
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def fresh_oryx(home: str):
    os.environ["ORYX_HOME"] = home
    for mod in [m for m in list(sys.modules) if m.startswith("oryx")]:
        del sys.modules[mod]


def setup():
    tmp = tempfile.mkdtemp(prefix="oryx_b3d4_")
    fresh_oryx(tmp)
    from oryx.checkpoint import CheckpointService
    from oryx.store import Store

    store = Store(Path(tmp) / "t.db")
    ws = Path(tmp) / "ws"
    ws.mkdir()
    return CheckpointService(store, [str(ws)]), store, ws


# ---------------------------------------------------------------- 打点与快照
def test_mark_and_snapshot() -> None:
    svc, store, ws = setup()
    task = store.create_task("改文件", "efficiency", "test")
    cid = svc.mark(task, 0, "第一轮")
    check("打点成功", bool(cid))
    check("当前检查点可查", svc.current(task) == cid)

    f = ws / "a.txt"
    f.write_text("原始内容", encoding="utf-8")
    ok = svc.snapshot_write(task, str(f), "新内容")
    check("快照写入成功", ok)

    f.write_text("新内容", encoding="utf-8")
    r = svc.rewind(cid, "Code")
    check("回退成功", r["ok"], str(r))
    check("文件已还原", f.read_text(encoding="utf-8") == "原始内容",
          f.read_text(encoding="utf-8"))
    check("还原列表非空", len(r["content"]["restored"]) == 1, str(r["content"]))


def test_snapshot_new_file_then_delete() -> None:
    """Agent 新建的文件，回退时应该被删掉。"""
    svc, store, ws = setup()
    task = store.create_task("建文件", "efficiency", "test")
    cid = svc.mark(task, 0, "")

    newf = ws / "new.txt"
    svc.snapshot_write(task, str(newf), "Agent 生成的内容")
    newf.write_text("Agent 生成的内容", encoding="utf-8")
    check("文件已创建", newf.exists())

    r = svc.rewind(cid, "Code")
    check("回退成功", r["ok"])
    check("新建文件已删除", not newf.exists())
    check("删除列表非空", len(r["content"]["removed"]) == 1, str(r["content"]))


def test_multiple_files_restored() -> None:
    svc, store, ws = setup()
    task = store.create_task("多文件", "efficiency", "test")
    cid = svc.mark(task, 0, "")

    paths = []
    for i in range(3):
        f = ws / f"f{i}.txt"
        f.write_text(f"old{i}", encoding="utf-8")
        svc.snapshot_write(task, str(f), f"new{i}")
        f.write_text(f"new{i}", encoding="utf-8")
        paths.append(f)

    r = svc.rewind(cid, "Code")
    check("三个文件都还原", len(r["content"]["restored"]) == 3, str(r["content"]))
    check("内容都对",
          all(paths[i].read_text(encoding="utf-8") == f"old{i}" for i in range(3)))


def test_multiple_checkpoints_reverts_range() -> None:
    """回退到中间某个点，只退它之后的改动。"""
    svc, store, ws = setup()
    task = store.create_task("两轮", "efficiency", "test")

    a = ws / "a.txt"
    a.write_text("v0", encoding="utf-8")

    cp1 = svc.mark(task, 0, "第一轮")
    svc.snapshot_write(task, str(a), "v1")
    a.write_text("v1", encoding="utf-8")

    cp2 = svc.mark(task, 1, "第二轮")
    svc.snapshot_write(task, str(a), "v2")
    a.write_text("v2", encoding="utf-8")
    check("改到 v2", a.read_text(encoding="utf-8") == "v2")

    # 回退到 cp2：只该退第二轮，文件回到 v1
    r = svc.rewind(cp2, "Code")
    check("回退到 cp2 成功", r["ok"])
    check("文件回到 v1", a.read_text(encoding="utf-8") == "v1",
          a.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- scope 三态
def test_scope_conversation_only() -> None:
    svc, store, ws = setup()
    task = store.create_task("对话", "efficiency", "test")
    store.add_message(task, "user", "第一句")
    cid = svc.mark(task, 0, "")
    store.add_message(task, "assistant", "回答1")
    store.add_message(task, "user", "第二句")
    store.add_message(task, "assistant", "回答2")

    f = ws / "x.txt"
    f.write_text("keep", encoding="utf-8")
    svc.snapshot_write(task, str(f), "changed")
    f.write_text("changed", encoding="utf-8")

    r = svc.rewind(cid, "Conversation")
    check("仅回退对话成功", r["ok"])
    check("文件未被还原", f.read_text(encoding="utf-8") == "changed")
    # 打点时 msg_count=1（第一句已入库），之后追加了 3 条
    check("删掉了 3 条后续消息", r["content"]["messages_removed"] == 3,
          str(r["content"]))
    remaining = store.messages_for_task(task)
    check("只剩打点前的消息", len(remaining) == 1 and remaining[0]["content"] == "第一句",
          str(remaining))


def test_scope_code_only() -> None:
    svc, store, ws = setup()
    task = store.create_task("代码", "efficiency", "test")
    store.add_message(task, "user", "第一句")
    cid = svc.mark(task, 0, "")
    store.add_message(task, "assistant", "回答")

    f = ws / "y.txt"
    f.write_text("old", encoding="utf-8")
    svc.snapshot_write(task, str(f), "new")
    f.write_text("new", encoding="utf-8")

    r = svc.rewind(cid, "Code")
    check("文件已还原", f.read_text(encoding="utf-8") == "old")
    check("消息未被动", len(store.messages_for_task(task)) == 2,
          str(len(store.messages_for_task(task))))


def test_scope_both() -> None:
    svc, store, ws = setup()
    task = store.create_task("两者", "efficiency", "test")
    store.add_message(task, "user", "第一句")
    cid = svc.mark(task, 0, "")
    store.add_message(task, "assistant", "回答")

    f = ws / "z.txt"
    f.write_text("old", encoding="utf-8")
    svc.snapshot_write(task, str(f), "new")
    f.write_text("new", encoding="utf-8")

    r = svc.rewind(cid, "CodeAndConversation")
    check("文件已还原", f.read_text(encoding="utf-8") == "old")
    check("消息已裁剪", r["content"]["messages_removed"] == 1, str(r["content"]))


def test_invalid_scope_rejected() -> None:
    svc, store, ws = setup()
    task = store.create_task("x", "efficiency", "test")
    cid = svc.mark(task, 0, "")
    r = svc.rewind(cid, "Bogus")
    check("非法 scope 被拒", not r["ok"] and "scope" in r["content"], str(r))


def test_unknown_checkpoint() -> None:
    svc, store, ws = setup()
    r = svc.rewind("nope", "Code")
    check("不存在的检查点被拒", not r["ok"], str(r))


# ---------------------------------------------------------------- 边界
def test_out_of_scope_path_skipped() -> None:
    """允许范围外的路径不快照，回退时也不碰。"""
    svc, store, ws = setup()
    other = Path(tempfile.mkdtemp(prefix="oryx_outside_"))
    outside = other / "secret.txt"
    outside.write_text("外部", encoding="utf-8")

    task = store.create_task("越界", "efficiency", "test")
    cid = svc.mark(task, 0, "")
    ok = svc.snapshot_write(task, str(outside), "改了")
    check("范围外路径不快照", not ok)
    check("文件未被改动", outside.read_text(encoding="utf-8") == "外部")


def test_no_active_checkpoint_no_snapshot() -> None:
    """没打点就不快照 —— 避免孤儿快照堆积。"""
    svc, store, ws = setup()
    task = store.create_task("无点", "efficiency", "test")
    f = ws / "n.txt"
    f.write_text("x", encoding="utf-8")
    ok = svc.snapshot_write(task, str(f), "y")
    check("无检查点时不快照", not ok)


def test_status_listing() -> None:
    svc, store, ws = setup()
    task = store.create_task("列表", "efficiency", "test")
    svc.mark(task, 0, "第一轮")
    f = ws / "s.txt"
    f.write_text("a", encoding="utf-8")
    svc.snapshot_write(task, str(f), "b")
    svc.mark(task, 1, "第二轮")

    st = svc.status(task)
    check("列出 2 个检查点", len(st["checkpoints"]) == 2, str(st))
    check("当前点标记正确", st["checkpoints"][-1]["current"] is True, str(st))
    check("第一个点有文件数", st["checkpoints"][0]["files"] == 1, str(st))


def test_large_file_not_stored() -> None:
    """超大文件只记元信息，不存内容。"""
    svc, store, ws = setup()
    task = store.create_task("大文件", "efficiency", "test")
    cid = svc.mark(task, 0, "")
    big = ws / "big.bin"
    big.write_bytes(b"x" * (3 * 1024 * 1024))
    ok = svc.snapshot_write(task, str(big), "y")
    check("超大文件仍记录元信息", ok)
    items = store.checkpoint_files(cid)
    check("内容未存下", items and items[0]["before"] is None, str(items)[:120])


def test_prune_old() -> None:
    svc, store, ws = setup()
    task = store.create_task("过期", "efficiency", "test")
    svc.mark(task, 0, "")
    check("有检查点", len(store.list_checkpoints(task)) == 1)
    # 清理 0 天 = 立刻清掉全部
    n = store.prune_checkpoints(days=0)
    check("过期被清理", n >= 1, f"pruned={n}")
    check("列表已空", len(store.list_checkpoints(task)) == 0)


def main() -> int:
    tests = [
        ("D4-1 打点与写前快照", test_mark_and_snapshot),
        ("D4-2 新建文件回退即删除", test_snapshot_new_file_then_delete),
        ("D4-3 多文件还原", test_multiple_files_restored),
        ("D4-4 多检查点区间回退", test_multiple_checkpoints_reverts_range),
        ("D4-5 仅回退对话", test_scope_conversation_only),
        ("D4-6 仅回退代码", test_scope_code_only),
        ("D4-7 两者都退", test_scope_both),
        ("D4-8 非法 scope 拒绝", test_invalid_scope_rejected),
        ("D4-9 不存在的检查点", test_unknown_checkpoint),
        ("D4-10 范围外路径跳过", test_out_of_scope_path_skipped),
        ("D4-11 无检查点不快照", test_no_active_checkpoint_no_snapshot),
        ("D4-12 检查点列表", test_status_listing),
        ("D4-13 超大文件不存内容", test_large_file_not_stored),
        ("D4-14 过期清理", test_prune_old),
    ]
    for label, fn in tests:
        print(f"\n[{label}]")
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            FAILED.append(label)
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())