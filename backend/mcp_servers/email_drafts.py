"""Email-drafts MCP server — creates Gmail DRAFTS for RFQ mailouts.

Creates drafts only; it never sends. A human opens the draft in Gmail, reviews,
and sends. This keeps the irreversible step (sending) under human control.

Authorize once: python -m mcp_servers.gmail_auth authorize

Run:
    python -m mcp_servers.email_drafts
    MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8932 python -m mcp_servers.email_drafts
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from mcp_servers import gmail_auth

mcp = MCPServer("pigulkin-email")


@mcp.tool()
def create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Створити чернетку листа в Gmail (НЕ надсилає). Людина переглядає й надсилає вручну.

    to/cc/bcc — email-адреси через кому. Повертає id чернетки й посилання на неї.
    """
    return gmail_auth.create_draft(to, subject, body, cc, bcc)


@mcp.tool()
def list_drafts(max_results: int = 20) -> dict:
    """Перелічити наявні чернетки в Gmail (id + тема)."""
    return gmail_auth.list_drafts(max_results)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("MCP_PORT", "8932")),
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
