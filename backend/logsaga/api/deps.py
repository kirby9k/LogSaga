"""
FastAPI shared dependencies.

The SQLAlchemy engine is created once at startup and stored in app.state.
Routes obtain a Session via get_db() and Settings via get_cfg().
"""

from __future__ import annotations

from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from logsaga.config import Settings


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a SQLAlchemy Session, closing it when the request finishes."""
    SessionLocal = sessionmaker(bind=request.app.state.engine)
    with SessionLocal() as db:
        yield db


def get_cfg(request: Request) -> Settings:
    """Return the application Settings (read once at startup)."""
    return request.app.state.settings
