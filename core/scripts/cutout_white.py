"""把插画的白色背景抠成透明。

为什么不用 `mix-blend-mode: multiply`
------------------------------------
`mix-blend-mode` 只在**最近的层叠上下文**里混合。
界面里有祖先元素（`#scroll` / `.wrap`）带了 `z-index` 或 `position`，
自成一层叠上下文 —— 于是 multiply 只能在那一层里跟「透明」混合，
白底还是白底，方块原封不动地摆在紫色渐变上。

直接抠像素更可靠：白 → 全透明，接近白的边缘 → 按亮度给半透明，
过渡自然，不会有一圈硬边。

用法：python scripts/cutout_white.py <源图> <目标图>
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# 高于这个值算「纯背景」，直接抠掉
PURE = 236
# 到这个值算「边缘」，按亮度渐变给 alpha
EDGE = 206


def cutout(src: Path, dst: Path) -> int:
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    px = im.load()
    removed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            # 用**最暗通道**判断「白不白」——
            # 淡紫的元素 min 通道会明显低于 255，不会被误抠
            mn = min(r, g, b)
            if mn >= PURE:
                px[x, y] = (r, g, b, 0)
                removed += 1
            elif mn >= EDGE:
                # 越接近白越透明
                t = (PURE - mn) / float(PURE - EDGE)
                px[x, y] = (r, g, b, int(a * t))
                removed += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)
    return removed


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    n = cutout(src, dst)
    total = Image.open(src).size
    print(f"已抠掉 {n} / {total[0] * total[1]} 像素 → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
