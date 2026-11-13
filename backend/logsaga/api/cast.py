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

Episode credits
---------------
TMDB's /tv/{id}/season/{s}/episode/{e}/credits returns a top-level dict with
``cast`` and ``guest_stars`` arrays (no ``credits`` wrapper — unlike movie/show).
When episode-level credits are cached we merge cast + guest_stars and store them
against the episode's media_item_id.  ``populate_cast_for_show`` aggregates those
rows, sorting by number of appearances then average billing order.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
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

    For episodes uses a three-tier lookup:
      1. Existing item_cast rows keyed to this episode's media_item_id
      2. TMDB episode credits cache (tv:{id}:ep:{s}:{e}:credits)
      3. Fallback to show-level cast (backward-compatible with enrich-cast)

    For movies checks item_cast rows keyed to the movie's media_item_id,
    then populates from the movie's TMDB cache entry.

    Returns up to 20 CastMemberOut objects in billing order.
    """
    if item.media_type == "episode":
        if item.show_id is None:
            return []

        # 1. Already populated at episode level?
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

        # 2. Episode credits in cache?
        # Prefer item's own tmdb_id; fall back to parent show's tmdb_id.
        # (Most episode media_item rows have tmdb_id=NULL; the TMDB ID lives
        # on kodi_show.tmdb_id for TV shows.)
        if item.season is not None and item.episode is not None:
            ep_tmdb_id = item.tmdb_id
            if not ep_tmdb_id:
                _parent = db.get(KodiShow, item.show_id)
                if _parent:
                    ep_tmdb_id = _parent.tmdb_id
            if ep_tmdb_id:
                ep_key = f"tv:{ep_tmdb_id}:ep:{item.season}:{item.episode}:credits"
                ep_cached = db.get(TMDBCache, ep_key)
                if ep_cached is not None:
                    ep_credits = json.loads(ep_cached.data_json)
                    return _insert_episode_cast(ep_credits, db, media_item_id=item.id)

        # 3. Fallback to show-level
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

    Uses a two-tier lookup:
      1. Aggregate episode-level item_cast rows (sorts by appearances)
      2. Fallback to show-level item_cast rows (populated by enrich-cast), then
         populate from the show's TMDB cache entry if neither exists.

    Returns up to 20 CastMemberOut objects.
    """
    # 1. Episode-level aggregation
    ep_ids = [
        r[0]
        for r in db.query(MediaItem.id)
        .filter(
            MediaItem.show_id == show.id,
            MediaItem.media_type == "episode",
        )
        .all()
    ]
    if ep_ids:
        ep_cast_rows = (
            db.query(ItemCast)
            .filter(ItemCast.media_item_id.in_(ep_ids))
            .all()
        )
        if ep_cast_rows:
            return _aggregate_episode_cast(ep_cast_rows, db)

    # 2. Fallback: show-level (existing behavior)
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


def _insert_episode_cast(
    ep_credits: dict,
    db: Session,
    *,
    media_item_id: int,
) -> list[CastMemberOut]:
    """Insert Person + ItemCast rows from TMDB episode credits JSON.

    Episode credits use top-level ``cast`` + ``guest_stars`` arrays (no
    ``credits`` wrapper).  We merge both arrays and delegate to _insert_cast
    via the standard ``{"credits": {"cast": combined}}`` wrapper.
    """
    combined = ep_credits.get("cast", []) + ep_credits.get("guest_stars", [])
    return _insert_cast(
        {"credits": {"cast": combined}},
        db,
        media_item_id=media_item_id,
        show_id=None,
    )


def _aggregate_episode_cast(rows: list[ItemCast], db: Session) -> list[CastMemberOut]:
    """Aggregate episode-level ItemCast rows into a show-level cast list.

    Sorts by number of episode appearances (desc) then average billing order (asc)
    so series regulars rise to the top.  Returns up to _CAST_RETURN_LIMIT entries.
    """
    counts: dict[int, int] = defaultdict(int)
    order_sum: dict[int, int] = defaultdict(int)
    char_by_person: dict[int, Optional[str]] = {}

    for row in rows:
        pid = row.person_id
        counts[pid] += 1
        order_sum[pid] += row.credit_order
        if pid not in char_by_person:
            char_by_person[pid] = row.character

    sorted_pids = sorted(
        counts,
        key=lambda p: (-counts[p], order_sum[p] / counts[p]),
    )

    result: list[CastMemberOut] = []
    for i, pid in enumerate(sorted_pids[:_CAST_RETURN_LIMIT]):
        person = db.get(Person, pid)
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
                character=char_by_person.get(pid),
                credit_order=i,
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
