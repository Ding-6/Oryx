"""新补的三个文件工具：fs_move / fs_glob / fs_grep。

为什么单独测：
    这三个工具以前**只存在于语义标注里**（loop.py 的 embed_text 提示词写着
    「重命名文件」「按模式批量查找」「正则搜内容」），注册表里却没有它们 ——
    语义检索永远搜不到，模型只能退到 shell 跑 mv / findstr，
    而 shell 是 L2 危险级，每次都要弹审批框。

    所以要验的不只是「函数能用」，还有：
      · 真的注册进 registry 了（否则语义检索还是搜不到）；
      · 风险等级对（fs_move 不该是 L2，fs_glob/fs_grep 该是 L0）；
      · 进了 MUTATING_TOOLS 清单（不然自证字段说「没动过」）；
      · fs_grep 的内容进了 taint 名单（返回的是文件原文）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from oryx.taint import CLOSE, OPEN
from oryx.tools import MUTATING_TOOLS, build_registry, fs_glob, fs_grep, fs_move, tool_result_text


@pytest.fixture()
def tree(tmp_path):
    """一份可复现的目录树。"""
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    return 42\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# 标题\n\nalpha 的说明\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("class Gamma:\n    pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------ 注册与风险


def test_all_three_tools_are_registered():
    """三个工具都要真的在注册表里 —— 少一个语义检索就搜不到它。"""
    registry = build_registry()
    for name in ("fs_move", "fs_glob", "fs_grep"):
        assert name in registry, f"{name} 没注册进 registry"


def test_risk_levels_are_right():
    """风险等级要对：移动是 L1（可逆），glob/grep 是 L0（纯读）。

    fs_move 定成 L2 会让「改个文件名」每次都弹审批框 ——
    那就等于这个工具没加，跟退到 shell 没区别。
    """
    from oryx.policy import Risk

    registry = build_registry()
    assert registry["fs_move"].risk == Risk.L1_WRITE
    assert registry["fs_glob"].risk == Risk.L0_READ
    assert registry["fs_grep"].risk == Risk.L0_READ


def test_mutating_tools_list_includes_fs_move():
    """fs_move 会改东西，必须在 MUTATING_TOOLS 清单里。

    不在清单里的话自证字段会说「未改动」—— 而文件真的被挪走了。
    """
    assert "fs_move" in MUTATING_TOOLS
    assert "fs_glob" not in MUTATING_TOOLS, "glob 只读，不该算会改动东西"
    assert "fs_grep" not in MUTATING_TOOLS, "grep 只读，不该算会改动东西"


def test_fs_grep_output_is_tainted():
    """grep 返回的是文件原文，必须裹外部来源标记。"""
    from oryx.taint import is_untrusted_tool

    assert is_untrusted_tool("fs_grep"), "fs_grep 没进外部来源名单"


# ------------------------------------------------------------------ fs_move


def test_move_renames_a_file(tree):
    """重命名：内容跟着走。"""
    src = tree / "a.py"
    dst = tree / "renamed.py"
    result = fs_move(str(src), str(dst))
    assert result["ok"] is True, str(result)
    assert dst.exists(), "目标文件不在"
    assert not src.exists(), "源文件还在"
    assert dst.read_text("utf-8") == "def alpha():\n    pass\n"


def test_move_refuses_to_overwrite_by_default(tree):
    """目标已存在时默认拒绝 —— 静默覆盖会丢用户的数据。"""
    src = tree / "a.py"
    dst = tree / "b.py"
    result = fs_move(str(src), str(dst))
    assert result["ok"] is False, "覆盖了已有文件"
    assert dst.read_text("utf-8") == "def beta():\n    return 42\n", "原文件被覆盖了"
    assert src.exists(), "源文件被挪走了（应该整个操作都失败）"


def test_move_overwrites_when_asked(tree):
    """显式传 overwrite=true 才覆盖。"""
    src = tree / "a.py"
    dst = tree / "b.py"
    result = fs_move(str(src), str(dst), overwrite=True)
    assert result["ok"] is True, str(result)
    assert dst.read_text("utf-8") == "def alpha():\n    pass\n"


def test_move_to_another_directory(tree):
    """移动到别的目录。"""
    target = tree / "sub"
    result = fs_move(str(tree / "a.py"), str(target / "moved.py"))
    assert result["ok"] is True, str(result)
    assert (target / "moved.py").exists()
    assert not (tree / "a.py").exists()


def test_move_reports_itself_as_a_mutation(tree):
    """自证字段要说「动了东西」。

    工具自己的额外字段走 ``meta``（``_ok(content, **meta)``），
    与 loop 层统一注入的顶层 ``mutated`` 是两回事。
    """
    result = fs_move(str(tree / "a.py"), str(tree / "renamed.py"))
    assert result["meta"].get("moved") is True, "移动没标 moved"


def test_move_missing_source_fails(tree):
    """源不存在时要报错，不能静默成功。"""
    result = fs_move(str(tree / "nope.py"), str(tree / "x.py"))
    assert result["ok"] is False
    assert not (tree / "x.py").exists(), "源不存在却创建了目标"


def test_move_same_path_fails(tree):
    """源与目标相同要报错。"""
    result = fs_move(str(tree / "a.py"), str(tree / "a.py"))
    assert result["ok"] is False


# ------------------------------------------------------------------ fs_glob


def test_glob_finds_by_pattern(tree):
    """按模式找文件。"""
    result = fs_glob(str(tree), "*.py")
    assert result["ok"] is True, str(result)
    names = sorted(item["name"] for item in result["content"])
    assert names == ["a.py", "b.py", "c.py"], str(names)


def test_glob_skips_dependency_dirs(tree):
    """node_modules 里的文件不该被收进来 —— 找业务文件时全是噪声。"""
    result = fs_glob(str(tree), "*.py")
    paths = [item["path"] for item in result["content"]]
    assert not any("node_modules" in p for p in paths), str(paths)


def test_glob_can_include_directories(tree):
    """include_dirs=true 时也返回匹配的目录。"""
    result = fs_glob(str(tree), "sub", include_dirs=True)
    assert any(item["is_dir"] for item in result["content"]), str(result)


def test_glob_respects_limit(tree):
    """命中数到上限就停，并自述被截断。"""
    result = fs_glob(str(tree), "*.py", limit=1)
    assert len(result["content"]) == 1
    assert result["meta"].get("truncated") is True, "命中上限却没自述被截断"


def test_glob_on_missing_dir_fails(tree):
    result = fs_glob(str(tree / "nope"), "*")
    assert result["ok"] is False


# ------------------------------------------------------------------ fs_grep


def test_grep_finds_lines_with_line_numbers(tree):
    """grep 返回行号 —— 这是它相对 search_files 的核心价值。"""
    result = fs_grep(str(tree), r"alpha")
    assert result["ok"] is True, str(result)
    hits = result["content"]
    assert hits, "没命中"
    assert all(h["line"] > 0 for h in hits), "行号不对"
    texts = [h["text"] for h in hits]
    assert any("def alpha" in t for t in texts), str(texts)


def test_grep_supports_regex(tree):
    """走正则，不是子串匹配。

    判据选 `def\\s+\\w+` 这种形状：如果实现退化成子串匹配，
    「class Gamma:」那行不会被误命中 —— 命中数就是准的。
    """
    result = fs_grep(str(tree), r"def\s+\w+")
    assert result["ok"] is True, str(result)
    hits = result["content"]
    # a.py 的 def alpha、b.py 的 def beta —— c.py 是 class Gamma，notes.md 没有 def
    assert len(hits) == 2, str(hits)
    assert all("def " in h["text"] for h in hits), str(hits)
    assert not any("Gamma" in h["text"] for h in hits), "正则不该命中 class 那行"


def test_grep_can_filter_by_filename(tree):
    """glob 参数只搜匹配的文件。"""
    result = fs_grep(str(tree), r"alpha", glob="*.md")
    hits = result["content"]
    assert all(h["path"].endswith(".md") for h in hits), str(hits)


def test_grep_ignore_case(tree):
    """ignore_case=true 时大小写不敏感。"""
    strict = fs_grep(str(tree), r"GAMMA")
    assert not strict["content"], "默认不该忽略大小写"
    loose = fs_grep(str(tree), r"GAMMA", ignore_case=True)
    assert loose["content"], "ignore_case 没生效"


def test_grep_rejects_bad_regex(tree):
    """正则不合法要报错，不要抛异常。"""
    result = fs_grep(str(tree), r"[unclosed")
    assert result["ok"] is False
    assert "正则" in str(result.get("content"))


def test_grep_empty_pattern_fails(tree):
    result = fs_grep(str(tree), "")
    assert result["ok"] is False


def test_grep_skips_binary_files(tree):
    """二进制文件跳过 —— 解出来是乱码，命中也是噪声。"""
    (tree / "blob.bin").write_bytes(b"\x00\x01\x02alpha\x00binary")
    result = fs_grep(str(tree), r"alpha")
    paths = [h["path"] for h in result["content"]]
    assert not any(p.endswith(".bin") for p in paths), str(paths)


def test_grep_output_is_wrapped_for_the_model(tree):
    """返回给模型时要裹外部来源标记。"""
    result = fs_grep(str(tree), r"alpha")
    text = tool_result_text(result, "fs_grep")
    assert OPEN in text, "grep 结果没裹标记"
    assert CLOSE in text, "标记没闭合"


# ------------------------------------------------------------------ 接线


def test_tools_work_through_the_runtime(tree, tmp_path):
    """走真实的 _handle_tool_call —— 单元测函数只能证明「函数自己能用」，
    证不了「工具真的被注册、能被调用、结果真的发出来」。
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

    runtime._handle_tool_call(
        "", 1, ToolCall(id="c1", name="fs_glob",
                        arguments={"root": str(tree), "pattern": "*.py"})
    )
    done = [e for e in events if e["type"] == "step.done"]
    assert done, "fs_glob 没发出 step.done"
    assert done[0].get("mutated") is False, "glob 是只读工具，却说改了东西"

    runtime._handle_tool_call(
        "", 2, ToolCall(id="c2", name="fs_move",
                        arguments={"src": str(tree / "a.py"), "dst": str(tree / "moved.py")})
    )
    done = [e for e in events if e["type"] == "step.done"]
    assert len(done) >= 2
    assert done[-1].get("mutated") is True, "move 改了东西，却说没改"


