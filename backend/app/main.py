"""Pigulkin backend — FastAPI app entrypoint.

Serves the supplier REST API and the Пігулькін chat endpoint. Run:

    uvicorn app.main:app --reload      # from the backend/ directory

Data is loaded separately via `python import_data.py`.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import batch, chat, suppliers

settings = get_settings()

app = FastAPI(title="Pigulkin backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(suppliers.router)
app.include_router(chat.router)
app.include_router(batch.router)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env, "chat_enabled": settings.chat_enabled}
