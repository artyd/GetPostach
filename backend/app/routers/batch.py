"""Bulk RFQ generation via the Message Batches API (large mailouts)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..services import batch as batch_service
from ..services import chat as chat_service

router = APIRouter(prefix="/api/batch", tags=["batch"])
settings = get_settings()


class RfqBatchRequest(BaseModel):
    product: str
    companies: list[str]                       # domains / slugs / names
    signature: str = "Procurement Team\nAlliance Group 95"
    notes: str = ""


@router.post("/rfq")
def create_rfq(req: RfqBatchRequest, db: Session = Depends(get_db)):
    if not settings.chat_enabled:
        return {"error": "chat_disabled", "detail": "ANTHROPIC_API_KEY not set"}
    try:
        return batch_service.create_rfq_batch(
            db, req.product, req.companies, signature=req.signature, notes=req.notes)
    except chat_service.ChatError as exc:
        return {"error": "chat_error", "detail": str(exc)}


@router.get("/{batch_id}")
def batch_status(batch_id: str, db: Session = Depends(get_db)):
    if not settings.chat_enabled:
        return {"error": "chat_disabled"}
    return batch_service.get_batch(db, batch_id)


@router.post("/{batch_id}/create-drafts")
def batch_create_drafts(batch_id: str, db: Session = Depends(get_db)):
    if not settings.chat_enabled:
        return {"error": "chat_disabled"}
    return batch_service.create_drafts_from_batch(db, batch_id)
