"""质量闸门 —— 一道命令跑完全部守门检查。

为什么要有这个
--------------
阶段一建了好几个守门工具（接口对齐、未定义名、规格一致性、错误体统一），
但**散在各处**，没人会记得每次都跑一遍 —— 守门工具不常跑等于没有。

所以把它们收进一个入口：

    python core/scripts/gate.py            # 全跑
    python core/scripts/gate.py --fast     # 跳过慢的（单测）
    python core/scripts/gate.py --list     # 只列出有哪些检查

每一项都给出**独立可执行**的命令 —— 哪一项挂了、怎么单独重跑，
输出里直接写清楚。CI 与本地 git hook 都调这个。

各项的作用
----------
| 检查 | 挡住的回归 |
|---|---|
| 未定义名 | `NameError` → 500（曾经真的有两个接口 500） |
| 接口对齐 | 前端调了后端没有 → 运行时 404 |
| 规格一致 | 规格文档与代码脱节 → 照文档重写会错 |
| 错误体统一 | 错误响应形状又分裂成好几种 |
| 全量单测 | 任何行为回归 |

退出码 0 = 全过；非 0 = 有项目没过（CI 直接红）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent

PY = sys.executable


def _run(label: str, args: list[str], cwd: Path) -> tuple[bool, str]:
    print(f"\n{'=' * 68}")
    print(f"▶ {label}")
    print(f"  $ {' '.join(args)}")
    print("=" * 68)
    t0 = time.time()
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    dt = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    # pytest 的进度行在 stderr；只回显最后一段，避免刷屏
    tail = "\n".join(out.strip().splitlines()[-25:])
    if tail:
        print(tail)
    ok = proc.returncode == 0
    print(f"  → {'✅ 通过' if ok else '❌ 失败'}（{dt:.1f}s）")
    return ok, " ".join(args)


def main() -> int:
    ap = argparse.ArgumentParser(description="Oryx 质量闸门")
    ap.add_argument("--fast", action="store_true", help="跳过全量单测（慢）")
    ap.add_argument("--list", action="store_true", help="只列出检查项")
    args = ap.parse_args()

    checks: list[tuple[str, list[str], Path]] = [
        (
            "未定义名（会变成运行时 500）",
            [PY, "-m", "pytest", "tests/test_no_undefined_names.py", "-q",
             "-p", "no:warnings", "-p", "no:cacheprovider", "--tb=short"],
            CORE,
        ),
        (
            "接口对齐（前端调了后端没有 → 404）",
            [PY, "scripts/check_api_coverage.py"],
            CORE,
        ),
        (
            "规格文档与代码一致",
            [PY, "-m", "pytest", "tests/test_interface_spec_v2.py", "-q",
             "-p", "no:warnings", "-p", "no:cacheprovider", "--tb=short"],
            CORE,
        ),
        (
            "错误响应体统一",
            [PY, "-m", "pytest", "tests/test_error_contract.py", "-q",
             "-p", "no:warnings", "-p", "no:cacheprovider", "--tb=short"],
            CORE,
        ),
        (
            # 前端模块的 import 断链。
            # ESM 在**链接期**解析导入 —— 导一个不存在的名字，
            # 浏览器报 SyntaxError，**整个模块都不加载**，
            # 而且不报任何错到界面上（模块直接不执行）。
            # 实测过：把一个函数搬了家忘了改导入，
            # 结果顶栏的交互模式/人设/风格/模式/权限全都不绑定，
            # 界面看着正常，一点就废。这种错只能靠脚本盯。
            "前端模块 import 不断链",
            [PY, "tests/check_web_imports.py"],
            CORE,
        ),
    ]
    if not args.fast:
        checks.append(
            (
                "全量单测",
                [PY, "-m", "pytest", "-q", "-p", "no:warnings",
                 "-p", "no:cacheprovider", "--tb=short"],
                CORE,
            )
        )

    if args.list:
        for label, cmd, _cwd in checks:
            print(f"· {label}\n    {' '.join(cmd)}")
        return 0

    results: list[tuple[str, bool]] = []
    for label, cmd, cwd in checks:
        ok, _ = _run(label, cmd, cwd)
        results.append((label, ok))

    print(f"\n{'=' * 68}")
    print("闸门结果")
    print("=" * 68)
    for label, ok in results:
        print(f"  {'✅' if ok else '❌'}  {label}")
    failed = [l for l, ok in results if not ok]
    print()
    if failed:
        print(f"❌ {len(failed)} 项没过")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
