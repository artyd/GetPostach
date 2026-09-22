"""Auth endpoints — register (invite-code gated) / login / me.

Issues HMAC-signed bearer tokens the frontend stores and sends on /api/chat*.
"""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth as auth_lib
from ..auth_models import User
from ..config import get_settings
from ..db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    company: str = ""
    code: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    name: str
    email: str
    company: str = ""


def _user_public(u: User, token: str) -> AuthResponse:
    return AuthResponse(token=token, name=u.name or "", email=u.email, company=u.company or "")


@router.get("/config")
def auth_config():
    """Lets the frontend know whether auth is on and if an invite code is needed."""
    return {"auth_enabled": settings.auth_enabled, "signup_code_required": bool(settings.signup_code)}


@router.post("/register", response_model=AuthResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    email = (req.email or "").strip().lower()
    name = (req.name or "").strip()
    if not email or not req.password or not name:
        raise HTTPException(status_code=400, detail="Заповніть ім'я, email і пароль.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Некоректний email.")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Пароль має бути не менше 6 символів.")
    if settings.signup_code and (req.code or "").strip() != settings.signup_code:
        raise HTTPException(status_code=403, detail="Невірний код запрошення.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Такий email уже зареєстровано — увійдіть.")
    u = User(
        email=email, name=name, company=(req.company or "").strip(),
        password_hash=auth_lib.hash_password(req.password),
        created=time.strftime("%Y-%m-%d"),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return _user_public(u, auth_lib.create_token(email))


@router.post("/login", response_model=AuthResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = (req.email or "").strip().lower()
    u = db.query(User).filter(User.email == email).first()
    if not u or not auth_lib.verify_password(req.password or "", u.password_hash):
        raise HTTPException(status_code=401, detail="Невірний email або пароль.")
    return _user_public(u, auth_lib.create_token(email))


@router.get("/me", response_model=AuthResponse)
def me(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    sub = auth_lib.require_user(authorization)
    if sub == "anonymous":
        raise HTTPException(status_code=404, detail="Auth disabled")
    u = db.query(User).filter(User.email == sub).first()
    if not u:
        raise HTTPException(status_code=401, detail="Не автентифіковано")
    # refresh the token so active users stay logged in
    return _user_public(u, auth_lib.create_token(u.email))
