"""统一密钥打码层：任何要回给模型或用户的文本，出门前先过一遍这里。

为什么要有这一层：
- 命令输出里可能带 `cat .env` 的内容；
- 子代理读过文件/网页，可能把密钥抄进结论；
- 工具返回值原样进上下文，`shell` 的命令行本身就可能是 `echo sk-xxx`；
- provider 列表、错误信息里常夹带 base_url 的 userinfo。

设计取自 Marvis 的 `log_summary.redact_log_text`（8 条定向正则 + 兜底整行抹除），
但 Marvis 自己有**两套**打码实现且不一致（`system_manager._redact` 只有 2 条正则、
无兜底）—— Oryx 这里做成**单一入口**，比参考项目更严。

原则：**宁可全抹也不漏**。命中兜底正则时整段返回 `[redacted]`，不做局部替换。
误伤代价是丢一段上下文，比泄一个密钥小得多。

已知边界（不打算靠正则解决）：
- **完全裸的凭据**（没有任何键名、没有固定前缀，比如裸的 `super-secret-value`）
  检测不到 —— 它和普通单词无法区分。Marvis 同样如此。
  真实泄密路径（`.env` 内容、JSON 配置、命令行、URL userinfo）都有上下文，已覆盖。
- 高熵字符串检测（哈希、commit id、base64 附件）误伤率太高，不做。
"""

from __future__ import annotations

import re
from typing import Any

# ── 定向正则（照抄 Marvis 的模式，顺序固定）──────────────────────────────

# 私钥块。**整块**抹掉 —— 只抹 BEGIN 那一行的话，后面的 base64 正文
# 就是完整的密钥材料，照样能拿去用。
_PRIVATE_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
# 兜底：只有 BEGIN 没有 END 的畸形私钥块，抹掉头一行。
_PRIVATE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

# URL 里的 user:pass@host —— 只抹口令，保留用户与主机。
_URL_PASS = re.compile(r"(?i)(://[^:/\s]+:)([^@\s/]+)(@)")

# JWT 三段式。
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")

# 云厂商与代码托管的固定前缀令牌。
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB = re.compile(r"\bghp_[0-9A-Za-z]{20,}\b")
_OPENAI = re.compile(r"\bsk-[0-9A-Za-z]{16,}\b")

# HTTP 认证头。
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")

# Authorization 头整行抹除。值可能跨两个词（Bearer + token），
# 所以按「到行尾」处理，比只抹一个词彻底。
_AUTH_HEADER = re.compile(r"(?i)\b(authorization|proxy-authorization)(\s*[=:]\s*)[^\r\n]+")

# key=value / key: value / "key":"value" 形式的凭据。
# 键名里必须含敏感词；键两侧允许有引号（JSON / .env 引号值）。
# 分组：键 / 分隔符 / 值引号 / 值。分隔符与引号原样保留，JSON 才不会被改成非法格式。
# 值用负向前瞻排除已打码的 —— 否则 "[redacted]" 会被再套一层变成 "[redacted]]"。
_ASSIGN = re.compile(
    r"(?i)([\"']?[A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"access[_-]?key)[A-Za-z0-9_.-]*[\"']?)(\s*[=:]\s*)"
    r"([\"']?)(?!\[redacted\])([^\s\"',;}\]]+)"
)

# 自然语言里的凭据：「密码是 hunter2」「password is hunter2」。
# Marvis 的 _ASSIGN 只认 = 和 :，散文里这两种写法漏得很干净 ——
# 而模型输出的总结最爱这么写。
_NL_ASSIGN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"authorization)\b\s+(?:is|was|为|是|等于)\s+(\S+)"
)
_NL_ASSIGN_CN = re.compile(r"(密码|密钥|令牌|口令)\s*(?:是|为|等于)\s*(\S+)")

# ── 兜底：上面漏掉的疑似凭据 ───────────────────────────────────────────
# 阈值比主正则更宽（sk- 8 位起、ghp_ 8 位起、AKIA 8 位起），专门兜边角。
# URL 那条用负向前瞻排除已打码的，避免把 "[redacted]" 再当成口令。
_LEFTOVER = re.compile(
    r"(?i)(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.|-----BEGIN |"
    r"://[^/\s]+:(?!\[redacted\])[^/\s@]+@)"
)

# 只读文本（不动任何东西）也带这四个字段，自证「我没动过」。
READONLY_PROOF: dict[str, Any] = {
    "preview": True,
    "deleted": False,
    "modified": False,
    "moved": False,
}


def redact(text: str) -> str:
    """把文本里的凭据样式抹掉。

    正常文本（"error: file not found"）原样返回；
    命中兜底正则时**整段**返回 `"[redacted]"`。
    """
    if not text:
        return text
    out = _PRIVATE_BLOCK.sub("[redacted]", text)
    out = _PRIVATE.sub("[redacted]", out)
    out = _URL_PASS.sub(r"\1[redacted]\3", out)
    out = _JWT.sub("[redacted]", out)
    out = _AWS.sub("[redacted]", out)
    out = _GITHUB.sub("[redacted]", out)
    out = _OPENAI.sub("[redacted]", out)
    # Authorization 头先于 Bearer 处理 —— 头里常是「Bearer + token」两个词，
    # 只抹 Bearer 会把 token 留在后面。
    out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", out)
    out = _BEARER.sub("Bearer [redacted]", out)
    # key=value / "key":"value" —— 分隔符与引号原样保留，JSON 才不会被打成非法格式。
    out = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]", out)
    # 自然语言：「密码是 xxx」「password is xxx」
    out = _NL_ASSIGN.sub(lambda m: f"{m.group(1)} is [redacted]", out)
    out = _NL_ASSIGN_CN.sub(lambda m: f"{m.group(1)}是[redacted]", out)
    if _LEFTOVER.search(out):
        return "[redacted]"
    return out


def redact_obj(obj: Any) -> Any:
    """递归处理 dict / list / tuple 里的字符串。

    键名本身也要打码 —— `{"api_key": "..."}` 的键名不该原样出现在日志里。
    """
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            new_key = redact(str(key)) if isinstance(key, str) else key
            out[new_key] = redact_obj(value)
        return out
    if isinstance(obj, list):
        return [redact_obj(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(item) for item in obj)
    return obj


def redact_result(result: dict[str, Any]) -> dict[str, Any]:
    """对工具返回体做打码。

    只动 `content` 与 `meta` —— 这两个是真正回给模型的部分。
    `ok` 之类的控制字段保持原样。
    """
    if not isinstance(result, dict):
        return result
    out = dict(result)
    if "content" in out:
        out["content"] = redact_obj(out["content"])
    if isinstance(out.get("meta"), dict):
        out["meta"] = redact_obj(out["meta"])
    return out
