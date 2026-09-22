"""Lightweight auth — stdlib only (no JWT/bcrypt dependency).

- pbkdf2-sha256 password hashing
- compact HMAC-signed bearer tokens (body.signature)
- FastAPI `require_user` dependency + a tiny in-memory rate limiter

Auth is enforced only when AUTH_SECRET is configured (settings.auth_enabled),
so local dev without a secret stays open.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Header, HTTPException

from .config import get_settings

settings = get_settings()


def _secret() -> bytes:
    return (settings.auth_secret or "dev-insecure-secret").encode("utf-8")


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return "pbkdf2$120000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ---------- tokens ----------
def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(sub: str, days: int = 30) -> str:
    payload = {"sub": sub, "exp": int(time.time()) + days * 86400}
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return body + "." + sig


def verify_token(token: str) -> str | None:
    try:
        body, sig = token.split(".")
        expected = _b64u(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64u_dec(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload.get("sub")
    except Exception:
        return None


# ---------- FastAPI dependency ----------
def require_user(authorization: str = Header(default="")) -> str:
    """Returns the user id (email) or raises 401. No-op when auth is disabled."""
    if not settings.auth_enabled:
        return "anonymous"
    tok = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    sub = verify_token(tok) if tok else None
    if not sub:
        raise HTTPException(status_code=401, detail="Не автентифіковано")
    return sub


# ---------- rate limiter (in-memory, per key) ----------
_RL: dict[str, list[float]] = {}


def rate_limit(key: str, limit: int | None = None, window: int = 60) -> None:
    limit = limit or settings.chat_rate_limit
    now = time.time()
    q = _RL.setdefault(key, [])
    cutoff = now - window
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Забагато запитів — трохи зачекайте.")
    q.append(now)
