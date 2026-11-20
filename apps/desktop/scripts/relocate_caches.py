"""把 C 盘上的大目录搬到 D 盘，原处留一个目录联接（junction）。

**为什么要用联接，而不是只设环境变量**
--------------------------------------
设环境变量（比如 ``CODEX_HOME``）只在那个程序**确实认**它的时候才管用。
认不认，得翻它的二进制才知道 —— 官方安装的 WindowsApps 目录还读不了。

联接是文件系统层面的：程序照旧按原路径访问，实际读写的是 D 盘。
**认不认环境变量都无所谓**，也不会因为改路径而找不到自己的配置。

**为什么要分两步**
----------------
目录**被进程占着**的时候，Windows 不让改名，也就建不了联接
（实测：Codex 正在跑，``.cache/codex-runtimes`` 改名失败，
报「另一个程序正在使用此文件」）。

所以拆成两步：
  1. ``--move``    先把数据复制到 D 并校验（这一步不动原目录，零风险）
  2. ``--finish``  关掉占用它的程序之后再跑 —— 改名 + 建联接 + 删原目录

用法::

    python apps/desktop/scripts/relocate_caches.py --move      # 第一步：复制
    python apps/desktop/scripts/relocate_caches.py --finish    # 第二步：换目录 + 建联接

第二步会跳过还被占着的项，并告诉你哪些还没搬完。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 要搬的目录：(显示名, C 盘源, D 盘目标)
#
# 每个目标都会在原路径留一个**目录联接** ——
# 程序照旧按原路径访问，实际读写的是 D 盘。
# 认不认环境变量都无所谓（见文件头的说明）。
TARGETS = [
    ("DSH 家目录", Path.home() / ".dsh", Path("D:/OryxData/dsh")),
    ("Codex 家目录", Path.home() / ".codex", Path("D:/OryxData/codex")),
    ("Codex 运行时", Path.home() / ".cache" / "codex-runtimes",
     Path("D:/OryxData/cache/codex-runtimes")),
    ("Marvis 家目录", Path.home() / ".marvis", Path("D:/OryxData/marvis")),
    ("EchoAgent 家目录", Path.home() / ".echo-agent",
     Path("D:/OryxData/echo-agent")),
]

# 搬完顺手设的环境变量 —— 让程序**下次启动就直接用 D 盘**，
# 不用等联接生效（联接是兜底，环境变量是正解）。
#
# 只写**确认过**的：EchoAgent 的 exe 里能搜到 `ECHO_AGENT_HOME`，
# DSH 的 app.asar 里 `resolveDshHome()` 查 `DSH_HOME_ENV`。
# 没确认过的不写 —— 写错了会让程序找不到自己的配置，比不搬还糟。
ENV_VARS = {
    # DSH 的家目录**本来就是** `D://DSH-data`（`C://Users//11032//.dsh`
    # 是个指向它的联接）。别指到别处 —— 指错了 DSH 就找不到自己的配置。
    "DSH_HOME": "D:/DSH-data",
    "CODEX_HOME": "D:/OryxData/codex",
    "ECHO_AGENT_HOME": "D:/OryxData/echo-agent",
}

STATE_FILE = Path("D:/OryxData/relocate-state.json")


def is_junction(p: Path) -> bool:
    """这个路径是不是目录联接（junction）？

    **为什么必须先判断**
    ------------------
    实测踩过这个坑：`C://Users//11032//.dsh` **本来就是**一个联接，
    指向 `D://DSH-data` —— 数据早就在 D 盘了。

    但 `os.walk` 会**跟着联接走**，量出来是 1708 MB，
    看起来像是「C 盘上有 1.7GB」——
    实际上一个字节都不在 C 盘。

    不判断就动手的结果：把已经搬到 D 盘的目录又搬一遍，
    白拷 800 MB，还把 `DSH_HOME` 指到一个**不完整**的副本上，
    程序启动后找不到自己的配置。

    判据：Windows 上 `os.path.islink()` 对 junction 返回 True
    （Python 3.8+）；再配一个 `readlink` 兜底。
    """
    if not p.exists():
        return False
    try:
        if p.is_symlink():
            return True
    except OSError:
        pass
    try:
        os.readlink(p)
        return True
    except OSError:
        pass
    # readlink 在部分 Windows 环境下对 junction 不报错，
    # 补一个「能不能列出目标目录」的反证
    try:
        r = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(p)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="gbk", errors="replace", timeout=10,
        )
        return "名称替代" in (r.stdout or "") or "Name Surrogate" in (r.stdout or "") \
            or "Mount Point" in (r.stdout or "") or "Junction" in (r.stdout or "")
    except Exception:
        return False


def junction_target(p: Path) -> str | None:
    """联接指向哪里（读不出来就返回 None）。"""
    try:
        return os.readlink(p)
    except OSError:
        pass
    try:
        r = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(p)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="gbk", errors="replace", timeout=10,
        )
        for line in (r.stdout or "").splitlines():
            if "Print Name" in line or "打印名称" in line:
                return line.split(":", 1)[-1].strip()
    except Exception:
        pass
    return None


def _on_d_drive(path: str) -> bool:
    """这个路径在 D 盘上吗？

    注意联接目标带 `\\?\` 前缀（Windows 的扩展路径语法），
    `Path.resolve()` 对它不一定给得出盘符 —— 实测会误判成「不在 D 盘」，
    于是把一个已经指向 D 盘的联接又当成「目标还在 C 盘」去重搬一遍。
    所以先剥掉前缀，再看盘符。
    """
    raw = (path or "").strip()
    for prefix in ("\\\\?\\", "//?/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.replace("\\", "/")
    if raw[:2].upper() == "D:":
        return True
    try:
        return Path(raw).resolve().drive.upper().startswith("D:")
    except OSError:
        return False


def dir_size(p: Path) -> int:
    """目录总字节数。"""
    total = 0
    for root, dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def copy_verified(src: Path, dst: Path, timeout: int = 600) -> tuple[bool, str]:
    """复制并校验大小。返回 (够不够, 说明)。

    **为什么要超时**
    --------------
    目标目录正被程序使用（DSH 一直在写 profiles），robocopy 会在被锁的文件上
    反复重试 —— 实测拷 1.5GB 卡了 16 分钟只多拷了 5MB。

    所以给一个上限：拷不完就停，**剩下的等 --finish 时再补一轮**
    （那时程序已经关掉，没有锁，增量补齐很快）。
    """
    if not src.exists():
        return True, "源不存在"
    dst.mkdir(parents=True, exist_ok=True)
    before = dir_size(src)
    try:
        subprocess.run(
            ["robocopy", str(src), str(dst), "/E", "/NFL", "/NDL", "/NJH", "/NJS",
             "/NP", "/R:1", "/W:1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            timeout=timeout,
        )
        done = True
    except subprocess.TimeoutExpired:
        done = False
    after = dir_size(dst)
    ok = after >= before * 0.98
    note = "" if done else "（超时中断，剩下的等 --finish 补齐）"
    print(f"    复制 {before/1024/1024:>8.1f} MB → D {after/1024/1024:>8.1f} MB"
          f"  {'✓' if ok else '… 还没拷完'}{note}")
    return ok, ("ok" if ok else "partial")


def make_junction(link: Path, target: Path) -> bool:
    """在 link 处建一个指向 target 的目录联接。

    **为什么用 PowerShell 的 New-Item，不用 mklink**
    ----------------------------------------------
    实测 `mklink /J C:\\Users\\11032\\.dsh D:\\DSH-data` 报
    「参数格式不正确 - "DSH-data"」—— 目标明明存在也建不了。
    `New-Item -ItemType Junction` 同样的目标一次就成。

    顺带两个坑：
      · mklink 的输出是**中文**（"为 ... 创建联接"），系统代码页 GBK；
        用 text=True 会按 UTF-8 解码直接抛 UnicodeDecodeError ——
        联接其实已经建好了，脚本却在读输出时崩掉，白跑一遍。
      · **悬空的联接**（目标被删了）`Path.exists()` 返回 False，
        但它还在占着路径 —— 删它必须用 `os.path.lexists` 判断，
        否则永远删不掉。
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(link):
        return False
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' "
         f"-ErrorAction Stop | Out-Null"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="gbk", errors="replace",
    )
    if r.returncode != 0:
        print(f"    建联接失败：{(r.stdout or '').strip()[:160]}")
        # 兜底：mklink（有些环境里 cmd 没被禁）
        r2 = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="gbk", errors="replace",
        )
        return r2.returncode == 0
    return True


def do_move() -> None:
    """第一步：复制到 D 并校验。原目录不动。"""
    state = {}
    for name, src, dst in TARGETS:
        print(f"\n[{name}]")
        print(f"    源 {src}")
        print(f"    目标 {dst}")
        if not src.exists():
            print("    源不存在，跳过")
            state[name] = {"src": str(src), "dst": str(dst), "done": True}
            continue
        # **源本来就是联接的话，数据已经在别处了** ——
        # 别再搬一遍（实测踩过：.dsh 本来就指向 D://DSH-data，
        # 不判断就白拷 800MB，还把环境变量指到不完整的副本上）。
        if is_junction(src):
            tgt = junction_target(src)
            print(f"    源本来就是联接 → {tgt}")
            if tgt and not _on_d_drive(tgt):
                # 联接指向的**目标还在 C 盘** —— 真正要搬的是那个目标，
                # 搬完再把联接改指到 D。
                print("    但目标还在 C 盘 —— 搬目标，之后把联接改指到 D")
                ok, how = copy_verified(Path(tgt), dst)
                state[name] = {
                    "src": str(src), "dst": str(dst), "copied": ok, "how": how,
                    "done": False, "repoint": True, "old_target": tgt,
                }
                continue
            print("    数据已经不在 C 盘了，跳过")
            state[name] = {"src": str(src), "dst": str(dst), "done": True,
                           "junction_to": tgt}
            continue
        ok, how = copy_verified(src, dst)
        state[name] = {"src": str(src), "dst": str(dst), "copied": ok,
                       "how": how, "done": False}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n状态记在 {STATE_FILE}")
    set_env_vars()
    print("关掉占用这些目录的程序后，跑 --finish 完成搬家。")


def set_env_vars() -> None:
    """把确认过的环境变量写进用户级（永久生效）。

    只写**在程序里搜到过**的那些 —— 写一个它不认的名字没有意义，
    写错了反而会让人以为「设了就生效」。
    """
    import winreg

    print("\n=== 设置用户级环境变量 ===")
    for key, value in ENV_VARS.items():
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
            ) as k:
                winreg.SetValueEx(k, key, 0, winreg.REG_SZ, value)
            print(f"  {key} = {value}")
        except OSError as exc:
            print(f"  {key} 设置失败：{exc}")
    # 广播 WM_SETTINGCHANGE —— 已经在跑的程序也能收到
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0, 1000, None)
    except Exception:
        pass


def do_finish() -> None:
    """第二步：改名 → 建联接 → 删原目录。"""
    if not STATE_FILE.exists():
        print("没有状态文件，先跑 --move")
        return
    state = json.loads(STATE_FILE.read_text("utf-8"))
    pending = []
    for name, info in state.items():
        src = Path(info["src"])
        dst = Path(info["dst"])
        print(f"\n[{name}] {src}")
        if not os.path.lexists(src):
            print("    源已不在，跳过")
            continue
        if not dst.exists():
            print("    D 盘还没有数据，先跑 --move")
            pending.append(name)
            continue
        # ── 情况 A：源本身是联接，但**目标还在 C 盘** ──
        # 要搬的是那个目标，搬完把联接改指到 D。
        if info.get("repoint"):
            old_target = Path(info["old_target"])
            print(f"    联接指向的旧目标：{old_target}")
            ok, how = copy_verified(old_target, dst, timeout=900)
            if not ok:
                # **拷贝没完成就不能改指** ——
                # 实测踩过：复制超时（Marvis 有 4.5 万个小文件，拷不完），
                # 但流程照样往下走，把联接指到一份**残缺**的副本上，
                # 又把 C 盘的完整版删了 —— 数据就这么丢了。
                print("    ✗ 拷贝没完成，**不改指、不删旧目标**（数据安全优先）")
                print("      关掉占用它的程序再跑一次 --finish")
                pending.append(name)
                continue
            # 删掉旧联接，重建一个指向 D 的
            if os.path.lexists(src):
                # 悬空的联接 exists() 返回 False —— 用 lexists 才判断得出来
                os.rmdir(src)
            if make_junction(src, dst):
                shutil.rmtree(old_target, ignore_errors=True)
                print(f"    ✓ 联接已改指到 {dst}，旧目标已清理")
                info["done"] = True
            else:
                # 改指失败 —— 把旧联接恢复回去，别让程序找不到数据
                make_junction(src, old_target)
                print("    ✗ 改指失败，已恢复原联接")
                pending.append(name)
            continue
        # ── 情况 B：源是普通目录 ──
        # 换目录前再补一轮 —— 程序已经关掉，这一轮没有锁，增量补齐很快
        ok, how = copy_verified(src, dst, timeout=900)
        if not ok:
            print("    ✗ 拷贝没完成，**不换目录**（数据安全优先）")
            pending.append(name)
            continue
        # ① 源改名（腾出联接的位置）
        parked = src.with_name(src.name + ".moving")
        try:
            if os.path.lexists(parked):
                shutil.rmtree(parked, ignore_errors=True)
            os.rename(src, parked)
        except OSError as exc:
            print(f"    ✗ 改名失败（还被占用）：{exc}")
            print("      → 关掉占用它的程序，再跑一次 --finish")
            pending.append(name)
            continue
        # ② 建联接
        if not make_junction(src, dst):
            print("    ✗ 建联接失败，把原目录搬回去")
            try:
                os.rename(parked, src)
            except OSError:
                pass
            pending.append(name)
            continue
        # ③ 删原目录（联接已经建好，删掉不影响新路径）
        shutil.rmtree(parked, ignore_errors=True)
        print(f"    ✓ 已搬到 {dst}，原路径留了联接")
        info["done"] = True
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    if pending:
        print(f"\n还没搬完：{', '.join(pending)}")
        print("（它们正被程序占用 —— 关掉那些程序再跑一次 --finish）")
    else:
        print("\n全部搬完了。")


def main() -> int:
    ap = argparse.ArgumentParser(description="把 C 盘的大目录搬到 D 盘")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--move", action="store_true", help="第一步：复制到 D")
    g.add_argument("--finish", action="store_true", help="第二步：换目录 + 建联接")
    g.add_argument("--status", action="store_true", help="看当前状态")
    args = ap.parse_args()
    if args.move:
        do_move()
    elif args.finish:
        do_finish()
    else:
        if STATE_FILE.exists():
            print(STATE_FILE.read_text("utf-8"))
        else:
            print("还没开始。先跑 --move")
    return 0


if __name__ == "__main__":
    sys.exit(main())
