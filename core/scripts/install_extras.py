"""把可选依赖装到 D 盘。

为什么需要这个脚本：C 盘经常被塞满，而 OCR、浏览器这些可选依赖
连同它们的模型权重加起来有 1GB 上下。默认 `pip install` 会：

  · 把包装进解释器所在盘（这里已经是 D 盘，问题不大）
  · 把下载缓存放 %LOCALAPPDATA%\\pip\\cache（C 盘，72MB 起）
  · 让 Playwright 把 Chromium 下到 %LOCALAPPDATA%\\ms-playwright（C 盘，**701MB**）

所以这个脚本做三件事：
  1. 把 pip 缓存指到 D 盘
  2. 把 PLAYWRIGHT_BROWSERS_PATH 指到 D 盘（Chromium 就落这儿）
  3. 装完打印实际占用了多少

用法：
    python core/scripts/install_extras.py            # 装 OCR + 浏览器
    python core/scripts/install_extras.py --ocr      # 只装 OCR
    python core/scripts/install_extras.py --browser  # 只装浏览器
    python core/scripts/install_extras.py --dry-run  # 只看要装什么
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 数据根目录 —— 与 Rust 侧 paths.rs、Python 侧 config.app_dir() 保持一致
DATA_ROOT = Path(os.environ.get("OYX_DATA_ROOT") or "D:/OryxData")
CACHE_DIR = DATA_ROOT / "cache"

# OCR 引擎：RapidOCR（ONNX，端侧运行，不联网）
OCR_PACKAGE = "rapidocr-onnxruntime"
# 浏览器 Agent：Playwright。系统装了 Chrome/Edge 就不用下内置 Chromium，
# 但下一下更稳（回退路径）。
BROWSER_PACKAGE = "playwright"


def prepare_env() -> None:
    """把各类缓存指到 D 盘。**必须在 pip 启动前设好** —— pip 读的是进程环境。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pip_cache = CACHE_DIR / "pip"
    playwright = CACHE_DIR / "playwright"
    pip_cache.mkdir(parents=True, exist_ok=True)
    playwright.mkdir(parents=True, exist_ok=True)

    os.environ["PIP_CACHE_DIR"] = str(pip_cache)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright)
    # HuggingFace / torch 的权重缓存（转写、embedding 用）
    for key, sub in (
        ("HF_HOME", "huggingface"),
        ("HUGGINGFACE_HUB_CACHE", "huggingface/hub"),
        ("TRANSFORMERS_CACHE", "huggingface/transformers"),
        ("TORCH_HOME", "torch"),
    ):
        target = CACHE_DIR / sub
        target.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(target)

    print(f"缓存根目录：{CACHE_DIR}")
    print(f"  pip       → {pip_cache}")
    print(f"  playwright → {playwright}")


def dir_size(path: Path) -> int:
    """目录占用字节数。"""
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def human(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def pip_install(*packages: str) -> int:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages]
    print(f"\n$ {' '.join(cmd)}\n")
    return subprocess.call(cmd)


def playwright_install_browser() -> int:
    """下内置 Chromium。

    只在系统没有 Chrome/Edge 时才真的需要（browser.py 的回退路径）。
    但先检查一下 —— 已经装了系统浏览器就别浪费 700MB。

    **要下两个包**：Playwright 1.49 之后 headless 模式走的是**独立的**
    `chromium-headless-shell`，不是 `chromium` 目录里的 chrome.exe。
    只下 chromium 的话，`launch(headless=True)` 会报
    「Executable doesn't exist at .../chromium_headless_shell-XXXX/
    chrome-headless-shell-win64/chrome-headless-shell.exe」。
    """
    for exe in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(exe).exists():
            print(f"系统已装浏览器（{Path(exe).name}）—— 不下内置 Chromium，省 700MB")
            return 0

    rc = 0
    for pkg in ("chromium", "chromium-headless-shell"):
        cmd = [sys.executable, "-m", "playwright", "install", pkg]
        print(f"\n$ {' '.join(cmd)}\n")
        rc |= subprocess.call(cmd)
    return rc


def report() -> None:
    print("\n" + "=" * 56)
    print("占用情况")
    print("=" * 56)
    for label, path in (
        ("pip 缓存", CACHE_DIR / "pip"),
        ("Playwright 浏览器", CACHE_DIR / "playwright"),
        ("HuggingFace/torch 缓存", CACHE_DIR / "huggingface"),
    ):
        print(f"  {label:<22} {human(dir_size(path)):>10}  {path}")

    # 模型目录（embedding 模型是手动下的，不在这儿）
    model_dir = DATA_ROOT / "data" / "models"
    if model_dir.exists():
        print(f"  {'向量模型':<22} {human(dir_size(model_dir)):>10}  {model_dir}")

    print(f"\n全部在 D 盘：C 盘不新增占用。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr", action="store_true", help="装 OCR 引擎")
    parser.add_argument("--browser", action="store_true", help="装浏览器 Agent")
    parser.add_argument("--dry-run", action="store_true", help="只看要装什么，不真装")
    args = parser.parse_args()

    # 都不指定就两个都装
    do_ocr = args.ocr or not (args.ocr or args.browser)
    do_browser = args.browser or not (args.ocr or args.browser)

    print("=" * 56)
    print("安装可选依赖（全部落到 D 盘）")
    print("=" * 56)
    print(f"  OCR 引擎：{OCR_PACKAGE}")
    print(f"  浏览器  ：{BROWSER_PACKAGE}")

    if args.dry_run:
        print("\n--dry-run：不装。")
        prepare_env()
        report()
        return 0

    prepare_env()

    rc = 0
    if do_ocr:
        rc |= pip_install(OCR_PACKAGE)
    if do_browser:
        rc |= pip_install(BROWSER_PACKAGE)
        rc |= playwright_install_browser()

    report()
    if rc:
        print("\n有安装失败，看上面的输出。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
