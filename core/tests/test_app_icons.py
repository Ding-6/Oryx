"""应用库图标加载的性能回归。

用户的原话：「现在在应用库里的应用点击打开后需要特别久才能打开」。

**根因不是「打开」慢，是图标请求把后端线程池占满了**：
应用库有 258 个应用，前端给每个卡片挂一个 /api/app/icon 请求，
每个请求占住一个线程池槽位约 1.1 秒（要起一个 PowerShell 进程）。
40 个槽位被占满 → 同一时刻点的「打开应用」只能排队。

实测：图标请求进行中点「打开」，最长等 **96 秒**；空闲时只要 4~40 毫秒。

这些断言守住三件事：
  1. 前端不再逐个请求图标（主因）
  2. 后端单图标提取有并发上限（防御纵深）
  3. 图标落磁盘缓存，第二次打开应用库不用重跑 PowerShell
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# 前端已拆成 ES Module —— 读「页面 + 全部模块 + 全部样式」的合并视图
from webread import page_source  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把图标磁盘缓存指到临时目录 —— 不能碰用户真实的缓存目录。"""
    from oryx import appicon

    monkeypatch.setenv("ORYX_HOME", str(tmp_path))
    monkeypatch.setattr(appicon, "_icon_disk_dir", None, raising=False)
    return tmp_path


# ---------- 1. 前端：不再逐个请求 ----------
def test_frontend_does_not_request_icons_one_by_one():
    """前端不许再出现 /api/app/icon。

    批量接口 /api/apps/icons 已经在了，逐个请求只是「先发 258 个慢请求，
    再用批量结果替换」—— 白白把线程池占满。

    注意：**注释里提到这个路径是允许的**（那里写着「以前错在哪」），
    所以要先把注释剥掉再查 —— 只查代码。
    """
    text = page_source()
    assert "/api/apps/icons" in text, "批量图标接口不见了"

    code = _strip_js_comments(text)
    assert "/api/app/icon" not in code, (
        "前端又出现了 /api/app/icon —— 逐个请求会把后端线程池占满，"
        "点「打开应用」会排队等最多 96 秒"
    )


