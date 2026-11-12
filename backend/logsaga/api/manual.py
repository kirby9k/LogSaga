"""
Manual watch history — logic for POST /api/watched.

Creates WatchSession rows directly (no kodi_observation required), following
the same pattern as the backfill command in cli.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from sqlalchemy.orm import Session

from logsaga.api.schemas import PostWatchedOut, WatchedItemIn
from logsaga.models import (
    Artwork,
    CompletionReason,
    KodiShow,
    KodiState,
    MediaItem,
    MediaType,
    RecentlyViewed,
    SessionSource,
    SessionStatus,
    WatchSession,
)
from logsaga.tmdb.client import TMDBClient

logger = structlog.get_logger(__name__)

# Window within which a duplicate completed session is ignored (1 hour)
_DEDUP_WINDOW = timedelta(hours=1)


def _add_artwork_stubs(db: Session, item_id: int, urls: dict[str, Optional[str]]) -> None:
    """Create pending Artwork rows so fetch_tmdb_artwork_async can download them later."""
    for art_type, url in urls.items():
        if not url:
            continue
        exists = db.query(Artwork).filter_by(media_item_id=item_id, art_type=art_type).first()
        if not exists:
            db.add(Artwork(media_item_id=item_id, art_type=art_type, source_url=url))
    db.flush()


def _find_or_create_show(
    db: Session,
    tmdb_id: int,
    tv_data: dict,
) -> KodiShow:
    """Return existing KodiShow for the TMDB TV show, or create a TMDB-only stub."""
    tmdb_id_str = str(tmdb_id)

    # Check by tmdb_id first (covers both Kodi-synced and TMDB-only shows)
    show = db.query(KodiShow).filter_by(tmdb_id=tmdb_id_str).first()
    if show:
        return show

    year: Optional[int] = None
    premiered = tv_data.get("first_air_date") or None
    if premiered:
        try:
            year = int(premiered[:4])
        except (ValueError, IndexError):
            pass

    genres_list = [g.get("name", "") for g in tv_data.get("genres", [])]
    genres = "/".join(genres_list) if genres_list else None

    show = KodiShow(
        kodi_idShow=None,  # TMDB-only — will be linked when Kodi sees it
        title=tv_data.get("name", "Unknown"),
        plot=tv_data.get("overview") or None,
        year=year,
        premiered=premiered,
        status=tv_data.get("status") or None,
        genres=genres,
        tmdb_id=tmdb_id_str,
        kodi_state=KodiState.unwatched,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(show)
    db.flush()
    return show


def _find_or_create_movie(
    db: Session,
    tmdb_id: int,
    movie_data: dict,
) -> MediaItem:
    """Return existing MediaItem for the TMDB movie, or create a stub."""
    tmdb_id_str = str(tmdb_id)

    item = db.query(MediaItem).filter_by(
        media_type=MediaType.movie, tmdb_id=tmdb_id_str
    ).first()
    if item:
        return item

    year: Optional[int] = None
    release_date = movie_data.get("release_date") or None
    if release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, IndexError):
            pass

    genres_list = [g.get("name", "") for g in movie_data.get("genres", [])]
    genres = "/".join(genres_list) if genres_list else None

    runtime_min = movie_data.get("runtime")
    runtime_s = int(runtime_min) * 60 if runtime_min else None

    # Extract director from crew
    director: Optional[str] = None
    for person in movie_data.get("credits", {}).get("crew", []):
        if person.get("job") == "Director":
            director = person.get("name")
            break

    item = MediaItem(
        media_type=MediaType.movie,
        title=movie_data.get("title", "Unknown"),
        year=year,
        plot=movie_data.get("overview") or None,
        genres=genres,
        release_date=release_date,
        runtime_s=runtime_s,
        director=director,
        tmdb_id=tmdb_id_str,
        kodi_state=KodiState.unwatched,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(item)
    db.flush()

    _add_artwork_stubs(db, item.id, {
        "poster": TMDBClient.poster_url(movie_data.get("poster_path")),
        "fanart": TMDBClient.backdrop_url(movie_data.get("backdrop_path")),
    })

    return item


def _find_or_create_episode(
    db: Session,
    show: KodiShow,
    season: int,
    episode: int,
    ep_data: dict,
    tv_data: dict,
) -> MediaItem:
    """Return existing MediaItem for the episode, or create a stub."""
    tmdb_id_str = str(show.tmdb_id) if show.tmdb_id else None

    if tmdb_id_str:
        item = db.query(MediaItem).filter_by(
            media_type=MediaType.episode,
            tmdb_id=tmdb_id_str,
            season=season,
            episode=episode,
        ).first()
        if item:
            return item

    year: Optional[int] = None
    release_date = ep_data.get("air_date") or None
    if release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, IndexError):
            pass

    runtime_min = ep_data.get("runtime")
    runtime_s = int(runtime_min) * 60 if runtime_min else None

    item = MediaItem(
        media_type=MediaType.episode,
        title=ep_data.get("name", f"Episode {episode}"),
        show_title=tv_data.get("name", show.title),
        season=season,
        episode=episode,
        year=year,
        plot=ep_data.get("overview") or None,
        release_date=release_date,
        runtime_s=runtime_s,
        tmdb_id=tmdb_id_str,
        show_id=show.id,
        kodi_state=KodiState.unwatched,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(item)
    db.flush()

    _add_artwork_stubs(db, item.id, {
        "thumb": TMDBClient.poster_url(ep_data.get("still_path"), size="w300"),
        "tvshow.poster": TMDBClient.poster_url(tv_data.get("poster_path")),
        "tvshow.fanart": TMDBClient.backdrop_url(tv_data.get("backdrop_path")),
    })

    return item


def _has_duplicate_session(
    db: Session, item_id: int, watched_at: datetime
) -> bool:
    """Return True if a completed session already exists within 1 hour of watched_at."""
    # Ensure watched_at is naive UTC for comparison
    if watched_at.tzinfo is not None:
        watched_at = watched_at.astimezone(timezone.utc).replace(tzinfo=None)

    lo = watched_at - _DEDUP_WINDOW
    hi = watched_at + _DEDUP_WINDOW

    return (
        db.query(WatchSession)
        .filter(
            WatchSession.media_item_id == item_id,
            WatchSession.status == SessionStatus.completed,
            WatchSession.completed_at >= lo,
            WatchSession.completed_at <= hi,
        )
        .first()
        is not None
    )


def _make_session(item: MediaItem, watched_at: datetime) -> WatchSession:
    """Build a completed WatchSession for a manually-added watch."""
    # Normalise to naive UTC
    if watched_at.tzinfo is not None:
        watched_at = watched_at.astimezone(timezone.utc).replace(tzinfo=None)

    return WatchSession(
        media_item_id=item.id,
        started_at=watched_at,
        last_activity_at=watched_at,
        ended_at=watched_at,
        completed_at=watched_at,
        max_position_s=float(item.runtime_s) if item.runtime_s else None,
        runtime_s=item.runtime_s,
        percent=100.0 if item.runtime_s else None,
        status=SessionStatus.completed,
        completion_reason=CompletionReason.manual,
        source=SessionSource.manual,
    )


def _upsert_recently_viewed(
    db: Session,
    media_type: str,
    tmdb_id: int,
    title: str,
    poster_url: Optional[str],
) -> None:
    """Upsert a recently_viewed row; cap the table at 50 entries."""
    now = datetime.utcnow()
    row = db.query(RecentlyViewed).filter_by(
        media_type=media_type, tmdb_id=tmdb_id
    ).first()
    if row:
        row.title = title
        row.poster_url = poster_url
        row.viewed_at = now
    else:
        db.add(
            RecentlyViewed(
                media_type=media_type,
                tmdb_id=tmdb_id,
                title=title,
                poster_url=poster_url,
                viewed_at=now,
            )
        )
        db.flush()

        # Cap at 50 rows: delete oldest if over limit
        count = db.query(RecentlyViewed).count()
        if count > 50:
            oldest = (
                db.query(RecentlyViewed)
                .order_by(RecentlyViewed.viewed_at.asc())
                .first()
            )
            if oldest:
                db.delete(oldest)


async def create_manual_sessions(
    db: Session,
    items: list[WatchedItemIn],
    tmdb_client: Optional[TMDBClient],
    schedule_artwork,  # callable(item_id) → None  (fire-and-forget)
) -> PostWatchedOut:
    """
    Process a list of WatchedItemIn and create WatchSession rows.

    schedule_artwork is a callback that queues artwork download for a
    newly-created MediaItem (may be None if no artwork task is available).
    """
    created = 0
    skipped = 0
    errors: list[str] = []

    for req in items:
        try:
            watched_at = req.watched_at
            if watched_at.tzinfo is not None:
                watched_at = watched_at.astimezone(timezone.utc).replace(tzinfo=None)

            if req.media_type == "movie":
                # Fetch TMDB data if item not yet in local DB
                tmdb_id_str = str(req.tmdb_id)
                existing = db.query(MediaItem).filter_by(
                    media_type=MediaType.movie, tmdb_id=tmdb_id_str
                ).first()

                if existing is None:
                    if tmdb_client is None:
                        errors.append(
                            f"Movie tmdb_id={req.tmdb_id}: not in library and TMDB not configured"
                        )
                        skipped += 1
                        continue
                    movie_data = await tmdb_client.movie_detail(req.tmdb_id)
                    item = _find_or_create_movie(db, req.tmdb_id, movie_data)
                    is_new = True
                else:
                    item = existing
                    is_new = False

                if _has_duplicate_session(db, item.id, watched_at):
                    skipped += 1
                    continue

                db.add(_make_session(item, watched_at))
                created += 1
                if item.kodi_media_id is None:
                    item.kodi_state = KodiState.watched

                if is_new and schedule_artwork:
                    schedule_artwork(item.id)

            elif req.media_type == "episode":
                if req.season is None or req.episode is None:
                    errors.append(
                        f"Episode tmdb_id={req.tmdb_id}: season and episode are required"
                    )
                    skipped += 1
                    continue

                tmdb_id_str = str(req.tmdb_id)
                existing_ep = db.query(MediaItem).filter_by(
                    media_type=MediaType.episode,
                    tmdb_id=tmdb_id_str,
                    season=req.season,
                    episode=req.episode,
                ).first()

                if existing_ep is None:
                    if tmdb_client is None:
                        errors.append(
                            f"Episode tmdb_id={req.tmdb_id} S{req.season:02d}E{req.episode:02d}: "
                            "not in library and TMDB not configured"
                        )
                        skipped += 1
                        continue

                    tv_data = await tmdb_client.tv_detail(req.tmdb_id)
                    season_data = await tmdb_client.season_detail(req.tmdb_id, req.season)

                    # Find the specific episode in the season response
                    ep_data: Optional[dict] = None
                    for ep in season_data.get("episodes", []):
                        if ep.get("episode_number") == req.episode:
                            ep_data = ep
                            break

                    if ep_data is None:
                        errors.append(
                            f"Episode tmdb_id={req.tmdb_id} S{req.season:02d}E{req.episode:02d}: "
                            "not found on TMDB"
                        )
                        skipped += 1
                        continue

                    # Ensure episode's air_date is not in the future
                    air_date_str = ep_data.get("air_date")
                    if air_date_str:
                        try:
                            air_dt = datetime.strptime(air_date_str, "%Y-%m-%d")
                            if air_dt > datetime.utcnow():
                                errors.append(
                                    f"Episode tmdb_id={req.tmdb_id} S{req.season:02d}E{req.episode:02d}: "
                                    "air date is in the future"
                                )
                                skipped += 1
                                continue
                        except ValueError:
                            pass

                    show = _find_or_create_show(db, req.tmdb_id, tv_data)
                    item = _find_or_create_episode(
                        db, show, req.season, req.episode, ep_data, tv_data
                    )
                    is_new = True
                else:
                    item = existing_ep
                    is_new = False

                if _has_duplicate_session(db, item.id, watched_at):
                    skipped += 1
                    continue

                db.add(_make_session(item, watched_at))
                created += 1
                if item.kodi_media_id is None:
                    item.kodi_state = KodiState.watched

                if is_new and schedule_artwork:
                    schedule_artwork(item.id)

            else:
                errors.append(f"Unknown media_type: {req.media_type!r}")
                skipped += 1

        except Exception as exc:
            logger.exception("manual_watch_error", error=str(exc))
            errors.append(f"tmdb_id={req.tmdb_id}: {exc}")
            skipped += 1

    db.commit()
    return PostWatchedOut(created=created, skipped=skipped, errors=errors)
