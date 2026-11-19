"""多模态交付：HTML / PDF / 图片，以及「产物」的统一判定。

对齐 Marvis 的「动手能力 → 直接产出可用文件」，补齐 Office 三件套之外的
三种交付形态。设计取舍：

1. **只走 Python 库，不调 shell** —— shell 是 L2 危险级，每次要弹确认卡。
   让用户「导出一份 PDF」还要先点一次授权，是把交付能力做成了负担。
2. HTML 纯标准库：自包含、离线可打开、所有用户输入 HTML 转义，
   内联 SVG 画图，不引 CDN（本机 Agent 不能假设有网）。
3. 图片用 Pillow（本来就是 OCR 的硬依赖）；PDF 用 matplotlib 的 PdfPages
   （可选依赖，缺失时**明确报错**，绝不静默产出 0 字节的假文件）。
4. 产物判定按**结果元数据**（确实写了字节 + 带路径），不再按工具名硬编码 ——
   以后新增任何会落盘的工具都自动进产物面板。

四个写函数统一返回 ``{"ok", "path", "bytes", "summary", "kind"}``，
``bytes`` 是判定「真的落盘了」的凭据，前端产物面板据此收敛。
"""

from __future__ import annotations

import html
import math
import os
import time
from pathlib import Path
from typing import Any

MAX_SECTIONS = 200
MAX_ROWS = 200
MAX_POINTS = 60
MAX_TEXT_CHARS = 20_000

# 产物路径在工具结果里可能叫这些名字（按优先级找）。
ARTIFACT_PATH_KEYS = ("path", "image_path", "output", "file", "dest", "dest_path")


def _prepare(path: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _result(target: Path, summary: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "path": str(target),
        "bytes": target.stat().st_size if target.exists() else 0,
        "summary": summary,
        "kind": kind,
        **extra,
    }


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value)[:limit] if value is not None else ""


