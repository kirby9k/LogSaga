"""
FastAPI shared dependencies.

The SQLAlchemy engine is created once at startup and stored in app.state.
Routes obtain a Session via get_db() and Settings via get_cfg().
"""

from __future__ import annotations

from typing import Generator, Optional

from fastapi import Depends, HTTPException, Request
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


def get_tmdb_client(
    cfg: Settings = Depends(get_cfg),
    db: Session = Depends(get_db),
) -> "TMDBClient":  # type: ignore[name-defined]  # noqa: F821
    """
    Return a TMDBClient instance.

    Raises HTTP 503 if TMDB_API_KEY is not configured.
    Use this only on endpoints that strictly require TMDB access.
    For search (which degrades gracefully), check cfg.tmdb_enabled inline.
    """
    from logsaga.tmdb.client import TMDBClient

    if not cfg.tmdb_enabled:
        raise HTTPException(
            status_code=503,
            detail="TMDB integration not configured. Set TMDB_API_KEY in your environment.",
        )
    return TMDBClient(api_key=cfg.tmdb_api_key, db=db)


def get_tmdb_client_optional(
    cfg: Settings = Depends(get_cfg),
    db: Session = Depends(get_db),
) -> "Optional[TMDBClient]":  # type: ignore[name-defined]  # noqa: F821
    """Return a TMDBClient instance, or None if TMDB is not configured.

    Use for endpoints where TMDB access is a nice-to-have (e.g. biography
    fetching) but the endpoint can still return useful data without it.
    """
    from logsaga.tmdb.client import TMDBClient

    if not cfg.tmdb_enabled:
        return None
    return TMDBClient(api_key=cfg.tmdb_api_key, db=db)
