"""跑一遍全部浏览器验证，汇总结果。

为什么要有这个
------------
以前是手敲一长串 for 循环 —— 一次跑十一个脚本，
中间哪个卡住了就整批卡住（SIGTERM 杀掉，**前面跑过的结果全丢**）。

而且逐个跑完得盯着屏幕才知道哪几个过了。这里：

* 每个脚本都有**超时保护** —— 卡住的那个被判失败，不拖死整批
* 跑完给一份**汇总**：谁过了、谁没过、谁被超时干掉了、各花多久
* 端口冲突与残留进程在跑之前先查一遍

用法
----
    python tests/run_web_checks.py            # 跑全部
    python tests/run_web_checks.py --fast     # 跳过最慢的两个
    python tests/run_web_checks.py caps uiext # 只跑名字里含这些词的
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _webdrive import kill_tree

HERE = Path(__file__).resolve().parent
CORE = HERE.parent

# 每个脚本的超时上限（秒）。
#
# 定这个数的依据：最慢的 settings_ext 要起六个 Chrome，
# 每个 Chrome 都要「启动 → 加载页面 → 杀进程树 → 清 profile」，
# 实测 190 秒 —— 给到 420 秒，足够跑完又不至于卡死到天荒地老。
TIMEOUT = 420

# 「慢」的两个：跑全量浏览器验证时可以不跑。
SLOW = {"check_web_settings_ext.py", "check_web_functional.py"}

# 这些是纯语法/静态检查，快得很 —— 单独跑没意义，跳过。
SKIP = {"check_web_script.py"}


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _precheck(scripts: list[Path]) -> None:
    """跑之前先看看有没有端口被占着。

    端口被占 → 那个脚本连的是**别人的服务**，测出来的结论全是假的。
    这里提前说一声，让人知道该等还是该清理。
    """
    busy = []
    for p in scripts:
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:_CDP_PORT|CDP_PORT)\s*=\s*(\d+)", src):
            port = int(m.group(1))
            if not _port_free(port):
                busy.append(f"{p.name}(CDP {port})")
        for m in re.finditer(r"^\s*(?:port|PORT)\s*=\s*(\d+)", src, re.M):
            port = int(m.group(1))
            if not _port_free(port):
                busy.append(f"{p.name}(服务 {port})")
    if busy:
        print("⚠ 这些端口被占着，相关脚本会**换一个端口继续跑**：")
        for b in sorted(set(busy)):
            print(f"    {b}")
        print("  （可能是上次的测试进程没退出 —— 等一会儿或手动清理）\n")


def _result_of(out: str) -> tuple[bool, str]:
    """从输出里判断过没过。"""
    failed = re.findall(r"^\s*\[FAIL\]\s*(.+)$", out, re.M)
    summary = re.search(r"失败\s*(\d+)\s*项", out)
    if failed:
        return False, "；".join(f.strip() for f in failed[:3])
    if summary and int(summary.group(1)) > 0:
        return False, summary.group(0)
    if "全部通过" in out:
        return True, ""
    # 有些脚本没有「全部通过」这句结尾 —— 只要**有 PASS 且没有 FAIL**就算过。
    # （早先这里判成失败，纯粹因为输出格式不统一 —— 不是真没过。）
    passed = re.findall(r"^\s*\[PASS\]", out, re.M)
    if passed:
        return True, ""
    # 没有结论 —— **把末尾输出带出来**。
    # 「没有结论」本身不说明任何问题，光看它没法定位；
    # 崩溃前的最后几行往往就是原因（比如 Chrome 起不来）。
    tail = "\n".join((out or "").strip().splitlines()[-8:])
    return False, "没有「全部通过」的结论" + (f"｜末尾输出：{tail}" if tail else "")


def _kill_test_browsers() -> int:
    """杀掉**测试用的**浏览器进程，返回杀了几个。

    认法：命令行里有 `--user-data-dir`，且路径在系统临时目录下。
    不按进程名无差别杀 —— 用户自己开的浏览器不该被碰。

    为什么需要：Chrome 的子进程退出很慢（实测一轮残留 20 个 msedge），
    它们占着 CPU → 后面的脚本起 Chrome 越来越慢 → 超时 → 卡住。
    """
    if os.name != "nt":
        return 0
    import json as _json
    import subprocess as sp

    # wmic 在新版 Windows 上已被移除（实测 WinError 2）——
    # 用 PowerShell 的 Get-CimInstance 一样能拿到命令行。
    # 连 ParentProcessId 一起拿 —— Chrome 的**子进程**命令行里没有
    # --user-data-dir（只有 --type=renderer），光看命令行会漏掉它们，
    # 父进程被杀后它们就成了孤儿（实测 43 个残留只杀到 15 个）。
    ps = (
        "Get-CimInstance Win32_Process "
        "-Filter \"Name='msedge.exe' OR Name='chrome.exe'\" "
        "| Select-Object ProcessId,ParentProcessId,CommandLine "
        "| ConvertTo-Json -Compress"
    )
    try:
        raw = sp.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception:
        return 0
    if not raw or not raw.strip():
        return 0
    try:
        items = _json.loads(raw)
    except Exception:
        return 0
    if isinstance(items, dict):
        items = [items]
    # 先找出「根」：命令行里带测试 profile 的那些
    roots: set[str] = set()
    parent: dict[str, str] = {}
    for it in items:
        pid = str((it or {}).get("ProcessId") or "")
        ppid = str((it or {}).get("ParentProcessId") or "")
        cmd = str((it or {}).get("CommandLine") or "").lower()
        if pid:
            parent[pid] = ppid
        # 只认**测试用的**：命令行里指向系统临时目录的 profile
        if "oryx_" in cmd and "--user-data-dir" in cmd and pid.isdigit():
            roots.add(pid)

    if not roots:
        return 0

    # 沿 ParentProcessId 往下算，把所有后代都算进去
    # （子进程命令行里没有 --user-data-dir，光看命令行会漏掉）
    targets = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, ppid in parent.items():
            if pid not in targets and ppid in targets:
                targets.add(pid)
                changed = True

    killed = 0
    for pid in sorted(targets, key=lambda x: int(x)):
        try:
            sp.run(["taskkill", "/T", "/F", "/PID", pid],
                   capture_output=True, timeout=20)
            killed += 1
        except Exception:
            pass
    return killed


def _sweep_profiles() -> None:
    """清掉遗留的 Chrome profile 目录。

    **只在脚本之间做** —— 探针之间不能做：那个 Chrome 可能还在退出，
    删它正在用的 profile 会让它出错、端口不释放，连锁影响后面的脚本。
    """
    import glob
    import shutil
    import time as _t

    base = tempfile.gettempdir()
    for path in glob.glob(os.path.join(base, "oryx_web_profile*")):
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def main() -> int:
    # 浏览器用的临时目录指到数据根目录（D 盘）——
    # Edge 会在系统 temp 里造 HeadlessEdge* / msedge_url_fetcher_*（每个 140MB），
    # 不指走的话一次验证能攒十几个 GB 到 C 盘。
    try:
        from _webdrive import setup_browser_temp
        _applied = setup_browser_temp()
        if _applied.get("TEMP"):
            print(f"浏览器临时目录已指向：{_applied['TEMP']}\n")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="只跑名字里含这些词的脚本")
    ap.add_argument("--fast", action="store_true", help="跳过最慢的两个")
    args = ap.parse_args()

    scripts = sorted(p for p in HERE.glob("check_web_*.py")
                     if p.name not in SKIP)
    if args.fast:
        scripts = [p for p in scripts if p.name not in SLOW]
    if args.names:
        scripts = [p for p in scripts
                   if any(n in p.name for n in args.names)]
    if not scripts:
        print("没有匹配的脚本")
        return 1

    print(f"准备跑 {len(scripts)} 个浏览器验证脚本"
          f"（每个超时 {TIMEOUT} 秒）\n")
    _precheck(scripts)

    results: list[tuple[str, bool, str, float]] = []
    for p in scripts:
        name = p.name
        t0 = time.time()
        # 跑之前清掉上一个脚本遗留的 Chrome profile ——
        # 这时上一个脚本的 Chrome 都退干净了，是安全的时机。
        # （放在探针之间做是错的：那个 Chrome 可能还在退出，
        #   删它正在用的 profile 会让它出错、端口不释放。）
        _sweep_profiles()
        try:
            # 用 Popen 不用 subprocess.run ——
            # 后者超时后只杀直接子进程，而脚本起的 **Chrome 是孙进程**，
            # 还持有 stdout 管道 → communicate() 等不到 EOF → 整批卡死
            #（实测脚本级超时形同虚设，卡了 1800+ 秒）。
            proc = subprocess.Popen(
                [sys.executable, str(p)],
                cwd=str(CORE), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
            try:
                out, _ = proc.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                kill_tree(proc)          # 杀整棵进程树（Chrome 也走）
                try:
                    out, _ = proc.communicate(timeout=20)
                except Exception:
                    out = ""
                finally:
                    # **一定要关掉管道** —— 残留进程还持有它的话，
                    # 下一次 communicate 又是无限等待。
                    try:
                        if proc.stdout:
                            proc.stdout.close()
                    except Exception:
                        pass
                ok, why = False, f"**超时**（>{TIMEOUT} 秒，疑似卡死）"
            else:
                ok, why = _result_of(out)
                if "端口被占用" in out and "跳过" in out:
                    ok, why = True, "跳过（端口被占）"
        except Exception as exc:  # noqa: BLE001
            ok, why = False, f"启动失败：{exc}"
        dt = time.time() - t0
        # 跑完兜底清理测试用的浏览器进程 ——
        # 它们退出很慢，累积起来会把后面的脚本顶到超时。
        _kill_test_browsers()
        results.append((name, ok, why, dt))
        mark = "✓" if ok else "✗"
        extra = f"  —— {why}" if why else ""
        print(f"  {mark} {name:<32} {dt:6.1f}s{extra}", flush=True)

    print("\n" + "=" * 62)
    bad = [r for r in results if not r[0:2][1]]
    passed = len(results) - len(bad)
    print(f"{passed}/{len(results)} 通过，总耗时 {sum(r[3] for r in results):.0f}s")
    if bad:
        print("\n没过的：")
        for name, ok, why, dt in bad:
            print(f"  ✗ {name}  {why}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