def _numbers(values: Any) -> tuple[list[str], list[float]]:
    """把模型给的数值清洗成 (标签, 数值)。非法项跳过而不是让整份图崩掉。"""
    labels: list[str] = []
    nums: list[float] = []
    for index, item in enumerate(values or []):
        if len(nums) >= MAX_POINTS:
            break
        if isinstance(item, dict):
            label = str(item.get("label") or f"项{index + 1}")
            raw = item.get("value", item.get("values"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            label, raw = str(item[0]), item[1]
        else:
            label, raw = f"项{index + 1}", item
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        labels.append(label[:24])
        nums.append(number)
    return labels, nums


def _chart_data(section: dict[str, Any]) -> tuple[list[str], list[float], str]:
    chart = section.get("chart") if isinstance(section.get("chart"), dict) else {}
    labels = chart.get("labels") or []
    values = chart.get("values") or []
    pairs = list(zip(labels, values)) if isinstance(labels, list) and isinstance(values, list) else values
    clean_labels, nums = _numbers(pairs)
    ctype = str(chart.get("type") or "bar").lower()
    if ctype not in ("bar", "line", "pie"):
        ctype = "bar"
    return clean_labels, nums, ctype


# ---------------------------------------------------------------- SVG 图表
def _svg_chart(labels: list[str], values: list[float], ctype: str = "bar",
               title: str = "", width: int = 680, height: int = 280) -> str:
    """生成内联 SVG 图表片段（HTML 与 .svg 文件共用同一份绘制逻辑）。"""
    esc = lambda s: html.escape(str(s), quote=True)
    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
             f'role="img" aria-label="{esc(title or "数据图表")}">']
    parts.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    if title:
        parts.append(f'<text x="24" y="30" font-size="16" font-weight="600" '
                     f'fill="#1f2430" font-family="Microsoft YaHei,sans-serif">{esc(title)}</text>')
    top, bottom, left, right = 56, 226, 56, width - 24
    if not values:
        parts.append(f'<text x="24" y="130" font-size="13" fill="#8a93a6">没有可用于绘图的数据</text>')
        parts.append("</svg>")
        return "".join(parts)

    lo = min(0.0, min(values))
    hi = max(values)
    span = (hi - lo) or 1.0
    plot_w = right - left
    plot_h = bottom - top

    # 网格线与刻度
    for i in range(5):
        y = top + plot_h * i / 4
        tick = hi - span * i / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
                     f'stroke="#e8ecf3" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'font-size="10" fill="#8a93a6">{tick:.4g}</text>')
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#c9d2e0"/>')

    def _y(v: float) -> float:
        return bottom - (v - lo) / span * plot_h

    if ctype == "bar":
        slot = plot_w / max(len(values), 1)
        bar_w = min(slot * 0.62, 54)
        for i, (label, value) in enumerate(zip(labels, values)):
            x = left + slot * i + (slot - bar_w) / 2
            y = _y(value)
            h = max(bottom - y, 1.0)
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                         f'rx="3" fill="#4a6cf7" opacity="{0.72 + 0.28 * i / max(len(values), 1):.2f}"/>')
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" '
                         f'font-size="10" fill="#4a5568">{value:.4g}</text>')
            parts.append(f'<text x="{left + slot * (i + 0.5):.1f}" y="{bottom + 18}" text-anchor="middle" '
                         f'font-size="10" fill="#5c667a">{esc(label)}</text>')
    elif ctype == "line":
        points = []
        for i, value in enumerate(values):
            x = left + (plot_w * i / max(len(values) - 1, 1)) if len(values) > 1 else left + plot_w / 2
            y = _y(value)
            points.append(f"{x:.1f},{y:.1f}")
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#4a6cf7"/>')
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#4a6cf7" stroke-width="2"/>')
        for i, label in enumerate(labels):
            x = left + (plot_w * i / max(len(labels) - 1, 1)) if len(labels) > 1 else left + plot_w / 2
            parts.append(f'<text x="{x:.1f}" y="{bottom + 18}" text-anchor="middle" '
                         f'font-size="10" fill="#5c667a">{esc(label)}</text>')
    else:  # pie
        total = sum(abs(v) for v in values) or 1.0
        cx, cy, radius = width / 2, (top + bottom) / 2, min(plot_h / 2 - 8, 92)
        palette = ("#4a6cf7", "#22b07d", "#f2a33c", "#e05d6f", "#8a63d2", "#33a9c9")
        angle = -math.pi / 2
        for i, (label, value) in enumerate(zip(labels, values)):
            sweep = 2 * math.pi * abs(value) / total
            x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            angle += sweep
            x2, y2 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            large = 1 if sweep > math.pi else 0
            parts.append(f'<path d="M {cx:.1f} {cy:.1f} L {x1:.1f} {y1:.1f} '
                         f'A {radius:.1f} {radius:.1f} 0 {large} 1 {x2:.1f} {y2:.1f} Z" '
                         f'fill="{palette[i % len(palette)]}" stroke="#fff" stroke-width="1.5"/>')
            lx = 24 + (i % 2) * 150
            ly = 60 + (i // 2) * 18
            if ly < bottom:
                parts.append(f'<rect x="{lx}" y="{ly - 9}" width="10" height="10" rx="2" '
                             f'fill="{palette[i % len(palette)]}"/>')
                parts.append(f'<text x="{lx + 16}" y="{ly}" font-size="10" fill="#5c667a">'
                             f'{esc(label)} {value:.4g}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- HTML
_HTML_CSS = """
:root{--bg:#f5f7fb;--card:#fff;--ink:#1f2430;--muted:#6b7488;--line:#e5eaf2;--accent:#4a6cf7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 "Microsoft YaHei","Segoe UI",sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:40px 24px 72px}
header{background:linear-gradient(135deg,#4a6cf7,#7b5cf0);color:#fff;border-radius:16px;padding:32px 36px;box-shadow:0 8px 28px rgba(74,108,247,.18)}
h1{margin:0 0 8px;font-size:26px;letter-spacing:.5px}
header p{margin:0;opacity:.9;font-size:14px}
.meta{margin-top:14px;font-size:12px;opacity:.75}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px 28px;margin-top:20px;box-shadow:0 2px 10px rgba(31,36,48,.04)}
h2{margin:0 0 14px;font-size:19px;border-left:4px solid var(--accent);padding-left:12px}
p{margin:0 0 10px;color:#394152}
ul{margin:8px 0 0;padding-left:22px}li{margin:4px 0;color:#394152}
table{width:100%;border-collapse:collapse;margin-top:14px;font-size:13.5px}
th{background:#f0f3fb;color:#394152;text-align:left;font-weight:600}
th,td{border:1px solid var(--line);padding:9px 12px}
tr:nth-child(even) td{background:#fafbfe}
.note{background:#fff8e8;border-left:3px solid #f2a33c;padding:10px 14px;border-radius:6px;margin-top:14px;color:#7a5a1e}
figure{margin:16px 0 0}figure svg{width:100%;height:auto;border:1px solid var(--line);border-radius:10px}
footer{text-align:center;color:var(--muted);font-size:12px;margin-top:32px}
@media print{body{background:#fff}section{box-shadow:none;break-inside:avoid}}
"""


def write_html(
    path: str,
    title: str,
    sections: list[dict[str, Any]] | None = None,
    subtitle: str = "",
) -> dict[str, Any]:
    """生成一份自包含的 HTML 报告（离线可打开，无需 CDN）。

    sections 每项可含：heading / paragraphs / bullets / table{headers,rows}
    / chart{type,labels,values} / note。
    """
    target = _prepare(path)
    esc = lambda s: html.escape(_text(s), quote=True)
    out: list[str] = [
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(title)}</title><style>{_HTML_CSS}</style></head><body><div class=\"wrap\">",
        f"<header><h1>{esc(title)}</h1>",
        f"<p>{esc(subtitle)}</p>" if subtitle else "",
        f'<div class="meta">Oryx 生成 · {time.strftime("%Y-%m-%d %H:%M")}</div></header>',
    ]
    count = 0
    for section in (sections or [])[:MAX_SECTIONS]:
        if not isinstance(section, dict):
            continue
        out.append("<section>")
        heading = section.get("heading")
        if heading:
            out.append(f"<h2>{esc(heading)}</h2>")
        for paragraph in section.get("paragraphs") or []:
            out.append(f"<p>{esc(paragraph)}</p>")
        bullets = section.get("bullets") or []
        if bullets:
            out.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in bullets) + "</ul>")
        table = section.get("table")
        if isinstance(table, dict):
            headers = table.get("headers") or []
            rows = (table.get("rows") or [])[:MAX_ROWS]
            out.append("<table>")
            if headers:
                out.append("<thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr></thead>")
            out.append("<tbody>" + "".join(
                "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                for row in rows if isinstance(row, (list, tuple))
            ) + "</tbody></table>")
        labels, values, ctype = _chart_data(section)
        chart = section.get("chart") if isinstance(section.get("chart"), dict) else {}
        if values or chart:
            out.append(f"<figure>{_svg_chart(labels, values, ctype, _text(chart.get('title') or heading or ''))}</figure>")
        if section.get("note"):
            out.append(f'<div class="note">{esc(section["note"])}</div>')
        out.append("</section>")
        count += 1
    out.append("<footer>由 Oryx 在本机生成 · 内容未经人工复核</footer></div></body></html>")
    target.write_text("".join(out), encoding="utf-8")
    return _result(target, f"生成 HTML 报告：{count} 个小节", "html", sections=count)


