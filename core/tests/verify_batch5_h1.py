"""第六批 · 本地资料四视图改造的 HTTP 契约验证。

起真实内核 + 真实 HTTP，逐字段核对前端期望的结构。重点是三条安全语义：

    1. /api/fs/list 能浏览整盘，但禁区与 device/UNC 路径必须被挡
    2. /api/apps/launch 只接受已登记的 id
    3. /api/apps/custom 的 CRUD 往返不丢字段

运行：python -m tests.verify_batch5_h1
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []
PYTHON = sys.executable
CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parent


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def strip_js_comments(text: str) -> str:
    """剥掉块注释与行注释，只留代码。

    为什么需要：index.html 里有大段注释写着「以前错在哪」，
    注释里出现某个接口路径是**应该的**（那是给人看的说明）。
    直接搜全文会误报成「代码里还在用」。
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


# ---------------------------------------------------------------- HTTP 客户端
class Server:
    """起一个真实的 Oryx server（uvicorn），用 HTTP 访问它。"""

    def __init__(self, port: int = 8793) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.token = ""
        # 隔离的 ORYX_HOME：自定义应用存在那儿，不能碰用户真实数据
        self.home = tempfile.mkdtemp(prefix="oryx_h1_home_")

    def start(self) -> bool:
        env = {**os.environ, "ORYX_HOME": self.home}
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "oryx", "--no-browser", "--port", str(self.port)],
            cwd=str(CORE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/", timeout=10
                ) as resp:
                    html = resp.read().decode("utf-8")
                m = re.search(r'const ORYX_TOKEN = "([^"]*)"', html)
                if m:
                    self.token = m.group(1)
                    return bool(self.token)
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def _raw(self, method: str, path: str, body=None, token: bool = True):
        from urllib.parse import quote

        url = f"http://127.0.0.1:{self.port}{quote(path, safe='/?=&%')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            req.add_header("X-Oryx-Token", self.token)
        req.add_header("X-Oryx-Request", "1")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())

    def get(self, path: str, token: bool = True):
        return self._raw("GET", path, token=token)

    def post(self, path: str, body=None, token: bool = True):
        return self._raw("POST", path, body if body is not None else {}, token=token)

    def put(self, path: str, body=None):
        return self._raw("PUT", path, body if body is not None else {})

    def delete(self, path: str):
        return self._raw("DELETE", path)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()


# ---------------------------------------------------------------- 契约
def test_fs_list_shape(srv: Server) -> None:
    """列目录的返回结构 —— 前端要照它渲染。"""
    d = srv.get("/api/fs/list?path=C%3A%2F")
    for field in ("path", "parent", "entries", "count", "total", "truncated"):
        check(f"返回含 {field}", field in d, str(list(d.keys())))
    check("entries 是数组", isinstance(d.get("entries"), list), type(d.get("entries")).__name__)
    check("count 与 entries 一致", d.get("count") == len(d.get("entries") or []),
          f"{d.get('count')} vs {len(d.get('entries') or [])}")
    for e in (d.get("entries") or [])[:20]:
        for field in ("name", "path", "is_dir", "size", "mtime", "ext", "hidden"):
            if field not in e:
                check(f"条目含 {field}", False, str(list(e.keys())))
                return
    check("条目字段齐全", True)


def test_fs_list_drive_root(srv: Server) -> None:
    """盘符根能列出来，且 parent 为空（再往上就没了）。"""
    d = srv.get("/api/fs/list?path=C%3A%2F")
    check("根目录 parent 为空", d.get("parent") == "", repr(d.get("parent")))
    check("根目录有内容", (d.get("total") or 0) > 0, str(d.get("total")))


