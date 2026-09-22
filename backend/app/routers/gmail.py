"""Gmail OAuth (web flow) — headless-server friendly one-time authorization.

Flow:
  1. Admin (logged in) hits GET /api/gmail/authorize  -> {auth_url}
  2. Browser opens auth_url, consents at Google
  3. Google redirects to GET /api/gmail/callback?code=&state= (public, state-signed)
  4. We exchange the code and store the token in GMAIL_TOKEN_PATH

Scope stays gmail.compose (drafts only, never sends).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from mcp_servers import gmail_auth

from ..auth import require_user
from ..config import get_settings

router = APIRouter(prefix="/api/gmail", tags=["gmail"])
settings = get_settings()

_STATE_TTL = 600  # seconds


def _secret() -> bytes:
    return (settings.auth_secret or "dev-insecure-secret").encode("utf-8")


def _sign_state() -> str:
    body = base64.urlsafe_b64encode(f"{int(time.time())}".encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return body + "." + sig


def _verify_state(state: str) -> bool:
    try:
        body, sig = state.split(".")
        expected = base64.urlsafe_b64encode(hmac.new(_secret(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(sig, expected):
            return False
        ts = int(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode())
        return (time.time() - ts) < _STATE_TTL
    except Exception:
        return False


def _page(title: str, msg: str, ok: bool) -> HTMLResponse:
    color = "#1f9e46" if ok else "#C64A72"
    html = f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<div style="font-family:system-ui,Segoe UI,sans-serif;max-width:520px;margin:12vh auto;text-align:center;padding:0 20px">
  <div style="font-size:44px">{'✅' if ok else '⚠️'}</div>
  <h2 style="color:{color};margin:12px 0 6px">{title}</h2>
  <p style="color:#41525b;line-height:1.5">{msg}</p>
  <p style="color:#7b8a91;font-size:13px">Можете закрити цю вкладку.</p>
</div>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.get("/status")
def status(user: str = Depends(require_user)):
    return {
        "credentials_present": gmail_auth.credentials_present(),
        "authorized": gmail_auth.is_authorized(),
        "redirect_uri": gmail_auth.redirect_uri(),
    }


@router.get("/authorize")
def authorize(user: str = Depends(require_user)):
    if not gmail_auth.credentials_present():
        raise HTTPException(status_code=400,
                            detail="Спочатку додайте gmail_credentials.json у secrets/ на сервері.")
    state = _sign_state()
    try:
        flow = gmail_auth.web_flow(state=state)
        auth_url, _ = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent", state=state)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Не вдалося почати авторизацію: {exc}")
    return {"auth_url": auth_url}


@router.get("/callback")
def callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return _page("Авторизацію скасовано", f"Google повернув помилку: {error}", ok=False)
    if not code or not _verify_state(state):
        return _page("Помилка авторизації", "Недійсний або застарілий запит. Спробуйте ще раз із кабінету.", ok=False)
    try:
        flow = gmail_auth.web_flow(state=state)
        flow.fetch_token(code=code)
        gmail_auth.save_token(flow.credentials)
    except Exception as exc:  # noqa: BLE001
        return _page("Помилка авторизації", f"Не вдалося отримати токен: {exc}", ok=False)
    return _page("Gmail підключено", "Пігулькін тепер може створювати чернетки RFQ у вашому Gmail (лише чернетки — нічого не надсилається).", ok=True)
