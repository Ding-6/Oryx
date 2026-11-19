"""思考过程、会话续接、图库过滤。

这三块都是"看起来对、实际错"的类型，所以每条都钉住具体语义：

    思考过程  provider 要能从两种协议里解析出 thinking/reasoning，
              内核要发出 REASONING_START/CONTENT/END 三段事件。
    会话续接  不点「新建对话」时，消息要落在**同一个** task 里，
              并且下一轮要把历史带进模型上下文。
    图库过滤  图标、占位图、小文件、杂物目录一律不收。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx import imagegallery, providers


# ================================================================ 思考过程
def _sse(*events: dict) -> list[str]:
    return ["data: " + json.dumps(e) for e in events]


def test_anthropic_thinking_delta_is_captured():
    """Anthropic 的扩展思考：content_block type=thinking + thinking_delta。"""
    lines = _sse(
        {"type": "content_block_start", "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "先看"}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "一下目录"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好的"}},
    )
    reply = providers.ProviderReply()
    chunks: list[str] = []
    providers.AnthropicProvider._consume(lines, reply, None, chunks.append)
    assert reply.reasoning == "先看一下目录"
    assert reply.text == "好的"
    # 回调是**逐块**触发的（流式语义），不是最后一次性给全文
    assert chunks == ["先看", "一下目录"]
    assert "".join(chunks) == reply.reasoning


def test_anthropic_thinking_and_text_are_separate():
    """思考内容不能混进正文 —— 那是两回事。"""
    lines = _sse(
        {"type": "content_block_start", "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "思考"}},
        {"type": "content_block_stop"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "答案"}},
    )
    reply = providers.ProviderReply()
    providers.AnthropicProvider._consume(lines, reply, None, None)
    assert reply.reasoning == "思考"
    assert reply.text == "答案"


def test_openai_reasoning_content_is_captured():
    """DeepSeek reasoner 用 reasoning_content。"""
    lines = _sse(
        {"choices": [{"delta": {"reasoning_content": "推理中"}}]},
        {"choices": [{"delta": {"reasoning_content": "继续"}}]},
        {"choices": [{"delta": {"content": "最终"}}]},
    )
    reply = providers.ProviderReply()
    chunks: list[str] = []
    providers.OpenAICompatProvider._consume(lines, reply, None, chunks.append)
    assert reply.reasoning == "推理中继续"
    assert reply.text == "最终"
    # 逐块触发（流式语义）
    assert chunks == ["推理中", "继续"]
    assert "".join(chunks) == reply.reasoning


def test_openai_reasoning_alias_is_captured():
    """Qwen3 一类用 reasoning 字段。"""
    lines = _sse({"choices": [{"delta": {"reasoning": "想一想"}}]})
    reply = providers.ProviderReply()
    providers.OpenAICompatProvider._consume(lines, reply, None, None)
    assert reply.reasoning == "想一想"


def test_openai_reasoning_list_form_is_captured():
    """有些实现把 reasoning_content 写成数组。"""
    lines = _sse(
        {"choices": [{"delta": {"reasoning_content": [{"text": "一"}, {"text": "二"}]}}]}
    )
    reply = providers.ProviderReply()
    providers.OpenAICompatProvider._consume(lines, reply, None, None)
    assert reply.reasoning == "一二"


def test_no_reasoning_means_empty():
    """不返回思考的模型不能凭空造一段出来。"""
    lines = _sse({"choices": [{"delta": {"content": "普通回答"}}]})
    reply = providers.ProviderReply()
    providers.OpenAICompatProvider._consume(lines, reply, None, None)
    assert reply.reasoning == ""


def test_reasoning_callback_not_called_for_empty_chunk():
    lines = _sse({"choices": [{"delta": {"reasoning_content": ""}}]})
    reply = providers.ProviderReply()
    chunks: list[str] = []
    providers.OpenAICompatProvider._consume(lines, reply, None, chunks.append)
    assert chunks == []


# ================================================================ 会话续接
def test_copy_messages_preserves_roles():
    from oryx.store import Store

    store = Store()
    a = store.create_task("第一句", "craft", "test")
    b = store.create_task("第二轮", "craft", "test")
    store.add_message(a, "user", "你好")
    store.add_message(a, "assistant", "在")
    copied = store.copy_messages(a, b)
    assert copied == 2
    msgs = store.messages_for_task(b)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert [m["content"] for m in msgs] == ["你好", "在"]


def test_copy_messages_into_empty_task_is_noop():
    from oryx.store import Store

    store = Store()
    a = store.create_task("空", "craft", "test")
    b = store.create_task("空2", "craft", "test")
    assert store.copy_messages(a, b) == 0
    assert store.messages_for_task(b) == []


def test_chat_image_paths_finds_step_paths():
    """对话里产出过的图片要从步骤参数里捞出来。"""
    from oryx.store import Store

    store = Store()
    task = store.create_task("截图", "craft", "test")
    store.create_step(task, 1, "browser_screenshot", {"path": r"C:\tmp\shot.png"}, 1)
    paths = store.chat_image_paths()
    assert any("shot.png" in p for p in paths)


def test_chat_image_paths_finds_message_paths():
    from oryx.store import Store

    store = Store()
    task = store.create_task("看图", "craft", "test")
    store.add_message(task, "user", r"这张图 C:\Users\me\Pictures\a.png 帮我看看")
    paths = store.chat_image_paths()
    assert any("a.png" in p for p in paths)


def test_chat_image_paths_ignores_non_images():
    from oryx.store import Store

    store = Store()
    task = store.create_task("看文档", "craft", "test")
    store.add_message(task, "user", r"打开 C:\Users\me\Documents\a.docx")
    paths = store.chat_image_paths()
    assert not any(".docx" in p for p in paths)


# ================================================================ 重新生成
def test_prepare_retry_keeps_question_drops_answer(tmp_path):
    """重新生成：只删助手回复，用户提问一个字都不动。"""
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("第一句", "craft", "test")
    store.add_message(task, "user", "第一句")
    store.add_message(task, "assistant", "回答一")
    store.add_message(task, "user", "第二句")
    store.add_message(task, "assistant", "回答二")

    prep = store.prepare_retry(task)
    assert prep["ok"] is True
    assert prep["goal"] == "第二句", prep["goal"]
    assert prep["removed"] == 1

    msgs = store.messages_for_task(task)
    assert [m["content"] for m in msgs] == ["第一句", "回答一", "第二句"]


def test_prepare_retry_without_messages_fails(tmp_path):
    """空会话不能重跑 —— 要给明确原因，不是静默成功。"""
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("空", "craft", "test")
    prep = store.prepare_retry(task)
    assert prep["ok"] is False
    assert prep["reason"]


def test_prepare_retry_without_question_fails(tmp_path):
    """只有助手消息、没有提问 —— 没法重跑。"""
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("只有回答", "craft", "test")
    store.add_message(task, "assistant", "自说自话")
    prep = store.prepare_retry(task)
    assert prep["ok"] is False
    # 失败时不能删消息 —— 用户的记录还在
    assert len(store.messages_for_task(task)) == 1


def test_prepare_retry_drops_later_rounds(tmp_path):
    """重试最后一轮时，更晚的轮次也要一起清掉。

    不然后面那几轮的回复会跟新的回答对不上号。
    """
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("多轮", "craft", "test")
    for i in range(3):
        store.add_message(task, "user", f"问{i}")
        store.add_message(task, "assistant", f"答{i}")

    prep = store.prepare_retry(task)
    assert prep["goal"] == "问2"
    assert prep["removed"] == 1
    msgs = store.messages_for_task(task)
    assert [m["content"] for m in msgs] == ["问0", "答0", "问1", "答1", "问2"]


# ================================================================ 图库过滤
def test_icon_named_files_are_excluded():
    assert imagegallery._name_says_icon("icon.png")
    assert imagegallery._name_says_icon("logo_32.png")
    assert imagegallery._name_says_icon("favicon.ico")
    assert imagegallery._name_says_icon("avatar.jpg")
    assert imagegallery._name_says_icon("app-icon@2x.png")
    assert not imagegallery._name_says_icon("screenshot.png")
    assert not imagegallery._name_says_icon("报告.png")


def test_junk_files_are_excluded():
    assert imagegallery._is_hidden_or_system(Path("Thumbs.db"))
    assert imagegallery._is_hidden_or_system(Path("desktop.ini"))


# ================================================================ 本地模型
def test_local_base_url_strips_openai_suffix():
    """传进来的可能是 OpenAI 兼容端点（.../v1），要退回服务根地址。

    不然 /api/tags 会打到 http://host/v1/api/tags —— 404，
    于是"本机已装模型"永远是空的。
    """
    from oryx import localmodels

    assert localmodels._base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert localmodels._base_url("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert localmodels._base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert localmodels._base_url(None) == localmodels.DEFAULT_OLLAMA_URL


def test_local_provider_spec_sanitizes_name():
    """模型名里的冒号不能直接当 provider 名 —— 换成横杠。"""
    from oryx import localmodels

    spec, name = localmodels.provider_spec_for("qwen3.5:9b", "http://127.0.0.1:11434")
    assert name == "qwen3.5-9b"
    assert spec["model"] == "qwen3.5:9b"
    assert spec["base_url"] == "http://127.0.0.1:11434/v1"
    assert spec["type"] == "openai"


def test_local_models_unreachable_returns_empty():
    """服务没起来时返回空列表 —— 这是正常状态，不是错误。"""
    from oryx import localmodels

    # 指向一个肯定没人监听的端口
    assert localmodels.list_local_models("http://127.0.0.1:59999") == []


# ================================================================ 模型配置编辑
def test_is_local_provider_detects_loopback():
    """本地 / 云端的判据是地址，不该靠 provider 名字猜。"""
    from oryx import localmodels

    assert localmodels.is_local_provider({"base_url": "http://127.0.0.1:11434/v1"})
    assert localmodels.is_local_provider({"base_url": "http://localhost:1234/v1"})
    assert not localmodels.is_local_provider({"base_url": "https://api.anthropic.com"})
    assert not localmodels.is_local_provider({"base_url": "https://api.deepseek.com/v1"})
    # 没有地址的（还没配全的）不算本地
    assert not localmodels.is_local_provider({})
    assert not localmodels.is_local_provider({"base_url": ""})


def test_provider_list_marks_local_flag():
    """列表里要标出哪些是本地的 —— 前端据此分区显示。"""
    from oryx.config import Config
    from oryx.domains import model_config

    config = Config({
        "providers": {
            "cloud": {"type": "openai", "base_url": "https://api.deepseek.com/v1", "model": "ds"},
            "local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen"},
        }
    })
    items = {i["name"]: i for i in model_config.list_providers(config)["items"]}
    assert items["cloud"]["is_local"] is False
    assert items["local"]["is_local"] is True


def test_edit_provider_updates_fields():
    """编辑已保存的模型配置：改地址、改模型名。"""
    from oryx.config import Config
    from oryx.domains import model_config

    config = Config({
        "providers": {
            "p1": {"type": "openai", "base_url": "https://old/v1", "model": "old-model"},
        }
    })
    req = model_config.ProviderEditRequest(base_url="https://new/v1", model="new-model")
    model_config.edit_provider(config, "p1", req)
    spec = config.data["providers"]["p1"]
    assert spec["base_url"] == "https://new/v1"
    assert spec["model"] == "new-model"
    # 没传的字段不能被清掉
    assert spec["type"] == "openai"


def test_edit_provider_missing_raises_404():
    """编辑不存在的 provider 要明确报错。"""
    from fastapi import HTTPException

    from oryx.config import Config
    from oryx.domains import model_config

    config = Config({"providers": {}})
    req = model_config.ProviderEditRequest(model="m")
    with pytest.raises(HTTPException) as exc:
        model_config.edit_provider(config, "nope", req)
    assert exc.value.status_code == 404


def test_edit_provider_empty_payload_raises_400():
    """什么都不传就说"没要改的" —— 别静默成功。"""
    from fastapi import HTTPException

    from oryx.config import Config
    from oryx.domains import model_config

    config = Config({"providers": {"p1": {"type": "openai", "model": "m"}}})
    with pytest.raises(HTTPException) as exc:
        model_config.edit_provider(config, "p1", model_config.ProviderEditRequest())
    assert exc.value.status_code == 400


def test_edit_provider_keeps_api_key_when_blank():
    """密钥留空 = 不改它。

    前端不回显明文密钥，用户留空提交不该把已存的密钥擦掉。
    """
    from oryx.config import Config
    from oryx.domains import model_config

    config = Config({
        "providers": {
            "p1": {"type": "openai", "base_url": "https://x/v1", "model": "m", "api_key": "sk-secret"},
        }
    })
    model_config.edit_provider(config, "p1", model_config.ProviderEditRequest(model="m2", api_key=""))
    assert config.data["providers"]["p1"]["api_key"] == "sk-secret"


def test_switch_local_provider_also_sets_local_provider():
    """切到本地模型要同时写 local_provider。

    本地模式下读的是 local_provider —— 只改 active_provider 的话，
    切到本地模式还是旧模型，看起来就像"切换没生效"。
    """
    from oryx.config import Config
    from oryx.manage import switch_provider

    config = Config({
        "providers": {
            "local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen"},
            "cloud": {"type": "openai", "base_url": "https://api.deepseek.com/v1", "model": "ds"},
        }
    })
    switch_provider(config, "local")
    assert config.data["active_provider"] == "local"
    assert config.data["local_provider"] == "local"


def test_switch_cloud_provider_also_sets_cloud_provider():
    """切到云端模型要写 cloud_provider —— 切计算模式时按它取。"""
    from oryx.config import Config
    from oryx.manage import switch_provider

    config = Config({
        "providers": {
            "local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen"},
            "cloud": {"type": "openai", "base_url": "https://api.deepseek.com/v1", "model": "ds"},
        }
    })
    switch_provider(config, "cloud")
    assert config.data["active_provider"] == "cloud"
    assert config.data["cloud_provider"] == "cloud"
    # 本地槽位不该被云端模型占掉
    assert config.data.get("local_provider", "") == ""


def test_mode_switch_uses_remembered_slot():
    """切计算模式要回到用户上次在这一类里选过的模型。

    不是"第一个云端 provider" —— 那会让用户切个模式就莫名换了模型。
    """
    from oryx.config import Config
    from oryx.manage import pick_provider_for_mode

    config = Config({
        "mode": "efficiency",
        "active_provider": "ds-1",
        "cloud_provider": "ds-1",
        "local_provider": "qwen-local",
        "providers": {
            "ds-1": {"type": "openai", "base_url": "https://a/v1", "model": "m1"},
            "ds-2": {"type": "openai", "base_url": "https://b/v1", "model": "m2"},
            "qwen-local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "q"},
        },
    })
    assert pick_provider_for_mode(config, "local") == "qwen-local"
    assert pick_provider_for_mode(config, "efficiency") == "ds-1"


def test_mode_switch_falls_back_when_slot_empty():
    """槽位是空的（第一次用这一类）→ 按地址找同类里能用的。"""
    from oryx.config import Config
    from oryx.manage import pick_provider_for_mode

    config = Config({
        "providers": {
            "ds-1": {"type": "openai", "base_url": "https://a/v1", "model": "m1"},
            "qwen-local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "q"},
        },
    })
    assert pick_provider_for_mode(config, "local") == "qwen-local"
    assert pick_provider_for_mode(config, "efficiency") == "ds-1"


def test_mode_switch_returns_empty_when_no_local_model():
    """一条本地模型都没登记时，本地模式挑不出东西 —— 返回空串而不是随便一个云端的。"""
    from oryx.config import Config
    from oryx.manage import pick_provider_for_mode

    config = Config({
        "providers": {
            "ds-1": {"type": "openai", "base_url": "https://a/v1", "model": "m1"},
        },
    })
    assert pick_provider_for_mode(config, "local") == ""


def test_delete_clears_dangling_local_provider():
    """删掉 local_provider 指向的那条时，要把它清空 —— 悬空指针比没有更糟。

    「登记完再删掉」是正常操作，不能因为这个拦着不让删。
    """
    from oryx.config import Config
    from oryx.manage import delete_provider

    config = Config({
        "active_provider": "cloud",
        "local_provider": "local",
        "providers": {
            "local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen"},
            "cloud": {"type": "openai", "base_url": "https://x/v1", "model": "ds"},
        }
    })
    assert delete_provider(config, "local") is True
    assert "local" not in config.data["providers"]
    assert config.data["local_provider"] == ""


def test_delete_rejects_provider_in_use():
    """正在用的 provider 不能删。"""
    from oryx.config import Config
    from oryx.manage import delete_provider

    config = Config({
        "active_provider": "cloud",
        "providers": {
            "cloud": {"type": "openai", "base_url": "https://x/v1", "model": "ds"},
        }
    })
    with pytest.raises(ValueError):
        delete_provider(config, "cloud")


def test_default_config_has_no_hardcoded_local_model():
    """默认配置里不能写死某个本地模型名。

    写死的模型（比如 qwen3:8b）早就不在本机了 ——
    留着会让「本机已装模型」列表凭空多一条假的。
    """
    from oryx.config import DEFAULT_CONFIG

    for name, spec in DEFAULT_CONFIG["providers"].items():
        assert "127.0.0.1" not in str(spec.get("base_url", "")), name
    assert DEFAULT_CONFIG.get("local_provider", "") == ""


def test_tiny_files_are_not_icons():
    """小于 8 KB 的不是照片 —— 那是图标或占位图。"""
    p = Path("x.png")
    assert imagegallery.MIN_BYTES == 8 * 1024


def test_skip_dirs_include_program_folders():
    assert imagegallery._should_skip_dir("node_modules")
    assert imagegallery._should_skip_dir("Program Files")
    assert imagegallery._should_skip_dir("assets")
    assert imagegallery._should_skip_dir(".git")
    assert not imagegallery._should_skip_dir("Pictures")


def test_collect_keeps_chat_images(tmp_path):
    """对话里的图一定要收进来 —— 这是用户最在意的一类。"""
    img = tmp_path / "chat-shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20000)
    out = imagegallery.collect(
        allowed_roots=[str(tmp_path)],
        chat_images=[str(img)],
        limit=50,
        time_budget=2.0,
    )
    names = [i["name"] for i in out["images"]]
    assert "chat-shot.png" in names
    item = next(i for i in out["images"] if i["name"] == "chat-shot.png")
    assert item["source"] == "chat"


def test_collect_drops_icon_named_files(tmp_path):
    (tmp_path / "icon.png").write_bytes(b"0" * 20000)
    (tmp_path / "real-photo.png").write_bytes(b"0" * 20000)
    out = imagegallery.collect(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    names = [i["name"] for i in out["images"]]
    assert "real-photo.png" in names
    assert "icon.png" not in names


def test_collect_drops_tiny_files(tmp_path):
    (tmp_path / "tiny.png").write_bytes(b"0" * 100)
    out = imagegallery.collect(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    assert not any(i["name"] == "tiny.png" for i in out["images"])


def test_collect_drops_asset_dirs(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "banner.png").write_bytes(b"0" * 20000)
    out = imagegallery.collect(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    assert not any(i["name"] == "banner.png" for i in out["images"])


def test_collect_marks_source(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(b"0" * 20000)
    out = imagegallery.collect(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    assert out["images"]
    assert out["images"][0]["source"] in ("common", "recent")
    assert "counts" in out


# ---- AppData 白名单 ----
def test_appdata_itself_is_allowed():
    """AppData 这一层要放行 —— 里面的白名单目录在它下面。"""
    assert imagegallery._appdata_ok(["AppData"]) is True


def test_appdata_cache_branch_is_blocked():
    """AppData/Local/Google 是浏览器缓存，不收。"""
    assert imagegallery._appdata_ok(["AppData", "Local", "Google"]) is False


def test_appdata_allowlist_branch_passes():
    """白名单里的子树要放行。"""
    assert imagegallery._appdata_ok(["AppData", "Roaming", "Tencent"]) is True
    assert imagegallery._appdata_ok(
        ["AppData", "Roaming", "Tencent", "xwechat"]
    ) is True


def test_appdata_intermediate_level_is_allowed():
    """中间层不能提前下结论 —— Roaming/Tencent 下面还有 xwechat。"""
    assert imagegallery._appdata_ok(["AppData", "Roaming"]) is True
    assert imagegallery._appdata_ok(["AppData", "Roaming", "Tencent"]) is True


def test_onedrive_ui_assets_are_not_collected():
    """AppData 下的 OneDrive 装的是 UI 素材，不该进图库。

    实测 AppData/Local/Microsoft/OneDrive 下的 15 张全是
    LogoImages、QuotaError、tryforfree 这类程序自带图。
    """
    assert imagegallery._appdata_ok(
        ["AppData", "Local", "Microsoft", "OneDrive"]
    ) is False


# ---- counts 与缓存 ----
def test_counts_describe_returned_list(tmp_path):
    """counts 要描述**返回的这份列表**，不是扫描时遇见的总数。

    否则前端分区标签会显示「最近图片 41447」，而列表里只有 400 张。
    """
    for i in range(5):
        (tmp_path / f"p{i}.png").write_bytes(b"0" * 20000)
    out = imagegallery.collect(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    assert out["counts"]
    total = sum(out["counts"].values())
    assert total == len(out["images"]), f"counts {out['counts']} vs {len(out['images'])} 张"


def test_cache_reuses_result(tmp_path):
    """缓存命中时不再扫盘。"""
    img = tmp_path / "pic.png"
    img.write_bytes(b"0" * 20000)
    imagegallery.invalidate_cache()
    first = imagegallery.collect_cached(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    second = imagegallery.collect_cached(
        allowed_roots=[str(tmp_path)], chat_images=[], limit=50, time_budget=2.0
    )
    assert first is second, "同一份输入应该命中缓存"


def test_cache_key_follows_roots(tmp_path):
    """授权目录变了必须重新扫 —— 缓存键要带上它。"""
    a = tmp_path / "a"
    a.mkdir()
    (a / "pic.png").write_bytes(b"0" * 20000)
    b = tmp_path / "b"
    b.mkdir()
    imagegallery.invalidate_cache()
    first = imagegallery.collect_cached(
        allowed_roots=[str(a)], chat_images=[], limit=50, time_budget=2.0
    )
    second = imagegallery.collect_cached(
        allowed_roots=[str(b)], chat_images=[], limit=50, time_budget=2.0
    )
    assert first is not second, "换目录应该重扫"
    imagegallery.invalidate_cache()
