"""Bulk RFQ letter generation via the Anthropic Message Batches API.

For large mailouts (many suppliers at once) batching is ~50% cheaper and async:
create one batch of letter-generation requests (one per supplier), poll, then
optionally turn the results into Gmail drafts. Grounds each letter in the
supplier's real contact + last price from the DB.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..config import get_settings
from . import chat as chat_service
from . import queries

settings = get_settings()

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]")

LETTER_SYSTEM = """\
Ти складаєш професійний RFQ-лист (запит комерційної пропозиції) від відділу закупівель \
Alliance Group 95. Пиши мовою, доречною для країни постачальника (для Китаю/Індії — англійською, \
для України — українською). Лист має бути коротким, конкретним і ввічливим: привітання на ім'я \
контакту, чіткий запит ціни на вказаний товар (обсяг, інкотермс FOB/CIF за потреби, умови оплати, \
термін виробництва, актуальний COA), і підпис. Не вигадуй цифр, яких немає в контексті. \
Поверни ЛИШЕ текст листа (без пояснень)."""


def _slug(term: str) -> str:
    return _SLUG_RE.sub("-", term)[:64]


def create_rfq_batch(db: Session, product: str, companies: list[str],
                     signature: str = "Procurement Team\nAlliance Group 95",
                     notes: str = "") -> dict:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = chat_service._client()
    requests = []
    seen = set()
    skipped = []
    for term in companies:
        d = queries.supplier_detail(db, term)
        if not d:
            skipped.append(term)
            continue
        cid = _slug(d["slug"] or d["key"])
        if cid in seen:
            continue
        seen.add(cid)
        contact = (d.get("contacts") or [{}])[0]
        price_line = ""
        for g in d.get("prices", []):
            if product.lower() in (g.get("product") or "").lower():
                price_line = f"Остання відома ціна від них: {g.get('last_display')}."
                break
        ctx = (
            f"Компанія: {d['name']} (країна: {d.get('country') or '—'}, тип: {d.get('type') or '—'}).\n"
            f"Контактна особа: {contact.get('name') or '—'} <{contact.get('email') or '—'}>.\n"
            f"Товар для запиту: {product}.\n"
            f"{price_line}\n"
            f"Підпис відправника:\n{signature}\n"
            + (f"Додатково: {notes}\n" if notes else "")
        )
        requests.append(Request(
            custom_id=cid,
            params=MessageCreateParamsNonStreaming(
                model=settings.batch_model,
                max_tokens=1024,
                system=LETTER_SYSTEM,
                messages=[{"role": "user", "content": ctx + "\nНапиши RFQ-лист."}],
            ),
        ))

    if not requests:
        return {"error": "no_valid_companies", "skipped": skipped}

    batch = client.messages.batches.create(requests=requests)
    return {"batch_id": batch.id, "count": len(requests), "skipped": skipped,
            "status": batch.processing_status}


def get_batch(db: Session, batch_id: str) -> dict:
    client = chat_service._client()
    b = client.messages.batches.retrieve(batch_id)
    out = {
        "batch_id": batch_id,
        "status": b.processing_status,
        "counts": {
            "succeeded": b.request_counts.succeeded,
            "errored": b.request_counts.errored,
            "processing": b.request_counts.processing,
        },
    }
    if b.processing_status != "ended":
        return out

    letters = []
    for r in client.messages.batches.results(batch_id):
        slug = r.custom_id
        d = queries.supplier_detail(db, slug)
        name = d["name"] if d else slug
        email = ((d.get("contacts") or [{}])[0].get("email") if d else "") or ""
        if r.result.type == "succeeded":
            txt = "".join(bl.text for bl in r.result.message.content if getattr(bl, "type", "") == "text")
            letters.append({"slug": slug, "name": name, "email": email, "letter": txt.strip()})
        else:
            letters.append({"slug": slug, "name": name, "email": email, "error": r.result.type})
    out["letters"] = letters
    return out


def create_drafts_from_batch(db: Session, batch_id: str, subject_prefix: str = "RFQ") -> dict:
    """Turn a finished batch's letters into Gmail drafts (one per supplier with an email)."""
    from mcp_servers import gmail_auth

    data = get_batch(db, batch_id)
    if data.get("status") != "ended":
        return {"error": "batch_not_ready", "status": data.get("status")}
    created, skipped = [], []
    for lt in data.get("letters", []):
        if not lt.get("email") or lt.get("error"):
            skipped.append({"slug": lt["slug"], "reason": lt.get("error") or "no_email"})
            continue
        subject = f"{subject_prefix} — {lt['name']}"
        res = gmail_auth.create_draft(to=lt["email"], subject=subject, body=lt["letter"])
        if res.get("ok"):
            created.append({"slug": lt["slug"], "draft_id": res["draft_id"]})
        else:
            skipped.append({"slug": lt["slug"], "reason": res.get("error")})
    return {"created": created, "skipped": skipped}