def test_fs_list_subdirectory(srv: Server) -> None:
    """能进二级目录 —— 这是「点进去看文件夹内容」的核心。"""
    root = srv.get("/api/fs/list?path=C%3A%2F")
    dirs = [e for e in root["entries"] if e["is_dir"]]
    check("根目录下有目录", len(dirs) > 0, str(len(dirs)))
    if not dirs:
        return
    target = dirs[0]
    d = srv.get("/api/fs/list?path=" + target["path"].replace("\\", "/"))
    check("能进二级目录", "entries" in d, str(d)[:160])
    check("二级目录 parent 指向根",
          str(d.get("parent", "")).replace("\\", "/").rstrip("/").upper()
          == "C:".upper(),
          repr(d.get("parent")))


def test_fs_list_forbidden_blocked(srv: Server) -> None:
    """禁区目录要挡住。"""
    for path in ("C:\\Windows", "C:/Windows"):
        try:
            srv.get("/api/fs/list?path=" + path.replace("\\", "%5C").replace(":", "%3A"))
            check(f"{path} 被挡", False, "居然列出来了")
        except urllib.error.HTTPError as exc:
            check(f"{path} 被挡（403）", exc.code == 403, str(exc.code))


def test_fs_list_device_and_unc_blocked(srv: Server) -> None:
    """device path 与 UNC 要挡住。"""
    for label, raw in (
        ("device", "\\\\?\\C:\\Windows"),
        ("unc", "\\\\server\\share"),
    ):
        enc = raw.replace("\\", "%5C").replace(":", "%3A")
        try:
            srv.get("/api/fs/list?path=" + enc)
            check(f"{label} 路径被挡", False, "居然列出来了")
        except urllib.error.HTTPError as exc:
            check(f"{label} 路径被挡（403）", exc.code == 403, str(exc.code))


def test_fs_list_missing_path_404(srv: Server) -> None:
    try:
        srv.get("/api/fs/list?path=C%3A%5Cdefinitely-not-here-99887")
        check("不存在的路径回 404", False, "居然返回 200")
    except urllib.error.HTTPError as exc:
        check("不存在的路径回 404", exc.code == 404, str(exc.code))


def test_fs_list_requires_token(srv: Server) -> None:
    try:
        srv.get("/api/fs/list?path=C%3A%2F", token=False)
        check("无令牌被拒", False, "居然返回 200")
    except urllib.error.HTTPError as exc:
        check("无令牌被拒（401）", exc.code == 401, str(exc.code))


def test_fs_list_limit(srv: Server) -> None:
    """limit 要真的限住。"""
    d = srv.get("/api/fs/list?path=C%3A%2F&limit=3")
    check("limit 生效", len(d.get("entries") or []) <= 3, str(len(d.get("entries") or [])))
    check("total 仍是总数", (d.get("total") or 0) > 3, str(d.get("total")))


def test_custom_apps_crud(srv: Server) -> None:
    """自定义应用的增删改查往返。"""
    lst = srv.get("/api/apps/custom")
    check("列表返回 apps 键", "apps" in lst, str(list(lst.keys())))
    check("初始为空", lst.get("apps") == [], str(lst.get("apps")))

    # 造一个真实存在的目标
    exe = Path(srv.home) / "demo.exe"
    exe.write_bytes(b"MZ")

    created = srv.post("/api/apps/custom", {
        "name": "契约测试应用", "target": str(exe), "note": "往返用",
    })
    check("创建返回 ok", created.get("ok") is True, str(created)[:200])
    app = created.get("app") or {}
    aid = app.get("id") or ""
    check("返回 id", bool(aid), str(app)[:200])
    check("返回 name", app.get("name") == "契约测试应用", str(app.get("name")))
    check("返回 note", app.get("note") == "往返用", str(app.get("note")))

    lst = srv.get("/api/apps/custom")
    hit = next((x for x in lst["apps"] if x["id"] == aid), None)
    check("列表能查到", hit is not None, str(lst["apps"])[:200])
    if hit:
        for field in ("id", "name", "target", "note", "created_at"):
            check(f"列表项含 {field}", field in hit, str(list(hit.keys())))

    upd = srv.put(f"/api/apps/custom/{aid}", {
        "name": "改名了", "target": str(exe), "note": "改过了",
    })
    check("更新返回 ok", upd.get("ok") is True, str(upd)[:200])
    lst2 = srv.get("/api/apps/custom")
    check("更新不新增记录", len(lst2["apps"]) == 1, str(len(lst2["apps"])))
    check("名字已改", lst2["apps"][0]["name"] == "改名了", str(lst2["apps"][0]))

    srv.delete(f"/api/apps/custom/{aid}")
    check("删除后为空", srv.get("/api/apps/custom")["apps"] == [], "还有残留")
    try:
        srv.delete(f"/api/apps/custom/{aid}")
        check("重复删除回 404", False, "居然成功了")
    except urllib.error.HTTPError as exc:
        check("重复删除回 404", exc.code == 404, str(exc.code))


