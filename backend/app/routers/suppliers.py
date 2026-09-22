"""REST endpoints for the supplier database (used by the frontend and for debugging)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import prep, queries

router = APIRouter(prefix="/api", tags=["suppliers"])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return queries.stats(db)


@router.get("/suppliers")
def search(
    q: str | None = None,
    type_label: str | None = None,
    country: str | None = None,
    with_prices: bool = False,
    exhibitors: bool = False,
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return {"results": queries.search_suppliers(
        db, query=q, type_label=type_label, country=country,
        only_with_prices=with_prices, only_exhibitors=exhibitors, limit=limit,
    )}


@router.get("/suppliers/{term}")
def supplier(term: str, db: Session = Depends(get_db)):
    d = queries.supplier_detail(db, term)
    if not d:
        raise HTTPException(404, "supplier not found")
    return d


@router.get("/benchmark")
def benchmark(product: str, limit: int = Query(40, ge=1, le=200), db: Session = Depends(get_db)):
    return queries.benchmark_price(db, product, limit=limit)


@router.get("/exhibitors")
def exhibitors(db: Session = Depends(get_db)):
    return {"exhibitors": queries.list_exhibitors(db)}


@router.get("/meeting-brief/{term}")
def meeting_brief(term: str, db: Session = Depends(get_db)):
    d = prep.prepare_meeting_brief(db, term)
    if not d:
        raise HTTPException(404, "supplier not found")
    return d
