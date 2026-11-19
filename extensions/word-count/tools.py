"""字数统计工具的实现。

这是**实现层** —— 清单（oryx.json）里声明了 schema 与风险等级，
这里只提供函数。

约定：函数返回 {"ok": bool, "content": str, "meta": dict}。
"""


def count_text(text: str = "", file: str = "") -> dict:
    """统计文本或文件的字数、字符数、行数。"""
    if not text and file:
        try:
            with open(file, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            return {"ok": False, "content": f"读不了 {file}：{exc}", "meta": {}}
    chars = len(text)
    words = len(text.split())
    lines = len(text.splitlines()) if text else 0
    # 中文按字算（没有空格分词），西文按词算 —— 两个都给
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return {
        "ok": True,
        "content": f"{chars} 字符 / {words} 词 / {cjk} 汉字 / {lines} 行",
        "meta": {"chars": chars, "words": words, "cjk": cjk, "lines": lines},
    }
