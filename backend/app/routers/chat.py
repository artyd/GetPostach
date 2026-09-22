"""Пігулькін chat endpoint — the seam the frontend's GP_PIGULKIN_API talks to."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..services import chat as chat_service

router = APIRouter(prefix="/api", tags=["chat"])
settings = get_settings()


class ChatMessage(BaseModel):
    role: str  # "user" | "bot"/"assistant"
    text: str | None = None
    content: str | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str


@router.get("/chat/health")
def chat_health():
    return {"chat_enabled": settings.chat_enabled, "model": settings.chat_model}


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    if not settings.chat_enabled:
        return ChatResponse(reply=(
            "Бекенд Пігулькіна працює, але мовна модель ще не під'єднана "
            "(немає ANTHROPIC_API_KEY). Щойно ключ додадуть — я відповідатиму по суті: "
            "шукатиму постачальників, порівнюватиму ціни й готуватиму брифи."
        ))
    try:
        text = chat_service.reply(db, [m.model_dump() for m in req.messages])
    except chat_service.ChatError as exc:
        return ChatResponse(reply=f"Помилка чату: {exc}")
    return ChatResponse(reply=text)