# ---------------------------------------------------------------- PDF
def _font_path() -> str:
    """找一份能显示中文的 TrueType 字体。

    优先 simhei.ttf（纯 TTF，matplotlib / Pillow 都认）；
    .ttc 是字体集合，matplotlib 的 FontProperties 对它的支持不稳，故排后面。
    """
    windir = os.environ.get("WINDIR", "C:/Windows")
    fonts = Path(windir) / "Fonts"
    for name in ("simhei.ttf", "msyh.ttf", "simsun.ttf", "msyh.ttc"):
        candidate = fonts / name
        if candidate.exists():
            return str(candidate)
    return ""


def _display_width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _wrap(text: str, units: int = 76) -> list[str]:
    """按显示宽度折行（中文算 2 个单位），避免长段落跑出纸面。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        if _display_width(current + ch) > units:
            lines.append(current)
            current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    return lines or [""]


def _pdf_text_page(pdf: Any, heading: str, paragraphs: list[str], bullets: list[str],
                   font: Any, subtitle: str = "") -> None:
    from matplotlib.figure import Figure

    fig = Figure(figsize=(8.27, 11.69), facecolor="white")
    fig.text(0.08, 0.94, heading, fontproperties=font, fontsize=19, color="#1f2430", va="top")
    fig.text(0.08, 0.905, subtitle, fontproperties=font, fontsize=10, color="#6b7488", va="top")
    y = 0.86
    for paragraph in paragraphs:
        for line in _wrap(paragraph):
            if y < 0.09:
                break
            fig.text(0.08, y, line, fontproperties=font, fontsize=10.5, color="#394152", va="top")
            y -= 0.021
        y -= 0.012
    for bullet in bullets:
        for index, line in enumerate(_wrap(bullet, 72)):
            if y < 0.09:
                break
            # 用 ASCII 短横而不是 U+2022 •：SimHei 没有这个字形，
            # matplotlib 会逐页刷一条 "Glyph missing" 警告。
            prefix = "- " if index == 0 else "  "
            fig.text(0.09, y, prefix + line, fontproperties=font, fontsize=10.5, color="#394152", va="top")
            y -= 0.021
    pdf.savefig(fig)


def _pdf_table_page(pdf: Any, heading: str, headers: list[str], rows: list[Any], font: Any) -> None:
    from matplotlib.figure import Figure

    fig = Figure(figsize=(8.27, 11.69), facecolor="white")
    fig.text(0.08, 0.94, heading, fontproperties=font, fontsize=17, color="#1f2430", va="top")
    data = [[_text(c) for c in row] for row in rows[:MAX_ROWS] if isinstance(row, (list, tuple))]
    col_labels = [_text(h) for h in headers] if headers else [f"列{i + 1}" for i in range(len(data[0]) if data else 1)]
    rows_to_show = min(len(data), 28)
    ax = fig.add_axes([0.07, 0.94 - min(0.78, 0.035 * (rows_to_show + 2)), 0.86,
                       min(0.78, 0.035 * (rows_to_show + 2))])
    ax.axis("off")
    table = ax.table(cellText=data[:rows_to_show], colLabels=col_labels, cellLoc="left",
                     loc="upper center", colWidths=[1 / max(len(col_labels), 1)] * len(col_labels))
    table.auto_set_font_size(False)
    for cell in table.get_celld().values():
        cell.set_text_props(fontproperties=font, fontsize=8.5)
        cell.set_edgecolor("#dfe5ef")
        cell.set_linewidth(0.6)
    pdf.savefig(fig)


def _pdf_chart_page(pdf: Any, heading: str, labels: list[str], values: list[float],
                    ctype: str, font: Any) -> None:
    from matplotlib.figure import Figure

    fig = Figure(figsize=(8.27, 11.69), facecolor="white")
    fig.text(0.08, 0.94, heading, fontproperties=font, fontsize=17, color="#1f2430", va="top")
    ax = fig.add_axes([0.12, 0.32, 0.78, 0.5])
    if ctype == "pie":
        ax.pie([abs(v) for v in values], labels=labels, autopct="%1.1f%%",
               textprops={"fontproperties": font, "fontsize": 9})
    elif ctype == "line":
        ax.plot(range(len(values)), values, marker="o", color="#4a6cf7", linewidth=2)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontproperties=font, fontsize=8, rotation=20)
    else:
        ax.bar(range(len(values)), values, color="#4a6cf7", alpha=0.85, width=0.62)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontproperties=font, fontsize=8, rotation=20)
        for i, value in enumerate(values):
            ax.text(i, value, f"{value:.4g}", ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    for label in ax.get_yticklabels():
        label.set_fontsize(8)
    pdf.savefig(fig)


def write_pdf(
    path: str,
    title: str,
    sections: list[dict[str, Any]] | None = None,
    subtitle: str = "",
) -> dict[str, Any]:
    """生成 PDF 报告。依赖 matplotlib（可选）；缺失时返回明确错误。

    为什么用 matplotlib 而不是手写 PDF 字节流：中文必须嵌入字体轮廓，
    手写要处理 TTF 子集化与 CID 映射，容易产出「能打开但全是方框」的假文件。
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.font_manager import FontProperties
    except Exception as exc:  # pragma: no cover - 取决于部署环境
        return {"ok": False, "error": f"生成 PDF 需要 matplotlib：{type(exc).__name__}: {exc}"}

    target = _prepare(path)
    font_path = _font_path()
    font = FontProperties(fname=font_path) if font_path else FontProperties()
    pages = 0
    try:
        with PdfPages(str(target)) as pdf:
            _pdf_text_page(pdf, _text(title) or "未命名报告", [], [], font,
                           subtitle=f"{_text(subtitle)}  ·  Oryx 生成  ·  {time.strftime('%Y-%m-%d %H:%M')}")
            pages += 1
            for section in (sections or [])[:MAX_SECTIONS]:
                if not isinstance(section, dict):
                    continue
                heading = _text(section.get("heading") or title or "未命名")
                paragraphs = [_text(p) for p in section.get("paragraphs") or []]
                bullets = [_text(b) for b in section.get("bullets") or []]
                _pdf_text_page(pdf, heading, paragraphs, bullets, font)
                pages += 1
                table = section.get("table")
                if isinstance(table, dict) and (table.get("rows") or table.get("headers")):
                    _pdf_table_page(pdf, heading + " · 数据表",
                                    list(table.get("headers") or []),
                                    list(table.get("rows") or []), font)
                    pages += 1
                labels, values, ctype = _chart_data(section)
                if values:
                    _pdf_chart_page(pdf, heading + " · 图表", labels, values, ctype, font)
                    pages += 1
    except Exception as exc:
        # 失败就把半成品删掉 —— 留一个打不开的 .pdf 比明确报错更糟
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "error": f"PDF 生成失败：{type(exc).__name__}: {exc}"}
    return _result(target, f"生成 PDF：封面 + {pages - 1} 页正文", "pdf", pages=pages)


