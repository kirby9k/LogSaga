"""
Cast population service for LogSAGA.

Lazily populates person + item_cast rows from the TMDB credits data
already cached in tmdb_cache. No live TMDB API calls are made here —
only the local SQLite cache is read.

Usage:
    from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show

    cast = populate_cast_for_item(media_item, db)
    cast = populate_cast_for_show(kodi_show, db)

Both return a list[CastMemberOut] of up to 20 cast members (billing order).
On first call the rows are inserted; subsequent calls return existing rows instantly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from logsaga.api.schemas import CastMemberOut
from logsaga.models import ItemCast, KodiShow, MediaItem, Person, TMDBCache

log = logging.getLogger(__name__)

# Maximum cast members stored and returned
_CAST_STORE_LIMIT = 50
_CAST_RETURN_LIMIT = 20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def populate_cast_for_item(item: MediaItem, db: Session) -> list[CastMemberOut]:
    """Return cast for a movie or episode item.

    For episodes, delegates to the show's cast (TV credits are show-level).
    Returns up to 20 CastMemberOut objects in billing order.
    """
    if item.media_type == "episode":
        if item.show_id is None:
            return []
        show = db.get(KodiShow, item.show_id)
        if show is None:
            return []
        return populate_cast_for_show(show, db)

    # Movie path
    existing = (
        db.query(ItemCast)
        .filter(ItemCast.media_item_id == item.id)
        .first()
    )
    if existing is not None:
        return _cast_rows_to_out(
            db.query(ItemCast)
            .filter(ItemCast.media_item_id == item.id)
            .order_by(ItemCast.credit_order)
            .limit(_CAST_RETURN_LIMIT)
            .all(),
            db,
        )

    if not item.tmdb_id:
        return []
    cached = db.get(TMDBCache, f"movie:{item.tmdb_id}")
    if cached is None:
        return []
    data = json.loads(cached.data_json)
    return _insert_cast(data, db, media_item_id=item.id, show_id=None)


def populate_cast_for_show(show: KodiShow, db: Session) -> list[CastMemberOut]:
    """Return cast for a TV show.

    Returns up to 20 CastMemberOut objects in billing order.
    """
    existing = (
        db.query(ItemCast)
        .filter(ItemCast.show_id == show.id)
        .first()
    )
    if existing is not None:
        return _cast_rows_to_out(
            db.query(ItemCast)
            .filter(ItemCast.show_id == show.id)
            .order_by(ItemCast.credit_order)
            .limit(_CAST_RETURN_LIMIT)
            .all(),
            db,
        )

    if not show.tmdb_id:
        return []
    cached = db.get(TMDBCache, f"tv:{show.tmdb_id}")
    if cached is None:
        return []
    data = json.loads(cached.data_json)
    return _insert_cast(data, db, media_item_id=None, show_id=show.id)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _cast_rows_to_out(rows: list[ItemCast], db: Session) -> list[CastMemberOut]:
    """Convert existing ItemCast ORM rows to CastMemberOut schemas."""
    result: list[CastMemberOut] = []
    for row in rows:
        person = db.get(Person, row.person_id)
        if person is None:
            continue
        profile_url = (
            f"https://image.tmdb.org/t/p/w185{person.profile_path}"
            if person.profile_path
            else None
        )
        result.append(
            CastMemberOut(
                tmdb_person_id=person.tmdb_person_id,
                name=person.name,
                character=row.character,
                credit_order=row.credit_order,
                profile_url=profile_url,
            )
        )
    return result


def _insert_cast(
    data: dict,
    db: Session,
    *,
    media_item_id: Optional[int],
    show_id: Optional[int],
) -> list[CastMemberOut]:
    """Insert Person + ItemCast rows from TMDB credits JSON.

    Uses INSERT OR IGNORE (ON CONFLICT DO NOTHING) so duplicate persons
    — from concurrent sessions or duplicate entries in TMDB's own cast data
    — are silently skipped rather than failing the entire batch.

    Stores up to _CAST_STORE_LIMIT cast members; returns up to _CAST_RETURN_LIMIT.
    """
    result: list[CastMemberOut] = []
    raw_cast = data.get("credits", {}).get("cast", [])[:_CAST_STORE_LIMIT]

    for order, p in enumerate(raw_cast):
        pid = p.get("id")
        if not pid:
            continue

        # INSERT OR IGNORE: safe if person already exists in DB (concurrent insert
        # from another session, or the same person appearing twice in TMDB cast data).
        db.execute(
            sqlite_insert(Person.__table__).values(
                tmdb_person_id=pid,
                name=p.get("name", ""),
                profile_path=p.get("profile_path"),
                created_at=datetime.utcnow(),
            ).on_conflict_do_nothing(index_elements=["tmdb_person_id"])
        )

        # INSERT OR IGNORE: safe if this (person, item/show) pair already exists.
        db.execute(
            sqlite_insert(ItemCast.__table__).values(
                person_id=pid,
                media_item_id=media_item_id,
                show_id=show_id,
                character=p.get("character"),
                credit_order=order,
            ).on_conflict_do_nothing()
        )

        profile_url = (
            f"https://image.tmdb.org/t/p/w185{p['profile_path']}"
            if p.get("profile_path")
            else None
        )
        result.append(
            CastMemberOut(
                tmdb_person_id=pid,
                name=p.get("name", ""),
                character=p.get("character"),
                credit_order=order,
                profile_url=profile_url,
            )
        )

    try:
        db.commit()
    except Exception as exc:
        log.warning("cast: commit failed: %s", exc)
        db.rollback()
        return []

    return result[:_CAST_RETURN_LIMIT]
