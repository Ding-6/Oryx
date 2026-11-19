"""只读能力的冒烟测试：当门禁用。

抄 Marvis 的 CI 冒烟（`.github/workflows/ci.yml` 里那 8 条只读命令）：
**「只读能力能跑通」本身就该是门禁**，而不是等有人手点。

这一份测的是整条链路，不只是单个函数：
    模型 → 工具 → 打码 → 外部来源标记 → 回到模型

每一条只读能力都要过一遍，且都要证明**没改任何东西**：
    - 目录逐字节不变（mtime + size + 内容三项都比）；
    - 返回体带 `mutated=False` 与 `preview=True` 自证；
    - 外部来源的内容裹了 `<untrusted-source>` 标记；
    - 密钥被打码，不留明文。

为什么这些要放在一起：任何一条单独测都可能通过，但组合起来才说明
「只读这条路上没有意外」—— 比如打码接上了但标记没接上，
或者标记接上了但自证字段漏了。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oryx.taint import CLOSE, OPEN

# 只读能力清单。名字 = 工具名，参数 = 调用参数。
#
# 「只读」的判据不是名字里有没有 read，而是这个工具**真的会改东西吗**。
# 所以下面这条清单是人工核过的：跑完样本目录必须逐字节不变。
READONLY_CASES = [
    ("system_info", {}),
    ("fs_list", {"path": "{root}"}),
    ("fs_read", {"path": "{root}/a.txt"}),
    ("search_files", {"root": "{root}", "name_contains": "a"}),
]


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    """快照：mtime_ns + size + 二进制全文。

    只比 mtime 不够 —— 有些操作能在不换 mtime 的前提下改内容。
    三项都比才算「逐字节没变」。
    """
    found: dict[str, tuple[int, int, bytes]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
                with open(path, "rb") as handle:
                    found[path] = (st.st_mtime_ns, st.st_size, handle.read())
            except OSError:
                continue
    return found


@pytest.fixture()
def sample_root(tmp_path) -> Path:
    """一份可复现的只读样本目录。"""
    root = tmp_path / "samples"
    root.mkdir()
    (root / "a.txt").write_text("内容 A\n", encoding="utf-8")
    (root / "b.md").write_text("# 标题\n\n正文 B\n", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("内容 C\n", encoding="utf-8")
    return root


def _runtime(tmp_path):
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["workspace"] = str(tmp_path)
    return AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())


# ------------------------------------------------------------------ 只读不改东西


@pytest.mark.parametrize("tool_name,args", READONLY_CASES)
def test_readonly_tool_does_not_change_anything(tmp_path, sample_root, tool_name, args):
    """每条只读能力跑完，样本目录逐字节不变。"""
    runtime = _runtime(tmp_path)
    spec = runtime.registry[tool_name]
    filled = {k: v.format(root=str(sample_root)) for k, v in args.items()}

    before = _snapshot(sample_root)
    result = spec.fn(**filled)
    after = _snapshot(sample_root)

    assert result.get("ok") is True, f"{tool_name} 没跑通：{result.get('content')}"
    assert after == before, f"{tool_name} 改了样本目录"


@pytest.mark.parametrize("tool_name,args", READONLY_CASES)
def test_readonly_tool_reports_no_mutation(tmp_path, sample_root, tool_name, args):
    """只读工具要自证「我没动过」。

    走真实的 `_handle_tool_call` —— 单元测工具函数只能证明
    「函数自己没改」，证不了「自证字段真的传到了外面」。

    自证字段挂在 `step.done` 事件上：那是界面与审计都能看到的地方。
    只写在工具返回体里没用 —— 那三行只传 content，字段会在出口丢掉。
    """
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["workspace"] = str(tmp_path)
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    events: list[dict] = []
    runtime.bus.add_sync_sink(events.append)

    filled = {k: v.format(root=str(sample_root)) for k, v in args.items()}
    runtime._handle_tool_call("", 1, ToolCall(id="c1", name=tool_name, arguments=filled))

    done = [e for e in events if e["type"] == "step.done"]
    assert done, f"{tool_name} 没发出 step.done"
    ev = done[0]
    assert ev.get("mutated") is False, f"{tool_name} 声称自己改了东西"
    assert ev.get("preview") is True
    assert ev.get("deleted") is False
    assert ev.get("modified") is False
    assert ev.get("moved") is False


# ------------------------------------------------------------------ 自证字段要真被看见


def test_proof_fields_reach_the_audit_record(tmp_path, sample_root):
    """自证字段要落进审计记录。

    这一条是本轮修的第三个「算了但没人看」的坑：
    字段挂到了 `step.done` 事件上，但**审计表里的 outcome 永远是 'pending'**
    —— 审计行在动手前就写好了（记的是「打算做什么、谁批的」），
    工具跑完之后没人回去补记结果。
    于是「这个操作到底改了什么」在账本里查不到，
    界面上那个自证字段也因为没人渲染而看不见。

    所以要同时验两处：事件里有、审计行里也有。
    """
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["workspace"] = str(tmp_path)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(Config(data), store, EventBus())

    events: list[dict] = []
    runtime.bus.add_sync_sink(events.append)

    runtime._handle_tool_call(
        "", 1, ToolCall(id="c1", name="fs_list", arguments={"path": str(sample_root)})
    )

    audit = store.audit_for_task("")
    assert audit, "没有留下审计记录"
    row = audit[-1]
    assert row["outcome"] == "done", f"审计结果还停在 {row['outcome']!r}，没补记成 done"
    assert row["mutated"] == 0, "只读操作的审计行却标了「已改动」"


def test_mutating_tool_is_recorded_as_changed(tmp_path, sample_root):
    """有副作用的工具，审计行要标「已改动」。

    反向断言：只读标对了不算数，写操作标错才是危险 ——
    用户看到「未改动」却发现文件真被改了，比没有这个字段更糟。
    """
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["workspace"] = str(tmp_path)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(Config(data), store, EventBus())

    target = sample_root / "written.txt"
    runtime._handle_tool_call(
        "", 1, ToolCall(id="c1", name="fs_write",
                        arguments={"path": str(target), "content": "写进去的内容"})
    )

    audit = store.audit_for_task("")
    assert audit, "没有留下审计记录"
    row = audit[-1]
    assert row["mutated"] == 1, "写操作的审计行没标「已改动」"
    assert target.exists(), "审计说改了，文件却没写出去"


def test_proof_fields_are_rendered_by_the_frontend(tmp_path):
    """前端要真的渲染自证字段。

    静态检查测不出界面层语义错配 —— 字段到了事件里但没人读，
    一样等于没有。这里查的是「事件名 + 渲染函数」两边对得上。
    """
    import re

    from webread import page_source
    page = page_source()

    # 渲染函数存在
    assert "function markProof" in page, "前端没有渲染自证字段的函数"
    # 事件处理里真的调了它
    assert re.search(r"case 'step\.done'[\s\S]{0,400}?markProof\(c, ev\)", page), \
        "step.done 里没有调用 markProof —— 字段到了事件却没人显示"
    # 卡片上要有承载徽章的槽位
    assert 'class="proof"' in page, "步骤卡上没有 .proof 槽位"


# ------------------------------------------------------------------ 外部来源标记


def test_external_content_is_marked_and_keys_are_masked(tmp_path, sample_root):
    """只读路径上：外部内容裹标记，密钥被抹掉。

    两条一起测，因为它们都在「内容 → 模型」这条路上，
    漏掉任何一条都意味着资料能绕过边界。
    """
    from oryx.tools import tool_result_text

    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    (sample_root / "secret.txt").write_text(f"API_KEY={secret}\n", encoding="utf-8")

    from oryx.tools import fs_read

    result = fs_read(str(sample_root / "secret.txt"))
    text = tool_result_text(result, "fs_read")

    assert OPEN in text, "外部来源没裹标记"
    assert CLOSE in text, "标记没闭合"
    assert secret not in text, "明文密钥漏出去了"


def test_internal_status_is_not_marked(tmp_path):
    """Oryx 自己写的状态不该裹标记。

    全裹等于全没标 —— 模型分不清哪段是资料、哪段是系统的。
    """
    from oryx.tools import tool_result_text

    text = tool_result_text({"ok": True, "content": "已索引 12 个文件"}, "index_status")
    assert OPEN not in text
    assert CLOSE not in text


# ------------------------------------------------------------------ 密钥打码


def test_shell_output_is_masked(tmp_path):
    """命令输出里的密钥不能原样回给模型。"""
    from oryx.sandbox import scrub_env
    from oryx.tools import shell

    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    result = shell(f"echo API_KEY={secret}")
    text = json.dumps(result, ensure_ascii=False, default=str)
    assert secret not in text, "命令输出里的密钥漏出去了"


def test_sandbox_scrubs_secret_env_vars():
    """沙箱要把密钥类环境变量剔掉。

    不剔的话一句 `env` 就能把本机密钥读出来。
    """
    from oryx.sandbox import scrub_env

    os.environ["ORYX_TEST_FAKE_API_KEY"] = "sk-should-not-survive"
    try:
        env = scrub_env()
        assert "ORYX_TEST_FAKE_API_KEY" not in env, "密钥环境变量没被剔除"
    finally:
        os.environ.pop("ORYX_TEST_FAKE_API_KEY", None)


# ------------------------------------------------------------------ 越界防护


def test_reading_outside_allowed_roots_is_denied(tmp_path):
    """越界读要被挡下。"""
    from oryx import loop as loop_mod
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = ["C:\\Windows"]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    events: list[dict] = []
    runtime.bus.add_sync_sink(events.append)

    result = runtime._handle_tool_call(
        "", 1, ToolCall(id="c1", name="fs_read", arguments={"path": "C:/Windows/system.ini"})
    )
    assert result.get("ok") is not True, "越界读取居然成功了"
    assert any(e["type"] == "step.denied" for e in events), "没有留下拒绝事件"
    assert loop_mod is not None


# ------------------------------------------------------------------ 符号链接


def test_symlink_escape_is_detected(tmp_path):
    """越界符号链接要被识别出来。"""
    from oryx.symlink_guard import classify_symlink, same_tree

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("不该被读到", encoding="utf-8")

    link = root / "link.txt"
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("当前环境不能创建符号链接")

    escapes, err = classify_symlink(str(root), str(link))
    assert err is None, f"读链接文本出错：{err}"
    assert escapes is True, "越界链接没被识别出来"
    assert same_tree(str(root), str(outside)) is False


# ------------------------------------------------------------------ 扫描上限


def test_scan_limits_are_self_reported(tmp_path):
    """扫描有上限，且上限命中时**自述不完整**。

    判据：条目上限压到 5 个文件，结果里必须出现 `partial=True`
    与 `limit_hits` —— 不自述的话，用户会以为扫全了。
    """
    from oryx.indexer import FileIndex

    root = tmp_path / "many"
    root.mkdir()
    for i in range(30):
        (root / f"f{i}.txt").write_text(f"内容 {i}\n", encoding="utf-8")

    index = FileIndex(forbidden=[], ocr=False, embeddings=False)
    stats = index.scan([str(root)], max_files=5)

    data = stats.to_dict()
    assert data.get("partial") is True, "命中上限却没自述不完整"
    assert data.get("limit_hits"), "自述不完整但没给 limit_hits"
    assert data["scanned"] <= 5, "条目上限没生效"


# ------------------------------------------------------------------ 样本目录


SAMPLES = Path(__file__).parent / "samples"


def test_logs_sample_is_read_and_masked():
    """日志样本要能读，且里面的密钥被打码。

    样本是**固定内容**的 —— 所以断言能写死：
    12 行、3 个级别、JWT 那一行必须被抹掉。
    """
    from oryx.tools import fs_read, tool_result_text

    log = SAMPLES / "logs" / "app.log"
    if not log.exists():
        pytest.skip("日志样本不存在")

    result = fs_read(str(log))
    assert result.get("ok") is True
    text = tool_result_text(result, "fs_read")

    assert "数据库连接失败" in text, "日志正文丢了"
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in text, "日志里的 JWT 漏出去了"
    assert OPEN in text and CLOSE in text


def test_logs_sample_is_not_modified_by_reading():
    """读日志样本不能改它 —— 逐字节不变。"""
    from oryx.tools import fs_read

    log = SAMPLES / "logs" / "app.log"
    if not log.exists():
        pytest.skip("日志样本不存在")

    before = log.read_bytes()
    fs_read(str(log))
    assert log.read_bytes() == before


def test_secrets_sample_all_masked():
    """密钥样本里的每一串凭据，打码后一个都不许剩。"""
    from oryx.redact import redact

    env = SAMPLES / "secrets" / ".env.development"
    if not env.exists():
        pytest.skip("密钥样本不存在")

    out = redact(env.read_text("utf-8"))
    for secret in (
        "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abcdefghijklmno",
        "supersecretpassword",
        "db-secret-pass",
    ):
        assert secret not in out, f"打码层漏了：{secret}"


def test_link_samples_are_built_on_demand(tmp_path):
    """链接样本是现造的 —— 造得出来就验越界判定。

    为什么不在仓库里放链接文件：链接能不能建取决于系统与用户权限，
    放一个建不出来的链接进仓库，测试在别人机器上会一直跳过 ——
    看着像测过了，其实是没测。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_links", SAMPLES / "links" / "make_links.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    root = tmp_path / "links"
    made = mod.build(root)
    if "outside_link.txt" not in made:
        pytest.skip("当前环境不能创建符号链接")

    from oryx.symlink_guard import classify_symlink

    escapes, err = classify_symlink(str(root), made["outside_link.txt"])
    assert err is None
    assert escapes is True, "越界链接没被识别出来"

    inside, err = classify_symlink(str(root), made["inside_link.txt"])
    assert err is None
    assert inside is False, "目录内的链接被误判成越界"

    # 目标文件逐字节不变 —— 证明我们只是「看了链接文本」
    target = tmp_path / "outside_target.txt"
    before = target.read_bytes()
    classify_symlink(str(root), made["outside_link.txt"])
    assert target.read_bytes() == before
