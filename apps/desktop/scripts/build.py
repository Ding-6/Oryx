"""一键打包桌面应用。

为什么要有这个脚本
------------------
打包要**四步**，每步都有各自的坑：

1. **同步** `core/` → `target/release/core/`（`cargo build` **不做**这件事）
2. `cargo build --release` —— 编译 Rust 壳
3. `npx tauri build --bundles nsis` —— 打出安装包
4. 验证产物

忘了第 1 步 → **安装包里是旧代码**（最隐蔽的那种错：
装完了才发现跑的是几个月前的界面）。

所以把四步串起来，并且**在动手前就把缺的东西说清楚**。

用法
----
    python apps/desktop/scripts/build.py            # 完整流程
    python apps/desktop/scripts/build.py --skip-build  # 只打包（已有编译产物）
    python apps/desktop/scripts/build.py --skip-newui  # 跳过新前端构建
    python apps/desktop/scripts/build.py --check      # 只检查前置条件

第 0 步是**构建新前端**（React 19）并放进 core/web/newui/ ——
产物不进 git，打包时必须现构建，否则装出来的程序里 /newui 会 404。
"""
from __future__ import annotations

import d_env  # noqa: E402  （同目录脚本，单独跑时也能 import）
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
CORE = DESKTOP.parent.parent / "core"
SRC_TAURI = DESKTOP / "src-tauri"
RELEASE = SRC_TAURI / "target" / "release"
BUNDLE = RELEASE / "bundle" / "nsis"


