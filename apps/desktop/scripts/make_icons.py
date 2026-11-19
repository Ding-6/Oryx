"""生成 Oryx 应用图标（PNG + ICO）。运行一次即可，产物在 src-tauri/icons/。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src-tauri" / "icons"

BG = (14, 16, 20, 255)
ACCENT = (91, 140, 255, 255)
ACCENT_DIM = (36, 48, 74, 255)


def draw_icon(size: int = 512) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = size // 5
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    cx = cy = size / 2
    outer = size * 0.30
    inner = size * 0.17
    draw.ellipse([cx - outer, cy - outer, cx + outer, cy + outer], fill=ACCENT)
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=BG)

    # 右下角一个小点，做成"正在运转"的意象
    dot_r = size * 0.055
    dx, dy = cx + size * 0.21, cy + size * 0.21
    draw.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r], fill=ACCENT_DIM)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = draw_icon(512)
    base.save(OUT / "icon.png")
    for name, size in [("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256), ("icon-256.png", 256)]:
        base.resize((size, size), Image.LANCZOS).save(OUT / name)
    base.resize((256, 256), Image.LANCZOS).save(
        OUT / "icon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("已生成图标：", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
