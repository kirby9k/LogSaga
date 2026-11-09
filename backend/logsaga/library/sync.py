"""
Full library sync: reads all movies, episodes, and shows from Kodi and upserts
them into the local SQLite database.

Protection:
- Uses the same read-only Kodi connection as the poller (logsaga account,
  SET SESSION TRANSACTION READ ONLY, SQL guard).
- Never touches kodi_observation or watch_session.
- History is completely safe: existing sessions are never modified.

Algorithm (per run):
  1. Fetch all shows → upsert kodi_show (keyed on kodi_idShow).
  2. Fetch all movies → upsert media_item (keyed on kodi_media_id + media_type='movie').
  3. Fetch all episodes → upsert media_item + set show_id FK.
  4. Update kodi_show.kodi_state from episode states.
  5. Mark items in media_item/kodi_show that are no longer in Kodi as kodi_state=removed.

Idempotent: running twice produces the same final state (no duplicates).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog

from logsaga.kodi.db import (
    fetch_library_episodes,
    fetch_library_movies,
    fetch_library_shows,
)
from logsaga.models import KodiShow, KodiState, MediaItem, MediaType

logger = structlog.get_logger(__name__)

# Sentinel date used for items where Kodi's files.dateAdded is NULL.
# Chosen to pre-date all real media: 1951-09-25 is before any plausible media file.
DATE_ADDED_SENTINEL = "1951-09-25 00:00:00"

# Batch size for DB flushes during large upserts
_BATCH = 500


@dataclass
class SyncResult:
    shows_new: int = 0
    shows_updated: int = 0
    shows_removed: int = 0
    movies_new: int = 0
    movies_updated: int = 0
    movies_removed: int = 0
    episodes_new: int = 0
    episodes_updated: int = 0
    episodes_removed: int = 0

    @property
    def total_new(self) -> int:
        return self.shows_new + self.movies_new + self.episodes_new

    @property
    def total_updated(self) -> int:
        return self.shows_updated + self.movies_updated + self.episodes_updated

    @property
    def total_removed(self) -> int:
        return self.shows_removed + self.movies_removed + self.episodes_removed


def _now() -> datetime:
    return datetime.utcnow()


def _derive_state(play_count: Optional[int], existing_state: Optional[str]) -> str:
    """
    Derive kodi_state from Kodi's play_count.

    If the item is currently in_progress (set by the poller), preserve that
    state unless Kodi now says play_count > 0 (then it's watched).
    """
    if play_count and play_count > 0:
        return KodiState.watched.value
    if existing_state == KodiState.in_progress.value:
        return KodiState.in_progress.value
    return KodiState.unwatched.value


def _sync_shows(kodi_conn, db, result: SyncResult) -> None:
    """Upsert all TV shows from Kodi into kodi_show."""
    shows = fetch_library_shows(kodi_conn)
    logger.debug("library_sync_shows_fetched", count=len(shows))

    # Load all existing shows into memory (keyed by kodi_idShow)
    existing: dict[int, KodiShow] = {
        s.kodi_idShow: s
        for s in db.query(KodiShow).all()
        if s.kodi_idShow is not None
    }

    seen_kodi_ids: set[int] = set()
    now = _now()

    for show in shows:
        kid = show.kodi_idShow
        seen_kodi_ids.add(kid)

        year: Optional[int] = None
        if show.premiered:
            try:
                year = int(show.premiered[:4])
            except (ValueError, IndexError):
                pass

        if kid in existing:
            row = existing[kid]
            row.title = show.title
            row.plot = show.plot
            row.year = year
            row.premiered = show.premiered
            row.studio = show.studio
            row.status = show.status
            row.genres = show.genres
            if show.tvdb_id:
                row.tvdb_id = show.tvdb_id
            if show.imdb_id:
                row.imdb_id = show.imdb_id
            if show.tmdb_id:
                row.tmdb_id = show.tmdb_id
            row.updated_at = now
            result.shows_updated += 1
        else:
            new_show = KodiShow(
                kodi_idShow=kid,
                title=show.title,
                plot=show.plot,
                year=year,
                premiered=show.premiered,
                studio=show.studio,
                status=show.status,
                genres=show.genres,
                tvdb_id=show.tvdb_id,
                imdb_id=show.imdb_id,
                tmdb_id=show.tmdb_id,
                kodi_state=KodiState.unwatched,
                created_at=now,
                updated_at=now,
            )
            db.add(new_show)
            result.shows_new += 1

    # Mark removed: shows in our DB not in the current Kodi fetch
    removed_shows = [
        row for kid, row in existing.items()
        if kid not in seen_kodi_ids and row.kodi_state != KodiState.removed
    ]
    for row in removed_shows:
        row.kodi_state = KodiState.removed
        row.updated_at = now
        result.shows_removed += 1


def _sync_movies(kodi_conn, db, result: SyncResult) -> None:
    """Upsert all movies from Kodi into media_item."""
    movies = fetch_library_movies(kodi_conn)
    logger.debug("library_sync_movies_fetched", count=len(movies))

    # Load all existing movie items (keyed by kodi_media_id)
    existing: dict[int, MediaItem] = {
        item.kodi_media_id: item
        for item in db.query(MediaItem).filter(
            MediaItem.media_type == MediaType.movie,
            MediaItem.kodi_media_id.isnot(None),
        ).all()
    }

    seen_ids: set[int] = set()
    now = _now()
    batch_count = 0

    for movie in movies:
        seen_ids.add(movie.media_id)

        date_added = movie.date_added or DATE_ADDED_SENTINEL

        if movie.media_id in existing:
            item = existing[movie.media_id]
            item.kodi_state = _derive_state(movie.play_count, item.kodi_state)
            item.date_added = date_added
            item.kodi_file_id = movie.file_id
            # Update title/metadata only if not already populated
            if not item.title:
                item.title = movie.title
            if not item.runtime_s and movie.runtime_s:
                item.runtime_s = int(movie.runtime_s)
            if not item.plot and movie.plot:
                item.plot = movie.plot
            if not item.genres and movie.genres:
                item.genres = movie.genres
            if not item.director and movie.director:
                item.director = movie.director
            if not item.release_date and movie.release_date:
                item.release_date = movie.release_date
            if not item.year and movie.release_date:
                try:
                    item.year = int(movie.release_date[:4])
                except (ValueError, IndexError):
                    pass
            item.updated_at = now
            result.movies_updated += 1
        else:
            year: Optional[int] = None
            if movie.release_date:
                try:
                    year = int(movie.release_date[:4])
                except (ValueError, IndexError):
                    pass

            new_item = MediaItem(
                media_type=MediaType.movie,
                title=movie.title,
                runtime_s=int(movie.runtime_s) if movie.runtime_s else None,
                release_date=movie.release_date,
                plot=movie.plot,
                genres=movie.genres,
                director=movie.director,
                year=year,
                date_added=date_added,
                kodi_file_id=movie.file_id,
                kodi_media_id=movie.media_id,
                kodi_state=_derive_state(movie.play_count, None),
                created_at=now,
                updated_at=now,
            )
            db.add(new_item)
            result.movies_new += 1

        batch_count += 1
        if batch_count % _BATCH == 0:
            db.flush()

    # Mark removed
    removed = [
        item for mid, item in existing.items()
        if mid not in seen_ids and item.kodi_state != KodiState.removed
    ]
    for item in removed:
        item.kodi_state = KodiState.removed
        item.updated_at = now
        result.movies_removed += 1


def _sync_episodes(
    kodi_conn, db, result: SyncResult, show_id_map: dict[int, int]
) -> None:
    """
    Upsert all episodes from Kodi into media_item and set show_id FK.

    show_id_map: {kodi_idShow: kodi_show.id}
    """
    episodes = fetch_library_episodes(kodi_conn)
    logger.debug("library_sync_episodes_fetched", count=len(episodes))

    # Load all existing episode items (keyed by kodi_media_id)
    existing: dict[int, MediaItem] = {
        item.kodi_media_id: item
        for item in db.query(MediaItem).filter(
            MediaItem.media_type == MediaType.episode,
            MediaItem.kodi_media_id.isnot(None),
        ).all()
    }

    seen_ids: set[int] = set()
    now = _now()
    batch_count = 0

    for ep in episodes:
        seen_ids.add(ep.media_id)

        date_added = ep.date_added or DATE_ADDED_SENTINEL
        show_id = show_id_map.get(ep.kodi_idShow)

        if ep.media_id in existing:
            item = existing[ep.media_id]
            item.kodi_state = _derive_state(ep.play_count, item.kodi_state)
            item.date_added = date_added
            item.kodi_file_id = ep.file_id
            item.show_id = show_id
            # Update metadata only if not already populated
            if not item.title:
                item.title = ep.title
            if not item.show_title and ep.show_title:
                item.show_title = ep.show_title
            if item.season is None and ep.season is not None:
                item.season = ep.season
            if item.episode is None and ep.episode is not None:
                item.episode = ep.episode
            if not item.plot and ep.plot:
                item.plot = ep.plot
            if not item.genres and ep.genres:
                item.genres = ep.genres
            if not item.release_date and ep.release_date:
                item.release_date = ep.release_date
            if not item.year and ep.release_date:
                try:
                    item.year = int(ep.release_date[:4])
                except (ValueError, IndexError):
                    pass
            item.updated_at = now
            result.episodes_updated += 1
        else:
            year: Optional[int] = None
            if ep.release_date:
                try:
                    year = int(ep.release_date[:4])
                except (ValueError, IndexError):
                    pass

            new_item = MediaItem(
                media_type=MediaType.episode,
                title=ep.title,
                show_title=ep.show_title,
                season=ep.season,
                episode=ep.episode,
                year=year,
                release_date=ep.release_date,
                plot=ep.plot,
                genres=ep.genres,
                date_added=date_added,
                kodi_file_id=ep.file_id,
                kodi_media_id=ep.media_id,
                show_id=show_id,
                kodi_state=_derive_state(ep.play_count, None),
                created_at=now,
                updated_at=now,
            )
            db.add(new_item)
            result.episodes_new += 1

        batch_count += 1
        if batch_count % _BATCH == 0:
            db.flush()

    # Mark removed
    removed = [
        item for mid, item in existing.items()
        if mid not in seen_ids and item.kodi_state != KodiState.removed
    ]
    for item in removed:
        item.kodi_state = KodiState.removed
        item.updated_at = now
        result.episodes_removed += 1


def _update_show_states(db) -> None:
    """
    Update kodi_show.kodi_state based on its episodes' states.

    - If any episode is watched → show is watched (at least partially)
    - If any episode is in_progress → show may be in_progress
    - Otherwise → unwatched

    This runs after all episodes are flushed, so the states are current.
    """
    from sqlalchemy import func, case

    # Get max state per show_id (watched > in_progress > unwatched)
    # Simple approach: single pass over all episodes grouped by show_id
    rows = (
        db.query(
            MediaItem.show_id,
            func.sum(
                case((MediaItem.kodi_state == KodiState.watched.value, 1), else_=0)
            ).label("watched_count"),
            func.sum(
                case((MediaItem.kodi_state == KodiState.in_progress.value, 1), else_=0)
            ).label("in_progress_count"),
        )
        .filter(MediaItem.show_id.isnot(None))
        .group_by(MediaItem.show_id)
        .all()
    )

    now = _now()
    for show_id, watched_count, in_progress_count in rows:
        show = db.get(KodiShow, show_id)
        if show is None:
            continue
        if watched_count > 0:
            show.kodi_state = KodiState.watched
        elif in_progress_count > 0:
            show.kodi_state = KodiState.in_progress
        else:
            show.kodi_state = KodiState.unwatched
        show.updated_at = now


def sync_library(kodi_conn, db, settings=None) -> SyncResult:
    """
    Full library sync: upsert all Kodi library items into media_item and kodi_show.

    - Never touches kodi_observation or watch_session.
    - Idempotent: safe to run multiple times.
    - Marks items no longer in the Kodi library as kodi_state = removed.

    Returns a SyncResult with counts of new/updated/removed items.
    """
    result = SyncResult()

    # 1. Sync shows first (episodes need show IDs)
    _sync_shows(kodi_conn, db, result)
    db.flush()

    # Build kodi_idShow → kodi_show.id map for FK resolution
    show_id_map: dict[int, int] = {
        s.kodi_idShow: s.id
        for s in db.query(KodiShow).all()
        if s.kodi_idShow is not None
    }

    # 2. Sync movies
    _sync_movies(kodi_conn, db, result)
    db.flush()

    # 3. Sync episodes (uses show_id_map)
    _sync_episodes(kodi_conn, db, result, show_id_map)
    db.flush()

    # 4. Update show states from episode states
    _update_show_states(db)

    db.commit()

    logger.info(
        "library_sync_complete",
        shows=f"{result.shows_new}new/{result.shows_updated}upd/{result.shows_removed}rm",
        movies=f"{result.movies_new}new/{result.movies_updated}upd/{result.movies_removed}rm",
        episodes=f"{result.episodes_new}new/{result.episodes_updated}upd/{result.episodes_removed}rm",
    )
    return result