def _strip_js_comments(text: str) -> str:
    """剥掉 JS/CSS 注释，只留代码。

    为什么要剥：这个文件里有大段注释写着「以前错在哪」，
    注释里出现 /api/app/icon 是**应该的** —— 直接搜字符串会误报。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def test_frontend_reuses_one_icon_request():
    """重复进应用库页面不要重复拉图标。"""
    text = page_source()
    assert "appIconsPromise" in text, "批量图标请求没复用同一个 Promise"


def test_frontend_has_no_per_icon_img_tag():
    """appIcon() 里不能挂指向单图标接口的 <img>。

    注意：批量数据已经在手时挂 <img src="data:image/png;base64,..."> 是对的 ——
    那是本地数据 URL，不发请求。要禁的是**指向 /api/app/icon 的那种**。
    """
    text = page_source()
    start = text.find("function appIcon(a)")
    assert start > 0, "找不到 appIcon 函数"
    # 函数体结束：下一个顶格的 "}"。不能找第一个 "\n}" ——
    # 模板串里也有 "\n}"，会截在半截函数体上。
    end = text.find("\n}\n", start)
    assert end > start, "找不到 appIcon 函数的结尾"
    body = text[start:end]
    assert "/api/app/icon" not in body, "appIcon() 里还有单图标请求"
    # 有图标时挂的是 data URL（不发请求），没图标时退回首字母色块
    assert "APP_ICONS[a.name]" in body
    assert 'class="fall"' in body, "appIcon() 应该退回首字母色块"
    # 没有 onerror 回退（那是旧逐个请求方案留下的）
    assert "onerror" not in body, "appIcon() 里还有 onerror —— 旧方案的残留"


# ---------- 2. 后端：并发上限 ----------
def test_single_icon_extraction_is_capped():
    """单图标提取必须有并发上限。

    这是防御纵深：万一哪天又有别的地方开始逐个取图标，
    也不能再把 FastAPI 的线程池（40 槽）占满。
    """
    from oryx import appicon

    assert appicon._EXTRACT_CONCURRENCY <= 8, "并发上限太高，仍可能占满线程池"
    assert hasattr(appicon, "_extract_try_begin")
    assert hasattr(appicon, "_extract_end")


def test_extract_fails_fast_instead_of_queueing():
    """忙的时候要**快速失败**，不能排队等名额。

    这个区别很要紧 —— 实测踩过：
      · 不限量：图标请求进行中点「打开」要等 **96 秒**
      · 用信号量排队：更糟，要等 **194 秒**

    排队为什么更糟：等待的线程**占着线程池的槽位**，
    「打开应用」还是排不进去。只有「立刻返回取不到」才能把槽位还回去。

    图标是锦上添花 —— 取不到就退回首字母色块，不值得让整个后端排队。
    """
    from oryx import appicon

    src = Path(appicon.__file__).read_text("utf-8")
    body = src[src.find("def _extract_icon_png("):]
    body = body[: body.find("def _extract_icon_png_unlocked")]
    assert "_extract_try_begin" in body, "_extract_icon_png 没有限并发"
    # 不能有阻塞等待
    assert "with _extract_sem" not in body, "又用信号量排队了 —— 实测更慢（194 秒）"
    assert ".acquire(" not in body, "又阻塞等名额了 —— 实测更慢（194 秒）"


def test_extract_releases_slot_on_failure():
    """提取失败（抛异常）也必须把名额还回去。

    不还的话名额会越占越少，最后一个图标都取不到了。
    """
    from oryx import appicon

    src = Path(appicon.__file__).read_text("utf-8")
    body = src[src.find("def _extract_icon_png("):]
    body = body[: body.find("def _extract_icon_png_unlocked")]
    assert "finally:" in body, "异常时名额不会释放"
    assert "_extract_end()" in body


def test_extract_slot_accounting_is_balanced():
    """名额的加减要配平。"""
    from oryx import appicon

    appicon._extract_try_begin()
    appicon._extract_try_begin()
    # 满了就该拒绝
    if appicon._EXTRACT_CONCURRENCY <= 2:
        assert not appicon._extract_try_begin()
    appicon._extract_end()
    appicon._extract_end()
    # 还回去了就能再要
    assert appicon._extract_try_begin()
    appicon._extract_end()


# ---------- 3. 后端：磁盘缓存 ----------
def test_icons_are_cached_on_disk(home, monkeypatch):
    """图标要落磁盘缓存 —— 第二次打开应用库不用重跑 PowerShell（省 6.5 秒）。"""
    from oryx import appicon

    monkeypatch.setattr(appicon, "_icon_disk_dir", None, raising=False)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    key = "unit-test-key"

    assert appicon.disk_icon_get(key) is None
    appicon.disk_icon_put(key, png)
    got = appicon.disk_icon_get(key)
    assert got == png, "磁盘缓存没存进去或读不出来"


def test_disk_cache_rejects_non_png(home, monkeypatch):
    """不是 PNG 的内容不能当图标用。"""
    from oryx import appicon

    monkeypatch.setattr(appicon, "_icon_disk_dir", None, raising=False)
    key = "unit-test-bad"
    appicon.disk_icon_put(key, b"not a png at all")
    assert appicon.disk_icon_get(key) is None


def test_cache_key_changes_when_target_changes(tmp_path):
    """缓存键要含目标文件的 mtime —— 应用重装后图标会自动重取。

    只用名字做键的话，重装的应用会一直显示旧图标。
    """
    from oryx import appicon

    exe = tmp_path / "app.exe"
    exe.write_bytes(b"MZ")
    item = {"name": "应用", "path": str(exe)}
    k1 = appicon._icon_cache_key(item)

    # 改文件内容 + 改 mtime → 键必须变
    import os
    import time

    time.sleep(0.02)
    exe.write_bytes(b"MZ" + b"X" * 64)
    os.utime(exe, (time.time() + 10, time.time() + 10))
    k2 = appicon._icon_cache_key(item)
    assert k1 != k2, "目标文件变了但缓存键没变 —— 重装的应用会一直显示旧图标"


def test_extract_batch_uses_disk_cache(monkeypatch):
    """批量提取要查磁盘缓存 —— 全命中时不该再起 PowerShell。"""
    from oryx import appicon

    calls = []

    def fake_ps(*a, **kw):
        calls.append(a)
        raise AssertionError("全命中磁盘缓存时不该起 PowerShell 进程")

    monkeypatch.setattr(appicon, "_run_ps", fake_ps)
    monkeypatch.setattr(appicon, "_powershell", lambda: "powershell.exe")

    png = b"\x89PNG\r\n\x1a\n" + b"1" * 16
    item = {"name": "缓存应用", "path": r"C:\不存在的\x.exe"}
    key = appicon._icon_cache_key(item)
    appicon.disk_icon_put(key, png)

    out = appicon.extract_batch([("缓存应用", item)])
    assert out.get("缓存应用") == png
    assert not calls, "磁盘缓存命中了还是起了 PowerShell"


def test_app_icon_endpoint_still_available():
    """单图标接口本身要留着 —— 后端能力不该删，只是前端不用了。

    留着还有个用处：以后要「懒加载单个图标」时可以直接用。
    """
    from oryx import server

    # **用 openapi() 而不是遍历 app.routes** —— 新版 FastAPI 的
    # include_router 把子路由收进一个 _IncludedRouter 对象，
    # 直接遍历 app.routes 看不到它们（getattr(r, "path", None) 取到 None）。
    # 这两个接口搬进 `domains/app.py` 之后就属于这种「收在子路由里」的情况。
    paths = set(server.app.openapi().get("paths") or {})
    assert "/api/app/icon" in paths, "单图标接口不见了"
    assert "/api/apps/icons" in paths, "批量图标接口不见了"
