"""SQLAlchemy models mirroring the CPHI_MILAN JSONL sources.

All tables are keyed on the supplier email *domain* (`key` / `company`), exactly as
in the source data. `ranking` is the central supplier list and also maps
domain <-> slug <-> name.
"""
from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Ranking(Base):
    """Central supplier list (data/ranking.jsonl) — 1 row per supplier."""
    __tablename__ = "ranking"

    slug: Mapped[str] = mapped_column(String(200), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), index=True)  # email domain
    name: Mapped[str] = mapped_column(String(500), index=True)
    type: Mapped[str | None] = mapped_column(String(64))       # factory / trader / ...
    type_label: Mapped[str | None] = mapped_column(String(120))  # human label (Завод/Трейдер)
    role: Mapped[str | None] = mapped_column(String(64), index=True)  # supplier / customer
    role_label: Mapped[str | None] = mapped_column(String(120))
    read: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str | None] = mapped_column(String(200))
    score: Mapped[float | None] = mapped_column(Float, index=True)
    parts: Mapped[dict | None] = mapped_column(JSON)           # score factor breakdown
    first: Mapped[str | None] = mapped_column(String(40))
    last: Mapped[str | None] = mapped_column(String(40))
    gap_days: Mapped[int | None] = mapped_column(Integer)
    inbound: Mapped[int | None] = mapped_column(Integer)
    outbound: Mapped[int | None] = mapped_column(Integer)
    threads: Mapped[int | None] = mapped_column(Integer)
    quotes: Mapped[int | None] = mapped_column(Integer)
    products: Mapped[list | None] = mapped_column(JSON)
    people: Mapped[list | None] = mapped_column(JSON)


class Company(Base):
    """data/companies.jsonl — raw mailbox stats per domain."""
    __tablename__ = "companies"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)  # domain
    domain: Mapped[str | None] = mapped_column(String(200))
    addrs: Mapped[dict | None] = mapped_column(JSON)   # {email: {in, out}}
    threads: Mapped[list | None] = mapped_column(JSON)  # [gmail thread ids]
    n_threads: Mapped[int | None] = mapped_column(Integer)
    first: Mapped[str | None] = mapped_column(String(40))
    last: Mapped[str | None] = mapped_column(String(40))
    inbound: Mapped[int | None] = mapped_column(Integer)
    outbound: Mapped[int | None] = mapped_column(Integer)
    responded: Mapped[int | None] = mapped_column(Integer)
    type: Mapped[str | None] = mapped_column(String(64))


class Contact(Base):
    """data/contacts.jsonl — people extracted from email signatures."""
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(200), index=True)  # domain
    name: Mapped[str | None] = mapped_column(String(300))
    role: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(300), index=True)
    emails_alt: Mapped[list | None] = mapped_column(JSON)
    phone: Mapped[str | None] = mapped_column(String(120))
    whatsapp: Mapped[str | None] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(String(500))
    msg: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)


class Profile(Base):
    """data/profiles.jsonl — LLM-derived company profile per domain."""
    __tablename__ = "profiles"

    company: Mapped[str] = mapped_column(String(200), primary_key=True)  # domain
    name: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[str | None] = mapped_column(String(64))
    type_reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    products: Mapped[list | None] = mapped_column(JSON)
    domains: Mapped[list | None] = mapped_column(JSON)  # multi-domain merge


class Quote(Base):
    """data/quotes.jsonl — every price mentioned in correspondence."""
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(200), index=True)  # domain
    thread: Mapped[str | None] = mapped_column(String(120))
    msg: Mapped[str | None] = mapped_column(String(120))
    date: Mapped[str | None] = mapped_column(String(40), index=True)
    product: Mapped[str | None] = mapped_column(String(500), index=True)
    cas: Mapped[str | None] = mapped_column(String(80))
    spec: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(32))
    incoterms: Mapped[str | None] = mapped_column(String(32))
    basis: Mapped[str | None] = mapped_column(String(200))
    moq: Mapped[str | None] = mapped_column(String(120))
    lead_time: Mapped[str | None] = mapped_column(String(120))
    payment: Mapped[str | None] = mapped_column(String(200))
    validity: Mapped[str | None] = mapped_column(String(120))
    packaging: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(Text)


class ThreadState(Base):
    """data/thread_states.jsonl — per-thread negotiation memory."""
    __tablename__ = "thread_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(200), index=True)  # domain
    thread: Mapped[str | None] = mapped_column(String(120))
    subject: Mapped[str | None] = mapped_column(Text)
    product: Mapped[str | None] = mapped_column(String(500))
    first: Mapped[str | None] = mapped_column(String(40))
    last: Mapped[str | None] = mapped_column(String(40))
    n_in: Mapped[int | None] = mapped_column(Integer)
    n_out: Mapped[int | None] = mapped_column(Integer)
    last_from: Mapped[str | None] = mapped_column(String(120))
    outcome: Mapped[str | None] = mapped_column(String(64), index=True)
    open_question: Mapped[str | None] = mapped_column(Text)


class Exhibitor(Base):
    """data/cphi-milan-2026-exhibitors.json — stand info for CPHI Milan 2026."""
    __tablename__ = "exhibitors"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)  # domain
    name: Mapped[str | None] = mapped_column(String(500))
    hall: Mapped[str | None] = mapped_column(String(64))
    stand: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(300))


# Composite indexes that speed up the common lookups the chat/MCP tools do.
Index("ix_quotes_company_product", Quote.company, Quote.product)
Index("ix_quotes_product_price", Quote.product, Quote.price)
