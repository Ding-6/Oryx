"""把源码同步到 `target/release/` 的打包副本。

为什么需要这个脚本
------------------
`cargo build --release` 会顺手拷一遍 —— 但**没装 Rust 时就做不了**，
而打包副本比源码旧是很隐蔽的问题：

* exe 早期版本从自己所在目录往上找 `core/`，会**先撞上这份拷贝** ——
  于是在源码里改了半天，双击图标看到的还是几天前的界面
* 现在 `main.rs` 的 `core_dir` 改成「仓库根优先」，开发期不受影响；
  但**把 exe 单独拷走**（脱离仓库运行）时跑的就是这份拷贝

所以这个脚本让「同步」这件事**不依赖 Rust**。

用法
----
    python apps/desktop/scripts/sync_core.py            # 同步
    python apps/desktop/scripts/sync_core.py --check    # 只检查是否最新

同步的内容与 `tauri.conf.json` 的 `bundle.resources` 一一对应。
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
REPO = DESKTOP.parents[1]
RELEASE = DESKTOP / "src-tauri" / "target" / "release"

# (源目录, 目标目录) —— 与 tauri.conf.json 的 bundle.resources 对应
PAIRS = [
    (REPO / "core" / "oryx", RELEASE / "core" / "oryx"),
    (REPO / "core" / "web", RELEASE / "core" / "web"),
    (REPO / "core" / "scripts", RELEASE / "core" / "scripts"),
    (REPO / "skills", RELEASE / "skills"),
    (REPO / "experts", RELEASE / "experts"),
    (REPO / "agents", RELEASE / "agents"),
]

SKIP_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log"}


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_NAMES for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue
        out.append(p)
    return out


def diff(src: Path, dst: Path) -> list[str]:
    """目标相对源：缺哪些、哪些内容不同、**多出哪些**。

    早先只查前两种 —— 源码里删掉的旧构建产物会永远留在打包副本里，
    而且 `--check` 还会误报「全部与源码一致」。2026-11-26 抓到一次：
    源码 `core/web/newui/assets` 清成 3 个文件后，副本里仍躺着 67 个
    历史 js/css/map，检查却是绿的。加上「多」这一项后，下面的增量同步
    才会走进删除分支（`set(dst_map) - set(src_map)` 本来就写好了）。
    """
    problems: list[str] = []
    src_files = _files(src)
    for f in src_files:
        rel = f.relative_to(src)
        target = dst / rel
        if not target.exists():
            problems.append(f"缺 {rel.as_posix()}")
            continue
        try:
            if not filecmp.cmp(f, target, shallow=False):
                problems.append(f"旧 {rel.as_posix()}")
        except OSError:
            problems.append(f"比不了 {rel.as_posix()}")
    src_rels = {f.relative_to(src) for f in src_files}
    for f in _files(dst):
        rel = f.relative_to(dst)
        if rel not in src_rels:
            problems.append(f"多 {rel.as_posix()}")
    return problems


def sync(check: bool = False) -> int:
    if not RELEASE.exists():
        print(f"{RELEASE} 不存在 —— 先跑一次 cargo build --release")
        return 1
    total_problems = 0
    for src, dst in PAIRS:
        if not src.exists():
            print(f"跳过（源不存在）：{src}")
            continue
        problems = diff(src, dst)
        if problems:
            total_problems += len(problems)
            if check:
                print(f"不同步 {src.relative_to(REPO)} → {dst.relative_to(RELEASE)}：")
                for p in problems[:6]:
                    print(f"    {p}")
                if len(problems) > 6:
                    print(f"    …… 还有 {len(problems) - 6} 处")
            else:
                # **增量同步**：只覆盖变化的文件，只删源里已经没有的。
                #
                # 以前是「整体替换」（rmtree + copytree）—— 简单，
                # 但一次要删掉几百个文件（触发批量删除的保护），
                # 而且把没变的文件也重写一遍，慢。
                # 增量只动真正不同的地方。
                src_map = {f.relative_to(src): f for f in _files(src)}
                dst_map = {f.relative_to(dst): f for f in _files(dst)} if dst.exists() else {}
                copied = 0
                for rel, f in src_map.items():
                    target_f = dst / rel
                    if not target_f.exists() or not filecmp.cmp(f, target_f, shallow=False):
                        target_f.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, target_f)
                        copied += 1
                removed = 0
                for rel in set(dst_map) - set(src_map):
                    try:
                        (dst / rel).unlink()
                        removed += 1
                    except OSError:
                        pass
                for d in sorted(dst.rglob("*"), reverse=True):
                    if d.is_dir() and not any(d.iterdir()):
                        try:
                            d.rmdir()
                        except OSError:
                            pass
                print(f"已同步 {src.relative_to(REPO)} → {dst.relative_to(RELEASE)}"
                      f"（更新 {copied}，删除 {removed}）")
        elif not check:
            print(f"已最新 {src.relative_to(REPO)}")

    if check:
        if total_problems:
            print(f"\n{total_problems} 处不同 —— 跑一次不带 --check 的同步")
            return 1
        print("\n全部与源码一致")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只检查是否最新，不真同步")
    args = ap.parse_args()
    return sync(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
