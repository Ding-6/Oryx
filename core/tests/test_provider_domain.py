"""模型服务配置的域契约。

界面上的「新增 / 编辑 / 激活 / 删除 / 本机模型」全靠这几条：

  · 新增同名**不能**静默覆盖（打错一个字就把别人的密钥和模型换了）
  · 名字不能带斜杠（它要放进 URL 路径里，带了斜杠路由根本匹配不到）
  · 编辑时留空密钥 = 不动已存的那份
  · 正在用的服务不能删
  · 激活要同时维护 local / cloud 两个槽位（否则切计算模式会乱跳）

这些是「接口稳定」的底线：出错要给中文人话，而不是 422 的机器消息或
「添加成功」式的假成功。
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from oryx.config import Config
from oryx.domains import model_config


def _cfg(providers=None, **extra) -> Config:
    return Config({"providers": providers or {}, **extra})


# ---------------------------------------------------------------- 列表

def test_list_marks_local_and_capabilities():
    cfg = _cfg({
        "cloud": {"type": "openai", "base_url": "https://api.example/v1",
                  "model": "m", "api_key": "sk-secret-123456"},
        "local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1",
                  "model": "qwen3:8b"},
    }, active_provider="cloud", cloud_provider="cloud")

    out = model_config.list_providers(cfg)
    items = {item["name"]: item for item in out["items"]}

    assert out["active"] == "cloud"
    assert out["cloud"] == "cloud", "云端槽位要一起返回，界面才分得清云端默认"
    assert items["local"]["is_local"] is True
    assert items["cloud"]["is_local"] is False
    assert items["cloud"]["has_key"] is True
    assert items["cloud"]["key_storage"] == "plaintext"
    assert items["cloud"]["capabilities"]["context_window"] > 0
    assert "sk-secret-123456" not in json.dumps(out), "密钥不能出现在返回里"


# ---------------------------------------------------------------- 新增

def test_create_rejects_duplicate_without_overwriting():
    cfg = _cfg({"p1": {"type": "openai", "base_url": "https://a", "model": "m"}})

    with pytest.raises(HTTPException) as exc:
        model_config.create_provider(
            cfg, model_config.ProviderRequest(name="p1", base_url="https://b", model="m2")
        )

    assert exc.value.status_code == 409
    assert "同名" in str(exc.value.detail)
    assert cfg.data["providers"]["p1"]["base_url"] == "https://a", "原配置不能被悄悄改掉"


@pytest.mark.parametrize(
    "name,base_url,model,needle",
    [
        ("", "https://a", "m", "名称不能为空"),
        ("a/b", "https://a", "m", "斜杠"),
        ("p", "ftp://a", "m", "http://"),
        ("p", "https://a", "", "模型名不能为空"),
    ],
)
def test_create_validates_fields(name, base_url, model, needle):
    cfg = _cfg()

    with pytest.raises(HTTPException) as exc:
        model_config.create_provider(
            cfg, model_config.ProviderRequest(name=name, base_url=base_url, model=model)
        )

    assert exc.value.status_code == 400
    assert needle in str(exc.value.detail)


def test_create_trims_values():
    cfg = _cfg()

    out = model_config.create_provider(
        cfg,
        model_config.ProviderRequest(
            name="  p1  ", base_url=" https://a/v1 ", model=" m ", api_key=" k "
        ),
    )

    assert out["name"] == "p1"
    spec = cfg.data["providers"]["p1"]
    assert spec["base_url"] == "https://a/v1"
    assert spec["model"] == "m"


# ---------------------------------------------------------------- 编辑

def test_edit_blank_key_keeps_existing():
    cfg = _cfg({"p1": {"type": "openai", "base_url": "https://a", "model": "m",
                       "api_key": "sk-old"}})

    model_config.edit_provider(
        cfg, "p1", model_config.ProviderEditRequest(model="m2", api_key="")
    )

    spec = cfg.data["providers"]["p1"]
    assert spec["model"] == "m2"
    assert spec["api_key"] == "sk-old", "留空提交不能把已存的密钥擦掉"


def test_edit_without_changes_rejected():
    cfg = _cfg({"p1": {"type": "openai", "base_url": "https://a", "model": "m"}})

    with pytest.raises(HTTPException) as exc:
        model_config.edit_provider(cfg, "p1", model_config.ProviderEditRequest())

    assert exc.value.status_code == 400
    assert "没有要改" in str(exc.value.detail)


def test_edit_validates_base_url():
    cfg = _cfg({"p1": {"type": "openai", "base_url": "https://a", "model": "m"}})

    with pytest.raises(HTTPException) as exc:
        model_config.edit_provider(
            cfg, "p1", model_config.ProviderEditRequest(base_url="not-a-url")
        )

    assert exc.value.status_code == 400
    assert "http://" in str(exc.value.detail)


# ---------------------------------------------------------------- 激活 / 删除

def test_activate_fills_matching_slot():
    cfg = _cfg({
        "cloud": {"type": "openai", "base_url": "https://x", "model": "m"},
        "local": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "q"},
    }, active_provider="cloud")

    out = model_config.activate_provider(cfg, "local")
    assert out["active_provider"] == "local"
    assert out["local_provider"] == "local"

    out2 = model_config.activate_provider(cfg, "cloud")
    assert out2["cloud_provider"] == "cloud"


def test_activate_unknown_raises_400():
    cfg = _cfg({"p1": {"type": "openai", "base_url": "https://a", "model": "m"}})

    with pytest.raises(HTTPException) as exc:
        model_config.activate_provider(cfg, "nope")

    assert exc.value.status_code == 400
    assert "没有这个 provider" in str(exc.value.detail)


def test_delete_active_provider_rejected():
    cfg = _cfg({"p1": {"type": "openai", "base_url": "https://a", "model": "m"}},
               active_provider="p1")

    with pytest.raises(HTTPException) as exc:
        model_config.delete_provider(cfg, "p1")

    assert exc.value.status_code == 400
    assert "不能删除" in str(exc.value.detail)


# ---------------------------------------------------------------- 本机模型

def test_register_local_reuses_existing_provider():
    """同一个模型已经有 provider 在跑 → 不新建，直接指向它。"""
    cfg = _cfg({"qwen-local": {"type": "openai",
                               "base_url": "http://127.0.0.1:11434/v1",
                               "model": "qwen3:8b"}})

    out = model_config.register_local_model(
        cfg, model_config.LocalModelRequest(model="qwen3:8b")
    )

    assert out["created"] is False
    assert out["name"] == "qwen-local"
    assert cfg.data["local_provider"] == "qwen-local"
    assert len(cfg.data["providers"]) == 1, "不能为同一个模型再建一条"
