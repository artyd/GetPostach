"""Core read queries over the supplier database.

These functions return plain JSON-serialisable dicts so they can be used directly
as (a) REST responses, (b) Claude tool-call results in the chat, and (c) MCP tools.
This is the single source of business logic — do not duplicate it.
"""
from __future__ import annotations

import difflib
from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Contact, Exhibitor, Profile, Quote, Ranking, ThreadState

CUR_SYM = {"EUR": "€", "USD": "$", "GBP": "£"}


def _sym(cur: str | None) -> str:
    if not cur:
        return ""
    return CUR_SYM.get(cur, cur + " ")


# ---------- supplier resolution ----------
def resolve_supplier(db: Session, term: str) -> Ranking | None:
    """Best-effort supplier lookup by domain/slug/name (exact → contains → fuzzy)."""
    if not term:
        return None
    t = term.strip().lower()

    # exact key (domain) or slug
    row = db.execute(
        select(Ranking).where(or_(func.lower(Ranking.key) == t, func.lower(Ranking.slug) == t))
    ).scalars().first()
    if row:
        return row

    # name contains
    rows = db.execute(
        select(Ranking).where(func.lower(Ranking.name).like(f"%{t}%")).limit(10)
    ).scalars().all()
    if len(rows) == 1:
        return rows[0]
    if rows:
        # prefer the highest-score candidate among contains-matches
        return max(rows, key=lambda r: r.score or 0)

    # fuzzy over all names
    all_rows = db.execute(select(Ranking)).scalars().all()
    names = {r.name.lower(): r for r in all_rows if r.name}
    match = difflib.get_close_matches(t, list(names.keys()), n=1, cutoff=0.6)
    return names[match[0]] if match else None


# ---------- search ----------
def search_suppliers(
    db: Session,
    query: str | None = None,
    type_label: str | None = None,
    country: str | None = None,
    status: str | None = None,
    only_with_prices: bool = False,
    only_exhibitors: bool = False,
    limit: int = 25,
) -> list[dict]:
    """Filtered supplier search. `query` matches name/country/products/domain."""
    stmt = select(Ranking).where(func.coalesce(Ranking.role, "") != "customer")

    if type_label:
        stmt = stmt.where(Ranking.type_label == type_label)
    if country:
        stmt = stmt.where(func.lower(Ranking.country).like(f"%{country.lower()}%"))
    if status:
        stmt = stmt.where(Ranking.status == status)
    if only_with_prices:
        stmt = stmt.where(func.coalesce(Ranking.quotes, 0) > 0)

    rows = db.execute(stmt.order_by(Ranking.score.desc())).scalars().all()

    q = (query or "").strip().lower()
    ex_keys = _exhibitor_keys(db) if only_exhibitors else None
    out: list[dict] = []
    for r in rows:
        if ex_keys is not None and r.key not in ex_keys:
            continue
        if q:
            hay = " ".join(filter(None, [
                r.name, r.country, r.key, " ".join(r.products or []),
            ])).lower()
            if q not in hay:
                continue
        out.append(_supplier_card(r))
        if len(out) >= limit:
            break
    return out


def _supplier_card(r: Ranking) -> dict:
    return {
        "slug": r.slug, "key": r.key, "name": r.name,
        "type": r.type_label or r.type, "country": r.country, "status": r.status,
        "score": r.score, "messages": (r.inbound or 0) + (r.outbound or 0),
        "threads": r.threads, "quotes": r.quotes,
        "products": (r.products or [])[:12],
    }


def _exhibitor_keys(db: Session) -> set[str]:
    return {e.key for e in db.execute(select(Exhibitor.key)).scalars().all()}


# ---------- supplier detail ----------
def supplier_detail(db: Session, term: str) -> dict | None:
    r = resolve_supplier(db, term)
    if not r:
        return None
    key = r.key

    prof = db.execute(select(Profile).where(Profile.company == key)).scalars().first()
    # multi-domain merge: a profile may list several domains
    domains = [key]
    if prof and prof.domains:
        domains = list({key, *[d for d in prof.domains if d]})

    contacts = db.execute(
        select(Contact).where(Contact.company.in_(domains))
    ).scalars().all()
    quotes = db.execute(
        select(Quote).where(Quote.company.in_(domains))
    ).scalars().all()
    threads = db.execute(
        select(ThreadState).where(ThreadState.company.in_(domains))
    ).scalars().all()
    ex = db.execute(select(Exhibitor).where(Exhibitor.key.in_(domains))).scalars().first()

    return {
        "slug": r.slug, "key": key, "name": r.name,
        "type": r.type_label or r.type, "country": r.country, "status": r.status,
        "score": r.score, "score_parts": r.parts,
        "first_contact": r.first, "last_contact": r.last, "gap_days": r.gap_days,
        "inbound": r.inbound, "outbound": r.outbound,
        "profile": {
            "summary": prof.summary if prof else None,
            "type_reason": prof.type_reason if prof else None,
            "products": (prof.products if prof else None) or r.products or [],
            "domains": domains,
        },
        "contacts": [_contact(c) for c in contacts],
        "prices": _group_quotes(quotes),
        "threads": [_thread(t) for t in threads],
        "exhibitor": ({
            "hall": ex.hall, "stand": ex.stand, "purpose": ex.purpose,
        } if ex else None),
    }