def test_custom_apps_validation(srv: Server) -> None:
    """坏的自定义应用要被拒。"""
    for label, body in (
        ("缺名字", {"target": "C:/x.exe"}),
        ("缺路径", {"name": "x"}),
        ("路径不存在", {"name": "x", "target": "C:/definitely-not-here-12345.exe"}),
    ):
        try:
            r = srv.post("/api/apps/custom", body)
            check(f"{label} 被拒", (not r.get("ok")), str(r)[:160])
        except urllib.error.HTTPError as exc:
            check(f"{label} 被拒（400）", exc.code == 400, str(exc.code))


def test_apps_launch_unknown_id(srv: Server) -> None:
    """启动不存在的应用要报错，不能静默成功。"""
    try:
        srv.post("/api/apps/launch", {"id": "根本不存在的应用"})
        check("未知 id 被拒", False, "居然返回成功")
    except urllib.error.HTTPError as exc:
        check("未知 id 被拒（400）", exc.code == 400, str(exc.code))


def test_apps_launch_rejects_path(srv: Server) -> None:
    """**launch 只接受 id** —— 传路径必须被拒。

    这是最关键的一条：接受路径就等于给了「运行任意程序」的通用原语。
    """
    for raw in ("C:\\Windows\\System32\\cmd.exe", "C:/Windows/System32/notepad.exe"):
        enc = raw.replace("\\", "%5C").replace(":", "%3A")
        try:
            srv.post("/api/apps/launch", {"id": enc})
            check(f"{raw} 被拒", False, "居然接受路径了")
        except urllib.error.HTTPError as exc:
            check(f"{raw} 被拒（400）", exc.code == 400, str(exc.code))


def test_apps_list_shape(srv: Server) -> None:
    """已安装应用的返回结构。"""
    d = srv.get("/api/apps?limit=50")
    check("返回 apps 键", "apps" in d, str(list(d.keys())))
    apps = d.get("apps") or []
    check("apps 是数组", isinstance(apps, list), type(apps).__name__)
    if apps:
        for field in ("name", "version", "publisher", "source", "path"):
            if field not in apps[0]:
                check(f"应用项含 {field}", False, str(list(apps[0].keys())))
                return
        check("应用项字段齐全", True)


def test_apps_icons_shape(srv: Server) -> None:
    """批量图标接口：一次拿全，返回 data:image/png;base64,... 的映射。"""
    d = srv.get("/api/apps/icons")
    check("返回 icons 键", "icons" in d, str(list(d.keys())))
    icons = d.get("icons") or {}
    check("icons 是映射", isinstance(icons, dict), type(icons).__name__)
    if not icons:
        check("取到至少一个图标", False, "一个都没取到")
        return
    check("取到至少一个图标", True)
    name, data = next(iter(icons.items()))
    check("图标是 data URL", str(data).startswith("data:image/png;base64,"),
          str(data)[:40])
    check("图标 key 是应用名", isinstance(name, str) and name)


def test_apps_all_have_icons(srv: Server) -> None:
    """应用列表里的每一条都应该拿到图标。

    这是这批改造的核心承诺：应用库不该再是一堆首字母色块。
    """
    apps = srv.get("/api/apps?limit=300").get("apps") or []
    icons = srv.get("/api/apps/icons").get("icons") or {}
    if not apps:
        check("有应用可测", False, "应用列表为空")
        return
    missing = [a["name"] for a in apps if a["name"] not in icons]
    check("每个应用都有图标", not missing,
          f"{len(missing)}/{len(apps)} 个没有：{missing[:8]}")


