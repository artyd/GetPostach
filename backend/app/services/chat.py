"""Пігулькін chat — Claude with tool-calling over the supplier database.

- reply(): non-streaming answer (tool loop).
- reply_stream(): SSE generator — streams text deltas + tool-status events.
- summarize(): cheap-model conversation summary for compaction.

Grounded in real data — the system prompt forbids inventing numbers.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

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
- Коли просять надіслати запит/RFQ або підготувати лист — можеш створити чернетку в Gmail \
через create_gmail_draft (лист НЕ надсилається, людина перевіряє й надсилає сама). \
Спочатку візьми контакт і ціни з бази, потім склади професійний лист.
- Не змішуй умови постачання (FOB/CIF/EXW) при порівнянні цін — зазначай інкотермс.
- Для цін завжди вказуй валюту, одиницю та (де є) дату й інкотермс.
"""


class ChatError(Exception):
    pass


def _client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ChatError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _system(summary: str | None) -> str:
    if summary:
        return (
            SYSTEM_PROMPT
            + "\n\n[Стислий контекст попередньої розмови — використовуй як фон]\n"
            + summary.strip()
        )
    return SYSTEM_PROMPT


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert frontend [{role: user|bot, text}] to Anthropic message format."""
    out = []
    for m in messages:
        role = "assistant" if m.get("role") in ("bot", "assistant") else "user"
        text = (m.get("text") or m.get("content") or "").strip()
        if not text:
            continue
        out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _run_tools(db: Session, content) -> list[dict]:
    results = []
    for block in content:
        if getattr(block, "type", "") == "tool_use":
            try:
                result = registry.dispatch(db, block.name, block.input)
            except Exception as exc:  # noqa: BLE001
                result = {"error": "tool_failed", "detail": str(exc)}
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
    return results


def reply(db: Session, messages: list[dict], summary: str | None = None, max_tool_iters: int = 6) -> str:
    client = _client()
    convo = _to_anthropic_messages(messages)
    if not convo:
        raise ChatError("no user message")

    response = None
    for _ in range(max_tool_iters):
        response = client.messages.create(
            model=settings.chat_model,
            max_tokens=settings.chat_max_tokens,
            system=_system(summary),
            thinking={"type": "adaptive"},
            tools=registry.TOOLS,
            messages=convo,
        )
        if response.stop_reason != "tool_use":
            break
        convo.append({"role": "assistant", "content": response.content})
        convo.append({"role": "user", "content": _run_tools(db, response.content)})

    text = "".join(b.text for b in (response.content if response else []) if getattr(b, "type", "") == "text")
    return text.strip() or "Вибачте, не вдалося сформувати відповідь. Спробуйте переформулювати запит."


def reply_stream(db: Session, messages: list[dict], summary: str | None = None,
                 max_tool_iters: int = 6) -> Iterator[dict]:
    """Yields event dicts: {type: delta|status|done|error, text?}."""
    try:
        client = _client()
        convo = _to_anthropic_messages(messages)
        if not convo:
            yield {"type": "error", "text": "no user message"}
            return

        for _ in range(max_tool_iters):
            with client.messages.stream(
                model=settings.chat_model,
                max_tokens=settings.chat_max_tokens,
                system=_system(summary),
                thinking={"type": "adaptive"},
                tools=registry.TOOLS,
                messages=convo,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield {"type": "delta", "text": event.delta.text}
                final = stream.get_final_message()

            if final.stop_reason != "tool_use":
                break
            # Surface which tools are being used, then continue the loop.
            used = [b.name for b in final.content if getattr(b, "type", "") == "tool_use"]
            yield {"type": "status", "text": "Звертаюся до бази: " + ", ".join(used)}
            convo.append({"role": "assistant", "content": final.content})
            convo.append({"role": "user", "content": _run_tools(db, final.content)})

        yield {"type": "done"}
    except ChatError as exc:
        yield {"type": "error", "text": str(exc)}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "text": f"Помилка чату: {exc}"}


def summarize(messages: list[dict]) -> str:
    """Compact a conversation into a short Ukrainian summary (cheap model)."""
    client = _client()
    convo = _to_anthropic_messages(messages)
    if not convo:
        return ""
    dump = "\n".join(f"{'Користувач' if m['role']=='user' else 'Пігулькін'}: {m['content']}" for m in convo)
    resp = client.messages.create(
        model=settings.summary_model,
        max_tokens=1024,
        system=(
            "Ти стискаєш робочу розмову закупівельника з асистентом у короткий контекст. "
            "Збережи всі конкретні факти: назви компаній, товари/субстанції, ціни з валютою та "
            "інкотермс, домовленості, відкриті питання й наступні кроки. Пиши маркерами, стисло, "
            "українською. Не додавай вступів."
        ),
        messages=[{"role": "user", "content": "Стисни цю розмову у контекст:\n\n" + dump}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
