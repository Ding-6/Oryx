"""把 C 盘上的大目录搬到 D 盘，原处留一个目录联接（junction）。

**为什么要用联接，而不是只设环境变量**
--------------------------------------
设环境变量（比如 ``CODEX_HOME``）只在那个程序**确实认**它的时候才管用。
认不认，得翻它的二进制才知道 —— 官方安装的 WindowsApps 目录还读不了。

联接是文件系统层面的：程序照旧按原路径访问，实际读写的是 D 盘。
**认不认环境变量都无所谓**，也不会因为改路径而找不到自己的配置。

**为什么要分两步**
----------------
目录**被进程占着**的时候，Windows 不让改名，也就建不了联接
（实测：Codex 正在跑，``.cache/codex-runtimes`` 改名失败，
报「另一个程序正在使用此文件」）。

所以拆成两步：
  1. ``--move``    先把数据复制到 D 并校验（这一步不动原目录，零风险）
  2. ``--finish``  关掉占用它的程序之后再跑 —— 改名 + 建联接 + 删原目录

用法::

    python apps/desktop/scripts/relocate_caches.py --move      # 第一步：复制
    python apps/desktop/scripts/relocate_caches.py --finish    # 第二步：换目录 + 建联接

第二步会跳过还被占着的项，并告诉你哪些还没搬完。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 要搬的目录：(显示名, C 盘源, D 盘目标)
TARGETS = [
    ("DSH 家目录", Path.home() / ".dsh", Path("D:/OryxData/dsh")),
    ("Codex 家目录", Path.home() / ".codex", Path("D:/OryxData/codex")),
    ("Codex 运行时", Path.home() / ".cache" / "codex-runtimes",
     Path("D:/OryxData/cache/codex-runtimes")),
]

STATE_FILE = Path("D:/OryxData/relocate-state.json")


def dir_size(p: Path) -> int:
    """目录总字节数。"""
    total = 0
    for root, dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def copy_verified(src: Path, dst: Path) -> bool:
    """复制并校验大小。返回是否成功。"""
    if not src.exists():
        return True
    dst.mkdir(parents=True, exist_ok=True)
    before = dir_size(src)
    # robocopy 比 shutil 稳（断点续传、属性保留），Windows 上也有
    subprocess.run(
        ["robocopy", str(src), str(dst), "/E", "/NFL", "/NDL", "/NJH", "/NJS",
         "/NP", "/R:1", "/W:1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    after = dir_size(dst)
    ok = after >= before * 0.98
    print(f"    复制 {before/1024/1024:>8.1f} MB → D {after/1024/1024:>8.1f} MB"
          f"  {'✓' if ok else '✗ 校验不过'}")
    return ok


def make_junction(link: Path, target: Path) -> bool:
    """在 link 处建一个指向 target 的目录联接。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists():
        return False
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    return r.returncode == 0


def do_move() -> None:
    """第一步：复制到 D 并校验。原目录不动。"""
    state = {}
    for name, src, dst in TARGETS:
        print(f"\n[{name}]")
        print(f"    源 {src}")
        print(f"    目标 {dst}")
        if not src.exists():
            print("    源不存在，跳过")
            state[name] = {"src": str(src), "dst": str(dst), "done": True}
            continue
        ok = copy_verified(src, dst)
        state[name] = {"src": str(src), "dst": str(dst), "copied": ok, "done": False}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n状态记在 {STATE_FILE}")
    print("关掉占用这些目录的程序后，跑 --finish 完成搬家。")


def do_finish() -> None:
    """第二步：改名 → 建联接 → 删原目录。"""
    if not STATE_FILE.exists():
        print("没有状态文件，先跑 --move")
        return
    state = json.loads(STATE_FILE.read_text("utf-8"))
    pending = []
    for name, info in state.items():
        src = Path(info["src"])
        dst = Path(info["dst"])
        print(f"\n[{name}] {src}")
        if not src.exists():
            print("    源已不在，跳过")
            continue
        if not dst.exists():
            print("    D 盘还没有数据，先跑 --move")
            pending.append(name)
            continue
        # ① 源改名（腾出联接的位置）
        parked = src.with_name(src.name + ".moving")
        try:
            if parked.exists():
                shutil.rmtree(parked, ignore_errors=True)
            os.rename(src, parked)
        except OSError as exc:
            print(f"    ✗ 改名失败（还被占用）：{exc}")
            print("      → 关掉占用它的程序，再跑一次 --finish")
            pending.append(name)
            continue
        # ② 建联接
        if not make_junction(src, dst):
            print("    ✗ 建联接失败，把原目录搬回去")
            try:
                os.rename(parked, src)
            except OSError:
                pass
            pending.append(name)
            continue
        # ③ 删原目录（联接已经建好，删掉不影响新路径）
        shutil.rmtree(parked, ignore_errors=True)
        print(f"    ✓ 已搬到 {dst}，原路径留了联接")
        info["done"] = True
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    if pending:
        print(f"\n还没搬完：{', '.join(pending)}")
        print("（它们正被程序占用 —— 关掉那些程序再跑一次 --finish）")
    else:
        print("\n全部搬完了。")


def main() -> int:
    ap = argparse.ArgumentParser(description="把 C 盘的大目录搬到 D 盘")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--move", action="store_true", help="第一步：复制到 D")
    g.add_argument("--finish", action="store_true", help="第二步：换目录 + 建联接")
    g.add_argument("--status", action="store_true", help="看当前状态")
    args = ap.parse_args()
    if args.move:
        do_move()
    elif args.finish:
        do_finish()
    else:
        if STATE_FILE.exists():
            print(STATE_FILE.read_text("utf-8"))
        else:
            print("还没开始。先跑 --move")
    return 0


if __name__ == "__main__":
    sys.exit(main())
