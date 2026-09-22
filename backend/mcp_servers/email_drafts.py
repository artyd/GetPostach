"""Email-drafts MCP server — creates Gmail DRAFTS for RFQ mailouts.

Creates drafts only; it never sends. A human opens the draft in Gmail, reviews,
and sends. This keeps the irreversible step (sending) under human control.

Authorize once: python -m mcp_servers.gmail_auth authorize

Run:
    python -m mcp_servers.email_drafts
    MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8932 python -m mcp_servers.email_drafts
"""
from __future__ import annotations

import base64
import os
from email.message import EmailMessage

from mcp.server.mcpserver import MCPServer

from mcp_servers import gmail_auth

mcp = MCPServer("pigulkin-email")


def _build_raw(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


@mcp.tool()
def create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Створити чернетку листа в Gmail (НЕ надсилає). Людина переглядає й надсилає вручну.

    to/cc/bcc — email-адреси через кому. Повертає id чернетки й посилання на неї.
    """
    try:
        service = gmail_auth.get_service()
    except Exception as exc:  # noqa: BLE001
        return {"error": "gmail_not_ready", "detail": str(exc)}
    try:
        raw = _build_raw(to, subject, body, cc, bcc)
        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        draft_id = draft.get("id")
        return {
            "ok": True, "draft_id": draft_id,
            "message_id": draft.get("message", {}).get("id"),
            "url": "https://mail.google.com/mail/u/0/#drafts",
            "note": "Чернетку створено. Перегляньте й надішліть вручну в Gmail.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": "draft_failed", "detail": str(exc)}


@mcp.tool()
def list_drafts(max_results: int = 20) -> dict:
    """Перелічити наявні чернетки в Gmail (id + тема)."""
    try:
        service = gmail_auth.get_service()
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


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.settings.host = os.getenv("MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("MCP_PORT", "8932"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
