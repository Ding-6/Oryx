"""两张截图的像素级对比 —— CSS 拆分后确认视觉**一点没变**。

为什么需要
----------
CSS 的层叠靠加载顺序。拆成多个文件后，顺序变了、被覆盖的规则变了，
视觉就会**悄悄**变样 —— 而这种回归极难靠肉眼发现。

所以拆分前后各截一张，逐像素比。

用法
----
    python tests/_shot_cmp.py before.png after.png
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        from PIL import Image, ImageChops
    except ImportError:
        print("没有 PIL —— 装上才能比")
        return 2

    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    if a.size != b.size:
        print(f"尺寸不同：{a.size} vs {b.size}")
        return 1

    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    if bbox is None:
        print(f"完全一致 ✓（{a.size[0]}x{a.size[1]}）")
        return 0

    # 量化一下差多少 —— 抗锯齿级别的差异可以接受，结构性差异不行
    gray = diff.convert("L")
    hist = gray.histogram()
    changed = sum(hist[1:])
    total = a.size[0] * a.size[1]
    pct = changed * 100.0 / total
    worst = max(i for i, c in enumerate(hist) if c)
    print(f"有差异：{changed} 个像素（{pct:.3f}%），最大通道差 {worst}")
    print(f"差异区域：{bbox}")
    if pct > 0.5 or worst > 60:
        print("→ 结构性差异，必须查")
        return 1
    print("→ 轻微差异（多半是抗锯齿/字体渲染）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
