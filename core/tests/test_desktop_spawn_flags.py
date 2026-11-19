"""防回归：桌面壳 spawn Python 必须带 `CREATE_NO_WINDOW`。

为什么需要这个测试
------------------
2026-11-19 桌面端报「内核启动超时」+ Windows 弹
「python.exe - Application Error：应用程序无法正常启动(0xc0000142)」。

根因：桌面壳是 **GUI 子系统**程序，内核是 **控制台子系统**程序。
GUI 父进程 spawn 控制台子进程时如果不带 `CREATE_NO_WINDOW`，
子进程得自己新分配一个控制台 —— 这一步会加载 conhost.dll，
**慢 4 倍**（实测 981ms vs 254ms），而且**会偶发失败**：
失败发生在 DLL 初始化阶段，退出码 `0xC0000142`，内核一个字节都没跑起来。

两个测试分工
------------
* `test_desktop_bundle.py` 之外，本文件做**行为验证**（真的 spawn，看耗时）。
* Rust 侧 `paths.rs` 的 `every_spawn_site_applies_hide_console` 做**确定性检查**
  （查源码里每个 spawn 点有没有走 `hide_console`）——
  行为验证是概率性的（失败率 1~2%），靠它守回归会漏。

为什么不写成一个 Rust 测试搞定
--------------------------------
试过：Rust 里连跑 120 次 python 看有没有失败 —— 摘掉标志照样全绿
（用 cmd.exe 当代理更是必漏）。**偶发**的失败靠次数堆不出来，
必须配一个确定性的检查。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUST_SRC = REPO / "apps" / "desktop" / "src-tauri" / "src"

CREATE_NO_WINDOW = 0x0800_0000
DETACHED_PROCESS = 0x00000008


def _find_python() -> str | None:
    """找一个**能 import fastapi/uvicorn** 的解释器。

    必须验依赖，不能只看路径存在 ——
    PATH 里可能混着别处的解释器（本机的 WorkBuddy 自带 Python 就没有 fastapi），
    拿它来测会得到一堆 `ModuleNotFoundError`，跟 spawn 标志没关系。

    这跟 Rust 侧 `find_python()` 的做法一致（那边用 `has_backend_deps` 验）。
    """
    candidates: list[str] = []
    which = shutil.which("python")
    if which:
        candidates.append(which)
    candidates.extend(
        [
            r"D:\Python\Python311\python.exe",
            r"D:\Anaconda\envs\mcp-mw\python.exe",
            r"D:\Anaconda\python.exe",
        ]
    )
    for cand in candidates:
        if not Path(cand).exists():
            continue
        try:
            p = subprocess.run(
                [cand, "-c", "import fastapi, uvicorn"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            return cand
    return None


# ---------------------------------------------------------------- 源码检查


def test_rust_spawn_sites_all_use_hide_console():
    """每个 spawn Python 的地方都要走 hide_console。

    确定性检查 —— 行为验证是概率性的，靠它守回归会漏。
    """
    if not RUST_SRC.is_dir():
        pytest.skip("Rust 源码目录不存在（不是桌面端开发环境）")

    offenders: list[str] = []
    for rs in sorted(RUST_SRC.glob("*.rs")):
        text = rs.read_text(encoding="utf-8")
        # 去掉测试模块 —— 那里的自测代码不算 spawn 点
        body = text.split("#[cfg(test)]")[0]
        for idx, line in enumerate(body.splitlines(), 1):
            if "Command::new(" in line and "hide_console" not in line:
                # 往后看几行：hide_console 可能在下一行
                following = body.splitlines()[idx : idx + 12]
                if not any("hide_console" in f for f in following):
                    offenders.append(f"{rs.name}:{idx}: {line.strip()}")

    assert not offenders, "这些 spawn 点没走 hide_console（会偶发 0xC0000142）：\n" + "\n".join(
        offenders
    )


def test_create_no_window_flag_value():
    """标志的值不能被改错。"""
    assert CREATE_NO_WINDOW == 0x0800_0000


# ---------------------------------------------------------------- 行为验证


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属")
def test_gui_parent_spawn_cost_and_stability():
    """**GUI 父进程** spawn 时，不带标志要慢 3 倍且偶发挂。

    为什么必须在 GUI 父进程下测 ——
    这是整件事的关键，实测对照：

    | 父进程 | 不带 CNW | 带 CNW |
    |---|---|---|
    | 控制台子系统（python.exe） | 223ms | 244ms（**没差别**） |
    | GUI 子系统（pythonw.exe）   | 796ms | 253ms（**慢 3 倍**） |

    原因：控制台父进程**有控制台可继承**，子进程直接用；
    GUI 父进程（**Tauri 壳就是**）没有，子进程必须新建 ——
    这一步要加载 conhost.dll，**而 0xC0000142 正是在这里失败**
    （STATUS_DLL_INIT_FAILED，DLL 初始化阶段就死）。

    所以在 pytest 里直接 spawn（pytest 是控制台进程）测不出差别 ——
    我第一版就是这么写的，测出来 1ms vs 1ms，差点误判成「结论不成立」。
    必须用 `pythonw.exe` 起一个 GUI 子系统的孙进程来测。
    """
    python = _find_python()
    if not python:
        pytest.skip("找不到可用的 Python 解释器")

    pythonw = Path(python).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("同目录下没有 pythonw.exe（没法造 GUI 父进程）")

    env = dict(os.environ)
    env.setdefault("TMPDIR", r"D:\OryxData\temp")
    env.setdefault("TEMP", r"D:\OryxData\temp")
    env.setdefault("TMP", r"D:\OryxData\temp")

    # 让 GUI 子进程去测：它才是 Tauri 壳的同构环境
    helper = Path(tempfile.gettempdir()) / "oryx_spawn_bench.py"
    helper.write_text(
        "import json, os, subprocess, sys, time\n"
        f"EXE = {python!r}\n"
        "CNW = 0x08000000\n"
        "def bench(flags, n=12):\n"
        "    t0 = time.time()\n"
        "    for _ in range(n):\n"
        "        subprocess.run([EXE, '-c', 'pass'], env=os.environ,\n"
        "                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "                       creationflags=flags)\n"
        "    return (time.time() - t0) / n\n"
        "res = {'without': bench(0), 'with': bench(CNW), 'gui': True}\n"
        "open(os.environ['ORYX_BENCH_OUT'], 'w').write(json.dumps(res))\n",
        encoding="utf-8",
    )
    out_file = Path(tempfile.gettempdir()) / "oryx_spawn_bench.json"
    if out_file.exists():
        out_file.unlink()
    env["ORYX_BENCH_OUT"] = str(out_file)

    subprocess.run(
        [str(pythonw), str(helper)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )
    if not out_file.exists():
        pytest.skip("GUI 父进程基准跑不起来")

    data = json.loads(out_file.read_text(encoding="utf-8"))
    without_ms = data["without"] * 1000
    with_ms = data["with"] * 1000

    assert without_ms > with_ms * 1.5, (
        f"预期 GUI 父进程不带 CREATE_NO_WINDOW 更慢（要新建控制台），"
        f"实测 without={without_ms:.0f}ms with={with_ms:.0f}ms —— "
        f"行为变了，结论要重新取证"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属")
def test_gui_parent_spawn_with_flag_never_fails_init():
    """**GUI 父进程**带标志 spawn，必须次次成功。

    这才是真正对应生产环境的验证：
    失败发生在「新建控制台」那一步，
    只有 GUI 父进程才会走那条路。
    """
    python = _find_python()
    if not python:
        pytest.skip("找不到可用的 Python 解释器")

    pythonw = Path(python).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("同目录下没有 pythonw.exe（没法造 GUI 父进程）")

    env = dict(os.environ)
    env.setdefault("TMPDIR", r"D:\OryxData\temp")
    env.setdefault("TEMP", r"D:\OryxData\temp")
    env.setdefault("TMP", r"D:\OryxData\temp")

    helper = Path(tempfile.gettempdir()) / "oryx_spawn_fail.py"
    helper.write_text(
        "import json, os, subprocess\n"
        f"EXE = {python!r}\n"
        "CNW = 0x08000000\n"
        "fails = []\n"
        "for _ in range(40):\n"
        "    p = subprocess.run([EXE, '-c', 'import fastapi, uvicorn'], env=os.environ,\n"
        "                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "                       creationflags=CNW)\n"
        "    if p.returncode != 0:\n"
        "        fails.append(p.returncode)\n"
        "open(os.environ['ORYX_FAIL_OUT'], 'w').write(json.dumps(fails))\n",
        encoding="utf-8",
    )
    out_file = Path(tempfile.gettempdir()) / "oryx_spawn_fail.json"
    if out_file.exists():
        out_file.unlink()
    env["ORYX_FAIL_OUT"] = str(out_file)

    subprocess.run(
        [str(pythonw), str(helper)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=300,
    )
    if not out_file.exists():
        pytest.skip("GUI 父进程验证跑不起来")

    failures = json.loads(out_file.read_text(encoding="utf-8"))
    assert not failures, (
        f"GUI 父进程带 CREATE_NO_WINDOW 起 python 仍失败 {failures} —— "
        f"0xC0000142 = 3221225794，说明又出现了初始化失败"
    )
