"""pytest 全局隔离。

**最重要的一件事：测试绝不能碰用户的真实数据。**

用户数据在 %APPDATA%\\Oryx（或 D:\\OryxData\\data）里：
  · oryx.db   —— 历史会话、消息、团队
  · index.db  —— 内容索引（1.26GB）
  · config.json —— 模型服务配置
  · 凭据管理器 —— api_key

之前出过两次事故，都是测试污染：
  1. `Config({"providers": {}})` + 一次 `save()` → 把用户真配置里的
     模型服务整个覆盖成空字典（记忆里记过这个坑）
  2. `Store()` 不带参数 → 落到真实 oryx.db；测试里一句
     `store.delete_session(...)` 把用户的历史会话全删了，
     而 pytest 全绿、没有任何报错

第 2 次的症状特别隐蔽：tasks/messages 表被清空，steps/audit 还留着 ——
正是「删会话」这个动作的痕迹。

怎么隔离：这里把 ORYX_HOME 指到临时目录，并**在测试模块被导入之前**生效。
必须在 conftest 里做 —— 各测试模块是导入期就 `from oryx.store import Store`，
那时候模块级路径已经算好了。

注意：只设 ORYX_HOME 不够 —— oryx.config 的 CONFIG_PATH 是在导入期算的。
所以这里还要把已经导入的模块的常量重新指过去（下面 _redirect_globals）。
"""

from __future__ import annotations

import atexit
import os
import shutil
import time
import sys
import tempfile
from pathlib import Path

# tests 目录本身要能 import —— tests/webread.py 提供「读前端源码」的辅助函数。
# pytest 的 rootdir 插入策略在不同版本/不同调用方式下不一致，
# 这里显式加一次，避免 `from webread import ...` 偶发 ImportError。
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# ── 0. 测试的临时目录也放 D 盘 ─────────────────────────────────────
#
# %TEMP% 在 C 盘。测试每跑一次就要建一批临时目录
# （每个 oryx-test-home-<pid>、每个 mkdtemp 的 oryx-session-*…），
# 跑完还不一定删得干净 —— 一次全量测试就能在 C 盘堆出几百 MB 残留。
#
# 用户明确要求「所有内容放 D 盘」。这里把 tempfile 的根目录换掉，
# 于是**所有** mkdtemp / gettempdir 都落到 D。
def _test_tmp_root() -> Path:
    """测试临时目录的根 —— 优先 D 盘，没有 D 盘才退回系统临时目录。"""
    override = os.environ.get("ORYX_TEST_TMP")
    if override:
        root = Path(override)
        root.mkdir(parents=True, exist_ok=True)
        return root
    # **不能放在 D://OryxData 里面** ——
    # 那是用户真实数据根（config.json / oryx.db / index.db 都在那儿）。
    # tests/test_paths.py 有一条护栏断言「测试路径里不能出现 OryxData」，
    # 就是为了防止测试误伤用户数据。
    # 所以测试用**独立**的根目录，同样放 D 盘，但跟数据根不沾边。
    for cand in (Path("D:/OryxTestTmp"),):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            return cand
        except OSError:
            continue
    return Path(tempfile.gettempdir())


_TMP_ROOT = _test_tmp_root()
# tempfile.tempdir 是 gettempdir()/mkdtemp() 的实际落点 ——
# 设了它，下面所有 mkdtemp 都跟着走。
tempfile.tempdir = str(_TMP_ROOT)


# ── 1. 把 ORYX_HOME 指到临时目录 ──────────────────────────────────
# 必须在任何 oryx 模块被导入之前做。
#
# **目录名要带进程 id** —— 不能用固定路径。
#
# 事故复盘：原来是固定的 `%TEMP%/oryx-test-home`，而 atexit 会
# `shutil.rmtree(_TEST_HOME)` 把整个目录删掉。于是**两个 pytest 进程并发**时，
# 先启动的那个进程正在用的 oryx.db 会被后启动那个的 atexit 删掉 ——
# 表现是 `sqlite3.OperationalError: no such table: tasks`，
# 而且**只在全量跑时偶发**，单跑那个文件永远绿，极难定位。
#
# 改成带 pid 的唯一目录后，每个进程只删自己那份，互不影响。
_TEST_HOME = _TMP_ROOT / f"oryx-test-home-{os.getpid()}"
_TEST_HOME.mkdir(parents=True, exist_ok=True)
os.environ["ORYX_HOME"] = str(_TEST_HOME)