def test_index_libraries_shape(srv: Server) -> None:
    """知识库集合的返回结构（空库也要有形状）。"""
    d = srv.get("/api/index/libraries")
    check("返回 libraries 键", "libraries" in d, str(list(d.keys())))
    check("libraries 是数组", isinstance(d.get("libraries"), list),
          type(d.get("libraries")).__name__)


def test_images_shape(srv: Server) -> None:
    """图库：每张图要带来源标记，前端据此分区。"""
    d = srv.get("/api/images?limit=50")
    check("返回 images 键", "images" in d, str(list(d.keys())))
    images = d.get("images") or []
    check("images 是数组", isinstance(images, list), type(images).__name__)
    check("返回 counts 键", "counts" in d, str(list(d.keys())))
    if not images:
        check("图库结构正确", True)
        return
    item = images[0]
    for field in ("name", "path", "dir", "ext", "bytes", "mtime", "source"):
        if field not in item:
            check(f"图片项含 {field}", False, str(list(item.keys())))
            return
    check("图片项字段齐全", True)
    check("source 是合法来源",
          item["source"] in ("chat", "common", "recent"), item["source"])


def test_images_exclude_icons(srv: Server) -> None:
    """图标不该出现在图库里 —— 这是用户的原话「太多标」。"""
    images = srv.get("/api/images?limit=400").get("images") or []
    icons = [
        i["name"] for i in images
        if any(w in i["name"].lower()
               for w in ("icon", "logo", "favicon", "avatar", "badge", "placeholder"))
    ]
    check("没有图标混进来", not icons, f"{len(icons)} 个：{icons[:6]}")


def test_chat_accepts_session_id(srv: Server) -> None:
    """对话要能带 session_id —— 不点新建对话就续接同一个会话。"""
    try:
        srv.post("/api/chat", {"goal": "随便说点什么", "session_id": "abc123"})
        check("接受 session_id", True)
    except urllib.error.HTTPError as exc:
        check("接受 session_id", False, f"{exc.code} {exc.read()[:200]}")


def test_chat_retry_contract(srv: Server) -> None:
    """/api/chat/retry 的对外契约。

    重新生成 = 删掉最后一轮助手回复、用同一提问重跑。
    关键语义：用户提问一个字都不能动。
    """
    # 1) 空 task_id 被拒
    try:
        srv.post("/api/chat/retry", {"task_id": ""})
        check("空 task_id 被拒（400）", False, "居然成功了")
    except urllib.error.HTTPError as exc:
        check("空 task_id 被拒（400）", exc.code == 400, str(exc.code))

    # 2) 未知会话被拒
    try:
        srv.post("/api/chat/retry", {"task_id": "根本不存在的会话"})
        check("未知会话被拒（404）", False, "居然成功了")
    except urllib.error.HTTPError as exc:
        check("未知会话被拒（404）", exc.code == 404, str(exc.code))

    # 3) 正常重试：先造一个带消息的会话
    created = srv.post("/api/chat", {"goal": "重试契约测试用的提问"})
    check("准备会话 ok", created.get("status") == "started", str(created)[:160])
    time.sleep(1.5)

    sessions = srv.get("/api/sessions?limit=5").get("sessions") or []
    hit = next((s for s in sessions if s.get("goal") == "重试契约测试用的提问"), None)
    check("找到刚建的会话", hit is not None, str(sessions[:2]))
    if not hit:
        return
    tid = hit["id"]

    detail = srv.get(f"/api/sessions/{tid}")
    msgs = detail.get("messages") or []
    check("会话里有消息", len(msgs) >= 1, f"{len(msgs)} 条")

    # 4) 重试接口接受这个会话
    try:
        r = srv.post("/api/chat/retry", {"task_id": tid})
        check("重试接口返回 ok", r.get("ok") is True, str(r)[:200])
        check("重试返回 task_id", r.get("task_id") == tid, str(r)[:200])
    except urllib.error.HTTPError as exc:
        check("重试接口返回 ok", False, f"{exc.code} {exc.read()[:200]}")


