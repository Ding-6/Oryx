"""桌面端打包脚本的防回归测试。

2026-11-26 重新打包时抓到两个真实缺陷：

1. ``build.py`` 的 ``run()`` 调用了 ``_d_env.apply(env)``，但模块导入名是
   ``d_env`` —— 一跑完整打包就 ``NameError``，脚本根本走不到编译那步。
   这个目录不在 ``test_no_undefined_names.py`` 的扫描范围（那边只看
   ``core/oryx``），所以补一条静态检查。
2. ``sync_core.py`` 的 ``diff()`` 只查「源有目标无 / 内容不同」，漏了
   「目标有源无」——源码里删掉的旧前端产物永远留在打包副本里，
   ``--check`` 却误报「全部与源码一致」。实测残留 67 个 js/css/map。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
SCRIPTS = REPO / "apps" / "desktop" / "scripts"


def _sync_core():
    """单独加载 sync_core.py —— 它只依赖标准库，不会拖进 build.py 的 d_env。"""
    spec = importlib.util.spec_from_file_location(
        "oryx_desktop_sync_core", SCRIPTS / "sync_core.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_desktop_scripts_have_no_undefined_names():
    """build.py / sync_core.py / d_env.py 不能有未定义名（抓 `_d_env` 那类笔误）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_no_undefined_names as guard

    for name in ("build.py", "sync_core.py", "d_env.py"):
        path = SCRIPTS / name
        assert path.exists(), path
        missing = guard.find_undefined(guard._read(path), str(path))
        assert missing == [], f"{name}: 未定义名 {missing}"


def test_diff_reports_extra_files(tmp_path):
    """目标多出来的文件必须被报出来 —— 否则同步不会走进删除分支。"""
    mod = _sync_core()
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("A")
    (dst / "a.txt").write_text("A")
    (dst / "old1.js").write_text("x")
    (dst / "old2.js").write_text("x")

    problems = mod.diff(src, dst)
    extras = [p for p in problems if p.startswith("多 ")]
    assert len(extras) == 2, problems
    assert not [p for p in problems if p.startswith("缺 ") or p.startswith("旧 ")]


def test_diff_is_clean_when_identical(tmp_path):
    mod = _sync_core()
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "sub").mkdir(parents=True)
    (dst / "sub").mkdir(parents=True)
    (src / "sub" / "a.txt").write_text("A")
    (dst / "sub" / "a.txt").write_text("A")
    assert mod.diff(src, dst) == []


def test_sync_removes_extra_files(tmp_path, monkeypatch):
    """同步后副本里只能剩源码有的文件 —— 修 `diff()` 前这里是残留的。"""
    mod = _sync_core()
    release = tmp_path / "release"
    release.mkdir()
    src = tmp_path / "src"
    dst = release / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("A")
    (dst / "a.txt").write_text("A")
    (dst / "old1.js").write_text("x")
    (dst / "old2.js").write_text("x")

    monkeypatch.setattr(mod, "RELEASE", release)
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "PAIRS", [(src, dst)])

    assert mod.sync() == 0
    assert (dst / "a.txt").read_text() == "A"
    assert not (dst / "old1.js").exists()
    assert not (dst / "old2.js").exists()
    assert mod.diff(src, dst) == []
    # --check 也必须能报出多余文件（这次先制造一份多余的）
    (dst / "old3.js").write_text("x")
    assert mod.sync(check=True) == 1


def test_shell_and_rust_probe_verify_identity():
    """两处探活都必须校验 Oryx 身份，不能只探端口 / 只看 res.ok。"""
    desktop = SCRIPTS.parent
    main_rs = (desktop / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8-sig")
    shell = (desktop / "dist" / "index.html").read_text(encoding="utf-8-sig")

    # Rust 侧：三态探活 + 身份子串 + 端口占用明确报错
    assert "fn probe_port" in main_rs
    assert "PING_IDENTITY" in main_rs
    assert "PortState::Foreign" in main_rs
    assert "port_conflict_message(port)" in main_rs
    # 老的「TCP connect 通就算就绪」函数必须消失
    assert "fn port_ready(" not in main_rs

    # 壳侧：res.ok 之后还要看 service 字段，且给出占用提示
    assert "data.service === 'oryx'" in shell
    assert "已被其它服务占用" in shell
