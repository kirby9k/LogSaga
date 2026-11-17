"""
LogSAGA — FastAPI application entry point.

Lifespan:
  - Creates SQLite engine and stores it in app.state
  - Starts background poll loop (asyncio task wrapping sync PyMySQL calls)
  - Starts MQTT publisher task if MQTT is configured

Routes:
  /api/*         REST API (see api/routes.py)
  /api/docs      Swagger UI
  /api/openapi.json  OpenAPI schema (used by openapi-typescript for TS codegen)
  /art/*         Local artwork files (StaticFiles)
  /              Frontend static build (if dist/ exists, populated in Phase 4+)
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager, suppress

# Load .env into os.environ early so module-level os.environ.get() calls
# (artwork dir, db path) see the same values as pydantic-settings does.
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from logsaga.api.routes import router
from logsaga.config import get_settings
from logsaga.models import Base, MediaItem, SessionStatus, SyncState, WatchSession
from logsaga.mqtt.publisher import run_mqtt_publisher

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _db_path(cfg=None) -> str:
    if cfg is not None:
        return cfg.logsaga_db_path
    return os.environ.get("LOGSAGA_DB_PATH", "logsaga.db")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# DB-stored configuration management
# ---------------------------------------------------------------------------

# Config keys that can be stored in AppSetting and override env vars at startup.
# Keys NOT listed here are always env-only (paths, build info, session rules).
_CONFIG_KEY_TYPES: dict[str, type] = {
    "kodi_db_host":          str,
    "kodi_db_port":          int,
    "kodi_db_user":          str,
    "kodi_db_password":      str,  # stored plain; pydantic coerces to SecretStr
    "kodi_db_name":          str,
    "kodi_jsonrpc_url":      str,
    "kodi_jsonrpc_user":     str,
    "kodi_jsonrpc_password": str,
    "tmdb_api_key":          str,
    "mqtt_host":             str,
    "mqtt_port":             int,
    "mqtt_user":             str,
    "mqtt_password":         str,
    "mqtt_discovery_prefix": str,
    "public_base_url":       str,
    "log_level":             str,
    "tz":                    str,
}
_CONFIG_KEYS: frozenset[str] = frozenset(_CONFIG_KEY_TYPES)


def _load_effective_settings(base: Any, engine: Any) -> Any:
    """
    Merge DB-stored config keys on top of env-var-based Settings.

    DB values win; env vars are the fallback for any key not stored in the DB.
    Returns a new Settings object — does not mutate the original.
    """
    from pydantic import SecretStr as _SecretStr

    from logsaga.config import Settings as _Settings
    from logsaga.models import AppSetting as _AppSetting

    with sessionmaker(bind=engine)() as db:
        db_rows = {row.key: row.value for row in db.query(_AppSetting).all()}

    overrides: dict[str, Any] = {}
    for key, cast_type in _CONFIG_KEY_TYPES.items():
        if key in db_rows:
            try:
                overrides[key] = cast_type(db_rows[key])
            except (ValueError, TypeError):
                pass  # bad stored value — fall back to env var

    if not overrides:
        return base

    # Build init_kwargs from the base settings then apply DB overrides on top.
    # Passing values as __init__ kwargs takes priority over all pydantic-settings
    # sources (env vars, .env file), so DB-stored values always win.
    init_kwargs: dict[str, Any] = {}
    for name in _Settings.model_fields:
        val = getattr(base, name)
        if isinstance(val, _SecretStr):
            init_kwargs[name] = val.get_secret_value()
        elif isinstance(val, Path):
            init_kwargs[name] = str(val)
        else:
            init_kwargs[name] = val
    init_kwargs.update(overrides)
    return _Settings(**init_kwargs)


def _maybe_migrate_env_config(base: Any, engine: Any) -> None:
    """
    Copy env-var-based integration config into AppSetting on first startup.

    Runs only once: skipped if any config keys already exist in AppSetting.
    Also skipped if no significant integration key is set (clean install with
    no Kodi/TMDB/MQTT configured yet).
    """
    from pydantic import SecretStr as _SecretStr

    from logsaga.models import AppSetting as _AppSetting

    with sessionmaker(bind=engine)() as db:
        existing = db.query(_AppSetting).filter(
            _AppSetting.key.in_(_CONFIG_KEYS)
        ).count()

    if existing > 0:
        return  # Already migrated or user set up config via UI

    def _get_str(key: str) -> str:
        val = getattr(base, key, "")
        if isinstance(val, _SecretStr):
            return val.get_secret_value()
        return str(val) if val else ""

    # Only migrate if at least one significant integration is configured via env.
    # Prevents polluting a clean install's DB with all-default values.
    is_configured = any(
        _get_str(k)
        for k in ("kodi_db_host", "tmdb_api_key", "mqtt_host", "kodi_jsonrpc_url")
    )
    if not is_configured:
        return

    with sessionmaker(bind=engine)() as db:
        entries = 0
        for key in _CONFIG_KEYS:
            val_str = _get_str(key)
            if not val_str:
                continue
            if db.get(_AppSetting, key) is None:
                db.add(_AppSetting(key=key, value=val_str))
                entries += 1
        if entries > 0:
            db.commit()
            logger.info("config_migrated_from_env", entries=entries)


# ---------------------------------------------------------------------------
# Kodi connection state (held across poll iterations)
# ---------------------------------------------------------------------------
class _KodiState:
    conn: Optional[Any] = None      # pymysql.Connection or None
    db_name: Optional[str] = None
    last_db_name_check: Optional[datetime] = None
    error_count: int = 0


def _ensure_kodi_conn(state: _KodiState, cfg) -> bool:
    """
    (Re)connect to Kodi's MariaDB if the connection is dead or the DB-name
    cache is stale (re-detected every hour).

    Returns True if connected, False on error.
    """
    from logsaga.kodi.db import KodiDBError, connect, detect_db_name

    now = _now_utc()
    need_name_refresh = (
        state.db_name is None
        or state.last_db_name_check is None
        or (now - state.last_db_name_check).total_seconds() > 3600
    )
    need_reconnect = state.conn is None

    if need_name_refresh:
        try:
            state.db_name = detect_db_name(cfg)
            state.last_db_name_check = now
        except Exception as exc:
            logger.error("kodi_db_name_detect_failed", error=str(exc))
            return False

    if need_reconnect:
        try:
            state.conn = connect(cfg, db_name=state.db_name)
            logger.info("kodi_db_connected", db=state.db_name)
        except Exception as exc:
            logger.error("kodi_db_connect_failed", error=str(exc))
            state.conn = None
            return False

    # Ping — reconnect on drop
    if state.conn is None:
        return False
    try:
        state.conn.ping(reconnect=True)
    except Exception:
        state.conn = None
        return _ensure_kodi_conn(state, cfg)

    return True


def _run_poll_cycle(engine, conn, cfg) -> tuple[int, list[int], bool, list[int]]:
    """
    Run one full poll cycle (sync — called via asyncio.to_thread).

    1. poll_once → PollResult (observations + file_count_changed + played_item_ids)
    2. For each affected item: build_incremental
    3. Close timed-out sessions
    4. Commit

    Returns (obs_count, new_item_ids, file_count_changed, played_item_ids) where:
      new_item_ids       — MediaItem IDs created this cycle (need full enrichment)
      file_count_changed — True when files.idFile count changed in Kodi
      played_item_ids    — items with active play observations (priority enrichment)
    """
    from logsaga.observe.poller import PollResult, poll_once
    from logsaga.sessions.builder import build_incremental

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        # Record the cycle start time so we can find items created in this cycle.
        cycle_start = _now_utc()

        try:
            result: PollResult = poll_once(conn, db, cfg)
        except Exception as exc:
            logger.error("poll_once_failed", error=str(exc))
            # Roll back any failed transaction before attempting to record the error,
            # otherwise SQLAlchemy raises "This Session's transaction has been rolled back".
            try:
                db.rollback()
                _record_error(db, str(exc))
                db.commit()
            except Exception:
                pass  # If we can't record the error, just move on
            return 0, [], False, []

        observations = result.observations

        if observations:
            # Group by item, build incremental sessions
            by_item: dict[int, list] = {}
            for obs in observations:
                by_item.setdefault(obs.media_item_id, []).append(obs)
            for item_id, item_obs in by_item.items():
                try:
                    build_incremental(item_id, item_obs, db, cfg)
                except Exception as exc:
                    logger.error("build_incremental_failed", item_id=item_id, error=str(exc))

        # Close any timed-out open sessions
        _close_stale_sessions(db, cfg)
        db.commit()

        # Identify any MediaItem rows created during this cycle so the caller
        # can schedule full enrichment for them without waiting for the next
        # library sync (which runs every 2 h by default).
        new_item_ids: list[int] = []
        if observations:
            new_rows = db.query(MediaItem.id).filter(
                MediaItem.created_at >= cycle_start
            ).all()
            new_item_ids = [r[0] for r in new_rows]

    return (
        len(observations) if observations else 0,
        new_item_ids,
        result.file_count_changed,
        result.played_item_ids,
    )


def _close_stale_sessions(db: Session, cfg) -> None:
    """Close open sessions that have timed out since the last poll."""
    from logsaga.sessions.rules import SessionSettings, close_timed_out_sessions

    now = _now_utc()
    open_sessions = (
        db.query(WatchSession)
        .filter(WatchSession.status == SessionStatus.open)
        .all()
    )
    if not open_sessions:
        return

    # Group by item to get the right settings (is_movie)
    by_item: dict[int, list] = {}
    for ws in open_sessions:
        by_item.setdefault(ws.media_item_id, []).append(ws)

    for item_id, sessions in by_item.items():
        item = db.get(MediaItem, item_id)
        if item is None:
            continue
        settings = SessionSettings(
            min_started_seconds=cfg.min_started_seconds,
            session_gap_hours=cfg.session_gap_hours,
            watched_percent=cfg.watched_percent,
            watched_remaining_minutes_movie=cfg.watched_remaining_minutes_movie,
            watched_remaining_minutes_episode=cfg.watched_remaining_minutes_episode,
            is_movie=(item.media_type == "movie"),
        )
        # Convert to SessionRow, close, write back
        from logsaga.sessions.builder import _load_session_rows, _persist_sessions

        rows = _load_session_rows(db, item_id)
        close_timed_out_sessions(rows, now, settings)
        _persist_sessions(db, item_id, rows)


def _record_error(db: Session, error: str) -> None:
    """Update sync_state with a poll error."""
    sync = db.get(SyncState, 1)
    now = _now_utc()
    if sync is None:
        sync = SyncState(id=1, error_count=0, poll_count=0, observation_count=0)
        db.add(sync)
    sync.last_error = error
    sync.last_error_at = now
    sync.error_count = (sync.error_count or 0) + 1


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
def _run_library_sync(engine, conn, cfg) -> bool:
    """
    Run one full library sync (sync — called via asyncio.to_thread).

    Fetches all movies/episodes/shows from Kodi and upserts them in the local DB.
    Never touches kodi_observation or watch_session.

    Returns True if any new items were created (caller should trigger artwork fetch).
    """
    from logsaga.library.sync import sync_library

    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    with SessionLocal() as db:
        try:
            result = sync_library(conn, db, cfg)
            logger.info(
                "library_sync_done",
                new=result.total_new,
                updated=result.total_updated,
                removed=result.total_removed,
            )
            return result.total_new > 0
        except Exception as exc:
            logger.error("library_sync_failed", error=str(exc))
            try:
                db.rollback()
            except Exception:
                pass
            return False


async def _fetch_artwork_for_items_bg(app: FastAPI, item_ids: list[int]) -> None:
    """
    Fire-and-forget: fetch artwork for specific MediaItem IDs.

    Called when the poll loop creates new MediaItem rows so that artwork is
    available immediately rather than waiting for the next library sync.
    Opens its own short-lived Kodi connection; uses per-item commits so the
    SQLite write lock is never held across network I/O.
    """
    if not item_ids:
        return

    cfg = app.state.settings
    engine = app.state.engine

    def _sync() -> int:
        from logsaga.artwork.cache import fetch_artwork_for_item
        from logsaga.kodi.db import connect, detect_db_name
        from logsaga.kodi.jsonrpc import KodiJSONRPC

        SessionLocal = sessionmaker(bind=engine, autoflush=False)
        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        rpc = KodiJSONRPC(cfg) if cfg.kodi_jsonrpc_url else None
        total = 0
        try:
            for iid in item_ids:
                with SessionLocal() as db:
                    item = db.get(MediaItem, iid)
                    if item is None:
                        continue
                    count = fetch_artwork_for_item(item, conn, rpc, db, cfg)
                    db.commit()
                    total += count
        finally:
            conn.close()
            if rpc:
                rpc.close()
        return total

    try:
        updated = await asyncio.to_thread(_sync)
        logger.info("auto_artwork_new_items", items=len(item_ids), artwork_rows=updated)
    except Exception as exc:
        logger.error("auto_artwork_new_items_failed", error=str(exc))


async def _enrich_new_items_bg(app: FastAPI, item_ids: list[int]) -> None:
    """
    Full enrichment for newly created MediaItems: artwork + TMDB detail + cast.

    Called from the poll loop when new items are first detected. Steps:
      1. Artwork  — delegates to the existing _fetch_artwork_for_items_bg.
      2. TMDB detail + cast — for each item with a tmdb_id, fetches movie_detail
         or tv_detail (writing to tmdb_cache), then populates item_cast rows.

    Skips gracefully if TMDB is not configured or the item has no tmdb_id.
    """
    if not item_ids:
        return

    # Step 1: artwork (opens its own Kodi connection internally)
    await _fetch_artwork_for_items_bg(app, item_ids)

    # Step 2: TMDB detail + cast
    cfg = app.state.settings
    if not cfg.tmdb_api_key:
        return

    engine = app.state.engine

    for iid in item_ids:
        try:
            from sqlalchemy.orm import sessionmaker as _sm

            from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show
            from logsaga.tmdb.client import TMDBClient

            with _sm(bind=engine, autoflush=False)() as db:
                item = db.get(MediaItem, iid)
                if item is None or not item.tmdb_id:
                    continue

                now = _now_utc()

                if item.media_type == "movie":
                    item.cast_refresh_attempted_at = now
                    db.commit()
                    with _sm(bind=engine, autoflush=False)() as db2:
                        item2 = db2.get(MediaItem, iid)
                        if item2 is None:
                            continue
                        tmdb2 = TMDBClient(cfg.tmdb_api_key, db2)
                        await tmdb2.movie_detail(int(item2.tmdb_id))
                        populate_cast_for_item(item2, db2)
                        item2.cast_fetched_at = now
                        db2.commit()
                elif item.media_type == "episode" and item.show_id:
                    from logsaga.models import KodiShow as _KodiShow
                    show = db.get(_KodiShow, item.show_id)
                    if show and show.tmdb_id:
                        show.cast_refresh_attempted_at = now
                        db.commit()
                        with _sm(bind=engine, autoflush=False)() as db2:
                            show2 = db2.get(_KodiShow, item.show_id)
                            if show2 is None:
                                continue
                            tmdb2 = TMDBClient(cfg.tmdb_api_key, db2)
                            await tmdb2.tv_detail(int(show2.tmdb_id))
                            populate_cast_for_show(show2, db2)
                            show2.cast_fetched_at = now
                            db2.commit()
        except Exception as exc:
            logger.error("enrich_new_item_failed", item_id=iid, error=str(exc))


async def _enrich_played_items_bg(app: FastAPI, item_ids: list[int]) -> None:
    """
    Priority enrichment for items with active play observations.

    Checks whether each item is missing artwork or cast and triggers
    _enrich_new_items_bg for those that have gaps.  Items already fully
    enriched are skipped with no work done.
    """
    if not item_ids:
        return

    engine = app.state.engine

    def _find_gaps() -> list[int]:
        from logsaga.models import Artwork as _Artwork
        from logsaga.models import KodiShow as _KodiShow

        SessionLocal = sessionmaker(bind=engine, autoflush=False)
        needs_enrichment: list[int] = []

        with SessionLocal() as db:
            for iid in item_ids:
                item = db.get(MediaItem, iid)
                if item is None:
                    continue

                # Check artwork: at least one Artwork row with a local file
                has_art = (
                    db.query(_Artwork.id)
                    .filter(
                        _Artwork.media_item_id == iid,
                        _Artwork.local_path.isnot(None),
                    )
                    .first()
                ) is not None

                # Check cast: movie uses item cast_fetched_at; episode uses show
                if item.media_type == "movie":
                    has_cast = item.cast_fetched_at is not None
                elif item.media_type == "episode" and item.show_id:
                    show = db.get(_KodiShow, item.show_id)
                    has_cast = show is not None and show.cast_fetched_at is not None
                else:
                    has_cast = True  # no cast path available; skip

                if not has_art or not has_cast:
                    needs_enrichment.append(iid)

        return needs_enrichment

    try:
        to_enrich = await asyncio.to_thread(_find_gaps)
        if to_enrich:
            logger.info("played_items_need_enrichment", count=len(to_enrich))
            await _enrich_new_items_bg(app, to_enrich)
    except Exception as exc:
        logger.error("enrich_played_items_failed", error=str(exc))


async def _delayed_fill_artwork_bg(app: FastAPI, delay_s: int = 300) -> None:
    """
    Re-run artwork fill after a delay.

    Kodi may not have finished writing its art table rows for brand-new items
    by the time the library sync fires.  Scheduling a second pass a few minutes
    later catches what the first attempt missed without polling continuously.
    """
    await asyncio.sleep(delay_s)
    await _fill_missing_artwork_bg(app)


async def _fill_missing_artwork_bg(app: FastAPI) -> None:
    """
    Fire-and-forget: fetch artwork for all items that have no downloaded artwork.

    Runs after library sync when new items are found.  Uses a subquery to skip
    items that already have at least one Artwork row with a local_path so this
    is a cheap no-op once artwork is fully populated.
    Opens its own Kodi connection only when there is actual work to do.
    """
    cfg = app.state.settings
    engine = app.state.engine

    def _sync() -> int:
        from logsaga.artwork.cache import fetch_artwork_for_item
        from logsaga.kodi.db import connect, detect_db_name
        from logsaga.kodi.jsonrpc import KodiJSONRPC
        from logsaga.models import Artwork as _Artwork

        SessionLocal = sessionmaker(bind=engine, autoflush=False)

        # Quick DB-only pass: find items with no downloaded artwork.
        with SessionLocal() as db:
            has_art_sq = (
                db.query(_Artwork.media_item_id)
                .filter(_Artwork.local_path.isnot(None))
                .distinct()
                .subquery()
            )
            item_ids = [
                r[0] for r in
                db.query(MediaItem.id)
                .filter(
                    MediaItem.kodi_media_id.isnot(None),
                    ~MediaItem.id.in_(has_art_sq),
                )
                .all()
            ]

        if not item_ids:
            return 0  # Nothing to do — skip opening a Kodi connection.

        logger.info("auto_artwork_fill_start", items_without_art=len(item_ids))

        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        rpc = KodiJSONRPC(cfg) if cfg.kodi_jsonrpc_url else None
        total = 0
        try:
            for iid in item_ids:
                with SessionLocal() as db:
                    item = db.get(MediaItem, iid)
                    if item is None:
                        continue
                    try:
                        count = fetch_artwork_for_item(item, conn, rpc, db, cfg)
                        db.commit()
                        total += count
                    except Exception as exc:
                        db.rollback()
                        logger.warning("auto_artwork_item_failed", item_id=iid, error=str(exc))
        finally:
            conn.close()
            if rpc:
                rpc.close()
        return total

    try:
        updated = await asyncio.to_thread(_sync)
        if updated:
            logger.info("auto_artwork_fill_done", artwork_rows=updated)
    except Exception as exc:
        logger.error("auto_artwork_fill_failed", error=str(exc))


async def _fill_missing_cast_bg(app: FastAPI) -> None:
    """
    Fire-and-forget: populate cast for all items/shows that have tmdb_id but
    no item_cast rows yet.

    Reads only the local tmdb_cache (no network calls) so it's safe to run
    after every library sync.  Returns quickly if everything is already done.
    """
    cfg = app.state.settings
    engine = app.state.engine

    def _sync() -> int:
        from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show

        SessionLocal = sessionmaker(bind=engine, autoflush=False)
        total = 0

        with SessionLocal() as db:
            from logsaga.models import ItemCast as _ItemCast
            from logsaga.models import KodiShow as _KodiShow

            # Shows with tmdb_id but no show-level cast rows
            shows_with_cast = {
                r[0]
                for r in db.query(_ItemCast.show_id)
                .filter(_ItemCast.show_id.isnot(None))
                .distinct()
                .all()
            }
            show_ids = [
                r[0]
                for r in db.query(_KodiShow.id)
                .filter(_KodiShow.tmdb_id.isnot(None))
                .limit(200)
                .all()
                if r[0] not in shows_with_cast
            ]

            # Movies with tmdb_id but no item-level cast rows
            items_with_cast = {
                r[0]
                for r in db.query(_ItemCast.media_item_id)
                .filter(_ItemCast.media_item_id.isnot(None))
                .distinct()
                .all()
            }
            movie_ids = [
                r[0]
                for r in db.query(MediaItem.id)
                .filter(
                    MediaItem.media_type == "movie",
                    MediaItem.tmdb_id.isnot(None),
                )
                .limit(200)
                .all()
                if r[0] not in items_with_cast
            ]

        for show_id in show_ids:
            with SessionLocal() as db:
                show = db.get(_KodiShow, show_id)
                if show is None:
                    continue
                rows = populate_cast_for_show(show, db)
                total += len(rows)

        for item_id in movie_ids:
            with SessionLocal() as db:
                item = db.get(MediaItem, item_id)
                if item is None:
                    continue
                rows = populate_cast_for_item(item, db)
                total += len(rows)

        return total

    if not cfg.tmdb_api_key:
        return

    try:
        inserted = await asyncio.to_thread(_sync)
        if inserted:
            logger.info("auto_cast_fill_done", cast_rows=inserted)
    except Exception as exc:
        logger.error("auto_cast_fill_failed", error=str(exc))


async def _run_cast_sync_bg(app: FastAPI) -> None:
    """
    One pass of the periodic cast sync: fetch TMDB credits for shows and movies
    that either have never had cast populated (cast_fetched_at IS NULL) or whose
    cast data is older than 30 days.

    Makes live TMDB API calls (results cached for 30 days). Uses a semaphore to
    bound concurrency at 3 so we don't hammer the TMDB API.

    Timestamps are written only on successful commit so interrupted or partial
    inserts are retried on the next pass (cast_fetched_at stays NULL until done).
    """
    from datetime import timedelta

    cfg = app.state.settings
    if not cfg.tmdb_api_key:
        return

    engine = app.state.engine

    from sqlalchemy.orm import sessionmaker as _sm

    from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show
    from logsaga.models import KodiShow as _KodiShow
    from logsaga.tmdb.client import TMDBClient

    SessionLocal = _sm(bind=engine, autoflush=False)
    now = _now_utc()
    stale_cutoff = now - timedelta(days=30)
    cooldown_cutoff = now - timedelta(hours=12)

    # Collect shows and movies that need cast (never populated OR older than 30 days),
    # excluding items attempted in the last 12 hours (failed IDs don't spam every sync).
    with SessionLocal() as db:
        show_ids = [
            r[0]
            for r in db.query(_KodiShow.id)
            .filter(
                _KodiShow.tmdb_id.isnot(None),
                (
                    _KodiShow.cast_fetched_at.is_(None)
                    | (_KodiShow.cast_fetched_at < stale_cutoff)
                ),
                (
                    _KodiShow.cast_refresh_attempted_at.is_(None)
                    | (_KodiShow.cast_refresh_attempted_at < cooldown_cutoff)
                ),
            )
            .all()
        ]

        movie_ids = [
            r[0]
            for r in db.query(MediaItem.id)
            .filter(
                MediaItem.media_type == "movie",
                MediaItem.tmdb_id.isnot(None),
                (
                    MediaItem.cast_fetched_at.is_(None)
                    | (MediaItem.cast_fetched_at < stale_cutoff)
                ),
                (
                    MediaItem.cast_refresh_attempted_at.is_(None)
                    | (MediaItem.cast_refresh_attempted_at < cooldown_cutoff)
                ),
            )
            .all()
        ]

    total = len(show_ids) + len(movie_ids)
    if total == 0:
        return

    logger.info("cast_sync_start", shows=len(show_ids), movies=len(movie_ids))

    sem = asyncio.Semaphore(3)

    async def _enrich_show(show_id: int) -> bool:
        import httpx as _httpx
        from logsaga.tmdb.matching import pick_tv_match
        async with sem:
            now = _now_utc()
            try:
                with SessionLocal() as db:
                    show = db.get(_KodiShow, show_id)
                    if show is None or not show.tmdb_id:
                        return False
                    # Stamp attempt time before the network call
                    show.cast_refresh_attempted_at = now
                    db.commit()
                with SessionLocal() as db:
                    show = db.get(_KodiShow, show_id)
                    if show is None:
                        return False
                    tmdb = TMDBClient(cfg.tmdb_api_key, db)
                    try:
                        await tmdb.tv_detail(int(show.tmdb_id))
                    except _httpx.HTTPStatusError as http_exc:
                        if http_exc.response.status_code != 404:
                            raise
                        # Bad tmdb_id — search by title+year for the correct one
                        data = await tmdb.search_tv(show.title, show.year)
                        new_id = pick_tv_match(data.get("results", []), show.title, show.year)
                        if new_id:
                            logger.info(
                                "cast_sync_id_corrected",
                                entity="show", show_id=show_id,
                                old_tmdb_id=show.tmdb_id, new_tmdb_id=new_id,
                                title=show.title,
                            )
                            show.tmdb_id = str(new_id)
                            db.commit()
                            await tmdb.tv_detail(new_id)  # fetch with correct ID
                        else:
                            logger.warning(
                                "cast_sync_id_cleared",
                                entity="show", show_id=show_id,
                                bad_tmdb_id=show.tmdb_id, title=show.title,
                            )
                            show.tmdb_id = None
                            db.commit()
                            return False
                    populate_cast_for_show(show, db)
                    # Only stamp success after populate committed inside populate_cast_for_show
                    show.cast_fetched_at = now
                    db.commit()
                    return True
            except Exception as exc:
                logger.warning("cast_sync_show_failed", show_id=show_id, error=str(exc))
                return False

    async def _enrich_movie(item_id: int) -> bool:
        import httpx as _httpx
        from logsaga.tmdb.matching import pick_movie_match
        async with sem:
            now = _now_utc()
            try:
                with SessionLocal() as db:
                    item = db.get(MediaItem, item_id)
                    if item is None or not item.tmdb_id:
                        return False
                    item.cast_refresh_attempted_at = now
                    db.commit()
                with SessionLocal() as db:
                    item = db.get(MediaItem, item_id)
                    if item is None:
                        return False
                    tmdb = TMDBClient(cfg.tmdb_api_key, db)
                    try:
                        await tmdb.movie_detail(int(item.tmdb_id))
                    except _httpx.HTTPStatusError as http_exc:
                        if http_exc.response.status_code != 404:
                            raise
                        # Bad tmdb_id — search by title+year for the correct one
                        data = await tmdb.search_movie(item.title, item.year)
                        new_id = pick_movie_match(data.get("results", []), item.title, item.year)
                        if new_id:
                            logger.info(
                                "cast_sync_id_corrected",
                                entity="movie", item_id=item_id,
                                old_tmdb_id=item.tmdb_id, new_tmdb_id=new_id,
                                title=item.title,
                            )
                            item.tmdb_id = str(new_id)
                            db.commit()
                            await tmdb.movie_detail(new_id)  # fetch with correct ID
                        else:
                            logger.warning(
                                "cast_sync_id_cleared",
                                entity="movie", item_id=item_id,
                                bad_tmdb_id=item.tmdb_id, title=item.title,
                            )
                            item.tmdb_id = None
                            db.commit()
                            return False
                    populate_cast_for_item(item, db)
                    item.cast_fetched_at = now
                    db.commit()
                    return True
            except Exception as exc:
                logger.warning("cast_sync_movie_failed", item_id=item_id, error=str(exc))
                return False

    coros = [_enrich_show(sid) for sid in show_ids] + [_enrich_movie(mid) for mid in movie_ids]
    results = await asyncio.gather(*coros, return_exceptions=False)
    shows_done = sum(1 for i, ok in enumerate(results) if ok and i < len(show_ids))
    movies_done = sum(1 for i, ok in enumerate(results) if ok and i >= len(show_ids))

    logger.info("cast_sync_done", shows_done=shows_done, movies_done=movies_done, total=total)


async def _cast_sync_loop(app: FastAPI) -> None:
    """
    Background task: run a full cast sync every cfg.cast_sync_hours.

    Initial delay of 10 minutes so library sync has time to complete first.
    Only runs if TMDB is configured.
    """
    cfg = app.state.settings
    if not cfg.tmdb_api_key:
        return

    interval_s = int(cfg.cast_sync_hours * 3600)

    # Initial delay — let the library sync run first
    await asyncio.sleep(600)

    while True:
        try:
            await _run_cast_sync_bg(app)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("cast_sync_loop_error", error=str(exc))

        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            break


async def _library_sync_loop(app: FastAPI) -> None:
    """
    Background task: run a full library sync every cfg.library_sync_hours.

    Also responds to app.state.library_sync_requested (asyncio.Event) which is
    set by the poll loop when files.idFile count changes — this brings new
    library additions and removals into LogSAGA within ~one poll cycle rather
    than waiting for the full scheduled interval.

    A lock (app.state.library_sync_lock) prevents two syncs running concurrently
    if the event fires while a sync is already in progress.

    Uses a separate Kodi connection from the poll loop.
    """
    kodi = app.state.library_kodi_state  # stored on app.state; config API can reset .conn

    # Initial sync after a short delay (let the poll loop start first)
    await asyncio.sleep(10)

    while True:
        cfg = app.state.settings  # re-read on each iteration (picks up hot-reloaded config)
        # Pause during a restore so the DB file is not held open during the swap.
        if getattr(app.state, "restore_in_progress", False):
            await asyncio.sleep(1)
            continue
        interval_s = int(cfg.library_sync_hours * 3600)
        try:
            async with app.state.library_sync_lock:
                connected = await asyncio.to_thread(_ensure_kodi_conn, kodi, cfg)
                if connected:
                    has_new = await asyncio.to_thread(
                        _run_library_sync, app.state.engine, kodi.conn, cfg
                    )
                    # Always scan for items missing artwork — exits immediately
                    # (no Kodi connection opened) when all items already have art.
                    asyncio.create_task(_fill_missing_artwork_bg(app))
                    if has_new:
                        # Delayed second pass: Kodi may not have finished writing
                        # art table rows for brand-new items by the time the sync
                        # fires.  5 minutes later they should be ready.
                        asyncio.create_task(_delayed_fill_artwork_bg(app, delay_s=300))
                        # Fire cast sync immediately with live TMDB calls so cast
                        # is populated within seconds rather than waiting up to 24 h
                        # for the scheduled cast_sync_loop.
                        asyncio.create_task(_run_cast_sync_bg(app))
                else:
                    logger.warning("library_sync_skipped_no_kodi_conn")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("library_sync_loop_error", error=str(exc))

        # Wait for either: a poll-triggered request OR the scheduled interval.
        # CancelledError breaks the loop cleanly.
        try:
            await asyncio.wait_for(
                app.state.library_sync_requested.wait(),
                timeout=interval_s,
            )
            app.state.library_sync_requested.clear()
            logger.info("library_sync_triggered_by_file_count_change")
        except asyncio.TimeoutError:
            pass  # Normal schedule — run on next iteration
        except asyncio.CancelledError:
            break

    if kodi.conn:
        try:
            kodi.conn.close()
        except Exception:
            pass


async def _poll_loop(app: FastAPI) -> None:
    """
    Background task: poll Kodi's MariaDB every cfg.poll_seconds.

    Uses asyncio.to_thread for the sync PyMySQL calls.
    Reconnects on connection drop; exponential backoff on errors.
    """
    kodi = app.state.poll_kodi_state  # stored on app.state; config API can reset .conn
    backoff = app.state.settings.poll_seconds  # initial value; reset on success

    while True:
        cfg = app.state.settings  # re-read on each iteration (picks up hot-reloaded config)
        # Pause during a restore so the DB file is not held open during the swap.
        if getattr(app.state, "restore_in_progress", False):
            await asyncio.sleep(1)
            continue
        try:
            await asyncio.sleep(cfg.poll_seconds)
            connected = await asyncio.to_thread(_ensure_kodi_conn, kodi, cfg)
            if not connected:
                logger.warning("kodi_db_unavailable", retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue

            backoff = cfg.poll_seconds  # reset on success
            new_obs, new_item_ids, file_count_changed, played_item_ids = (
                await asyncio.to_thread(
                    _run_poll_cycle, app.state.engine, kodi.conn, cfg
                )
            )
            if new_obs > 0:
                logger.info("poll_cycle_complete", new_observations=new_obs)

            # Library file count changed — trigger an immediate library sync
            # rather than waiting up to library_sync_hours for the scheduled run.
            if file_count_changed:
                logger.info("poll_file_count_changed_triggering_library_sync")
                app.state.library_sync_requested.set()

            # Newly created items need full enrichment — artwork + TMDB + cast.
            # Fire-and-forget; don't wait for the next library sync cycle.
            if new_item_ids:
                asyncio.create_task(_enrich_new_items_bg(app, new_item_ids))

            # Items with active play observations that already exist in the DB
            # may have missing artwork or cast (e.g. cast sync hadn't run yet).
            # Stage E wires this up; placeholder task created in _enrich_played_items_bg.
            played_only = [i for i in played_item_ids if i not in new_item_ids]
            if played_only:
                asyncio.create_task(_enrich_played_items_bg(app, played_only))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("poll_loop_unhandled_error", error=str(exc))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    if kodi.conn:
        try:
            kodi.conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()

    # ---------------------------------------------------------------------------
    # Logging: human-readable lines to stdout + rotating file at /config/logsaga.log
    # ---------------------------------------------------------------------------
    _log_level = getattr(logging, cfg.log_level, logging.INFO)

    def _fmt_log(_, method: str, event_dict: dict) -> str:
        """Format a structlog event as a readable text line."""
        ts  = event_dict.pop("timestamp", "")
        lvl = event_dict.pop("level", method).upper()
        evt = event_dict.pop("event", "")
        tail = ("  " + "  ".join(f"{k}={v!r}" for k, v in event_dict.items())
                if event_dict else "")
        return f"{ts} [{lvl:<7}] {evt}{tail}"

    _plain_fmt = logging.Formatter("%(message)s")

    # Reset root logger handlers (avoid duplicates on lifespan reload)
    _root = logging.getLogger()
    _root.setLevel(_log_level)
    for _h in list(_root.handlers):
        _root.removeHandler(_h)

    _sh = logging.StreamHandler()
    _sh.setFormatter(_plain_fmt)
    _root.addHandler(_sh)

    # Rotating file: 5 MB × 5 backups — lives next to logsaga.db
    _log_path = Path(_db_path(cfg)).parent / "logsaga.log"
    try:
        _fh = logging.handlers.RotatingFileHandler(
            str(_log_path), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        _fh.setFormatter(_plain_fmt)
        _root.addHandler(_fh)
    except OSError:
        pass  # Can't write log file — stdout only

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _fmt_log,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # SQLite engine
    db_path = _db_path(cfg)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)

    # On first startup: copy env-var config into AppSetting so it can be
    # managed via the UI without requiring a Docker env-var change.
    _maybe_migrate_env_config(cfg, engine)
    # Merge DB-stored config on top of env vars (DB values win).
    cfg = _load_effective_settings(cfg, engine)

    app.state.engine = engine
    app.state.settings = cfg

    # Asyncio synchronisation objects used to trigger an immediate library sync
    # from the poll loop when the file count changes.
    app.state.library_sync_requested = asyncio.Event()
    app.state.library_sync_lock = asyncio.Lock()

    # Set to True during a restore operation so background loops skip their
    # iteration and don't hold database connections during the DB file swap.
    app.state.restore_in_progress = False

    # Kodi connection state — stored on app.state so the config API can reset
    # .conn to force a reconnect when credentials change.
    app.state.poll_kodi_state = _KodiState()
    app.state.library_kodi_state = _KodiState()

    # Mount artwork static files now that cfg is available
    # (Cannot call app.mount inside lifespan cleanly — done at module level below)

    # Background tasks — handles stored on app.state so the config API can
    # cancel and restart MQTT/cast tasks when their settings change.
    app.state.mqtt_task: Optional[asyncio.Task] = None
    app.state.cast_task: Optional[asyncio.Task] = None

    tasks = []
    poll_task = asyncio.create_task(_poll_loop(app))
    tasks.append(poll_task)

    library_task = asyncio.create_task(_library_sync_loop(app))
    tasks.append(library_task)

    if cfg.tmdb_enabled:
        cast_task = asyncio.create_task(_cast_sync_loop(app))
        app.state.cast_task = cast_task
        tasks.append(cast_task)

    if cfg.mqtt_enabled:
        mqtt_task = asyncio.create_task(run_mqtt_publisher(cfg, engine))
        app.state.mqtt_task = mqtt_task
        tasks.append(mqtt_task)

    logger.info(
        "logsaga_started",
        db_path=db_path,
        poll_seconds=cfg.poll_seconds,
        library_sync_hours=cfg.library_sync_hours,
    )

    yield

    # Shutdown
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    engine.dispose()
    logger.info("logsaga_stopped")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="LogSAGA",
    description="Self-hosted watch history for Kodi.",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api")

# Artwork static files — use env var directly so module import works without all env vars set
_artwork_dir = os.environ.get("ARTWORK_DIR", "/config/artwork")
app.mount("/art", StaticFiles(directory=_artwork_dir, check_dir=False), name="art")

# Frontend static build (Phase 4+): only mount if dist/ exists.
# Assets are served as a StaticFiles mount; all other unknown paths are
# handled by a catch-all route that returns index.html so the React SPA
# handles routing (fixes 404 on hard refresh of deep URLs like /items/123).
_frontend_dist = Path(__file__).parent.parent / "dist"
if _frontend_dist.exists():
    _assets_dir = _frontend_dist / "assets"
    if _assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(_assets_dir)),
            name="frontend-assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:  # noqa: F811
        return FileResponse(str(_frontend_dist / "index.html"))