def test_local_models_endpoint(srv: Server) -> None:
    """/api/providers/local 的契约。

    用户装了本地模型（Ollama），要能列出来并一键切换。
    服务没起时返回空列表 —— 那是正常状态，不能报 500。
    """
    d = srv.get("/api/providers/local")
    check("返回 models 键", "models" in d, str(list(d.keys())))
    check("models 是数组", isinstance(d.get("models"), list), type(d.get("models")).__name__)
    check("返回 base_url", "base_url" in d, str(list(d.keys())))
    for m in (d.get("models") or []):
        for field in ("name", "size", "family", "provider"):
            if field not in m:
                check(f"本地模型项含 {field}", False, str(list(m.keys())))
                return
    check("本地模型项字段齐全", True)


def test_register_local_model_rejects_empty(srv: Server) -> None:
    """空模型名要挡掉。"""
    try:
        srv.post("/api/providers/local", {"model": ""})
        check("空模型名被拒（400）", False, "居然成功了")
    except urllib.error.HTTPError as exc:
        check("空模型名被拒（400）", exc.code == 400, str(exc.code))


def test_register_local_model_creates_provider(srv: Server) -> None:
    """登记一个本地模型后，provider 列表里要能看到它。"""
    name = "契约测试-本地模型"
    try:
        r = srv.post("/api/providers/local", {"model": name})
        check("登记返回 name", bool(r.get("name")), str(r)[:200])
        check("登记返回 model", r.get("model") == name, str(r)[:200])
        # provider 列表里要能查到
        items = srv.get("/api/providers").get("items") or []
        hit = next((i for i in items if i.get("name") == r.get("name")), None)
        check("provider 列表里有它", hit is not None, str([i.get("name") for i in items]))
        if hit:
            check("模型名对得上", hit.get("model") == name, str(hit))
        # 清理，别留垃圾在用户配置里
        srv.delete(f"/api/providers/{r.get('name')}")
    except urllib.error.HTTPError as exc:
        check("登记本地模型", False, f"{exc.code} {exc.read()[:200]}")


def test_web_page_served(srv: Server) -> None:
    """首页要真的带上新界面。"""
    url = f"http://127.0.0.1:{srv.port}/"
    with urllib.request.urlopen(url, timeout=20) as resp:
        html = resp.read().decode("utf-8")
    check("首页可访问", len(html) > 100_000, str(len(html)))
    # 此电脑：目录浏览器
    check("首页含目录浏览接口", "/api/fs/list" in html)
    check("首页含目录浏览器", "function renderPC" in html and "loadFsDir" in html)
    check("首页含面包屑", "fsCrumbs" in html)
    # 应用库
    check("首页含批量应用图标接口", "/api/apps/icons" in html)
    check("首页含启动应用", "/api/apps/launch" in html)
    check("首页含添加应用", "appAddCustom" in html)
    # 前端不许逐个请求图标 —— 那会把后端线程池占满，点「打开」排队等 96 秒
    # 只查代码：注释里写着「以前错在哪」，那提到这个路径是应该的
    check("首页不逐个请求应用图标", "/api/app/icon" not in strip_js_comments(html))
    # 知识库
    check("首页含知识库集合接口", "/api/index/libraries" in html)
    check("首页含库卡片", "kblib" in html)
    # 对话
    check("首页含模式提示", "modeHint" in html)
    check("首页带 interaction 到后端", "interaction" in html)
    # 对话界面布局（对标 Marvis）
    check("首页含内容列宽变量", "--chat-col" in html)
    check("首页含列宽测量", "function fitChatCol" in html)
    check("首页含输入框自适应", "function autoGrow" in html)
    check("首页含消息操作栏", "mactions" in html)
    check("首页含重新生成接口", "/api/chat/retry" in html)


