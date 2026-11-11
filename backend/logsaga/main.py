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
import os
from contextlib import asynccontextmanager, suppress

# Load .env into os.environ early so module-level os.environ.get() calls
# (artwork dir, db path) see the same values as pydantic-settings does.
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
# Kodi connection state (held across poll iterations)
# ---------------------------------------------------------------------------
class _KodiState:
    conn = None             # pymysql.Connection or None
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
    try:
        state.conn.ping(reconnect=True)
    except Exception:
        state.conn = None
        return _ensure_kodi_conn(state, cfg)

    return True


def _run_poll_cycle(engine, conn, cfg) -> tuple[int, list[int]]:
    """
    Run one full poll cycle (sync — called via asyncio.to_thread).

    1. poll_once → new observations
    2. For each affected item: build_incremental
    3. Close timed-out sessions
    4. Commit

    Returns (obs_count, new_item_ids) where new_item_ids are MediaItem IDs
    created for the first time during this cycle (need artwork fetch).
    """
    from logsaga.observe.poller import poll_once
    from logsaga.sessions.builder import build_incremental
    from logsaga.sessions.rules import SessionSettings, close_timed_out_sessions
    from logsaga.sessions.builder import _load_session_rows, _settings_from_config

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        # Record the cycle start time so we can find items created in this cycle.
        cycle_start = _now_utc()

        try:
            observations = poll_once(conn, db, cfg)
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
            return 0, []

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
        # can schedule artwork fetching for them without waiting for the next
        # library sync (which runs every 2 h by default).
        new_item_ids: list[int] = []
        if observations:
            new_rows = db.query(MediaItem.id).filter(
                MediaItem.created_at >= cycle_start
            ).all()
            new_item_ids = [r[0] for r in new_rows]

    return len(observations) if observations else 0, new_item_ids


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
        from logsaga.sessions.rules import SessionRow, SessionStatus as SS

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
        if updated:
            logger.info("auto_artwork_fill_done", artwork_rows=updated)
    except Exception as exc:
        logger.error("auto_artwork_fill_failed", error=str(exc))


async def _library_sync_loop(app: FastAPI) -> None:
    """
    Background task: run a full library sync every cfg.library_sync_hours.

    Uses a separate Kodi connection from the poll loop.
    """
    cfg = app.state.settings
    kodi = _KodiState()
    interval_s = int(cfg.library_sync_hours * 3600)

    # Initial sync after a short delay (let the poll loop start first)
    await asyncio.sleep(10)

    while True:
        try:
            connected = await asyncio.to_thread(_ensure_kodi_conn, kodi, cfg)
            if connected:
                has_new = await asyncio.to_thread(
                    _run_library_sync, app.state.engine, kodi.conn, cfg
                )
                # If new items were added, fill artwork for any items that
                # don't have it yet. Fire-and-forget; runs in background.
                if has_new:
                    asyncio.create_task(_fill_missing_artwork_bg(app))
            else:
                logger.warning("library_sync_skipped_no_kodi_conn")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("library_sync_loop_error", error=str(exc))

        try:
            await asyncio.sleep(interval_s)
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
    cfg = app.state.settings
    kodi = _KodiState()
    backoff = cfg.poll_seconds

    while True:
        try:
            await asyncio.sleep(cfg.poll_seconds)
            connected = await asyncio.to_thread(_ensure_kodi_conn, kodi, cfg)
            if not connected:
                logger.warning("kodi_db_unavailable", retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue

            backoff = cfg.poll_seconds  # reset on success
            new_obs, new_item_ids = await asyncio.to_thread(
                _run_poll_cycle, app.state.engine, kodi.conn, cfg
            )
            if new_obs > 0:
                logger.info("poll_cycle_complete", new_observations=new_obs)
            # Newly created items have no artwork yet — fetch it immediately
            # rather than waiting up to 2 h for the next library sync cycle.
            if new_item_ids:
                asyncio.create_task(_fetch_artwork_for_items_bg(app, new_item_ids))
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

    # Configure structlog
    import structlog
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(cfg.log_level)
        ),
    )

    # SQLite engine
    db_path = _db_path(cfg)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)

    app.state.engine = engine
    app.state.settings = cfg

    # Mount artwork static files now that cfg is available
    # (Cannot call app.mount inside lifespan cleanly — done at module level below)

    # Background tasks
    tasks = []
    poll_task = asyncio.create_task(_poll_loop(app))
    tasks.append(poll_task)

    library_task = asyncio.create_task(_library_sync_loop(app))
    tasks.append(library_task)

    if cfg.mqtt_enabled:
        mqtt_task = asyncio.create_task(run_mqtt_publisher(cfg, engine))
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