# ---------------------------------------------------------------- 图片
def _pil_font(size: int) -> Any:
    from PIL import ImageFont

    font_path = _font_path()
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def write_svg(path: str, chart: dict[str, Any], title: str = "") -> dict[str, Any]:
    """生成独立 .svg 文件（纯文本，浏览器/看图工具都能直接打开）。"""
    target = _prepare(path)
    labels, values, ctype = _chart_data({"chart": chart})
    heading = _text(title or chart.get("title") or "数据图表")
    body = _svg_chart(labels, values, ctype, heading, width=900, height=420)
    body = body.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1) if "xmlns=" not in body[:120] else body
    target.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + body, encoding="utf-8")
    return _result(target, f"生成 SVG 图表：{len(values)} 个数据点", "svg", points=len(values))


def write_image(path: str, chart: dict[str, Any] | None = None, title: str = "",
                width: int = 1200, height: int = 700) -> dict[str, Any]:
    """生成图表图片。.svg 结尾走矢量输出，其余走 Pillow 画 PNG。"""
    spec = chart if isinstance(chart, dict) else {}
    if str(path).lower().endswith(".svg"):
        return write_svg(path, spec, title)
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": f"生成图片需要 Pillow：{type(exc).__name__}: {exc}"}

    target = _prepare(path)
    labels, values, ctype = _chart_data({"chart": spec})
    W, H = max(400, min(int(width), 3000)), max(300, min(int(height), 2400))
    image = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(image)
    heading = _text(title or spec.get("title") or "数据图表")
    draw.text((36, 26), heading, fill="#1f2430", font=_pil_font(26))
    draw.text((36, 66), f"Oryx 生成 · {time.strftime('%Y-%m-%d %H:%M')}",
              fill="#8a93a6", font=_pil_font(13))
    left, right, top, bottom = 90, W - 40, 130, H - 90
    draw.line([(left, top), (left, bottom), (right, bottom)], fill="#c9d2e0", width=2)

    if not values:
        draw.text((left + 20, (top + bottom) // 2), "没有可用于绘图的数据",
                  fill="#8a93a6", font=_pil_font(16))
        image.save(str(target), format="PNG")
        return _result(target, "生成图片：无有效数据（已输出占位图）", "image", points=0)

    lo = min(0.0, min(values))
    hi = max(values)
    span = (hi - lo) or 1.0
    for i in range(5):
        y = top + (bottom - top) * i / 4
        draw.line([(left, y), (right, y)], fill="#eef1f7", width=1)
        draw.text((left - 12, y - 7), f"{hi - span * i / 4:.4g}", anchor="ra",
                  fill="#8a93a6", font=_pil_font(11))

    def _y(v: float) -> float:
        return bottom - (v - lo) / span * (bottom - top)

    palette = ["#4a6cf7", "#22b07d", "#f2a33c", "#e05d6f", "#8a63d2", "#33a9c9"]
    if ctype == "bar":
        slot = (right - left) / max(len(values), 1)
        bar_w = min(slot * 0.62, 70)
        for i, (label, value) in enumerate(zip(labels, values)):
            x = left + slot * i + (slot - bar_w) / 2
            y = _y(value)
            draw.rectangle([x, y, x + bar_w, bottom], fill=palette[i % len(palette)])
            draw.text((x + bar_w / 2, max(y - 20, top - 20)), f"{value:.4g}", anchor="ma",
                      fill="#4a5568", font=_pil_font(12))
            draw.text((left + slot * (i + 0.5), bottom + 12), label, anchor="ma",
                      fill="#5c667a", font=_pil_font(12))
    elif ctype == "line":
        points = []
        for i, value in enumerate(values):
            x = left + (right - left) * i / max(len(values) - 1, 1) if len(values) > 1 else (left + right) / 2
            points.append((x, _y(value)))
        if len(points) > 1:
            draw.line(points, fill="#4a6cf7", width=3)
        for x, y in points:
            draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill="#4a6cf7")
        for i, label in enumerate(labels):
            x = left + (right - left) * i / max(len(labels) - 1, 1) if len(labels) > 1 else (left + right) / 2
            draw.text((x, bottom + 12), label, anchor="ma", fill="#5c667a", font=_pil_font(12))
    else:  # pie
        radius = min((bottom - top) / 2 - 10, 190)
        cx, cy = (left + right) / 2, (top + bottom) / 2
        total = sum(abs(v) for v in values) or 1.0
        start = -90.0
        box = [cx - radius, cy - radius, cx + radius, cy + radius]
        for i, value in enumerate(values):
            sweep = 360.0 * abs(value) / total
            draw.pieslice(box, start, start + sweep, fill=palette[i % len(palette)], outline="white", width=2)
            start += sweep
        for i, label in enumerate(labels[:8]):
            draw.rectangle([right - 250, top + i * 26, right - 234, top + i * 26 + 14],
                           fill=palette[i % len(palette)])
            draw.text((right - 226, top + i * 26), f"{label} {values[i]:.4g}",
                      fill="#5c667a", font=_pil_font(12))
    image.save(str(target), format="PNG")
    return _result(target, f"生成 {ctype} 图：{len(values)} 个数据点", "image", points=len(values))


# ---------------------------------------------------------------- 产物判定
def extract_artifact_path(tool: str, result: dict[str, Any]) -> str:
    """从工具结果里判定「这次真的产出了一个可交付文件」。

    判定凭据是**结果元数据**：``ok`` 为真、结果里带 ``bytes``（确实写了字节）、
    且带一个路径字段。这比前端硬编码 ``FILE_TOOLS`` 可靠 —— 新增落盘工具
    不用改前端，只读工具（xlsx_read / fs_read）也不会被误收进产物面板。
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return ""
    content = result.get("content")
    # 工具实现普遍包一层 {"ok": true, "content": {...}}；
    # 也允许直接把产物字典放在 result 里。
    payload = content if isinstance(content, dict) else result
    if payload.get("bytes") is None:
        return ""
    for key in ARTIFACT_PATH_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
