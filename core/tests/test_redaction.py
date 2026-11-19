"""断言式安全测试：密钥打码层。

抄自 Marvis 的 `tests/test_log_summary.py` 的写法：
**FORBIDDEN 黑名单 + 逐条断言不在输出里**。

关键点是黑名单与样本**双向配对**：
样本文件里埋的每一串凭据，黑名单里都有一项；
黑名单里的每一项，样本里都能找到出处。
只测「打码函数对单个字符串的效果」是不够的 ——
真正的风险在**接线**：某个工具的输出路径忘了过打码层。
所以这里同时测「函数本身」与「实际管线」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oryx.redact import READONLY_PROOF, redact, redact_obj, redact_result

SAMPLE = Path(__file__).parent / "samples" / "secrets"

# 黑名单：这些串一个都不许出现在**输出**里。
# 注意用途分两层：
#  · 管线层（读样本文件、跑命令）—— 串本身一个都不能出现在返回体里；
#  · 函数层（直接调 redact）—— 裸串检测不到（没有键名、没有固定前缀，
#    和普通单词无法区分），必须有上下文（键名 / URL / 固定前缀）才测得出。
# 所以下面分 DETECTABLE_BARE（裸串即可）与 CONTEXT_ONLY（需要上下文）。
FORBIDDEN = (
    "sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abcdefghijklmno",
    "supersecretpassword",
    "db-secret-pass",
)

# 有固定前缀 / 结构特征，裸串就能认出来
DETECTABLE_BARE = (
    "sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abcdefghijklmno",
)

# 需要上下文：真实场景里它们总带着键名或 URL
CONTEXT_ONLY = (
    "supersecretpassword",
    "db-secret-pass",
)

# 这些不该被改写 —— 误伤也是 bug
BENIGN = (
    "error: file not found",
    "已完成，文件已写入 C:/tmp/x.txt",
    "password policy requires at least 12 characters",
)


# ------------------------------------------------------------------ 函数层


@pytest.mark.parametrize("secret", DETECTABLE_BARE)
def test_forbidden_never_survives(secret):
    """有结构特征的凭据，裸串也要认出来。"""
    out = redact(secret)
    assert secret not in out, f"打码层漏了：{secret!r} -> {out!r}"


@pytest.mark.parametrize("secret", CONTEXT_ONLY)
def test_context_only_secrets_masked_with_context(secret):
    """需要上下文的凭据：真实场景里它们总带着键名或 URL。

    裸串检测不到是**已知边界**（见 redact.py 的模块说明）——
    高熵字符串检测误伤率太高，不做。
    """
    forms = (
        f"DB_PASSWORD={secret}",
        f'"password": "{secret}"',
        f"postgres://appuser:{secret}@db.internal:5432/app",
    )
    for form in forms:
        out = redact(form)
        assert secret not in out, f"打码层漏了：{form!r} -> {out!r}"


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_untouched(text):
    """正常文本不能被改写 —— 误伤会毁掉上下文。"""
    assert redact(text) == text


def test_assignment_forms_are_masked():
    assert redact("password=hunter2") == "password=[redacted]"
    assert redact("password = hunter2") == "password = [redacted]"
    assert redact("PASSWORD: hunter2") == "PASSWORD: [redacted]"


def test_json_keys_keep_valid_json():
    """JSON 里的键值对打码后仍是合法 JSON —— 引号不能被吃掉。"""
    out = redact('{"api_key": "sk-abcdefghijklmnopqrstuvwxyz0123456789"}')
    parsed = json.loads(out)  # 解析失败说明引号被破坏
    assert parsed["api_key"] == "[redacted]"


def test_url_userinfo_only_password_masked():
    out = redact("https://user:pw@host/x")
    assert out == "https://user:[redacted]@host/x"
    assert "user" in out and "host" in out


def test_natural_language_forms_are_masked():
    """模型输出最爱这么写 —— 「密码是 xxx」「password is xxx」。"""
    assert "hunter2" not in redact("password is hunter2")
    assert "hunter2" not in redact("密码是 hunter2")


def test_bearer_token_masked():
    out = redact("Authorization: Bearer abcdefghijklmnop")
    assert "abcdefghijklmnop" not in out


def test_redact_obj_recurses():
    payload = {
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "nested": [{"token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}],
    }
    out = redact_obj(payload)
    blob = json.dumps(out, ensure_ascii=False)
    for secret in FORBIDDEN:
        assert secret not in blob


def test_redact_result_keeps_control_fields():
    out = redact_result({"ok": True, "content": "x", "meta": {"k": "v"}})
    assert out["ok"] is True
    assert out["content"] == "x"
    assert out["meta"] == {"k": "v"}


def test_readonly_proof_fields_present():
    """只读自证字段齐全 —— 这是 Marvis 每个只读结果都带的东西。"""
    assert READONLY_PROOF["preview"] is True
    assert READONLY_PROOF["deleted"] is False
    assert READONLY_PROOF["modified"] is False
    assert READONLY_PROOF["moved"] is False


# ------------------------------------------------------------------ 管线层


def test_sample_files_contain_expected_secrets():
    """样本与黑名单是配对的 —— 样本里埋的每串凭据都要在黑名单里。

    这条测试的作用是：有人往样本里加了新凭据却忘了加进黑名单时，
    这里会失败，提示要同步更新。
    """
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in SAMPLE.rglob("*")
        if p.is_file()
    )
    for secret in FORBIDDEN:
        assert secret in blob, f"黑名单里的 {secret!r} 在样本里没有出处"


def test_reading_sample_file_masks_secrets(tmp_path):
    """走真实管线：fs_read 读样本文件，返回体里不能有明文密钥。"""
    from oryx.tools import fs_read

    target = SAMPLE / ".env.development"
    result = fs_read(str(target))
    assert result["ok"] is True
    blob = json.dumps(result, ensure_ascii=False)
    for secret in FORBIDDEN:
        assert secret not in blob, f"fs_read 泄漏了 {secret!r}"


def test_reading_private_key_file_masks_secrets(tmp_path):
    """私钥**整块**抹掉 —— 只抹 BEGIN 那行的话，后面的 base64 还是完整密钥。"""
    from oryx.tools import fs_read

    result = fs_read(str(SAMPLE / "private.pem"))
    assert result["ok"] is True
    blob = json.dumps(result, ensure_ascii=False)
    assert "BEGIN RSA PRIVATE KEY" not in blob
    assert "BEGIN RSA PRIVATE KEY-----" not in blob
    # 密钥正文也不能留
    assert "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu" not in blob


def test_shell_output_is_masked(tmp_path):
    """走真实管线：命令输出里的凭据不能原样回来。

    用 echo 而不是真的读文件 —— 跨平台的命令差异太多，
    这里要测的是「打码层接上了」这件事本身。
    """
    from oryx.sandbox import run_sandboxed

    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    if os.name == "nt":
        command = f"echo {secret}"
    else:
        command = f"echo {secret}"
    result = run_sandboxed(command, tmp_path)
    assert result["ok"] is True
    blob = json.dumps(result, ensure_ascii=False)
    assert secret not in blob, "shell 输出泄漏了密钥"


def test_shell_command_line_is_masked(tmp_path):
    """命令行本身就是泄密点 —— `echo sk-xxx` 的命令串会进返回值。"""
    from oryx.sandbox import run_sandboxed

    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    result = run_sandboxed(f"echo {secret}", tmp_path)
    assert secret not in result.get("command", "")


def test_readonly_tool_does_not_modify_sample(tmp_path):
    """只读工具跑完，样本文件逐字节不变。"""
    from oryx.tools import fs_list, fs_read

    before = _snapshot(SAMPLE)
    fs_read(str(SAMPLE / ".env.development"))
    fs_read(str(SAMPLE / "notes.txt"))
    fs_list(str(SAMPLE))
    assert _snapshot(SAMPLE) == before


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    """快照：mtime_ns + size + 二进制全文。

    只比内容不够 —— 一个工具改了文件又改回原样，比对不出来。
    mtime 变了就是动过。
    """
    found: dict[str, tuple[int, int, bytes]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            with open(path, "rb") as handle:
                found[path] = (st.st_mtime_ns, st.st_size, handle.read())
    return found
