"""Tool definitions + dispatch shared by the chat (Claude tool-calling) and the
procurement MCP server. One schema, one implementation, two consumers.

Each tool's `input_schema` is a JSON Schema (Anthropic tool-use format). `dispatch`
runs the tool against a DB session and returns a JSON-serialisable dict.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..services import prep, queries

# ---- Tool schemas (Anthropic tool-use / MCP compatible) ----
TOOLS: list[dict] = [
    {
        "name": "search_suppliers",
        "description": (
            "Знайти постачальників у базі за ключовими словами (назва, країна, товар), "
            "з фільтрами. Використовуй, коли треба знайти або перелічити компанії."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Ключові слова: назва, країна, товар/субстанція"},
                "type_label": {"type": "string", "description": "Тип: Завод / Трейдер / Неясно"},
                "country": {"type": "string", "description": "Країна (частковий збіг)"},
                "only_with_prices": {"type": "boolean", "description": "Лише компанії, від яких є ціни"},
                "only_exhibitors": {"type": "boolean", "description": "Лише учасники CPHI Milan 2026"},
                "limit": {"type": "integer", "description": "Максимум результатів (за замовч. 25)"},
            },
        },
    },
    {
        "name": "get_supplier",
        "description": (
            "Повна картка постачальника: профіль, контактні особи, товари й ціни, "
            "історія переписки. Приймає домен, slug або назву компанії."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"term": {"type": "string", "description": "Домен, slug або назва компанії"}},
            "required": ["term"],
        },
    },
    {
        "name": "benchmark_price",
        "description": (
            "Порівняти ціну на товар/субстанцію по всіх постачальниках бази (від найдешевшої). "
            "Використовуй для питань про ринкову ціну та вибір найкращої пропозиції."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": "Назва товару або субстанції"},
                "limit": {"type": "integer", "description": "Максимум пропозицій (за замовч. 40)"},
            },
            "required": ["product"],
        },
    },
    {
        "name": "list_exhibitors",
        "description": "Список експонентів CPHI Worldwide Milan 2026 з бази (стенд, hall, тип).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "prepare_meeting_brief",
        "description": (
            "Зібрати матеріали для підготовки до зустрічі з постачальником на виставці: "
            "профіль, контакти, цінові позиції, бенчмарк цін по базі, стенд CPHI та відкриті "
            "питання з переписки. Використовуй, коли просять підготуватися до зустрічі/виставки."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"term": {"type": "string", "description": "Домен, slug або назва компанії"}},
            "required": ["term"],
        },
    },
    {
        "name": "base_stats",
        "description": "Загальна статистика бази: кількість постачальників, котирувань, контактів, тем, експонентів.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def dispatch(db: Session, name: str, args: dict) -> dict:
    args = args or {}
    if name == "search_suppliers":
        return {"results": queries.search_suppliers(
            db,
            query=args.get("query"),
            type_label=args.get("type_label"),
            country=args.get("country"),
            only_with_prices=bool(args.get("only_with_prices")),
            only_exhibitors=bool(args.get("only_exhibitors")),
            limit=int(args.get("limit") or 25),
        )}
    if name == "get_supplier":
        d = queries.supplier_detail(db, args.get("term", ""))
        return d or {"error": "not_found", "term": args.get("term")}
    if name == "benchmark_price":
        return queries.benchmark_price(db, args.get("product", ""), limit=int(args.get("limit") or 40))
    if name == "list_exhibitors":
        return {"exhibitors": queries.list_exhibitors(db)}
    if name == "prepare_meeting_brief":
        d = prep.prepare_meeting_brief(db, args.get("term", ""))
        return d or {"error": "not_found", "term": args.get("term")}
    if name == "base_stats":
        return queries.stats(db)
    return {"error": "unknown_tool", "name": name}
