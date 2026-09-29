"""Small password and signed-token primitives for the optional local auth boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.config import settings
from app.database import connect_db

_HASH_ITERATIONS = 240_000


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
    with connect_db() as conn:
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
    with connect_db() as conn:
        row = conn.execute("SELECT id, email, password_hash, created_at FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}


def issue_token(user: dict) -> str:
    expires = int(time.time()) + max(300, settings.auth_token_ttl_seconds)
    payload = f"{int(user['id'])}.{expires}"
    signature = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_token(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id_text, expires_text, signature = parts
    payload = f"{user_id_text}.{expires_text}"
    expected = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        if int(expires_text) < int(time.time()):
            return None
        user_id = int(user_id_text)
    except ValueError:
        return None
    with connect_db() as conn:
        row = conn.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None
