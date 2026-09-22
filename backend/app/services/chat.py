"""Пігулькін chat — Claude with tool-calling over the supplier database.

Manual agentic loop: Claude may call the DB tools (search, prices, meeting prep)
as many times as needed, then produces a final answer. Grounded in real data —
the system prompt forbids inventing numbers.
"""
from __future__ import annotations

import json

import anthropic
from sqlalchemy.orm import Session

from ..config import get_settings
from ..tools import registry

settings = get_settings()

SYSTEM_PROMPT = """\
Ти — Пігулькін, AI-помічник відділу закупівель Alliance Group 95 (фарма, косметика, \
ветеринарія, харчові інгредієнти). Ти маєш доступ до реальної бази постачальників: \
компанії, контактні особи, товари, ціни та історія переписки, а також список \
експонентів CPHI Worldwide Milan 2026.

Правила:
- Відповідай українською, стисло й по суті, як досвідчений закупівельник.
- Використовуй інструменти для будь-яких фактів про постачальників, ціни та контакти. \
НІКОЛИ не вигадуй цифри, ціни, email чи назви — бери їх лише з інструментів.
- Якщо даних немає — так і скажи, не здогадуйся.
- Коли просять підготуватися до зустрічі або виставки — використай prepare_meeting_brief \
і склади структурований бриф: хто вони, наші козирі, цільова ціна (з бенчмарку), \
відкриті питання, аргументи.
- Не змішуй умови постачання (FOB/CIF/EXW) при порівнянні цін — зазначай інкотермс.
- Для цін завжди вказуй валюту, одиницю та (де є) дату й інкотермс.
"""


class ChatError(Exception):
    pass


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert frontend [{role: user|bot, text}] to Anthropic message format."""
    out = []
    for m in messages:
        role = "assistant" if m.get("role") in ("bot", "assistant") else "user"
        text = (m.get("text") or m.get("content") or "").strip()
        if not text:
            continue
        out.append({"role": role, "content": text})
    # Anthropic requires the first message to be from the user.
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def reply(db: Session, messages: list[dict], max_tool_iters: int = 6) -> str:
    if not settings.anthropic_api_key:
        raise ChatError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    convo = _to_anthropic_messages(messages)
    if not convo:
        raise ChatError("no user message")

    response = None
    for _ in range(max_tool_iters):
        response = client.messages.create(
            model=settings.chat_model,
            max_tokens=settings.chat_max_tokens,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=registry.TOOLS,
            messages=convo,
        )
        if response.stop_reason != "tool_use":
            break

        # Append the assistant turn (incl. thinking + tool_use blocks) unchanged.
        convo.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result = registry.dispatch(db, block.name, block.input)
                except Exception as exc:  # noqa: BLE001
                    result = {"error": "tool_failed", "detail": str(exc)}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        convo.append({"role": "user", "content": tool_results})

    text = "".join(b.text for b in (response.content if response else []) if getattr(b, "type", "") == "text")
    return text.strip() or "Вибачте, не вдалося сформувати відповідь. Спробуйте переформулювати запит."
