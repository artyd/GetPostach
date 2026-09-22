"""Auth tables live in their OWN metadata, separate from the supplier `Base`.

import_data.py runs `Base.metadata.drop_all()` to rebuild the supplier data from
source — keeping users on a separate metadata means a data re-import never wipes
accounts. Created at app startup (see app/main.py).
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import declarative_base

AuthBase = declarative_base()


class User(AuthBase):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(Text, default="")
    company = Column(Text, default="")
    password_hash = Column(Text, nullable=False)
    created = Column(Text, default="")
