"""对比度体检 —— 找出暗色/浅色主题下**看不清的文字**。

为什么要有这个
--------------
视觉问题里最难发现的就是「颜色偏了一点」。
肉眼看截图，「好像有点淡」但说不清淡在哪、有多淡 ——
而 WCAG 给的是数字：正文对比度要 ≥ 4.5:1，大字 ≥ 3:1。

这个脚本走一遍每个视图页，把所有可见的文字节点算一遍，
把不达标的挑出来 —— 顺便定位到是哪个选择器、哪一行。

跑法：
    python scripts/check_contrast.py            # 明暗都查
    python scripts/check_contrast.py dark       # 只查暗色
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

VIEWS = ("chat", "work", "market", "automations", "workflows", "butlers", "persona")

# WCAG
AA_TEXT = 4.5
AA_LARGE = 3.0
LARGE_PX = 18.0
LARGE_BOLD_PX = 14.0


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready

    themes = sys.argv[1:] or ["light", "dark"]
    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)
    srv = start_server(8783, home, "shot-token")
    try:
        wait_ready(8783, timeout=40)
        time.sleep(1.5)
        with sync_playwright() as p:
            b = None
            for ch in ("chrome", "msedge", None):
                try:
                    b = p.chromium.launch(
                        headless=True, channel=ch,
                        args=["--no-sandbox", "--disable-gpu"],
                    )
                    break
                except Exception:
                    continue
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            pg = ctx.new_page()
            pg.goto("http://127.0.0.1:8783/", wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)

            total_bad = 0
            for theme in themes:
                pg.evaluate(
                    """(t) => {
                        document.documentElement.setAttribute('data-theme', t);
                    }""",
                    theme,
                )
                pg.wait_for_timeout(700)
                for v in VIEWS:
                    if v == "chat":
                        pg.evaluate(
                            "() => document.querySelector('.navitem.newchat').click()"
                        )
                    else:
                        pg.evaluate(
                            "(v) => document.querySelector('.navitem[data-view=\"' + v + '\"]').click()",
                            v,
                        )
                    pg.wait_for_timeout(1000)
                    bad = pg.evaluate(
                        """([aaText, aaLarge, largePx, largeBoldPx]) => {
                            function lum(rgb) {
                                const c = rgb.map(x => {
                                    x = x / 255;
                                    return x <= 0.03928 ? x / 12.92
                                        : Math.pow((x + 0.055) / 1.055, 2.4);
                                });
                                return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
                            }
                            function parse(s) {
                                const m = s.match(/rgba?\\(([^)]+)\\)/);
                                if (!m) return null;
                                const parts = m[1].split(',').map(x => parseFloat(x));
                                return {
                                    rgb: parts.slice(0, 3),
                                    a: parts.length > 3 ? parts[3] : 1,
                                };
                            }
                            function over(fg, bg) {
                                // 半透明前景合成到背景上
                                const a = fg.a;
                                return fg.rgb.map((c, i) => c * a + bg[i] * (1 - a));
                            }
                            /* 从渐变里取一个代表色 —— 取第一个 color-stop。
                               页面底现在是 `linear-gradient(...)`，只看
                               background-color 会一路往上找到纯白，
                               把「白字在紫底上」误判成「白字在白底上」。 */
                            function gradColor(img) {
                                if (!img || img === 'none') return null;
                                const m = img.match(/rgba?\\(([^)]+)\\)/);
                                if (!m) return null;
                                const parts = m[1].split(',').map(x => parseFloat(x));
                                return { rgb: parts.slice(0, 3), a: parts.length > 3 ? parts[3] : 1 };
                            }

                            function bgOf(el) {
                                let node = el;
                                while (node && node !== document.documentElement) {
                                    const cs = getComputedStyle(node);
                                    // 先看渐变：它盖在 background-color 上面，
                                    // 实际呈现的是渐变色。
                                    const g = gradColor(cs.backgroundImage);
                                    if (g && g.a > 0.01) {
                                        if (g.a < 1) {
                                            const under = bgOf(node.parentElement) || [255,255,255];
                                            return over(g, under);
                                        }
                                        return g.rgb;
                                    }
                                    const c = parse(cs.backgroundColor);
                                    if (c && c.a > 0.01) {
                                        if (c.a < 1) {
                                            const under = bgOf(node.parentElement) || [255,255,255];
                                            return over(c, under);
                                        }
                                        return c.rgb;
                                    }
                                    node = node.parentElement;
                                }
                                return [255, 255, 255];
                            }
                            const out = [];
                            const seen = new Set();
                            for (const el of document.querySelectorAll('#page *, #center *, #side *, #rail *')) {
                                if (!el.offsetParent && el.tagName !== 'BODY') continue;
                                const text = [...el.childNodes]
                                    .filter(n => n.nodeType === 3)
                                    .map(n => n.textContent.trim())
                                    .join('');
                                if (!text) continue;
                                const cs = getComputedStyle(el);
                                const fg = parse(cs.color);
                                if (!fg) continue;
                                const bg = bgOf(el);
                                const color = fg.a < 1 ? over(fg, bg) : fg.rgb;
                                const l1 = lum(color), l2 = lum(bg);
                                const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
                                const size = parseFloat(cs.fontSize);
                                const weight = parseInt(cs.fontWeight) || 400;
                                const large = size >= largePx || (size >= largeBoldPx && weight >= 700);
                                const need = large ? aaLarge : aaText;
                                if (ratio < need) {
                                    const key = cs.color + '|' + el.className + '|' + el.tagName;
                                    if (seen.has(key)) continue;
                                    seen.add(key);
                                    out.push({
                                        sel: el.tagName.toLowerCase() +
                                             (el.className ? '.' + String(el.className).trim().split(/\\s+/).join('.') : ''),
                                        color: cs.color,
                                        bg: 'rgb(' + bg.map(Math.round).join(',') + ')',
                                        size: size,
                                        ratio: Math.round(ratio * 100) / 100,
                                        need: need,
                                        sample: text.slice(0, 18),
                                    });
                                }
                            }
                            return out;
                        }""",
                        [AA_TEXT, AA_LARGE, LARGE_PX, LARGE_BOLD_PX],
                    )
                    if bad:
                        total_bad += len(bad)
                        print(f"── {theme} / {v} ──")
                        for b_ in sorted(bad, key=lambda x: x["ratio"]):
                            print(
                                f"  {b_['ratio']:>5}:1 (需 {b_['need']})  {b_['sel'][:52]:54}"
                                f" {b_['size']}px  {b_['color']} on {b_['bg']}  「{b_['sample']}」"
                            )
            print(f"\n合计不达标：{total_bad}")
            b.close()
    finally:
        stop_server(srv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
