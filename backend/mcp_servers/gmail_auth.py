"""Gmail OAuth helper for the email-drafts MCP server.

The team authorizes ONCE with `python -m mcp_servers.gmail_auth authorize`
(opens a browser, you consent, a token is stored). After that the MCP server
uses the stored token to create drafts — no browser needed at runtime.

Scope is gmail.compose: create/read/update/delete DRAFTS only. It does NOT send
mail — a human reviews and sends each draft from Gmail.

Env:
    GMAIL_CREDENTIALS_PATH  OAuth client secret JSON from Google Cloud Console
                            (APIs & Services → Credentials → OAuth client → Desktop app)
    GMAIL_TOKEN_PATH        Where the authorized token is stored (default ./gmail_token.json)
"""
from __future__ import annotations

import os

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def _paths() -> tuple[str, str]:
    creds = os.getenv("GMAIL_CREDENTIALS_PATH", "gmail_credentials.json")
    token = os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")
    return creds, token


def get_credentials():
    """Load stored credentials, refreshing if expired. Raises if not authorized yet."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    _, token_path = _paths()
    if not os.path.exists(token_path):
        raise RuntimeError(
            f"Gmail not authorized yet (no token at {token_path}). "
            "Run: python -m mcp_servers.gmail_auth authorize"
        )
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        else:
            raise RuntimeError("Gmail token invalid — re-run authorize.")
    return creds


def get_service():
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def build_raw(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    import base64
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Create a Gmail draft (does NOT send). Returns draft id or a friendly error."""
    try:
        service = get_service()
    except Exception as exc:  # noqa: BLE001
        return {"error": "gmail_not_ready", "detail": str(exc)}
    try:
        raw = build_raw(to, subject, body, cc, bcc)
        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        return {
            "ok": True, "draft_id": draft.get("id"),
            "url": "https://mail.google.com/mail/u/0/#drafts",
            "note": "Чернетку створено. Перегляньте й надішліть вручну в Gmail.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": "draft_failed", "detail": str(exc)}


def list_drafts(max_results: int = 20) -> dict:
    try:
        service = get_service()
    except Exception as exc:  # noqa: BLE001
        return {"error": "gmail_not_ready", "detail": str(exc)}
    try:
        resp = service.users().drafts().list(userId="me", maxResults=max_results).execute()
        out = []
        for d in resp.get("drafts", []):
            full = service.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
            headers = {h["name"]: h["value"] for h in full.get("message", {}).get("payload", {}).get("headers", [])}
            out.append({"draft_id": d["id"], "to": headers.get("To", ""), "subject": headers.get("Subject", "")})
        return {"drafts": out}
    except Exception as exc:  # noqa: BLE001
        return {"error": "list_failed", "detail": str(exc)}


def credentials_present() -> bool:
    """True if the OAuth client secret JSON has been placed on the server."""
    creds_path, _ = _paths()
    return os.path.exists(creds_path)


def is_authorized() -> bool:
    """True if we have a usable (refreshable) Gmail token."""
    try:
        get_credentials()
        return True
    except Exception:  # noqa: BLE001
        return False


def redirect_uri() -> str:
    return os.getenv("GMAIL_REDIRECT_URI", "https://getpostach.alliancegroup95.com/api/gmail/callback")


def web_flow(state: str | None = None):
    """Build a Google OAuth *web* flow (headless-server friendly, redirect-based)."""
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google may add openid/scope
    from google_auth_oauthlib.flow import Flow

    creds_path, _ = _paths()
    if not os.path.exists(creds_path):
        raise RuntimeError(
            "Missing OAuth client secret. Add gmail_credentials.json to the server's secrets/ "
            "(Google Cloud → OAuth client, type 'Web application')."
        )
    return Flow.from_client_secrets_file(creds_path, scopes=SCOPES, redirect_uri=redirect_uri(), state=state)


def save_token(creds) -> None:
    _, token_path = _paths()
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())


def authorize() -> None:
    """One-time interactive consent on a machine WITH a browser (local dev only).

    On the production (headless) server use the web flow instead:
    GET /api/gmail/authorize -> Google consent -> /api/gmail/callback.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path, token_path = _paths()
    if not os.path.exists(creds_path):
        raise SystemExit(
            f"Missing OAuth client secret at {creds_path}. Download it from Google Cloud "
            "Console (OAuth client) and set GMAIL_CREDENTIALS_PATH."
        )
    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    print(f"Authorized. Token saved to {token_path}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        authorize()
    else:
        print("Usage: python -m mcp_servers.gmail_auth authorize")
