"""Пігулькін chat endpoints — the seam the frontend's GP_PIGULKIN_API talks to.

- POST /api/chat         non-streaming answer
- POST /api/chat/stream  SSE stream (text deltas + tool status)
- POST /api/chat/compact summarize old turns for context compaction
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import rate_limit, require_user
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..services import chat as chat_service

router = APIRouter(prefix="/api", tags=["chat"])
settings = get_settings()

_NO_KEY = (
    "Бекенд Пігулькіна працює, але мовна модель ще не під'єднана (немає ANTHROPIC_API_KEY). "
    "Щойно ключ додадуть — я відповідатиму по суті."
)


class ChatMessage(BaseModel):
    role: str
    text: str | None = None
    content: str | None = None


class Attachment(BaseModel):
    kind: str = "text"          # "image" | "pdf" | "text"
    name: str | None = None
    media_type: str | None = None
    data: str = ""              # base64 for image/pdf, raw text for text


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    summary: str | None = None  # compacted context from earlier in the conversation
    attachments: list[Attachment] | None = None


class ChatResponse(BaseModel):
    reply: str


class CompactResponse(BaseModel):
    summary: str


@router.get("/chat/health")
def chat_health():
    return {"chat_enabled": settings.chat_enabled, "model": settings.chat_model}


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db), user: str = Depends(require_user)):
    rate_limit(user)
    if not settings.chat_enabled:
        return ChatResponse(reply=_NO_KEY)
    atts = [a.model_dump() for a in req.attachments] if req.attachments else None
    try:
        text = chat_service.reply(db, [m.model_dump() for m in req.messages], summary=req.summary, attachments=atts)
    except chat_service.ChatError as exc:
        return ChatResponse(reply=f"Помилка чату: {exc}")
    return ChatResponse(reply=text)


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, user: str = Depends(require_user)):
    rate_limit(user)
    msgs = [m.model_dump() for m in req.messages]
    summary = req.summary
    atts = [a.model_dump() for a in req.attachments] if req.attachments else None

    def sse():
        if not settings.chat_enabled:
            yield "data: " + json.dumps({"type": "delta", "text": _NO_KEY}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"
            return
        db = SessionLocal()
        try:
            for ev in chat_service.reply_stream(db, msgs, summary=summary, attachments=atts):
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        finally:
            db.close()

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/compact", response_model=CompactResponse)
def chat_compact(req: ChatRequest, db: Session = Depends(get_db), user: str = Depends(require_user)):
    if not settings.chat_enabled:
        return CompactResponse(summary="")
    try:
        base = req.summary + "\n" if req.summary else ""
        summary = base + chat_service.summarize([m.model_dump() for m in req.messages])
    except chat_service.ChatError:
        summary = req.summary or ""
    return CompactResponse(summary=summary.strip())
