"""跨端协议：配对、加密、消息封装。

设计取舍：
- **中转不可信**。中转服务器只转发密文，看不到屏幕内容和操作。
- **配对码即密钥材料**。6 位码由 PC 生成，手机输入；双方用 HKDF 派生出会话密钥。
  短码熵低，所以配对码只在一次会话内有效，且每帧都带随机 nonce。
- **一条通道两种消息**。明文首字节区分：0x01 = JSON 控制，0x02 = JPEG 帧。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTOCOL_VERSION = 1
KDF_SALT = b"oryx-remote"
KDF_INFO = b"oryx-remote-v1"
NONCE_BYTES = 12
TAG_BYTES = 16

KIND_CONTROL = 0x01
KIND_FRAME = 0x02

# 去掉容易混淆的字符（0/O/1/I）
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 8


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def derive_key(code: str) -> bytes:
    """配对码 → 32 字节 AES 密钥。iOS 侧用 CryptoKit 的 HKDF 得到同样结果。"""
    normalized = normalize_code(code).encode("utf-8")
    if not normalized:
        raise ValueError("配对码不能为空")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=KDF_SALT, info=KDF_INFO)
    return hkdf.derive(hashlib.sha256(normalized).digest())


def seal(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def unseal(key: bytes, blob: bytes) -> bytes:
    if len(blob) < NONCE_BYTES + TAG_BYTES:
        raise ValueError("消息太短")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def pack_control(key: bytes, message: dict[str, Any]) -> bytes:
    payload = bytes([KIND_CONTROL]) + json.dumps(message, ensure_ascii=False).encode("utf-8")
    return seal(key, payload)


def pack_frame(key: bytes, jpeg: bytes) -> bytes:
    return seal(key, bytes([KIND_FRAME]) + jpeg)


def unpack(key: bytes, blob: bytes) -> tuple[int, Any]:
    """返回 (kind, payload)。kind=1 时 payload 是 dict，kind=2 时是 JPEG bytes。"""
    plaintext = unseal(key, blob)
    if not plaintext:
        raise ValueError("空消息")
    kind, body = plaintext[0], plaintext[1:]
    if kind == KIND_CONTROL:
        return kind, json.loads(body.decode("utf-8"))
    return kind, body


# ------------------------------------------------------------------ 控制消息


def control(message_type: str, **fields: Any) -> dict[str, Any]:
    return {"t": message_type, "v": PROTOCOL_VERSION, **fields}


def hello(role: str) -> dict[str, Any]:
    return control("hello", role=role)


def screen_meta(width: int, height: int) -> dict[str, Any]:
    return control("meta", w=width, h=height)


def input_event(action: str, **fields: Any) -> dict[str, Any]:
    """action: tap / move / swipe / wheel / key / text"""
    return control("input", kind=action, **fields)