def _purge_stale_dirs() -> None:
    """清理上次运行遗留的测试目录。

    跑在**启动时**（而不是退出时）—— 这时上一个进程的 sqlite 句柄早已释放，
    删得干净。删不掉的（别的进程还在用）跳过，留到下次。

    为什么要这样：Windows 上进程还活着的时候删 sqlite 文件会报
    `WinError 32`（另一个程序正在使用此文件）。同一个文件在进程退出后
    就能正常删掉 —— 是时序问题，不是逻辑问题。硬删（ignore_errors=True）
    的代价是**静默失败**：每次跑测试都在 %TEMP% 里留一个孤儿目录。
    """
    parent = _TEST_HOME.parent
    for marker in parent.glob("oryx-test-home-*.stale"):
        target = None
        try:
            target = Path(marker.read_text(encoding="utf-8").strip())
        except OSError:
            target = None
        if target is not None and target.exists() and target != _TEST_HOME:
            shutil.rmtree(target, ignore_errors=True)
        try:
            marker.unlink()
        except OSError:
            pass


_purge_stale_dirs()

# 各类缓存也别写用户目录
os.environ.setdefault("PIP_CACHE_DIR", str(_TEST_HOME / "pip-cache"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_TEST_HOME / "playwright"))


def _redirect_globals() -> None:
    """把已经导入的模块里的路径常量指到测试目录。

    oryx.config / oryx.store / oryx.indexer 的路径常量是**导入期**算的。
    单纯设 ORYX_HOME 只对「之后才导入」的模块有效；
    这里显式改一遍，堵住「先导入、后设环境变量」的缝。
    """
    import importlib

    for name in ("oryx.config", "oryx.store", "oryx.indexer", "oryx.embeddings"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        if name == "oryx.config":
            mod.CONFIG_PATH = _TEST_HOME / "config.json"
            mod.FEATURES_PATH = _TEST_HOME / "features.yaml"
        elif name == "oryx.store":
            mod.DEFAULT_STORE_PATH = _TEST_HOME / "oryx.db"
            mod.DEFAULT_INDEX_PATH = _TEST_HOME / "index.db"
        elif name == "oryx.indexer":
            mod.DEFAULT_INDEX_PATH = _TEST_HOME / "index.db"
        elif name == "oryx.embeddings":
            # 模型是只读资源，复用真实目录（205MB，拷一份进临时目录太浪费）
            mod.DEFAULT_MODEL_DIR = _real_model_dir()


def _real_model_dir() -> Path:
    """真实模型目录在哪。

    模型在 %APPDATA%\\Oryx\\models 或 D:\\OryxData\\data\\models 下 ——
    数据目录搬到 D 盘之后就是后者。找不到就返回测试目录下的路径
    （那样相关测试会跳过，而不是误读别的模型）。
    """
    candidates = [
        Path("D:/OryxData/data/models/bge-small-zh-v1.5"),
        Path(os.environ.get("APPDATA", "")) / "Oryx" / "models" / "bge-small-zh-v1.5",
    ]
    for c in candidates:
        if c.exists():
            return c
    return _TEST_HOME / "models" / "bge-small-zh-v1.5"


_redirect_globals()


@atexit.register
def _cleanup() -> None:
    """退出时**登记**待清理的目录，真正的删除放到下次启动时做。

    为什么不在退出时删
    ------------------
    sqlite 的文件句柄在 Windows 上释放有延迟：进程还活着的时候删
    `%TEMP%\\oryx-test-home-*\\oryx.db` 会报 `WinError 32`（另一个程序正在使用此文件）。
    实测：同一个文件在进程退出后就能正常删掉 —— 说明不是逻辑问题，是时序问题。

    硬删（`ignore_errors=True`）的代价是**静默失败**：每次跑测试都在 %TEMP% 里
    留一个孤儿目录，日积月累。

    所以改成两段式：
      · 退出时把目录名记进 `_STALE_DIRS`（同目录下的一个小标记文件）；
      · 下次启动时（`_TEST_HOME` 建好之后）把陈旧目录清掉。

    只删**自己那份**（目录名带 pid）—— 别的 pytest 进程可能正在用，
    删了会让对方莫名其妙报「no such table」。
    """
    marker = _TEST_HOME.parent / f"{_TEST_HOME.name}.stale"
    try:
        marker.write_text(str(_TEST_HOME), encoding="utf-8")
    except OSError:
        pass
    # 尽力当场删一次：进程快退出了，多数情况下这时锁已经松开。
    # 删不掉也没关系 —— 上面留了标记，下次启动会清。
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


# ── 3. 防「两个 pytest 进程互相删对方的库」 ─────────────────────────
# 事故复盘（2026-11-06）：全量测试偶发 2 个失败 ——
#   sqlite3.OperationalError: no such table: tasks
#   （test_domains.py 的两个用例）
#
# 症状极难定位：
#   · 单独跑那个文件永远绿；
#   · 全量跑**偶发**失败，复跑又过了；
#   · 失败点在「store 连得上库但库里没表」——
#     说明**库文件被人删了**，不是建表逻辑坏了。
#
# 根因：`_TEST_HOME` 原来是**固定路径** `%TEMP%/oryx-test-home`，
# 而 atexit 会 `shutil.rmtree(_TEST_HOME)` 把整个目录删掉。
# 于是**两个 pytest 进程并发**时（我一边跑全量一边起验收服务），
# 先启动的进程正在用的 oryx.db 会被后启动那个的 atexit 删掉 ——
# 表现就是上面的「no such table」。
#
# 对照实验（已验证）：
#   旧版：A 进程建 db → B 进程 atexit rmtree → A 的 db 不见了 → A 报错 ✓
#   新版：A、B 各用自己的目录（带 pid）→ B 只删自己那份 → A 不受影响 ✓
#
# 规则：**测试的临时目录必须带进程 id**，atexit 只能删自己那份。
# 看到「no such table」先查是不是库文件被别的进程删了。


# ── 4. 防「module scope fixture 泄漏环境变量」 ─────────────────────
# 事故复盘：test_security.py 改成 module scope（服务只起一次，解决「19 次
# 重建服务导致卡死」）之后，环境变量用 os.environ 直接设 —— module scope 下
# monkeypatch fixture 只活到函数结束，于是 ORYX_HOME **泄漏到后续测试**。
#
# 症状极难定位：后面 test_semantic / test_toolindex / test_tool_search_e2e
# 十几个用例莫名全挂（向量写到了临时目录，读不回来），
# 而它们单独跑全绿 —— 典型的跨用例污染。
#
# 规则：**module scope 的 fixture 里，环境变量必须用 pytest.MonkeyPatch
# 上下文自己管**，并在 finally 里 undo；sys.modules 的增删也要还原。
# 改这类 fixture 时，务必连带跑一遍后续几个测试文件确认没泄漏。


# ── 2. 防「模块级 store 被裸替换」的跨用例污染 ──────────────────────
# 事故复盘：domains/* 的路由在 app 装配时用闭包捕获了 oryx.server.store。
# 测试里一句 `server_mod.store = Store(tmp_path/"oryx.db")` 会让「用例里
# from oryx.server import store 拿到的对象」和「路由闭包里的对象」指向两个库，
# 而且不会恢复 —— 字母序更靠后的用例（test_domains.py 的会话改名）莫名 404。
# 这种污染单跑复现不了、全量才炸，所以逐用例钉死对象身份。
import sys

import pytest


@pytest.fixture(autouse=True)
def _guard_server_store_identity():
    """用例结束后 oryx.server.store 必须仍是用例开始时那一个对象。"""
    mod = sys.modules.get("oryx.server")
    before = getattr(mod, "store", None) if mod is not None else None
    yield
    mod = sys.modules.get("oryx.server")
    if mod is None or before is None:
        # 这个用例才第一次把 server 导入进程，没有可比对的「之前」值。
        return
    assert getattr(mod, "store", None) is before, (
        "oryx.server.store 在用例结束后被换成了别的对象。"
        "domains 路由在 app 装配时闭包捕获了旧 store，裸替换会造成跨用例 404；"
        "要替换请用 monkeypatch.setattr 让 pytest 自动恢复。"
    )
