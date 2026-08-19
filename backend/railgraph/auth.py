# -*- coding: utf-8 -*-
"""Accounts: signup/login/session/account-management on top of Postgres.

A session is an opaque random token handed to the browser as an httpOnly
cookie; only its SHA-256 hash is stored, so a DB leak alone can't be replayed
as a live session.  Everything else (password hashing, expiry, revocation)
follows from that one table.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from . import config

_hasher = PasswordHasher()
_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);

-- Added after the initial rollout: the login ID, distinct from the contact
-- email.  Nullable at the DB level (old rows predate it) but always set by
-- the signup endpoint, so a partial unique index is enough.
ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS users_username_idx ON users (username) WHERE username IS NOT NULL;

CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sessions_token_hash_idx ON sessions (token_hash);
"""


async def init_db() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)
    return _pool


async def close_db() -> None:
    if _pool is not None:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    assert _pool is not None, "auth.init_db() has not run yet"
    return _pool


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _create_session(conn: asyncpg.Connection, user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.SESSION_TTL_S)
    await conn.execute(
        "INSERT INTO sessions (id, user_id, token_hash, expires_at) VALUES ($1, $2, $3, $4)",
        uuid.uuid4(), user_id, _hash_token(token), expires_at,
    )
    return token


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        config.SESSION_COOKIE, token, max_age=config.SESSION_TTL_S,
        httponly=True, samesite="lax", path="/",
    )


def _clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(config.SESSION_COOKIE, path="/")


def _public_user(row: asyncpg.Record) -> dict:
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "email": row["email"],
        "display_name": row["display_name"],
        "created_at": row["created_at"].isoformat(),
    }


async def user_from_token(token: str | None) -> asyncpg.Record | None:
    if not token or _pool is None:
        return None
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT u.* FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = $1 AND s.revoked_at IS NULL
              AND s.expires_at > now() AND u.deleted_at IS NULL
            """,
            _hash_token(token),
        )


async def require_user(
    request: Request, rg_session: str | None = Cookie(default=None),
) -> asyncpg.Record:
    user = await user_from_token(rg_session)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return user


# ---------------------------------------------------------------------- API
router = APIRouter(prefix="/api/auth", tags=["auth"])


USERNAME_RE = r"^[a-zA-Z0-9_]{4,20}$"


class SignupBody(BaseModel):
    username: str = Field(pattern=USERNAME_RE)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=60)


class LoginBody(BaseModel):
    username: str
    password: str


class UpdateAccountBody(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=200)


class DeleteAccountBody(BaseModel):
    password: str


@router.post("/signup")
async def signup(body: SignupBody, response: Response):
    assert _pool is not None
    async with _pool.acquire() as conn:
        if await conn.fetchrow("SELECT id FROM users WHERE username = $1", body.username):
            raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
        if await conn.fetchrow("SELECT id FROM users WHERE email = $1", body.email):
            raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다")
        user_id = uuid.uuid4()
        await conn.execute(
            """INSERT INTO users (id, username, email, password_hash, display_name)
               VALUES ($1, $2, $3, $4, $5)""",
            user_id, body.username, body.email, _hasher.hash(body.password), body.display_name,
        )
        token = await _create_session(conn, user_id)
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    _set_session_cookie(response, token)
    return _public_user(row)


@router.post("/login")
async def login(body: LoginBody, response: Response):
    assert _pool is not None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE username = $1 AND deleted_at IS NULL", body.username,
        )
        if row is None:
            raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
        try:
            _hasher.verify(row["password_hash"], body.password)
        except VerifyMismatchError:
            raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
        token = await _create_session(conn, row["id"])
    _set_session_cookie(response, token)
    return _public_user(row)


@router.post("/logout")
async def logout(response: Response, rg_session: str | None = Cookie(default=None)):
    if rg_session and _pool is not None:
        async with _pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET revoked_at = now() WHERE token_hash = $1",
                _hash_token(rg_session),
            )
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: asyncpg.Record = Depends(require_user)):
    return _public_user(user)


@router.patch("/me")
async def update_account(
    body: UpdateAccountBody,
    user: asyncpg.Record = Depends(require_user),
):
    assert _pool is not None
    async with _pool.acquire() as conn:
        if body.new_password:
            if not body.current_password:
                raise HTTPException(status_code=400, detail="현재 비밀번호를 입력하세요")
            try:
                _hasher.verify(user["password_hash"], body.current_password)
            except VerifyMismatchError:
                raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다")
            await conn.execute(
                "UPDATE users SET password_hash = $1, updated_at = now() WHERE id = $2",
                _hasher.hash(body.new_password), user["id"],
            )
        if body.display_name:
            await conn.execute(
                "UPDATE users SET display_name = $1, updated_at = now() WHERE id = $2",
                body.display_name, user["id"],
            )
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user["id"])
    return _public_user(row)


@router.delete("/me")
async def delete_account(
    body: DeleteAccountBody,
    response: Response,
    user: asyncpg.Record = Depends(require_user),
):
    assert _pool is not None
    try:
        _hasher.verify(user["password_hash"], body.password)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE users SET deleted_at = now() WHERE id = $1", user["id"],
            )
            await conn.execute(
                "UPDATE sessions SET revoked_at = now() WHERE user_id = $1", user["id"],
            )
    _clear_session_cookie(response)
    return {"ok": True}
