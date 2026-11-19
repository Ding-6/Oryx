"""在临时目录里造出符号链接样本。

为什么不在仓库里放真的链接文件：
链接不是普通文件 —— 能不能建取决于操作系统与当前用户有没有开发者模式。
放一个建不出来的链接进仓库，测试在别人机器上会一直跳过，
看着像「测过了」，其实是没测。

所以：链接**现造**，造在临时目录里，用完即弃。

用法：
    python make_links.py <目标目录>
会在目标目录下造出：
    real.txt            真实文件
    inside_link.txt     指向同目录内的链接（不该算越界）
    outside_link.txt    指向目录外的链接（该算越界）
    dangling_link.txt   指向不存在的路径
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def build(root: Path) -> dict[str, str]:
    """造样本。返回 {名字: 路径}。建不出来的链接会跳过。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "real.txt").write_text("真实内容\n", encoding="utf-8")
    outside = root.parent / "outside_target.txt"
    outside.write_text("目录外的内容\n", encoding="utf-8")

    made: dict[str, str] = {"real.txt": str(root / "real.txt")}
    targets = {
        "inside_link.txt": str(root / "real.txt"),
        "outside_link.txt": str(outside),
        "dangling_link.txt": str(root / "不存在的文件.txt"),
    }
    for name, target in targets.items():
        path = root / name
        try:
            os.symlink(target, path)
        except (OSError, NotImplementedError, AttributeError) as exc:
            print(f"[skip] 建不了 {name}：{exc}")
            continue
        made[name] = str(path)
    return made


if __name__ == "__main__":
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "_built"
    for name, path in build(where).items():
        print(f"{name} -> {path}")
