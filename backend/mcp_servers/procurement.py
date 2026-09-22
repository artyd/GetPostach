"""Procurement MCP server — exposes the supplier database to any MCP client
(Claude Desktop, Claude Code, the Пігулькін backend) for the pharma / cosmetics /
veterinary purchasing team.

Reuses the exact same tool logic as the chat (app.tools.registry) — one
implementation, two front doors.

Run:
    # stdio (Claude Desktop / Claude Code)
    python -m mcp_servers.procurement
    # remote HTTP (shared server)
    MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8931 python -m mcp_servers.procurement
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from app.db import SessionLocal
from app.tools import registry

mcp = MCPServer("pigulkin-procurement")


def _run(name: str, args: dict) -> dict:
    db = SessionLocal()
    try:
        return registry.dispatch(db, name, args)
    finally:
        db.close()


@mcp.tool()
def search_suppliers(
    query: str = "",
    type_label: str = "",
    country: str = "",
    only_with_prices: bool = False,
    only_exhibitors: bool = False,
    limit: int = 25,
) -> dict:
    """Знайти постачальників за ключовими словами (назва, країна, товар) з фільтрами."""
    return _run("search_suppliers", {
        "query": query, "type_label": type_label or None, "country": country or None,
        "only_with_prices": only_with_prices, "only_exhibitors": only_exhibitors, "limit": limit,
    })


@mcp.tool()
def get_supplier(term: str) -> dict:
    """Повна картка постачальника (профіль, контакти, ціни, історія). Приймає домен, slug або назву."""
    return _run("get_supplier", {"term": term})


@mcp.tool()
def benchmark_price(product: str, limit: int = 40) -> dict:
    """Порівняти ціну на товар по всіх постачальниках бази (від найдешевшої)."""
    return _run("benchmark_price", {"product": product, "limit": limit})


@mcp.tool()
def list_exhibitors() -> dict:
    """Список експонентів CPHI Worldwide Milan 2026 з бази (стенд, hall, тип)."""
    return _run("list_exhibitors", {})


@mcp.tool()
def prepare_meeting_brief(term: str) -> dict:
    """Матеріали для підготовки до зустрічі на виставці: профіль, контакти, ціни, бенчмарк, стенд, відкриті питання."""
    return _run("prepare_meeting_brief", {"term": term})


@mcp.tool()
def base_stats() -> dict:
    """Загальна статистика бази постачальників."""
    return _run("base_stats", {})


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.settings.host = os.getenv("MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("MCP_PORT", "8931"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
