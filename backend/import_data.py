"""Load the CPHI_MILAN JSONL sources into the database.

Rebuild-from-source, like build-data.js: drops the data tables and re-imports.
Run from the backend/ directory:

    python import_data.py                     # uses CPHI_DATA_DIR from .env
    python import_data.py /path/to/CPHI/data  # explicit data dir

Works against SQLite (local) or Postgres (prod) via DATABASE_URL.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app import models


# ---------- helpers ----------
def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        print(f"  ! missing: {path}")
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def as_int(v):
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def as_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def dedupe(rows: list[dict], key: str) -> list[dict]:
    """Keep the first row per key (PK uniqueness)."""
    seen: dict = {}
    for r in rows:
        k = r.get(key)
        if k is None or k in seen:
            continue
        seen[k] = r
    return list(seen.values())


# ---------- import ----------
def main() -> int:
    settings = get_settings()
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else settings.cphi_data_dir)
    print(f"Data dir : {data_dir}")
    print(f"Database : {settings.database_url}")
    if not data_dir.exists():
        print(f"ERROR: data dir not found: {data_dir}")
        return 1

    print("Recreating tables …")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    session = SessionLocal()
    try:
        # ranking
        rows = dedupe(read_jsonl(data_dir / "ranking.jsonl"), "slug")
        session.bulk_save_objects([
            models.Ranking(
                slug=r.get("slug") or r.get("key"),
                key=r.get("key") or "",
                name=r.get("name") or r.get("key") or "",
                type=r.get("type"),
                type_label=r.get("type_label"),
                role=r.get("role"),
                role_label=r.get("role_label"),
                read=as_int(r.get("read")),
                country=r.get("country"),
                status=r.get("status"),
                score=as_float(r.get("score")),
                parts=r.get("parts"),
                first=r.get("first"),
                last=r.get("last"),
                gap_days=as_int(r.get("gap_days")),
                inbound=as_int(r.get("inbound")),
                outbound=as_int(r.get("outbound")),
                threads=as_int(r.get("threads")),
                quotes=as_int(r.get("quotes")),
                products=r.get("products"),
                people=r.get("people"),
            ) for r in rows
        ])
        print(f"  ranking       : {len(rows)}")

        # companies
        rows = dedupe(read_jsonl(data_dir / "companies.jsonl"), "key")
        session.bulk_save_objects([
            models.Company(
                key=r.get("key"), domain=r.get("domain"),
                addrs=r.get("addrs"), threads=r.get("threads"),
                n_threads=as_int(r.get("n_threads")),
                first=r.get("first"), last=r.get("last"),
                inbound=as_int(r.get("inbound")), outbound=as_int(r.get("outbound")),
                responded=as_int(r.get("responded")), type=r.get("type"),
            ) for r in rows
        ])
        print(f"  companies     : {len(rows)}")

        # contacts (autoincrement id)
        rows = read_jsonl(data_dir / "contacts.jsonl")
        session.bulk_save_objects([
            models.Contact(
                company=r.get("company"), name=r.get("name"), role=r.get("role"),
                email=r.get("email"), emails_alt=r.get("emails_alt"),
                phone=r.get("phone"), whatsapp=r.get("whatsapp"),
                website=r.get("website"), address=r.get("address"),
                company_name=r.get("company_name"), msg=r.get("msg"), note=r.get("note"),
            ) for r in rows
        ])
        print(f"  contacts      : {len(rows)}")

        # profiles
        rows = dedupe(read_jsonl(data_dir / "profiles.jsonl"), "company")
        session.bulk_save_objects([
            models.Profile(
                company=r.get("company"), name=r.get("name"), country=r.get("country"),
                type=r.get("type"), type_reason=r.get("type_reason"),
                summary=r.get("summary"), products=r.get("products"), domains=r.get("domains"),
            ) for r in rows
        ])
        print(f"  profiles      : {len(rows)}")

        # quotes (autoincrement id)
        rows = read_jsonl(data_dir / "quotes.jsonl")
        session.bulk_save_objects([
            models.Quote(
                company=r.get("company"), thread=r.get("thread"), msg=r.get("msg"),
                date=r.get("date"), product=r.get("product"), cas=r.get("cas"),
                spec=r.get("spec"), price=as_float(r.get("price")),
                currency=r.get("currency"), unit=r.get("unit"),
                incoterms=r.get("incoterms"), basis=r.get("basis"), moq=r.get("moq"),
                lead_time=r.get("lead_time"), payment=r.get("payment"),
                validity=r.get("validity"), packaging=r.get("packaging"), note=r.get("note"),
            ) for r in rows
        ])
        print(f"  quotes        : {len(rows)}")

        # thread_states (autoincrement id)
        rows = read_jsonl(data_dir / "thread_states.jsonl")
        session.bulk_save_objects([
            models.ThreadState(
                company=r.get("company"), thread=r.get("thread"), subject=r.get("subject"),
                product=r.get("product"), first=r.get("first"), last=r.get("last"),
                n_in=as_int(r.get("n_in")), n_out=as_int(r.get("n_out")),
                last_from=r.get("last_from"), outcome=r.get("outcome"),
                open_question=r.get("open_question"),
            ) for r in rows
        ])
        print(f"  thread_states : {len(rows)}")

        # exhibitors (from JSON object)
        ex_path = data_dir / "cphi-milan-2026-exhibitors.json"
        ex_rows = []
        if ex_path.exists():
            try:
                doc = json.loads(ex_path.read_text(encoding="utf-8"))
                ex_rows = dedupe(doc.get("exhibitors", []), "key")
            except json.JSONDecodeError:
                pass
        session.bulk_save_objects([
            models.Exhibitor(
                key=r.get("key"), name=r.get("name"), hall=r.get("hall"),
                stand=r.get("stand"), purpose=r.get("purpose"), source=r.get("source"),
            ) for r in ex_rows
        ])
        print(f"  exhibitors    : {len(ex_rows)}")

        session.commit()
        print("DONE.")
        return 0
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(f"ERROR: {exc}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