def test_move_is_snapshotted_for_rewind(tree, tmp_path):
    """移动要被 checkpoint 记下来 —— 否则回退后文件还在新位置。

    这一条要同时验「源」和「目标」都被记了：
    只记源 → 回退后文件变两份；只记目标 → 回退后文件丢了。
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

    runtime.checkpoints.mark("task1", 1, "第一轮")
    runtime._handle_tool_call(
        "task1", 1, ToolCall(id="c1", name="fs_move",
                            arguments={"src": str(tree / "a.py"), "dst": str(tree / "moved.py")})
    )

    cid = runtime.checkpoints.current("task1")
    assert cid, "没有活动检查点"
    files = store.checkpoint_files(cid)
    paths = {f["path"] for f in files}
    src_real = str((tree / "a.py").resolve())
    dst_real = str((tree / "moved.py").resolve())
    assert src_real in paths, f"源路径没被记录：{paths}"
    assert dst_real in paths, f"目标路径没被记录：{paths}"


def test_rewind_restores_a_moved_file(tree, tmp_path):
    """回退要把移动过的文件放回原处。

    走真实的 checkpoint.rewind —— 只测「快照记了什么」不够，
    要看回退之后文件真的回来了、目标真的没了。
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

    src = tree / "a.py"
    dst = tree / "moved.py"
    original = src.read_text("utf-8")

    runtime.checkpoints.mark("task1", 1, "第一轮")
    runtime._handle_tool_call(
        "task1", 1, ToolCall(id="c1", name="fs_move",
                            arguments={"src": str(src), "dst": str(dst)})
    )
    assert dst.exists() and not src.exists(), "移动本身没生效"

    cid = runtime.checkpoints.current("task1")
    result = runtime.checkpoints.rewind(cid, scope="Code")
    assert result["ok"] is True, str(result)

    assert src.exists(), f"回退后源文件没回来：{result}"
    assert src.read_text("utf-8") == original, "回退后内容不对"
    assert not dst.exists(), "回退后目标文件还留着（文件变成两份了）"
