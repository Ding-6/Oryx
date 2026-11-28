"""本地账号与登录会话。账号独立认证；不引入租户、角色或资源范围。"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from ..api.response import ok
from ..store import Store

router = APIRouter(prefix="/api/auth", tags=["账号"])
SESSION_COOKIE = "oryx_session"
SESSION_TTL = 30 * 24 * 60 * 60
_PBKDF2_ROUNDS = 310_000


class Credentials(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", value):
            raise ValueError("账号需为 3–32 位字母、数字、下划线或短横线")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 10:
            raise ValueError("密码至少需要 10 位")
        if len(value) > 256:
            raise ValueError("密码不能超过 256 位")
        return value


class LoginCredentials(BaseModel):
    """登录请求 —— **故意不复用注册那套强校验**。

    以前登录也用 `Credentials`：密码输短了直接 422「密码至少需要 10 位」，
    一方面把注册规则泄露给正在猜密码的人，另一方面用户看到的是
    「字段格式错误」而不是「账号或密码不正确」，完全对不上场景。
    登录只有两种结果：账号存在且密码匹配 → 200；否则一律 401。
    脏 body（数组 / 缺字段 / 非字符串）仍由 Pydantic 的 422 挡住 ——
    那是请求形状问题，不是认证结论。
    """

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class PasswordChangeRequest(BaseModel):
    """改密码请求。以前账号域没有这条路径；字段必须过 Pydantic，
    否则 `req.get` 式写法遇到数组 / 字符串 body 会直接 500（模块 10 的教训）。
    """

    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 10:
            raise ValueError("新密码至少需要 10 位")
        if len(value) > 256:
            raise ValueError("新密码不能超过 256 位")
        return value


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register(app, store: Store) -> None:
    @router.get("/status")
    async def _status():
        count = store.user_count()
        return ok({"account_count": count, "registration_open": True})

    @router.post("/register")
    async def _register(req: Credentials):
        try:
            user = store.create_user(req.username, _hash_password(req.password))
        except Exception as exc:
            # SQLite unique constraint, returned as a safe conflict rather than 500.
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "这个账号名已被使用") from None
            raise
        response = ok({"id": user["id"], "username": user["username"]})
        _issue_session(store, response, user["id"])
        return response

    @router.post("/login")
    async def _login(req: LoginCredentials):
        user = store.user_by_username(req.username)
        if not user or not _verify_password(req.password, user["password_hash"]):
            raise HTTPException(401, "账号或密码不正确")
        response = ok({"id": user["id"], "username": user["username"]})
        _issue_session(store, response, user["id"])
        return response

    @router.post("/password")
    async def _change_password(req: PasswordChangeRequest, request: Request):
        """改密码，并让**其它**登录会话全部失效。

        原密码不对 / 新密码与原密码相同都用 400，不用 401：
        401 在前端是「登录态失效」的信号，虽然 `shouldHandleUnauthorized()`
        会挡掉 `/api/auth/*`，但语义上这是表单填错，不该复用认证态错误码。

        当前会话保留（它的 token 与密码无关，本身就是随机值），
        其余会话按 user_id 批量删除 —— 改密码后别的设备下次请求即 401。
        """
        user_id = getattr(request.state, "user_id", None)
        record = store.user_credentials(user_id) if user_id else None
        if not record:
            raise HTTPException(401, "请先登录")
        if not _verify_password(req.old_password, record["password_hash"]):
            raise HTTPException(400, "原密码不正确")
        if req.new_password == req.old_password:
            raise HTTPException(400, "新密码不能与原密码相同")
        if not store.update_password_hash(user_id, _hash_password(req.new_password)):
            raise HTTPException(404, "没有这个账号")
        current = token_digest(request.cookies.get(SESSION_COOKIE, ""))
        revoked = store.delete_login_sessions_except(user_id, current)
        return ok({"changed": True, "revoked_sessions": revoked})

    @router.get("/me")
    async def _me(request: Request):
        user_id = getattr(request.state, "user_id", None)
        user = store.user_by_id(user_id) if user_id else None
        if not user:
            raise HTTPException(401, "请先登录")
        return ok(user)

    @router.post("/logout")
    async def _logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE, "")
        if token:
            store.delete_login_session(token_digest(token))
        response = ok({"logged_out": True})
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    app.include_router(router)


def _issue_session(store: Store, response: Response, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    store.create_login_session(token_digest(token), user_id, time.time() + SESSION_TTL)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="strict",
        path="/",
    )
