"""Exhibition / meeting preparation — ported concept from CPHI_MILAN.

Assembles everything a purchasing manager needs for a CPHI Milan supplier
meeting into one structured object: who they are, contacts, price positions,
a cross-base price benchmark for their top products, exhibitor stand, and the
open negotiation questions from the thread history. The chat model turns this
into a one-page brief.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import queries


def prepare_meeting_brief(db: Session, term: str, max_products: int = 5) -> dict | None:
    detail = queries.supplier_detail(db, term)
    if not detail:
        return None

    # Cross-base benchmark for the supplier's most-quoted products.
    benchmarks = []
    for grp in detail.get("prices", [])[:max_products]:
        product = grp.get("product")
        if not product:
            continue
        bench = queries.benchmark_price(db, product, limit=8)
        # mark our supplier's own offer within the benchmark
        for off in bench.get("offers", []):
            off["is_this_supplier"] = (off.get("key") == detail["key"])
        benchmarks.append(bench)

    open_questions = [
        {"product": t.get("product"), "subject": t.get("subject"),
         "outcome": t.get("outcome"), "open_question": t.get("open_question")}
        for t in detail.get("threads", [])
        if t.get("open_question") or t.get("outcome") in ("no_price", "quoted", "dead")
    ][:12]

    return {
        "supplier": {
            "name": detail["name"], "key": detail["key"], "slug": detail["slug"],
            "type": detail["type"], "country": detail["country"], "status": detail["status"],
            "score": detail["score"],
            "summary": detail["profile"]["summary"],
            "type_reason": detail["profile"]["type_reason"],
        },
        "exhibitor": detail.get("exhibitor"),
        "contacts": detail.get("contacts", []),
        "price_positions": detail.get("prices", [])[:max_products],
        "benchmarks": benchmarks,
        "open_questions": open_questions,
        "activity": {
            "first_contact": detail.get("first_contact"),
            "last_contact": detail.get("last_contact"),
            "gap_days": detail.get("gap_days"),
            "inbound": detail.get("inbound"), "outbound": detail.get("outbound"),
        },
    }
