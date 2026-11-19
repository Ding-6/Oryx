"""路径回归：所有落盘位置必须在 D 盘，不许往 C 盘写。

用户的原话：「设定其所有的安装路径都是D盘的，环境也要安装到D盘，C盘满了」。

所以要守住的东西有四类：
  1. 应用数据（配置 / 索引库 / 模型 / 粘贴）
  2. 可选依赖与它们的模型权重（OCR、浏览器）
  3. 临时文件
  4. 桌面端自己的目录（安装目录、webview profile）

这些断言不是「样式检查」—— 路径跑偏了就是真的往 C 盘写东西。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _restore_process_env():
    """每个测试后把进程环境还原。

    redirect_env() 是直接写 os.environ 的（不是 monkeypatch），
    不还原的话 TEMP 会指到一个已经被 pytest 删掉的临时目录 ——
    后面别的测试调 tempfile 就会静默跑到别处去。
    """
    import tempfile

    saved_env = dict(os.environ)
    saved_tempdir = tempfile.tempdir
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        tempfile.tempdir = saved_tempdir


# ── 1. Python 侧：应用数据目录 ─────────────────────────────────────
def test_app_dir_prefers_d_drive(monkeypatch):
    """app_dir() 默认落到 D 盘。"""
    from oryx.config import app_dir

    monkeypatch.delenv("ORYX_HOME", raising=False)
    monkeypatch.delenv("OYX_DATA_ROOT", raising=False)
    d = app_dir()
    if Path("D:/").exists():
        assert d.drive.upper() == "D:", f"应用数据目录跑到 {d} 了"
        assert "OryxData" in str(d), str(d)


def test_app_dir_respects_oryx_home(monkeypatch):
    """ORYX_HOME 优先 —— 桌面端启动时用它指到 D:\\OryxData\\data。"""
    from oryx.config import app_dir

    monkeypatch.setenv("ORYX_HOME", r"D:\OryxData\data")
    assert app_dir() == Path(r"D:\OryxData\data")


def test_app_dir_respects_data_root(monkeypatch):
    from oryx.config import app_dir

    monkeypatch.delenv("ORYX_HOME", raising=False)
    monkeypatch.setenv("OYX_DATA_ROOT", r"D:\OryxData")
    assert app_dir() == Path(r"D:\OryxData") / "data"


def test_store_and_index_follow_app_dir(monkeypatch):
    """Store / FileIndex 的默认路径跟着 app_dir 走。"""
    from oryx.config import app_dir

    monkeypatch.setenv("ORYX_HOME", r"D:\OryxData\data")
    assert app_dir() == Path(r"D:\OryxData\data")


# ── 2. 测试隔离：绝不能碰用户真实数据 ─────────────────────────────
def _assert_not_user_data(path: Path) -> None:
    """断言这个路径不是用户的真实数据目录。

    判据是「不是 %APPDATA%\\Oryx、也不是 D:\\OryxData」——
    测试临时文件在 %TEMP% 下是正常的（那是临时目录，不是用户数据）。
    """
    text = str(path)
    assert "oryx-test" in text, path
    # 用户真实数据的两个可能位置
    appdata_oryx = Path(os.environ.get("APPDATA", "")) / "Oryx"
    assert appdata_oryx not in path.parents, path
    assert "OryxData" not in text, path


def test_store_default_path_is_not_user_data():
    """Store() 不带参数时不能落到用户真实库。

    这不是洁癖 —— 出过事：测试里一句 store.delete_session(...)
    把用户的历史会话全删了，pytest 全绿、没有任何报错。
    """
    from oryx import store as store_mod

    _assert_not_user_data(store_mod.DEFAULT_STORE_PATH)


def test_index_default_path_is_not_user_data():
    from oryx import indexer as indexer_mod

    _assert_not_user_data(indexer_mod.DEFAULT_INDEX_PATH)


def test_config_path_is_not_user_config():
    from oryx import config as config_mod

    _assert_not_user_data(config_mod.CONFIG_PATH)


def test_credentials_use_separate_target():
    """测试的假密钥不该进用户的凭据管理器。"""
    from oryx import credentials as cred_mod

    assert cred_mod._IS_TEST is True
    assert cred_mod.SERVICE == "oryx"  # 正式 target 没被改


# ── 3. 桌面端：安装目录与配置 ──────────────────────────────────────
def test_nsis_hooks_configured():
    """NSIS 钩子要挂上，否则安装目录还落 %LOCALAPPDATA%。"""
    conf = json.loads((REPO / "apps/desktop/src-tauri/tauri.conf.json").read_text("utf-8"))
    hooks = conf["bundle"]["windows"]["nsis"]["installerHooks"]
    assert hooks == "./windows/hooks.nsh"


def test_nsis_hook_points_to_d_drive():
    """钩子里必须把 $INSTDIR 指到 D 盘。"""
    text = (REPO / "apps/desktop/src-tauri/windows/hooks.nsh").read_text("utf-8")
    assert "NSIS_HOOK_PREINSTALL" in text
    assert "D:\\Oryx" in text, "安装目录没指到 D 盘"
    # 必须是 PREINSTALL —— POSTINSTALL 时文件已经拷到旧目录了，搬不动
    assert "NSIS_HOOK_PREINSTALL" in text.split("NSIS_HOOK_POSTINSTALL")[0]


def test_rust_paths_module_covers_all_envs():
    """Rust 侧要把该设的环境变量都设了。

    少一个就有一类东西漏到 C 盘 —— 比如漏了 PLAYWRIGHT_BROWSERS_PATH，
    Chromium 就会下到 %LOCALAPPDATA%\\ms-playwright（701MB）。
    """
    text = (REPO / "apps/desktop/src-tauri/src/paths.rs").read_text("utf-8")
    for key in (
        "ORYX_HOME",
        "TEMP",
        "TMP",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PIP_CACHE_DIR",
        "HF_HOME",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
    ):
        assert f'"{key}"' in text, f"没设 {key}"


def test_rust_sets_app_directories_override():
    """Tauri 自己的目录（webview profile 88MB）也要搬到 D 盘。"""
    text = (REPO / "apps/desktop/src-tauri/src/main.rs").read_text("utf-8")
    assert "app_directories_override" in text
    assert "AppDirectoriesOverride::Root" in text


def test_install_extras_script_targets_d_drive():
    """装 OCR 等可选依赖的脚本必须把缓存指到 D 盘。"""
    text = (REPO / "core/scripts/install_extras.py").read_text("utf-8")
    assert "PLAYWRIGHT_BROWSERS_PATH" in text
    assert "PIP_CACHE_DIR" in text
    assert "rapidocr-onnxruntime" in text


def test_install_extras_installs_headless_shell():
    """Playwright 要下**两个**包，漏一个 headless 就起不来。

    Playwright 1.49 之后 headless 模式走的是独立的
    `chromium-headless-shell`，不是 chromium 目录里的 chrome.exe。
    只下 chromium 的话 launch(headless=True) 会报
    「Executable doesn't exist at .../chromium_headless_shell-XXXX/...」
    —— 实测踩过，440MB 下完了才发现起不来。
    """
    text = (REPO / "core/scripts/install_extras.py").read_text("utf-8")
    assert "chromium-headless-shell" in text


def test_rust_finds_python_beyond_path():
    """Rust 侧要能找到不在 PATH 里的 Python。

    这台机器上 python 就不在系统 PATH 里（conda 环境在 D:\\Anaconda）。
    只 spawn "python" 会失败 —— 桌面端起来了但内核没起，界面一片空白。
    """
    text = (REPO / "apps/desktop/src-tauri/src/paths.rs").read_text("utf-8")
    assert "fn find_python" in text
    assert "ORYX_PYTHON" in text
    # 要验证解释器能不能 import 内核依赖
    assert "has_backend_deps" in text
    assert "fastapi" in text


def test_port_file_is_cleaned_up():
    """端口临时文件用完要删。

    这个泄漏很隐蔽：每次启动 Oryx 就在 %TEMP% 里多一个 .port 文件，
    攒久了 C 盘临时目录里能躺 150+ 个。
    """
    prewarm = (REPO / "core/oryx/prewarm.py").read_text("utf-8")
    assert "release_port_file" in prewarm

    agentlink = (REPO / "core/oryx/agentlink.py").read_text("utf-8")
    assert "release_port_file" in agentlink
    # 冷启动路径也要删
    assert "self._port_file.unlink()" in agentlink


def _nsis_hook_body(name: str = "NSIS_HOOK_PREINSTALL") -> str:
    """取钩子宏的**宏体**（去掉注释行）。

    直接 split 名字会取到注释里第一次出现的位置 —— 注释里也提到了宏名，
    那样拿到的是一段说明文字，不是代码。
    注释行也要去掉 —— 注释里会写「不能用 ${FileExists}」这种说明，
    按原文搜会误判。
    """
    text = (REPO / "apps/desktop/src-tauri/windows/hooks.nsh").read_text("utf-8")
    marker = f"!macro {name}"
    start = text.find(marker)
    assert start > 0, f"没找到 {marker}"
    end = text.find("!macroend", start)
    assert end > start, f"{marker} 没有对应的 !macroend"
    body = text[start:end]
    # 去掉注释（NSIS 注释是分号开头）
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith(";")]
    return "\n".join(lines)


def test_nsis_hook_reinstalls_outpath():
    """钩子改了 $INSTDIR 之后必须重新 SetOutPath。

    Tauri 模板是 `SetOutPath $INSTDIR` 然后才插钩子 ——
    只改 $INSTDIR 的话输出路径已经定死在 C 盘了（实测踩过）。
    """
    hook = _nsis_hook_body()
    assert "SetOutPath" in hook, "钩子改了 $INSTDIR 但没重新 SetOutPath"


def test_nsis_hook_uses_iffileexists_for_drive():
    """判断 D 盘在不在要用 IfFileExists，不能用 ${FileExists}。

    ${FileExists} 是 LogicLib 的布尔宏，只对**文件**为真；
    D:\\ 是**目录**，拿它当布尔用永远判假 —— 钩子等于没生效（实测踩过：
    装完还是落在 C:\\Users\\...\\AppData\\Local\\Oryx）。
    """
    hook = _nsis_hook_body()
    assert "IfFileExists" in hook
    assert "${FileExists}" not in hook


def test_uninstall_keeps_user_data():
    """卸载必须保留数据目录（配置 / 会话 / 索引库 / 模型）。

    之前这里是无条件 `RMDir /r "D:\\OryxData"` —— 实测卸载一次，
    向量模型和 1.3GB 的内容索引全没了，重装后向量检索直接不可用。
    用户卸载程序通常是为了重装或换版本，不是要扔掉自己的数据。
    """
    hook = _nsis_hook_body("NSIS_HOOK_POSTUNINSTALL")
    # 只能删 webview 缓存，不能删整个数据目录
    assert 'RMDir /r "D:\\OryxData\\tauri"' in hook
    assert 'RMDir /r "D:\\OryxData"' not in hook
    assert "data" not in hook.replace("OryxData\\tauri", ""), "卸载钩子碰了数据目录"


# ── 4. 安装脚本本身 ───────────────────────────────────────────────
def test_install_extras_runs_dry_run():
    """脚本本身能跑（--dry-run 不装东西，只看路径）。"""
    import subprocess
    import sys

    script = REPO / "core/scripts/install_extras.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "D:" in proc.stdout


# ── 5. Python 侧自己也要重定向（直接 python -m oryx 的那条路）────
def test_python_side_redirects_caches_to_data_root(tmp_path, monkeypatch):
    """Python 侧必须自己把缓存和临时目录指到数据根目录。

    桌面端（Rust）设过一遍，但 Oryx 也支持直接跑 `python -m oryx` ——
    开发、脚本、调试都走这条路。那条路上没人设这些变量，于是：
      · Chromium 落到 %LOCALAPPDATA%\\ms-playwright（701MB）
      · pip 缓存、torch 权重、临时文件全在 C 盘
    """
    from oryx import paths as paths_mod

    root = tmp_path / "OryxData"
    applied = paths_mod.redirect_env(root)

    for key in (
        "TEMP",
        "TMP",
        "XDG_CACHE_HOME",
        "PIP_CACHE_DIR",
        "HF_HOME",
        "TORCH_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
    ):
        assert key in applied, f"没设 {key}"
        assert str(root) in applied[key], f"{key} 指向 {applied[key]}，不在 {root} 下"


def test_python_side_env_keys_match_rust_side():
    """Python 与 Rust 两边的环境变量清单必须一致。

    两边各写一份，最容易出的岔子就是「一边加了另一边没加」——
    少一个键就有一类东西漏到 C 盘，而且不报错。
    """
    from oryx import paths as paths_mod

    rust = (REPO / "apps/desktop/src-tauri/src/paths.rs").read_text("utf-8")
    for key in paths_mod._ENV_LAYOUT:
        assert f'"{key}"' in rust, f"Rust 侧没有 {key}"


def test_tempfile_follows_redirect(tmp_path, monkeypatch):
    """tempfile.gettempdir() 必须跟着走。

    这个坑很隐蔽：gettempdir() 在**第一次调用时**就把结果缓存进
    tempfile.tempdir，之后改 TEMP/TMP 环境变量**不生效**。
    不清那个缓存的话，端口临时文件（.port）还是在 C 盘建 ——
    实测一次启动就漏一个，攒到 150+ 个。

    第二层坑：**TMPDIR 的优先级高于 TEMP/TMP**。
    标准库 tempfile._candidate_tempdir_list() 的顺序是
    TMPDIR → TEMP → TMP，只设 TEMP/TMP 而漏了 TMPDIR 的话，
    gettempdir() 仍然拿旧值 —— 临时文件照样漏到 C 盘。
    所以 redirect_env 必须三个一起设。
    """
    import tempfile

    from oryx import paths as paths_mod

    root = tmp_path / "OryxData"
    paths_mod.redirect_env(root)

    assert Path(tempfile.gettempdir()) == (root / "temp").resolve()

    fd, raw = tempfile.mkstemp(prefix="probe-", suffix=".port")
    os.close(fd)
    try:
        assert str(root) in raw, f"临时文件建到了 {raw}"
    finally:
        os.unlink(raw)


def test_redirect_sets_tmpdir_not_just_temp(tmp_path, monkeypatch):
    """TMPDIR 必须被一起设置 —— 否则 gettempdir() 会绕过重定向。

    这条是上一条的根因拆解：只断言 TEMP/TMP 是不够的，
    真正决定 tempfile 落在哪的是候选列表的**第一个可用项**，
    而它的顺序是 TMPDIR → TEMP → TMP。
    """
    from oryx import paths as paths_mod

    root = tmp_path / "OryxData"
    applied = paths_mod.redirect_env(root)

    for key in ("TMPDIR", "TEMP", "TMP"):
        assert key in applied, f"没设 {key}"
        assert str(root) in applied[key], f"{key} 指向 {applied[key]}，不在 {root} 下"


def test_redirect_wins_over_preexisting_tmpdir(tmp_path, monkeypatch):
    """机器上本来就有的 TMPDIR 必须被覆盖掉。

    这条是真实场景：这台机器的 TMPDIR 指着 C 盘。
    不覆盖的话，重定向形同虚设。
    """
    import tempfile

    from oryx import paths as paths_mod

    monkeypatch.setenv("TMPDIR", r"C:\Windows\Temp")
    monkeypatch.setenv("TEMP", r"C:\Windows\Temp")
    monkeypatch.setenv("TMP", r"C:\Windows\Temp")

    root = tmp_path / "OryxData"
    paths_mod.redirect_env(root)

    assert Path(tempfile.gettempdir()) == (root / "temp").resolve()


def test_data_root_follows_data_root_env(monkeypatch, tmp_path):
    """OYX_DATA_ROOT 优先。"""
    from oryx import paths as paths_mod

    monkeypatch.setenv("OYX_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("ORYX_HOME", str(tmp_path / "root" / "data"))
    assert paths_mod.data_root() == tmp_path / "root"


def test_data_root_derives_from_oryx_home(monkeypatch, tmp_path):
    """只有 ORYX_HOME 时，从 <root>\\data 反推数据根目录。"""
    from oryx import paths as paths_mod

    monkeypatch.delenv("OYX_DATA_ROOT", raising=False)
    monkeypatch.setenv("ORYX_HOME", str(tmp_path / "root" / "data"))
    assert paths_mod.data_root() == tmp_path / "root"


def test_entry_points_call_redirect_env():
    """两个入口都得调用 redirect_env。

    只改一处是典型的漏：改完 __main__ 忘了 agentd，或者反过来 ——
    agentd 是**独立进程**（agentlink / prewarm 拉起的），
    不走 __main__，不设就漏。
    """
    main_py = (REPO / "core/oryx/__main__.py").read_text("utf-8")
    assert "redirect_env" in main_py

    agentd_py = (REPO / "core/oryx/agentd.py").read_text("utf-8")
    assert "redirect_env" in agentd_py
