from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx.domains import account as account_domain
from oryx.domains import session as session_domain
from oryx.domains.account import SESSION_COOKIE, token_digest
from oryx.store import Store


def _app(store: Store) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def bind_user(request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE, "")
        user = store.user_for_login_session(token_digest(token)) if token else None
        request.state.user_id = user["id"] if user else None
        return await call_next(request)

    # Domain modules keep a router singleton for production assembly. Use fresh
    # routers here so this isolated app cannot capture another test's Store.
    account_router, session_router = account_domain.router, session_domain.router
    account_domain.router = APIRouter(prefix="/api/auth", tags=["账号"])
    session_domain.router = APIRouter(prefix="/api/sessions", tags=["会话"])
    try:
        account_domain.register(app, store)
        session_domain.register(app, store)
    finally:
        account_domain.router, session_domain.router = account_router, session_router
    return app


def _data(response):
    body = response.json()
    assert body["code"] == response.status_code or response.status_code == 200
    return body["data"]


def test_first_account_claims_legacy_sessions_and_accounts_are_isolated(tmp_path):
    store = Store(tmp_path / "oryx.db")
    legacy_id = store.create_task("旧会话", "craft", "local")
    app = _app(store)
    first = TestClient(app)
    first_user = _data(first.post(
        "/api/auth/register", json={"username": "alpha", "password": "long-password-1"}
    ))
    own_id = store.create_task("Alpha 会话", "craft", "local", first_user["id"])

    second = TestClient(app)
    second_user = _data(second.post(
        "/api/auth/register", json={"username": "beta", "password": "long-password-2"}
    ))
    other_id = store.create_task("Beta 会话", "craft", "local", second_user["id"])
    alpha_memory = store.save_memory("alpha private fact", owner_id=first_user["id"])
    store.save_memory("beta private fact", owner_id=second_user["id"])

    assert store.get_task(legacy_id, first_user["id"]) is not None
    assert store.get_task(legacy_id, second_user["id"]) is None
    assert store.get_task(own_id, second_user["id"]) is None
    assert [m["text"] for m in store.search_memories(owner_id=first_user["id"])] == [
        "alpha private fact"
    ]
    assert [m["text"] for m in store.search_memories(owner_id=second_user["id"])] == [
        "beta private fact"
    ]
    assert not store.forget_memory(alpha_memory["id"], second_user["id"])

    first_ids = {item["id"] for item in _data(first.get("/api/sessions"))}
    second_ids = {item["id"] for item in _data(second.get("/api/sessions"))}
    assert first_ids == {legacy_id, own_id}
    assert second_ids == {other_id}
    assert second.get(f"/api/sessions/{own_id}").status_code == 404

    record = store.user_by_username("alpha")
    assert record is not None
    assert "long-password-1" not in record["password_hash"]

    first.post("/api/auth/logout")
    failed = first.post(
        "/api/auth/login", json={"username": "alpha", "password": "incorrect-password"}
    )
    assert failed.status_code == 401
    logged_in = first.post(
        "/api/auth/login", json={"username": "alpha", "password": "long-password-1"}
    )
    assert logged_in.status_code == 200
    assert first.cookies.get(SESSION_COOKIE)
    assert _data(first.get("/api/auth/me"))["username"] == "alpha"
