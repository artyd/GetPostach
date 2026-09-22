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


def authorize() -> None:
    """One-time interactive consent. Run this on a machine with a browser."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path, token_path = _paths()
    if not os.path.exists(creds_path):
        raise SystemExit(
            f"Missing OAuth client secret at {creds_path}. Download it from Google Cloud "
            "Console (OAuth client, type 'Desktop app') and set GMAIL_CREDENTIALS_PATH."
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
