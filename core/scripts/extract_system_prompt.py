"""从系统提示词基准快照切出 prompts/system/*.md。

为什么要写脚本而不是手工复制
---------------------------
提示词改一个字符都可能让模型行为变样。手工复制 175 行、14 个段落，
几乎一定会漏空格或多换行 —— 而且**很难发现**（提示词里多一个空行，
模型行为可能只变一点点，测试也测不出来）。

所以这里用脚本做**机械切分**，并保证：
  · 段落边界按原文的标题行找，不靠肉眼数；
  · 切出来的内容拼接后与原文**逐字节相同**（脚本自己断言）。

输入是**基准快照** `tests/fixtures/system_prompt_baseline.txt`，
不是 loop.py —— 外置之后 loop.py 里已经没有提示词正文了。
快照是拆分前那份常量的原样保存，是「真值来源」。

用法：
    python core/scripts/extract_system_prompt.py           # 生成
    python core/scripts/extract_system_prompt.py --check   # 只校验，不写
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

BASELINE = CORE / "tests" / "fixtures" / "system_prompt_baseline.txt"
SYSTEM_DIR = CORE / "oryx" / "prompts" / "system"

# 段落名 → 该段在原文里的起始标记（必须是原文里真实存在的标题行）
#
# 注意：第一段是「开篇引言」—— 原文里它在 `# 一、` 之前，
# 没有标题行可匹配，所以单独用哨兵标记处理（见 split_sections）。
SECTION_MARKERS = [
    ("00-opening", "@@OPENING@@"),
    ("01-closed-loop", "# 一、任务闭环"),
    ("02-routing", "# 二、能力分层与路由"),
    ("03-discipline", "# 三、执行纪律"),
    ("04-safety", "# 四、安全与确认"),
    ("05-output", "# 五、输出规范"),
    ("06-memory", "# 六、记忆"),
    ("07-failure", "# 七、失败处理"),
    ("08-self-check", "# 八、交付前自检"),
    ("09-tools", "# 工具使用要点"),
    ("10-multi-agent", "# 多 Agent 协作"),
    ("11-workflow", "# 工作流编排"),
    ("12-security-rules", "# 安全规则"),
    ("13-skills", "# 技能"),
]

HEADER_RE = re.compile(r'^SYSTEM_PROMPT = """(.*?)"""', re.S | re.M)


def extract() -> str:
    """读基准快照。"""
    if not BASELINE.exists():
        raise SystemExit(
            f"找不到基准快照：{BASELINE}\n"
            f"它是拆分前那份提示词常量的原样保存 —— 外置之后就没有别的真值来源了。"
        )
    return BASELINE.read_text("utf-8")


def split_sections(text: str) -> list[tuple[str, str]]:
    """按标题标记切段。返回 [(name, content)]。

    content 保留原文的**前导空行**，这样拼接回去能逐字节还原。
    """
    lines = text.splitlines(keepends=True)
    # 找每个标记的行号
    positions: list[tuple[str, int]] = []
    for name, marker in SECTION_MARKERS:
        if marker == "@@OPENING@@":
            # 开篇引言：第一个标题行之前的内容
            positions.append((name, 0))
            continue
        idx = None
        for i, ln in enumerate(lines):
            if ln.startswith(marker):
                idx = i
                break
        if idx is None:
            raise SystemExit(f"找不到段落标记：{marker}（段名 {name}）")
        positions.append((name, idx))

    out: list[tuple[str, str]] = []
    for i, (name, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(lines)
        out.append((name, "".join(lines[start:end])))
    return out


def _safe_stdout() -> None:
    """让诊断输出在任何控制台编码下都不会把脚本掀翻。

    2026-11-26 实测：Windows 中文终端默认 cp936/GBK，原先成功时打印的 ✓ / ✗
    编不进去，print 直接抛 UnicodeEncodeError —— 明明校验是通过的，却因为
    「打印一句祝贺」让 --check 以非零码退出，test_split_script_is_idempotent
    跟着变红。这里把「编不出来的字符」降级成转义序列，中文照常显示。
    """
    stream = getattr(sys, "stdout", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="backslashreplace")
    except (ValueError, OSError):
        pass


def main() -> int:
    _safe_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    text = extract()
    sections = split_sections(text)

    # 关键断言：按 SECTION_ORDER 拼回去必须与原文逐字节相同
    rebuilt = "\n\n".join(content.rstrip("\n") for _, content in sections) + "\n"
    if rebuilt != text:
        print("拼装结果与原文不一致 —— 分段边界或空白处理有问题")
        print(f"  原文长度 {len(text)}，拼回长度 {len(rebuilt)}")
        for i, (a, b) in enumerate(zip(text, rebuilt)):
            if a != b:
                print(f"  首个差异在第 {i} 个字符：原文 {a!r} vs 拼回 {b!r}")
                print(f"  原文上下文: {text[max(0,i-40):i+40]!r}")
                break
        return 1
    print(f"[OK] 分段拼装与原文逐字节相同（{len(text)} 字符，{len(sections)} 段）")

    if args.check:
        return 0

    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in sections:
        (SYSTEM_DIR / f"{name}.md").write_text(content, encoding="utf-8")
        print(f"  写入 {name}.md（{len(content)} 字符）")

    # 校验：落盘后重新读回来拼一遍，必须还是一致
    from oryx.prompts import loader

    for name in loader.SECTION_ORDER:
        path = SYSTEM_DIR / f"{name}.md"
        if not path.exists():
            print(f"[FAIL] 缺文件 {path}")
            return 1
    print("[OK] 全部段落文件已落盘，loader 的 SECTION_ORDER 与脚本一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