def _cargo() -> str | None:
    """找 cargo —— 装了 rustup 但没加进 PATH 时也能找到。

    候选路径要包括 **CARGO_HOME** ——
    工具链搬到 D 盘后（CARGO_HOME=D://OryxData//cache//cargo），
    老的 %USERPROFILE%\.cargo 路径已经不存在了。
    只认 PATH 和老路径的话，这里会误报「Rust 工具链没装」。
    """
    found = shutil.which("cargo")
    if found:
        return found
    candidates = []
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        candidates.append(Path(cargo_home) / "bin" / "cargo.exe")
    candidates.append(Path(os.environ.get("USERPROFILE", "")) / ".cargo" / "bin" / "cargo.exe")
    candidates.append(d_env.CACHE_ROOT / "cargo" / "bin" / "cargo.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    return None


NEW_WEB = DESKTOP.parent / "web"
NEWUI_OUT = CORE / "web" / "newui"


def build_new_ui(skip: bool = False) -> int:
    """构建新前端，放进 ``core/web/newui/``。

    为什么要有这一步
    ----------------
    新前端（React 19 + TS）构建后是一堆静态文件，
    内核靠 ``core/oryx/server.py`` 的 ``/newui`` 路由托管它们
    （静态资源在 ``/web/newui/``）。

    **产物不进 git** —— 那是构建输出，应该由构建流程生成。
    所以打包时必须现构建一次，否则装出来的程序里
    ``/newui`` 会 404（路由会明确提示「新界面还没构建」）。

    构建产物的 ``base`` 是 ``/web/newui/``（见 apps/web/vite.config.ts），
    资源路径才对得上。
    """
    if skip:
        print("\n=== 跳过新前端构建 ===")
        return 0
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        print("\n=== 跳过新前端构建：找不到 npm ===")
        return 0
    if not (NEW_WEB / "package.json").exists():
        print("\n=== 跳过新前端构建：apps/web 不存在 ===")
        return 0
    rc = run([npm, "run", "build"], NEW_WEB, "构建新前端")
    if rc != 0:
        return rc
    dist = NEW_WEB / "dist"
    if not (dist / "index.html").exists():
        print("新前端构建产物不对 —— dist/index.html 不存在")
        return 1
    if NEWUI_OUT.exists():
        shutil.rmtree(NEWUI_OUT)
    shutil.copytree(dist, NEWUI_OUT)
    print(f"  新前端已放到 {NEWUI_OUT}")
    return 0


def check_prereqs() -> list[str]:
    """返回「缺的东西」列表 —— 空列表表示齐了。"""
    missing: list[str] = []

    cargo = _cargo()
    if not cargo:
        missing.append(
            "Rust 工具链没装 —— 装："
            "RUSTUP_DIST_SERVER=https://mirrors.tuna.tsinghua.edu.cn/rustup "
            "rustup-init.exe -y --default-toolchain stable-msvc --profile minimal"
        )
    else:
        # MSVC 链接器 —— Tauri 在 Windows 上必须用 msvc target
        vs = Path("C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools")
        has_cl = any(vs.rglob("cl.exe")) if vs.exists() else False
        if not has_cl:
            missing.append(
                "MSVC C++ 工具链没装 —— 装："
                "vs_BuildTools.exe --quiet --wait --norestart "
                "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
            )
        # Windows SDK —— 链接要它的 import lib
        kits = Path("C:/Program Files (x86)/Windows Kits/10/Lib")
        if not kits.exists() or not any(kits.iterdir()):
            missing.append(
                "Windows SDK 没装 —— MSVC 工具链里勾上 "
                "「适用于桌面开发的 Windows SDK」"
            )

    if not (DESKTOP / "node_modules" / ".bin" / "tauri.cmd").exists():
        missing.append(
            "tauri CLI 没装 —— 在 apps/desktop 下跑："
            "npm install --no-save @tauri-apps/cli@2"
        )
    return missing


def run(cmd: list[str], cwd: Path, label: str) -> int:
    print(f"\n=== {label} ===")
    print("  " + " ".join(cmd))
    env = dict(os.environ)
    # 缓存一律指到 D 盘 —— npm/cargo/pip 默认都往 C 盘写，
    # 一次构建就能在 C 盘堆出几百 MB。用户要求所有内容放 D 盘。
    d_env.apply(env)
    cargo = _cargo()
    if cargo:
        env["PATH"] = str(Path(cargo).parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, cwd=str(cwd), env=env)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查前置条件")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过 cargo build（用已有编译产物）")
    ap.add_argument("--skip-newui", action="store_true",
                    help="跳过新前端构建（core/web/newui 已就位时）")
    args = ap.parse_args()

    missing = check_prereqs()
    if missing:
        print("缺这些：\n")
        for m in missing:
            print(f"  · {m}")
        if args.check:
            return 1
        print("\n装完再跑一次。")
        return 1
    print("前置条件齐了。")

    if args.check:
        return 0

    # 0) 构建新前端 —— 产物不进 git，得现构建
    rc = build_new_ui(skip=args.skip_newui)
    if rc != 0:
        return rc

    # 1) 同步 —— 最容易漏、又最致命的一步
    rc = run([sys.executable, str(DESKTOP / "scripts" / "sync_core.py")],
             DESKTOP, "同步源码到打包副本")
    if rc != 0:
        return rc

    # 2) 编译
    if not args.skip_build:
        cargo = _cargo()
        rc = run([cargo, "build", "--release"], SRC_TAURI, "编译 Rust 壳")
        if rc != 0:
            return rc

    # 3) 打包
    tauri = DESKTOP / "node_modules" / ".bin" / "tauri.cmd"
    rc = run([str(tauri), "build", "--bundles", "nsis"], DESKTOP, "打 NSIS 安装包")
    if rc != 0:
        return rc

    # 4) 报告产物
    print("\n=== 产物 ===")
    exe = RELEASE / "oryx-desktop.exe"
    if exe.exists():
        print(f"  可执行文件: {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    installers = sorted(BUNDLE.glob("*.exe")) if BUNDLE.exists() else []
    for f in installers:
        print(f"  安装包:     {f}  ({f.stat().st_size / 1024 / 1024:.1f} MB)")
    if not installers:
        print("  没有找到安装包 —— 检查上面的构建输出")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