def test_bundle_sync() -> None:
    """打包暂存目录的界面必须与源码同步。"""
    src = (ROOT / "core" / "web" / "index.html").read_text("utf-8")
    staged = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "core" / "web" / "index.html"
    if not staged.exists():
        check("打包暂存目录存在", False, str(staged))
        return
    copy = staged.read_text("utf-8")
    check("打包界面与源码一致", copy == src,
          f"源码 {len(src)} 字节 vs 暂存 {len(copy)} 字节")
    check("打包界面含目录浏览", "/api/fs/list" in copy)
    check("打包界面含批量图标", "/api/apps/icons" in copy)
    check("打包界面不逐个请求图标", "/api/app/icon" not in strip_js_comments(copy))


def test_bundle_modules_in_sync() -> None:
    """新增/改动的后端模块也要同步到打包暂存目录。

    桌面壳的 core_dir 优先找仓库根目录，但那份拷贝仍然存在 ——
    一旦 exe 被单独拷走（脱离仓库运行），跑起来的就是旧代码。
    """
    src_dir = ROOT / "core" / "oryx"
    staged_dir = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "core" / "oryx"
    if not staged_dir.exists():
        check("打包暂存目录存在", False, str(staged_dir))
        return
    for name in ("appicon.py", "manage.py", "server.py", "localfs.py",
                "imagegallery.py", "providers.py", "store.py", "loop.py"):
        src = src_dir / name
        staged = staged_dir / name
        if not staged.exists():
            check(f"打包含 {name}", False, "缺失")
            continue
        check(f"打包 {name} 与源码一致",
              staged.read_text("utf-8") == src.read_text("utf-8"),
              "内容不一致 —— 重新同步")


def main() -> int:
    srv = Server()
    print("启动 server …")
    if not srv.start():
        print("  FAIL server 没起来")
        return 1
    print(f"  server 就绪（端口 {srv.port}）\n")
    try:
        tests = [
            ("H1-1 目录列表结构", test_fs_list_shape),
            ("H1-2 盘符根", test_fs_list_drive_root),
            ("H1-3 进二级目录", test_fs_list_subdirectory),
            ("H1-4 禁区被挡", test_fs_list_forbidden_blocked),
            ("H1-5 device/UNC 被挡", test_fs_list_device_and_unc_blocked),
            ("H1-6 不存在的路径 404", test_fs_list_missing_path_404),
            ("H1-7 目录浏览要令牌", test_fs_list_requires_token),
            ("H1-8 limit 生效", test_fs_list_limit),
            ("H1-9 自定义应用 CRUD", test_custom_apps_crud),
            ("H1-10 自定义应用校验", test_custom_apps_validation),
            ("H1-11 启动未知应用", test_apps_launch_unknown_id),
            ("H1-12 launch 拒绝路径", test_apps_launch_rejects_path),
            ("H1-13 已安装应用结构", test_apps_list_shape),
            ("H1-13b 批量图标结构", test_apps_icons_shape),
            ("H1-13c 每个应用都有图标", test_apps_all_have_icons),
            ("H1-14 知识库集合结构", test_index_libraries_shape),
            ("H1-14b 图库结构", test_images_shape),
            ("H1-14c 图库排除图标", test_images_exclude_icons),
            ("H1-14d 对话接受 session_id", test_chat_accepts_session_id),
            ("H1-14e 重新生成契约", test_chat_retry_contract),
            ("H1-14f 本地模型列表", test_local_models_endpoint),
            ("H1-14g 本地模型登记校验", test_register_local_model_rejects_empty),
            ("H1-14h 本地模型登记", test_register_local_model_creates_provider),
            ("H1-15 首页带新界面", test_web_page_served),
            ("H1-16 打包界面同步", test_bundle_sync),
            ("H1-17 打包后端模块同步", test_bundle_modules_in_sync),
        ]
        for label, fn in tests:
            print(f"\n[{label}]")
            try:
                fn(srv) if fn.__code__.co_argcount else fn()
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                FAILED.append(label)
    finally:
        srv.stop()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
