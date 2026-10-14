"""Small password and signed-token primitives for the optional local auth boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from contextvars import ContextVar

from fastapi import HTTPException, Request

from app.config import settings
from app.database import connect_auth_db

_HASH_ITERATIONS = 240_000
current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def get_current_user_id() -> int | None:
    return current_user_id.get()


def get_current_user(request: Request) -> dict:
    """Explicit user dependency for routes that need an account identity."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(status_code=401, detail="需要登录后访问")
    with connect_auth_db() as conn:
        row = conn.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    return dict(row)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _HASH_ITERATIONS)
    return f"pbkdf2_sha256${_HASH_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_user(email: str, password: str) -> dict:
    normalized = normalize_email(email)
    with connect_auth_db() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users(email, password_hash) VALUES (?, ?)",
                (normalized, hash_password(password)),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise ValueError("邮箱已注册") from exc
            raise
        row = conn.execute("SELECT id, email, created_at FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.commit()
        return dict(row)


def authenticate_user(email: str, password: str) -> dict | None:
    with connect_auth_db() as conn:
        row = conn.execute("SELECT id, email, password_hash, created_at FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}


def issue_token(user: dict) -> str:
    expires = int(time.time()) + max(300, settings.auth_token_ttl_seconds)
    token_id = secrets.token_hex(16)
    payload = f"{int(user['id'])}.{expires}.{token_id}"
    signature = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}.{signature}"
    with connect_auth_db() as conn:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (_token_hash(token), int(user["id"]), expires),
        )
        conn.commit()
    return token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def revoke_token(token: str) -> bool:
    with connect_auth_db() as conn:
        cursor = conn.execute(
            "UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ? AND revoked_at IS NULL",
            (_token_hash(token),),
        )
        conn.commit()
    return cursor.rowcount > 0


def verify_token(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 4:
        return None
    user_id_text, expires_text, token_id, signature = parts
    payload = f"{user_id_text}.{expires_text}.{token_id}"
    expected = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        if int(expires_text) < int(time.time()):
            return None
        user_id = int(user_id_text)
    except ValueError:
        return None
    with connect_auth_db() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.created_at
            FROM users u JOIN auth_sessions s ON s.user_id = u.id
            WHERE u.id = ? AND s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at >= ?
            """,
            (user_id, _token_hash(token), int(time.time())),
        ).fetchone()
    return dict(row) if row else None