def _contact(c: Contact) -> dict:
    return {
        "name": c.name, "role": c.role, "email": c.email,
        "emails_alt": c.emails_alt or [], "phone": c.phone, "whatsapp": c.whatsapp,
        "website": c.website, "address": c.address,
    }


def _thread(t: ThreadState) -> dict:
    return {
        "subject": t.subject, "product": t.product, "outcome": t.outcome,
        "last": t.last, "open_question": t.open_question,
    }


def _group_quotes(quotes: list[Quote]) -> list[dict]:
    """Group quotes by product with min/max/last and a small history series."""
    by_prod: dict[str, list[Quote]] = defaultdict(list)
    for qrow in quotes:
        if qrow.product:
            by_prod[qrow.product].append(qrow)

    groups: list[dict] = []
    for product, qs in by_prod.items():
        qs_sorted = sorted(qs, key=lambda x: (x.date or ""))
        nums = [x.price for x in qs_sorted if isinstance(x.price, (int, float))]
        first_cur = next((x.currency for x in qs_sorted if x.currency), "USD")
        first_unit = next((x.unit for x in qs_sorted if x.unit), "kg")
        last_priced = next((x for x in reversed(qs_sorted) if isinstance(x.price, (int, float))), None)
        groups.append({
            "product": product,
            "currency": first_cur, "unit": first_unit,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
            "last": last_priced.price if last_priced else None,
            "last_display": (_sym(first_cur) + f"{last_priced.price:g}/{first_unit}") if last_priced else None,
            "n": len(qs_sorted),
            "history": [
                {"date": x.date, "price": x.price, "incoterms": x.incoterms, "basis": x.basis}
                for x in qs_sorted if isinstance(x.price, (int, float))
            ][-24:],
        })
    groups.sort(key=lambda g: g["n"], reverse=True)
    return groups


# ---------- price benchmark across the whole base ----------
def benchmark_price(db: Session, product: str, limit: int = 40) -> dict:
    """Compare a product's price across all suppliers (cheapest first)."""
    p = (product or "").strip().lower()
    rows = db.execute(
        select(Quote).where(func.lower(Quote.product).like(f"%{p}%"))
    ).scalars().all()

    by_co: dict[str, list[Quote]] = defaultdict(list)
    for qrow in rows:
        by_co[qrow.company].append(qrow)

    # domain -> supplier name
    names = {r.key: r.name for r in db.execute(select(Ranking)).scalars().all()}

    offers = []
    for company, qs in by_co.items():
        priced = [x for x in qs if isinstance(x.price, (int, float))]
        if not priced:
            continue
        latest = sorted(priced, key=lambda x: (x.date or ""))[-1]
        offers.append({
            "supplier": names.get(company, company), "key": company,
            "product": latest.product, "price": latest.price,
            "currency": latest.currency or "USD", "unit": latest.unit or "kg",
            "incoterms": latest.incoterms, "basis": latest.basis,
            "date": latest.date, "n_quotes": len(priced),
        })
    offers.sort(key=lambda o: (o["price"] if o["price"] is not None else 1e18))
    return {
        "product": product,
        "n_suppliers": len(offers),
        "offers": offers[:limit],
    }


# ---------- exhibitors ----------
def list_exhibitors(db: Session) -> list[dict]:
    ex = db.execute(select(Exhibitor)).scalars().all()
    names = {r.key: r for r in db.execute(select(Ranking)).scalars().all()}
    out = []
    for e in ex:
        r = names.get(e.key)
        out.append({
            "key": e.key, "name": e.name or (r.name if r else e.key),
            "hall": e.hall, "stand": e.stand, "purpose": e.purpose,
            "type": (r.type_label if r else None),
            "country": (r.country if r else None),
            "score": (r.score if r else None),
        })
    out.sort(key=lambda x: (x["score"] or 0), reverse=True)
    return out


def stats(db: Session) -> dict:
    return {
        "suppliers": db.execute(
            select(func.count()).select_from(Ranking).where(func.coalesce(Ranking.role, "") != "customer")
        ).scalar_one(),
        "quotes": db.execute(select(func.count()).select_from(Quote)).scalar_one(),
        "contacts": db.execute(select(func.count()).select_from(Contact)).scalar_one(),
        "threads": db.execute(select(func.count()).select_from(ThreadState)).scalar_one(),
        "exhibitors": db.execute(select(func.count()).select_from(Exhibitor)).scalar_one(),
    }
