"""第五批 · 工作流模板的 HTTP 契约验证。

模板是"另一条入口" —— 它必须复用现有的 workflow_run 链路，
不能因为多了一条入口就把子 Agent 白名单、审计、权限绕过去。
所以这套的核心断言是：**触发模板时，白名单校验照样生效**。

起真实内核 + 真实 HTTP（走 FastAPI + 鉴权头），跑真实请求，逐字段核对。

运行：python -m tests.verify_batch4_g1
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

# 一份合法的编排。agent 用的是真存在的子 Agent（agents/ 目录里有这三个）。
GOOD_PLAN = {
    "name": "模板契约验证",
    "goal": "读项目文档并如实汇报",
    "stages": [
        {"name": "读取", "steps": [
            {"id": "a", "name": "读需求", "agent": "doc-manager",
             "goal": "读一下项目文档里有哪些需求，读不到就明说"},
        ]},
        {"name": "复核", "steps": [
            {"id": "b", "name": "再确认", "agent": "doc-manager",
             "goal": "把刚才看到的要点压缩成三条", "needs": ["a"]},
        ]},
    ],
}


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- HTTP 客户端
class Server:
    """起一个真实的 Oryx server（uvicorn），用 HTTP 访问它。"""

    def __init__(self, port: int = 8792) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.token = ""
        # 隔离的 ORYX_HOME：这套会建/删模板，不能碰到用户真实库
        self.home = tempfile.mkdtemp(prefix="oryx_g1_home_")

    def start(self) -> bool:
        # CONFIG_PATH 是导入期算出来的，所以只能在子进程的环境里设 ORYX_HOME，
        # 父进程里改 os.environ 对已经 import 过的模块无效
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

    def _raw(self, method: str, path: str, body=None):
        # 路径里的中文必须 percent-encode —— 浏览器会自动做，
        # 而 urllib 不会，非 ASCII 直接抛 UnicodeEncodeError。
        # 模板 id 是按名字生成的（中文名 → 中文 id），所以这一步是必需的。
        from urllib.parse import quote

        url = f"http://127.0.0.1:{self.port}{quote(path, safe='/?=&')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Oryx-Token", self.token)
        req.add_header("X-Oryx-Request", "1")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())

    def get(self, path: str):
        return self._raw("GET", path)

    def post(self, path: str, body=None):
        return self._raw("POST", path, body if body is not None else {})

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
def test_isolated_home(srv: Server) -> None:
    """确认 server 跑在隔离的临时库里，不是用户的真实数据。"""
    import sqlite3

    check("配置落在临时目录", Path(srv.home).is_dir(), srv.home)
    check("临时目录里建了库", (Path(srv.home) / "oryx.db").exists())
    check("临时目录与用户真实目录不同",
          Path(srv.home).resolve() != (Path(os.environ.get("APPDATA", "")) / "Oryx").resolve(),
          srv.home)
    con = sqlite3.connect(Path(srv.home) / "oryx.db")
    try:
        names = [r[0] for r in con.execute("SELECT name FROM workflow_templates")]
    except Exception:
        names = []
    finally:
        con.close()
    check("临时库初始没有模板", not names, str(names[:5]))


def test_template_list_empty(srv: Server) -> None:
    """空列表也要返回 {"templates": [...]} —— 前端直接读 .templates。"""
    d = srv.get("/api/workflows/templates")
    check("列表返回 dict", isinstance(d, dict), str(d)[:120])
    check("列表有 templates 键", "templates" in d, str(list(d.keys())))
    check("templates 是数组", isinstance(d.get("templates"), list),
          type(d.get("templates")).__name__)
    check("空库时列表为空（前置条件）", d.get("templates") == [], str(d.get("templates")))


def test_create_and_read(srv: Server) -> None:
    """建模板 → 列表能查到 → 详情含 plan。"""
    r = srv.post("/api/workflows/templates", {
        "name": "文档巡检", "description": "过一遍文档",
        "plan": GOOD_PLAN, "args": {"env": "test"},
    })
    check("创建返回 ok", r.get("ok") is not False, str(r)[:200])
    item = r.get("content") or {}
    tid = item.get("id") or ""
    check("返回模板 id", bool(tid), str(item)[:200])
    check("返回的 name 是模板名", item.get("name") == "文档巡检", str(item.get("name")))
    if not tid:
        return

    lst = srv.get("/api/workflows/templates")["templates"]
    hit = next((x for x in lst if x["id"] == tid), None)
    check("列表能查到它", hit is not None, str([x["id"] for x in lst]))
    if hit:
        for field in ("id", "name", "description", "stage_count", "step_count",
                      "run_count", "created_at", "updated_at"):
            check(f"列表项含 {field}", field in hit, str(list(hit.keys())))
        check("阶段数正确", hit.get("stage_count") == 2, str(hit.get("stage_count")))
        check("步数正确", hit.get("step_count") == 2, str(hit.get("step_count")))
        # 列表不带 plan 正文 —— 否则模板一多响应体就撑起来
        check("列表项不含 plan 正文", "plan" not in hit, str(list(hit.keys())))

    d = srv.get(f"/api/workflows/templates/{tid}")
    check("详情返回 content 包装", "content" in d, str(list(d.keys())))
    det = d.get("content") or {}
    check("详情含 plan", isinstance(det.get("plan"), dict), str(type(det.get("plan"))))
    check("plan 的 goal 对得上", (det.get("plan") or {}).get("goal") == GOOD_PLAN["goal"],
          str((det.get("plan") or {}).get("goal")))
    check("plan 的 stages 完整", len((det.get("plan") or {}).get("stages") or []) == 2,
          str(len((det.get("plan") or {}).get("stages") or [])))
    # 往返：存进去的 plan 必须能被解析回等价结构
    st = (det.get("plan") or {}).get("stages") or []
    check("往返后 step 的 needs 还在",
          (st[1]["steps"][0].get("needs") or []) == ["a"], str(st[1]["steps"][0]))
    check("args 透传", det.get("args") == {"env": "test"}, str(det.get("args")))


def test_update_and_delete(srv: Server) -> None:
    """改模板 → 生效；删模板 → 404。"""
    r = srv.post("/api/workflows/templates", {"name": "待改的", "plan": GOOD_PLAN})
    tid = (r.get("content") or {}).get("id") or ""
    if not tid:
        check("建模板成功", False, str(r)[:160])
        return

    plan2 = json.loads(json.dumps(GOOD_PLAN))
    plan2["goal"] = "改过的目标"
    u = srv.put(f"/api/workflows/templates/{tid}", {
        "name": "改过的名字", "description": "改过了", "plan": plan2,
    })
    check("更新返回 ok", u.get("ok") is not False, str(u)[:200])
    det = srv.get(f"/api/workflows/templates/{tid}")["content"]
    check("名字已更新", det.get("name") == "改过的名字", str(det.get("name")))
    check("plan 已更新", (det.get("plan") or {}).get("goal") == "改过的目标",
          str((det.get("plan") or {}).get("goal")))
    check("更新不新增记录",
          len([x for x in srv.get("/api/workflows/templates")["templates"]
               if x["id"] == tid]) == 1)

    srv.delete(f"/api/workflows/templates/{tid}")
    try:
        srv.get(f"/api/workflows/templates/{tid}")
        check("删除后详情回 404", False, "居然还能查到")
    except urllib.error.HTTPError as exc:
        check("删除后详情回 404", exc.code == 404, str(exc.code))

    try:
        srv.delete(f"/api/workflows/templates/{tid}")
        check("重复删除回 404", False, "居然成功了")
    except urllib.error.HTTPError as exc:
        check("重复删除回 404", exc.code == 404, str(exc.code))


def test_bad_plan_rejected(srv: Server) -> None:
    """坏定义要 400 —— 前端把它显示在弹层的错误行里。"""
    for name, plan in (
        ("缺 goal", {"stages": [{"name": "S", "steps": [
            {"id": "a", "agent": "doc-manager", "goal": "x"}]}]}),
        ("空 stages", {"goal": "g", "stages": []}),
        ("阶段没有 step", {"goal": "g", "stages": [{"name": "S", "steps": []}]}),
        ("step 缺 agent", {"goal": "g", "stages": [{"name": "S", "steps": [
            {"id": "a", "goal": "x"}]}]}),
        ("needs 指向不存在的 step", {"goal": "g", "stages": [{"name": "S", "steps": [
            {"id": "a", "agent": "doc-manager", "goal": "x", "needs": ["zzz"]}]}]}),
    ):
        try:
            r = srv.post("/api/workflows/templates", {"name": "坏的", "plan": plan})
            rejected = (not r.get("ok")) or (r.get("content") or {}).get("id") is None
            check(f"{name} 被拒", rejected, str(r)[:200])
        except urllib.error.HTTPError as exc:
            check(f"{name} 被拒", exc.code in (400, 422), str(exc.code))


def test_unknown_agent_rejected_on_run(srv: Server) -> None:
    """**白名单没被绕过** —— 这是本批最关键的一条。

    模板是"另一条入口"，但它不能变成绕过子 Agent 白名单的后门。
    所以模板里写一个不存在的 agent，触发时必须被 run_workflow 的
    白名单检查挡下来，并明确告诉用户缺哪个。
    """
    plan = {
        "name": "白名单测试", "goal": "用不存在的子 Agent",
        "stages": [{"name": "S", "steps": [
            {"id": "a", "name": "跑", "agent": "根本不存在的agent", "goal": "x"},
        ]}],
    }
    r = srv.post("/api/workflows/templates", {"name": "白名单测试", "plan": plan})
    tid = (r.get("content") or {}).get("id") or ""
    if not tid:
        check("建模板成功", False, str(r)[:160])
        return

    try:
        out = srv.post(f"/api/workflows/templates/{tid}/run", {})
        # 走到了这里说明没被挡 —— 报错也可能是 400，要看具体内容
        check("未知 agent 被挡下", False, f"居然返回成功：{str(out)[:200]}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        check("未知 agent 被挡下（400）", exc.code == 400, str(exc.code))
        check("报错里点名了缺失的 agent", "根本不存在的agent" in body, body[:200])
        # 错误消息不该带 ValueError: 这种给开发者看的前缀
        check("错误消息没有异常类名前缀", "ValueError" not in body, body[:200])

    # 挡下之后不该产生一条 run —— 压根没开跑
    runs = srv.get("/api/workflows?limit=50")["runs"]
    check("被挡下时不产生运行记录",
          not any(r2.get("name") == "白名单测试" for r2 in runs),
          str([r2.get("name") for r2 in runs][:5]))


def test_run_template_creates_run(srv: Server) -> None:
    """触发模板 → 产生一条运行记录，且 plan 被落库（能反存成模板）。

    临时库没有 provider，step 会失败 —— 这是**预期**的：
    契约验证要的是链路通，不是模型输出。
    """
    r = srv.post("/api/workflows/templates", {
        "name": "触发验证", "plan": GOOD_PLAN,
    })
    tid = (r.get("content") or {}).get("id") or ""
    if not tid:
        check("建模板成功", False, str(r)[:160])
        return

    out = srv.post(f"/api/workflows/templates/{tid}/run", {})
    check("触发返回 ok 字段", "ok" in out, str(out)[:200])
    run_id = out.get("run_id") or ""
    check("触发返回 run_id", bool(run_id), str(out)[:200])
    check("触发返回 status", bool(out.get("status")), str(out)[:160])
    if not run_id:
        return

    # 运行记录里能查到它
    lst = srv.get("/api/workflows?limit=50")["runs"]
    hit = next((x for x in lst if x["id"] == run_id), None)
    check("运行记录里有这次运行", hit is not None, str([x["id"] for x in lst]))
    if hit:
        check("运行的名字来自模板", hit.get("name") == GOOD_PLAN["name"], str(hit.get("name")))
        check("步数与 plan 一致", hit.get("total_steps") == 2, str(hit.get("total_steps")))
        # has_plan 决定前端给不给「存为模板」按钮 —— 老记录没存 plan，
        # 硬给按钮点了只会弹报错
        check("列表项标记了有 plan", hit.get("has_plan") is True, str(hit.get("has_plan")))

    # 触发计数 +1
    tpl = srv.get(f"/api/workflows/templates/{tid}")["content"]
    check("模板触发计数 +1", tpl.get("run_count") == 1, str(tpl.get("run_count")))
    check("记录了上次触发时间", tpl.get("last_run_at") is not None, str(tpl.get("last_run_at")))

    # plan 被落库了 —— "从 run 存成模板"的数据来源
    p = srv.get(f"/api/workflows/{run_id}/plan")
    check("取 run 的 plan 返回 content", "content" in p, str(list(p.keys())))
    plan = p.get("content") or {}
    check("plan 的 goal 对得上", plan.get("goal") == GOOD_PLAN["goal"], str(plan.get("goal")))
    check("plan 的 stages 数对得上", len(plan.get("stages") or []) == 2,
          str(len(plan.get("stages") or [])))
    st = plan.get("stages") or []
    check("往返后 needs 还在", (st[1]["steps"][0].get("needs") or []) == ["a"],
          str(st[1]["steps"][0]))
    check("往返后 agent 还在", st[0]["steps"][0].get("agent") == "doc-manager",
          str(st[0]["steps"][0]))


def test_plan_of_missing_run(srv: Server) -> None:
    """查不存在的 run 的 plan 要 404。"""
    try:
        srv.get("/api/workflows/wfr_nonexistent/plan")
        check("查不到的 run 的 plan 回 404", False, "居然返回 200")
    except urllib.error.HTTPError as exc:
        check("查不到的 run 的 plan 回 404", exc.code == 404, str(exc.code))


def test_template_missing(srv: Server) -> None:
    """查不存在的模板 → 404；触发不存在的模板 → 404。"""
    for label, fn in (
        ("详情", lambda: srv.get("/api/workflows/templates/tpl_不存在")),
        ("触发", lambda: srv.post("/api/workflows/templates/tpl_不存在/run", {})),
    ):
        try:
            fn()
            check(f"{label}不存在的模板回 404", False, "居然返回 200")
        except urllib.error.HTTPError as exc:
            check(f"{label}不存在的模板回 404", exc.code == 404, str(exc.code))


def test_auth_guard(srv: Server) -> None:
    """模板接口必须和其他接口一样受双防护约束。"""
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}/api/workflows/templates", method="GET")
    req.add_header("X-Oryx-Request", "1")   # 有来源标记但没令牌
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            check("无令牌被拒", False, f"返回 {resp.status}")
    except urllib.error.HTTPError as exc:
        check("无令牌被拒", exc.code == 401, str(exc.code))


def test_web_page_served(srv: Server) -> None:
    """首页要真的带上模板界面（打包界面与源码一致）。"""
    url = f"http://127.0.0.1:{srv.port}/"
    with urllib.request.urlopen(url, timeout=20) as resp:
        html = resp.read().decode("utf-8")
    check("首页可访问", len(html) > 100_000, str(len(html)))
    check("首页含模板 tab", 'data-wftab="tpl"' in html)
    check("首页含模板卡片接线", "data-wftplrun=" in html)
    check("首页含模板编辑器", "function editTemplate" in html)
    check("首页含模板接口调用", "/api/workflows/templates" in html)
    check("首页含起步模板", "WF_TPL_SEEDS" in html)
    check("首页含详情页存模板", 'id="wfSaveTpl"' in html)


def test_bundle_sync() -> None:
    """打包暂存目录的界面必须与源码同步（否则双击启动加载旧界面）。"""
    src = (ROOT / "core" / "web" / "index.html").read_text("utf-8")
    staged = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "core" / "web" / "index.html"
    if not staged.exists():
        check("打包暂存目录存在", False, str(staged))
        return
    copy = staged.read_text("utf-8")
    check("打包界面与源码一致", copy == src,
          f"源码 {len(src)} 字节 vs 暂存 {len(copy)} 字节")
    check("打包界面含模板接口", "/api/workflows/templates" in copy)


def test_plan_roundtrip_unit() -> None:
    """纯单元：WorkflowPlan.to_dict() 与 parse() 必须严格互逆。

    这是最容易犯的错 —— 存进去的 JSON 自己解析不回来，
    "从 run 存成模板"就会变成一个空模板。
    """
    from oryx.workflow import WorkflowPlan

    parsed = WorkflowPlan.parse(GOOD_PLAN)
    back = parsed.to_dict()
    again = WorkflowPlan.parse(back).to_dict()
    check("plan 往返一次一致", back == again, str(back)[:200])
    check("往返后 goal 不变", back["goal"] == GOOD_PLAN["goal"], str(back.get("goal")))
    check("往返后 name 不变", back["name"] == GOOD_PLAN["name"], str(back.get("name")))
    check("往返后 stage 数不变", len(back["stages"]) == 2, str(len(back["stages"])))
    check("往返后 step 的 agent 不变",
          back["stages"][0]["steps"][0]["agent"] == "doc-manager",
          str(back["stages"][0]["steps"][0]))
    check("往返后 needs 不变",
          back["stages"][1]["steps"][0]["needs"] == ["a"],
          str(back["stages"][1]["steps"][0]))
    check("往返后 parallel 键存在",
          "parallel" in back["stages"][0]["steps"][0], str(back["stages"][0]["steps"][0]))


def test_old_run_without_plan(srv: Server) -> None:
    """没留档 plan 的老记录：has_plan 必须是 false。

    前端据此决定给不给「存为模板」按钮 —— 老记录补不回编排定义，
    硬给按钮点了只会弹一句"取不到这次运行的编排"。
    """
    import sqlite3

    db = Path(srv.home) / "oryx.db"
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO workflow_runs (id, name, goal, status, args, error, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("wfr_legacy", "老记录", "旧的目标", "done", "{}", "", time.time()),
        )
        con.commit()
    finally:
        con.close()

    lst = srv.get("/api/workflows?limit=50")["runs"]
    hit = next((x for x in lst if x["id"] == "wfr_legacy"), None)
    check("老记录能在列表里查到", hit is not None, str([x["id"] for x in lst]))
    if hit:
        check("老记录 has_plan 为 false", hit.get("has_plan") is False, str(hit.get("has_plan")))
    # 详情也要带这个标记 —— 详情弹层里也有「存为模板」按钮
    d = srv.get("/api/workflows/wfr_legacy")
    check("详情带 has_plan=false", (d.get("content") or {}).get("has_plan") is False,
          str((d.get("content") or {}).get("has_plan")))


def test_seed_plan_is_pure_plan() -> None:
    """起步模板的 plan 必须是**纯 plan**，不能带外层包装。

    前端把 seed 的 plan 直接塞进编辑器 —— 如果 seed.plan 里又套了一层
    {name, plan:{...}}，用户一点保存就报"工作流缺少 goal"。
    """
    import re as _re

    html = (CORE / "web" / "index.html").read_text("utf-8")
    # 从 WF_TPL_SEEDS 里抠出第一份 seed 的 plan 段做静态校验
    m = _re.search(r"const WF_TPL_SEEDS = \[(.*?)\n\];", html, _re.S)
    check("找到 WF_TPL_SEEDS", m is not None)
    if not m:
        return
    block = m.group(1)
    check("seed 里定义了 plan 字段", "plan: {" in block, block[:200])
    # 每份 seed 的 plan 顶层必须有 name / goal / stages
    check("seed 的 plan 顶层有 goal", "goal:" in block, block[:200])
    check("seed 的 plan 顶层有 stages", "stages:" in block, block[:200])
    check("seed 的 plan 顶层有 name", "name:" in block, block[:200])
    # 前端取的是 preset.plan 而不是 preset 本身
    check("编辑器取的是 preset.plan", "(preset && preset.plan)" in html, "preset.plan 没被正确取用")


def main() -> int:
    srv = Server()
    print("启动 server …")
    if not srv.start():
        print("  FAIL server 没起来")
        return 1
    print(f"  server 就绪（端口 {srv.port}）\n")
    try:
        tests = [
            ("G1-0 plan 往返（纯单元）", test_plan_roundtrip_unit),
            ("G1-1 隔离临时库", test_isolated_home),
            ("G1-2 空库时的模板列表", test_template_list_empty),
            ("G1-3 建模板 + 读详情", test_create_and_read),
            ("G1-4 更新与删除", test_update_and_delete),
            ("G1-5 坏定义被拒", test_bad_plan_rejected),
            ("G1-6 白名单没被绕过", test_unknown_agent_rejected_on_run),
            ("G1-7 触发模板产生运行记录", test_run_template_creates_run),
            ("G1-8 查不到 run 的 plan 回 404", test_plan_of_missing_run),
            ("G1-9 不存在的模板回 404", test_template_missing),
            ("G1-10 老记录没有 plan", test_old_run_without_plan),
            ("G1-11 起步模板是纯 plan", test_seed_plan_is_pure_plan),
            ("G1-12 鉴权双防护", test_auth_guard),
            ("G1-13 首页带模板界面", test_web_page_served),
            ("G1-14 打包界面同步", test_bundle_sync),
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
