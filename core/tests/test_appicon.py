"""应用图标的提取链与过滤规则。

重点是三件事：

1. 候选链的**顺序与完整性** —— DisplayIcon、快捷方式、安装目录都要能被认出来；
2. 批量提取必须真的比逐个快（逐个起 PowerShell 进程，1 个 1.1 秒）；
3. 应用列表要滤掉"装不了的组件包"，但不能误杀真实应用。

这些用例在非 Windows 上自动跳过 —— 图标提取是 Windows 专属能力。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx import appicon

pytestmark = pytest.mark.skipif(
    not appicon._IS_WINDOWS, reason="图标提取是 Windows 专属能力"
)


# ---------------------------------------------------------------- spec 拆分
def test_split_spec_plain_path():
    assert appicon.split_spec(r"D:\App\app.exe") == (r"D:\App\app.exe", 0)


def test_split_spec_with_ordinal():
    assert appicon.split_spec(r"D:\App\app.exe,0") == (r"D:\App\app.exe", 0)


def test_split_spec_with_negative_resource_id():
    """负数是资源 ID（IconLocation 里 imageres.dll,-27 就是这种）。"""
    path, index = appicon.split_spec(r'"C:\Windows\System32\imageres.dll",-27')
    assert index == -27
    assert path.lower().endswith("imageres.dll")


def test_split_spec_empty():
    assert appicon.split_spec("") == ("", 0)


def test_split_spec_expands_env():
    path, _ = appicon.split_spec(r"%SystemRoot%\system32\shell32.dll,3")
    assert "%" not in path
    assert path.lower().endswith("shell32.dll")


# ---------------------------------------------------------------- 候选链
def test_candidates_prefers_display_icon():
    item = {"display_icon": r"D:\A\a.exe,0", "path": r"D:\B\b.exe"}
    cands = appicon.candidates(item)
    assert cands
    assert cands[0][1].lower().endswith("a.exe")


def test_candidates_include_lnk():
    item = {"path": r"C:\Users\x\Desktop\Foo.lnk"}
    kinds = {c[0] for c in appicon.candidates(item)}
    assert "assoc" in kinds


def test_candidates_include_directory_exe(tmp_path):
    """安装目录要能落到目录里的主程序 exe 上。"""
    (tmp_path / "app.exe").write_bytes(b"MZ")
    item = {"path": str(tmp_path)}
    cands = appicon.candidates(item)
    assert any(c[1].lower().endswith(".exe") for c in cands)


def test_candidates_dedupe():
    item = {"display_icon": r"D:\A\a.exe", "path": r"D:\A\a.exe"}
    cands = appicon.candidates(item)
    keys = [(k, p.lower(), i) for k, p, i in cands]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------- 启动目标
def test_launch_target_prefers_lnk():
    lnk = Path(__file__).resolve().parent / "tmp_launch.lnk"
    lnk.write_bytes(b"x")
    try:
        item = {"path": str(lnk)}
        assert appicon.launch_target(item) == str(lnk)
    finally:
        lnk.unlink(missing_ok=True)


def test_launch_target_empty_for_ghost():
    """注册表里还留着记录、但文件早没了 —— 装不了。"""
    item = {"display_icon": r"D:\nope\gone.exe", "path": r"D:\nope\gone.exe"}
    assert appicon.launch_target(item) == ""


def test_launch_target_finds_exe_in_dir(tmp_path):
    exe = tmp_path / "thing.exe"
    exe.write_bytes(b"MZ")
    item = {"path": str(tmp_path)}
    assert appicon.launch_target(item) == str(exe)


# ---------------------------------------------------------------- exe_in_dir
def test_exe_in_dir_prefers_same_name(tmp_path):
    """目录名与 exe 同名时优先选它 —— ``D:\\7-ZIP\\7-ZIP.exe`` 这类。"""
    folder = tmp_path / "tool"
    folder.mkdir()
    (folder / "tool.exe").write_bytes(b"MZ")
    (folder / "helper.exe").write_bytes(b"MZ" * 100)
    got = appicon.exe_in_dir(str(folder))
    assert Path(got).stem == "tool"


def test_exe_in_dir_skips_uninstaller(tmp_path):
    """安装目录里体积最大的 exe 经常是卸载器 —— 选它等于把用户带到卸载流程。"""
    (tmp_path / "app.exe").write_bytes(b"MZ" * 10)
    (tmp_path / "Uninstaller.exe").write_bytes(b"MZ" * 5000)
    got = appicon.exe_in_dir(str(tmp_path))
    assert Path(got).stem == "app"


def test_exe_in_dir_falls_back_to_biggest(tmp_path):
    (tmp_path / "a.exe").write_bytes(b"MZ" * 10)
    (tmp_path / "b.exe").write_bytes(b"MZ" * 1000)
    got = appicon.exe_in_dir(str(tmp_path))
    assert Path(got).stem == "b"


def test_exe_in_dir_missing_returns_empty(tmp_path):
    assert appicon.exe_in_dir(str(tmp_path / "nope")) == ""


# ---------------------------------------------------------------- resolve_target
def test_resolve_target_finds_64_variant(tmp_path):
    """注册表记 studio.exe，磁盘上是 studio64.exe —— 要能对上。"""
    real = tmp_path / "studio64.exe"
    real.write_bytes(b"MZ")
    got = appicon.resolve_target(str(tmp_path / "studio.exe"))
    assert got == str(real)


def test_resolve_target_missing_returns_empty(tmp_path):
    assert appicon.resolve_target(str(tmp_path / "nothing.exe")) == ""


# ---------------------------------------------------------------- 批量提取
def test_extract_batch_returns_png():
    """拿一个真实存在的系统程序试试 —— 提取不到就跳过，不算失败。"""
    target = Path(r"C:\Windows\System32\notepad.exe")
    if not target.exists():
        pytest.skip("本机没有 notepad.exe")
    out = appicon.extract_batch([("notepad", {"path": str(target)})])
    assert "notepad" in out, "真实 exe 提不出图标"
    assert out["notepad"][:8] == b"\x89PNG\r\n\x1a\n"


def test_extract_batch_empty_input():
    assert appicon.extract_batch([]) == {}


def test_extract_batch_skips_missing_target():
    out = appicon.extract_batch([("ghost", {"path": r"D:\nope\ghost.exe"})])
    assert "ghost" not in out


def test_extract_batch_is_faster_than_single():
    """批量必须显著快于逐个 —— 这是这个接口存在的全部理由。

    逐个提取每个都要起一个 PowerShell 进程（实测 1.1 秒/个）；
    批量是一次进程里循环处理。

    **两边都要在冷缓存下测**：图标有磁盘缓存，先跑的那边会把缓存写好，
    后跑的那边直接命中 —— 那样测出来的是「冷 vs 热」，不是两种方式的差别。
    （不加清缓存时这个用例会假失败：批量 4.47s，逐个 0.04s。）
    """
    targets = []
    for name in ("notepad.exe", "cmd.exe", "control.exe", "calc.exe"):
        p = Path(r"C:\Windows\System32") / name
        if p.exists():
            targets.append((name, {"path": str(p)}))
    if len(targets) < 2:
        pytest.skip("系统程序不够，测不出批量优势")

    appicon.clear_disk_cache()
    t0 = time.time()
    batch = appicon.extract_batch(targets)
    batch_time = time.time() - t0

    appicon.clear_disk_cache()
    t0 = time.time()
    for name, item in targets:
        appicon.app_icon_png(item)
    single_time = time.time() - t0

    assert batch_time < single_time, (
        f"批量 {batch_time:.2f}s 不比逐个 {single_time:.2f}s 快"
    )
    assert batch, "批量一个都没提出来"
