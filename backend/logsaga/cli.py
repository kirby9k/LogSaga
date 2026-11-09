"""
LogSAGA CLI — entry point: `logsaga`

Commands:
  doctor           Check DB connection, grants, schema, and app health
  migrate          Run Alembic migrations (upgrade head)
  backfill         Import existing play history as sessions (source=backfill)
  sync --once      Run one poll cycle
  rebuild-sessions Delete all sessions and rebuild from observations
  fetch-artwork    Download artwork for all (or one) media items
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

import httpx
import typer
from dotenv import load_dotenv
load_dotenv()  # populate os.environ from .env so envvar= options in typer pick up settings
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker

app = typer.Typer(
    name="logsaga",
    help="LogSAGA — media scrobbling and viewing history.",
    no_args_is_help=True,
)


def _get_settings():
    """Import settings lazily so the CLI can show help without a .env file."""
    from logsaga.config import get_settings
    return get_settings()


def _get_db_session(db_path: str = "logsaga.db") -> tuple:
    """Return (engine, SessionLocal) for the SQLite database."""
    from logsaga.models import Base
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)  # ensure tables exist
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
@app.command()
def doctor(
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """Check database connection, grants, schema health, and active settings."""
    cfg = _get_settings()
    ok = True

    typer.echo("=== LogSAGA Doctor ===\n")

    # --- MariaDB connection ---
    typer.echo("[1] Kodi MariaDB connection")
    try:
        from logsaga.kodi.db import connect, detect_db_name, _verify_grants
        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        typer.echo(f"    Connected to {cfg.kodi_db_host}:{cfg.kodi_db_port}")
        typer.echo(f"    Database: {db_name}")
        typer.echo(f"    User: {cfg.kodi_db_user}")

        # Row counts
        import pymysql.cursors
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM files")
            files_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM movie")
            movie_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM episode")
            ep_count = cur.fetchone()["n"]
            cur.execute(
                "SELECT COUNT(*) AS n FROM bookmark WHERE type = 1"
            )
            bm_count = cur.fetchone()["n"]
        typer.echo(f"    files: {files_count}  movies: {movie_count}  "
                   f"episodes: {ep_count}  active bookmarks: {bm_count}")
        conn.close()
        typer.echo("    [OK]\n")
    except Exception as exc:
        typer.echo(f"    [FAIL] {exc}\n", err=True)
        ok = False

    # --- SQLite ---
    typer.echo("[2] SQLite database")
    try:
        engine, SessionLocal = _get_db_session(db_path)
        with SessionLocal() as db:
            result = db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [r[0] for r in result]
        typer.echo(f"    Path: {db_path}")
        typer.echo(f"    Tables: {', '.join(sorted(tables)) or '(none yet)'}")
        typer.echo("    [OK]\n")
    except Exception as exc:
        typer.echo(f"    [FAIL] {exc}\n", err=True)
        ok = False

    # --- Active settings ---
    typer.echo("[3] Active session rule settings")
    typer.echo(f"    MIN_STARTED_SECONDS:              {cfg.min_started_seconds}")
    typer.echo(f"    SESSION_GAP_HOURS:                {cfg.session_gap_hours}")
    typer.echo(f"    WATCHED_PERCENT:                  {cfg.watched_percent}")
    typer.echo(f"    WATCHED_REMAINING_MINUTES_MOVIE:  {cfg.watched_remaining_minutes_movie}")
    typer.echo(f"    WATCHED_REMAINING_MINUTES_EPISODE:{cfg.watched_remaining_minutes_episode}")
    typer.echo(f"    POLL_SECONDS:                     {cfg.poll_seconds}")
    typer.echo(f"    TZ:                               {cfg.tz}\n")

    if ok:
        typer.echo("All checks passed.")
        raise typer.Exit(0)
    else:
        typer.echo("One or more checks FAILED.", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------
@app.command()
def migrate() -> None:
    """Run Alembic migrations (upgrade to head)."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
    )
    raise typer.Exit(result.returncode)


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------
@app.command()
def backfill(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print counts without committing."),
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """
    Import existing play history as sessions (source=backfill).

    Loads all files with playCount > 0 as completed sessions at their lastPlayed,
    and all current resume points as partial sessions.
    Idempotent: files already in media_item are skipped.
    """
    cfg = _get_settings()

    from logsaga.kodi.db import connect, detect_db_name, fetch_file_snapshot, identify_file
    from logsaga.models import (
        Base, KodiFileState, KodiObservation, MediaItem, ObservationKind,
        SessionSource, SessionStatus, WatchSession, CompletionReason, KodiState,
    )
    from logsaga.sessions.builder import rebuild_for_item

    db_name = detect_db_name(cfg)
    kodi_conn = connect(cfg, db_name=db_name)

    engine, SessionLocal = _get_db_session(db_path)

    # Fetch all played files from Kodi
    snapshot = fetch_file_snapshot(kodi_conn)

    completed_new = 0
    partial_new = 0
    skipped = 0

    with SessionLocal() as db:
        for file_id, state in snapshot.items():
            # Check if already tracked
            existing = db.query(MediaItem).filter_by(kodi_file_id=file_id).first()
            if existing:
                skipped += 1
                continue

            identity = identify_file(kodi_conn, file_id)
            if identity is None:
                continue

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Determine when the item was last played.
            # Kodi writes lastPlayed in client local time (TZ env var).
            # Convert to UTC so timestamps are consistent with the live poll loop.
            last_played_dt: Optional[datetime] = None
            if state.last_played:
                try:
                    local_tz = ZoneInfo(cfg.tz)
                    last_played_dt = (
                        datetime.strptime(state.last_played, "%Y-%m-%d %H:%M:%S")
                        .replace(tzinfo=local_tz)
                        .astimezone(timezone.utc)
                        .replace(tzinfo=None)
                    )
                except (ValueError, KeyError):
                    last_played_dt = now

            backfill_year: Optional[int] = None
            if identity.release_date:
                try:
                    backfill_year = int(identity.release_date[:4])
                except (ValueError, IndexError):
                    pass

            item = MediaItem(
                media_type=identity.media_type,
                title=identity.title,
                show_title=identity.show_title,
                season=identity.season,
                episode=identity.episode,
                year=backfill_year,
                plot=identity.plot,
                genres=identity.genres,
                runtime_s=identity.runtime_s,
                release_date=identity.release_date,
                director=identity.director,
                video_codec=identity.video_codec,
                video_width=identity.video_width,
                video_height=identity.video_height,
                hdr_type=identity.hdr_type,
                audio_codec=identity.audio_codec,
                audio_channels=identity.audio_channels,
                imdb_id=identity.imdb_id,
                tmdb_id=identity.tmdb_id,
                tvdb_id=identity.tvdb_id,
                kodi_file_id=file_id,
                kodi_media_id=identity.media_id,
                kodi_state=KodiState.unwatched,
                created_at=now,
                updated_at=now,
            )

            # Set kodi_state
            if state.play_count and state.play_count > 0:
                if state.resume_pos_s and state.resume_pos_s > 0:
                    item.kodi_state = KodiState.in_progress
                else:
                    item.kodi_state = KodiState.watched
            elif state.resume_pos_s and state.resume_pos_s > 0:
                item.kodi_state = KodiState.in_progress

            db.add(item)
            db.flush()

            # Also save the kodi_file_state snapshot
            kfs = KodiFileState(
                kodi_file_id=file_id,
                play_count=state.play_count,
                last_played=state.last_played,
                resume_pos_s=state.resume_pos_s,
                resume_total_s=state.resume_total_s,
                updated_at=now,
            )
            db.add(kfs)

            # Create backfill session
            started = last_played_dt or now

            if state.play_count and state.play_count > 0 and not (
                state.resume_pos_s and state.resume_pos_s > 0
            ):
                # Completed item — create a completed session at lastPlayed
                ws = WatchSession(
                    media_item_id=item.id,
                    started_at=started,
                    last_activity_at=started,
                    ended_at=started,
                    completed_at=started,
                    max_position_s=identity.runtime_s,
                    runtime_s=identity.runtime_s,
                    percent=100.0 if identity.runtime_s else None,
                    status=SessionStatus.completed,
                    completion_reason=CompletionReason.kodi_playcount,
                    source=SessionSource.backfill,
                )
                db.add(ws)
                completed_new += 1
            elif state.resume_pos_s and state.resume_pos_s > 0:
                # In-progress item — create a partial session
                runtime = state.resume_total_s or identity.runtime_s
                pct = None
                if state.resume_pos_s and runtime and runtime > 0:
                    pct = (state.resume_pos_s / runtime) * 100.0
                ws = WatchSession(
                    media_item_id=item.id,
                    started_at=started,
                    last_activity_at=started,
                    ended_at=None,
                    completed_at=None,
                    max_position_s=state.resume_pos_s,
                    runtime_s=int(runtime) if runtime else None,
                    percent=pct,
                    status=SessionStatus.partial,
                    completion_reason=None,
                    source=SessionSource.backfill,
                )
                db.add(ws)
                partial_new += 1

        typer.echo(f"Backfill summary:")
        typer.echo(f"  Completed sessions to create: {completed_new}")
        typer.echo(f"  Partial sessions to create:   {partial_new}")
        typer.echo(f"  Already tracked (skipped):    {skipped}")

        if dry_run:
            typer.echo("\n--dry-run: not committing.")
            db.rollback()
        else:
            db.commit()
            typer.echo(f"\nDone. {completed_new + partial_new} sessions created.")

    kodi_conn.close()


# ---------------------------------------------------------------------------
# backfill-prior-plays
# ---------------------------------------------------------------------------
@app.command(name="backfill-prior-plays")
def backfill_prior_plays(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print counts without committing."),
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """
    Create placeholder sessions for plays that happened before the backfill.

    For each item where Kodi's cached playCount is greater than the number of
    sessions already recorded, creates the missing sessions with:
      started_at = 1970-01-01 (sentinel for "date unknown")
      source     = backfill_prior
      status     = completed

    Uses only the locally cached kodi_file_state — no Kodi connection needed.
    Idempotent: counts existing sessions before creating new ones.
    """
    from sqlalchemy import func
    from logsaga.models import (
        CompletionReason, KodiFileState, MediaItem, SessionSource,
        SessionStatus, WatchSession,
    )

    SENTINEL = datetime(1970, 1, 1, 0, 0, 0)

    _, SessionLocal = _get_db_session(db_path)

    with SessionLocal() as db:
        # Join media_item → kodi_file_state to find items with play_count > 0
        rows = (
            db.query(MediaItem, KodiFileState)
            .join(KodiFileState, KodiFileState.kodi_file_id == MediaItem.kodi_file_id)
            .filter(KodiFileState.play_count > 0)
            .all()
        )

        total_items = 0
        total_to_create = 0

        for item, state in rows:
            existing = (
                db.query(func.count(WatchSession.id))
                .filter(WatchSession.media_item_id == item.id)
                .scalar()
            ) or 0
            missing = state.play_count - existing
            if missing <= 0:
                continue
            total_items += 1
            total_to_create += missing

        typer.echo(f"Items needing prior-play sessions: {total_items}")
        typer.echo(f"Placeholder sessions to create:    {total_to_create}")

        if dry_run:
            typer.echo("\n--dry-run: not committing.")
            return

        if total_to_create == 0:
            typer.echo("\nNothing to do.")
            return

        created = 0
        for item, state in rows:
            existing = (
                db.query(func.count(WatchSession.id))
                .filter(WatchSession.media_item_id == item.id)
                .scalar()
            ) or 0
            missing = state.play_count - existing
            for _ in range(missing):
                ws = WatchSession(
                    media_item_id=item.id,
                    started_at=SENTINEL,
                    last_activity_at=SENTINEL,
                    ended_at=SENTINEL,
                    completed_at=SENTINEL,
                    max_position_s=float(item.runtime_s) if item.runtime_s else None,
                    runtime_s=item.runtime_s,
                    percent=100.0 if item.runtime_s else None,
                    status=SessionStatus.completed,
                    completion_reason=CompletionReason.kodi_playcount,
                    source=SessionSource.backfill_prior,
                )
                db.add(ws)
                created += 1

        db.commit()
        typer.echo(f"\nDone. {created} placeholder session(s) created.")


# ---------------------------------------------------------------------------
# sync --once
# ---------------------------------------------------------------------------
@app.command(name="sync")
def sync_once(
    once: bool = typer.Option(True, "--once", help="Run one poll cycle (required)."),
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """Run one poll cycle and print the observations generated."""
    cfg = _get_settings()

    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.observe.poller import poll_once
    from logsaga.sessions.builder import build_incremental

    db_name = detect_db_name(cfg)
    kodi_conn = connect(cfg, db_name=db_name)

    _, SessionLocal = _get_db_session(db_path)

    with SessionLocal() as db:
        observations = poll_once(kodi_conn, db, cfg)

        if not observations:
            typer.echo("No changes detected.")
        else:
            typer.echo(f"{len(observations)} observation(s):")
            # Group by media_item_id
            by_item: dict = {}
            for obs in observations:
                by_item.setdefault(obs.media_item_id, []).append(obs)

            for item_id, obs_list in by_item.items():
                from logsaga.models import MediaItem
                item = db.get(MediaItem, item_id)
                label = item.title if item else f"item_id={item_id}"
                if item and item.show_title:
                    label = f"{item.show_title} S{item.season:02d}E{item.episode:02d} \"{item.title}\""
                typer.echo(f"\n  {label}")
                for obs in obs_list:
                    typer.echo(f"    [{obs.kind}] at {obs.observed_at}")

                # Build incremental sessions for this item
                build_incremental(item_id, obs_list, db, cfg)

        db.commit()

    kodi_conn.close()


# ---------------------------------------------------------------------------
# sync-library
# ---------------------------------------------------------------------------

@app.command(name="sync-library")
def sync_library_cmd(
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print counts without committing."),
) -> None:
    """
    Full library sync: upsert all Kodi movies, episodes, and shows into the
    local database. Marks items no longer in Kodi as removed (kodi_state=removed).

    Idempotent — safe to run multiple times. Does NOT touch watch history.
    """
    cfg = _get_settings()

    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.library.sync import sync_library

    db_name = detect_db_name(cfg)
    typer.echo(f"Connecting to {db_name}...")
    kodi_conn = connect(cfg, db_name=db_name)

    _, SessionLocal = _get_db_session(db_path)

    with SessionLocal() as db:
        result = sync_library(kodi_conn, db, cfg)

        if dry_run:
            db.rollback()
            typer.echo("[dry-run] rolled back")
        else:
            # sync_library already committed
            pass

    kodi_conn.close()

    typer.echo(f"Shows:    {result.shows_new} new, {result.shows_updated} updated, {result.shows_removed} removed")
    typer.echo(f"Movies:   {result.movies_new} new, {result.movies_updated} updated, {result.movies_removed} removed")
    typer.echo(f"Episodes: {result.episodes_new} new, {result.episodes_updated} updated, {result.episodes_removed} removed")
    typer.echo(f"Total:    {result.total_new} new, {result.total_updated} updated, {result.total_removed} removed")


# ---------------------------------------------------------------------------
# enrich-metadata
# ---------------------------------------------------------------------------
@app.command(name="enrich-metadata")
def enrich_metadata(
    batch_size: int = typer.Option(100, "--batch-size", help="Items per batch (default 100)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print counts without committing."),
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
    force: bool = typer.Option(False, "--force", help="Re-enrich all items, not just incomplete ones."),
) -> None:
    """
    Backfill plot, year, genres, director and technical fields for existing
    MediaItems that were created before migration 0003.

    Idempotent: items where both plot and year are already set are skipped.
    Safe to run multiple times. Use --dry-run to preview counts first.
    Use --force to re-enrich all items (e.g. after a genres column fix).
    """
    import time
    from sqlalchemy import or_
    from logsaga.models import MediaItem

    cfg = _get_settings()
    _, SessionLocal = _get_db_session(db_path)

    with SessionLocal() as db:
        if force:
            already_complete = 0
            to_process = db.query(MediaItem).filter(
                MediaItem.kodi_media_id.isnot(None),
            ).count()
        else:
            already_complete = db.query(MediaItem).filter(
                MediaItem.kodi_media_id.isnot(None),
                MediaItem.plot.isnot(None),
                MediaItem.year.isnot(None),
            ).count()

            to_process = db.query(MediaItem).filter(
                MediaItem.kodi_media_id.isnot(None),
                or_(MediaItem.plot.is_(None), MediaItem.year.is_(None)),
            ).count()

        typer.echo(f"Items to enrich:          {to_process}")
        typer.echo(f"Already complete (skip):  {already_complete}")

        if dry_run:
            typer.echo("\n--dry-run: not connecting to Kodi or committing.")
            return

        if to_process == 0:
            typer.echo("\nNothing to do.")
            return

        from logsaga.kodi.db import connect, detect_db_name, enrich_item

        db_name = detect_db_name(cfg)
        kodi_conn = connect(cfg, db_name=db_name)

        enriched = 0
        not_found = 0

        try:
            items_q = db.query(MediaItem).filter(MediaItem.kodi_media_id.isnot(None))
            if not force:
                items_q = items_q.filter(
                    or_(MediaItem.plot.is_(None), MediaItem.year.is_(None))
                )
            items = items_q.order_by(MediaItem.id).all()

            for batch_start in range(0, to_process, batch_size):
                batch = items[batch_start : batch_start + batch_size]

                for item in batch:
                    identity = enrich_item(
                        kodi_conn,
                        item.media_type,
                        item.kodi_media_id,
                        item.kodi_file_id,
                    )

                    if identity is None:
                        not_found += 1
                        continue

                    item.plot = identity.plot
                    item.genres = identity.genres
                    item.release_date = identity.release_date
                    item.director = identity.director
                    item.video_codec = identity.video_codec
                    item.video_width = identity.video_width
                    item.video_height = identity.video_height
                    item.hdr_type = identity.hdr_type
                    item.audio_codec = identity.audio_codec
                    item.audio_channels = identity.audio_channels
                    if identity.release_date:
                        try:
                            item.year = int(identity.release_date[:4])
                        except (ValueError, IndexError):
                            pass

                    enriched += 1

                db.commit()

                processed_so_far = min(batch_start + batch_size, to_process)
                typer.echo(f"  {processed_so_far}/{to_process} processed...")

                if batch_start + batch_size < to_process:
                    time.sleep(0.1)

        finally:
            kodi_conn.close()

        typer.echo(f"\nDone.")
        typer.echo(f"  Enriched:              {enriched}")
        typer.echo(f"  Skipped (complete):    {already_complete}")
        typer.echo(f"  Not found in Kodi:     {not_found}")


# ---------------------------------------------------------------------------
# rebuild-sessions
# ---------------------------------------------------------------------------
@app.command(name="rebuild-sessions")
def rebuild_sessions(
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """Delete all watch_session rows and rebuild from observations."""
    from logsaga.models import WatchSession
    from logsaga.sessions.builder import rebuild_all

    cfg = _get_settings()
    _, SessionLocal = _get_db_session(db_path)

    with SessionLocal() as db:
        before = db.query(WatchSession).count()
        typer.echo(f"Sessions before rebuild: {before}")

        db.query(WatchSession).delete()
        db.flush()

        items_processed, sessions_created = rebuild_all(db, cfg)

        typer.echo(f"Items processed: {items_processed}")
        typer.echo(f"Sessions created: {sessions_created}")
        db.commit()


# ---------------------------------------------------------------------------
# fetch-artwork
# ---------------------------------------------------------------------------
_ARTWORK_BATCH_SIZE = 50  # progress report every N items


async def _bulk_fetch_async(
    item_ids: list,
    engine,
    kodi_conn,
    rpc,
    cfg,
    concurrency: int,
) -> int:
    """
    Download artwork for all items using async HTTP with bounded concurrency.

    Uses a semaphore to keep at most ``concurrency`` HTTP requests in-flight at
    once. Each item gets its own SQLAlchemy session so commits don't interfere
    across coroutines. The ``kodi_conn`` (PyMySQL) is shared safely because
    asyncio is single-threaded — only one coroutine executes synchronous code at
    a time (between await points).
    """
    from logsaga.artwork.cache import fetch_artwork_for_item_async
    from logsaga.models import MediaItem

    # autoflush=False: prevents SQLAlchemy from flushing pending changes (and
    # acquiring the SQLite write lock) automatically during db.query() calls.
    # Without this, _upsert_artwork's db.add() leaves pending changes that
    # trigger a flush on the next db.query() inside the loop — holding the write
    # lock while the coroutine suspends at "await client.get()", which blocks all
    # other coroutines that also need to write (db locked error).
    # With autoflush=False, the write lock is only held during the explicit
    # db.commit() at the end of each coroutine — a few milliseconds.
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    sem = asyncio.Semaphore(concurrency)
    total_items = len(item_ids)
    total_updated = 0
    completed = 0

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:

        async def fetch_one(iid: int) -> int:
            async with sem:
                with SessionLocal() as db:
                    item = db.get(MediaItem, iid)
                    if item is None:
                        return 0
                    try:
                        count = await fetch_artwork_for_item_async(
                            item, kodi_conn, rpc, db, cfg, client
                        )
                        db.commit()
                        return count
                    except Exception as exc:
                        db.rollback()
                        typer.echo(f"  [warn] item {iid}: {exc}", err=True)
                        return 0

        for coro in asyncio.as_completed([fetch_one(iid) for iid in item_ids]):
            count = await coro
            total_updated += count
            completed += 1
            if completed % _ARTWORK_BATCH_SIZE == 0 or completed == total_items:
                typer.echo(
                    f"  {completed}/{total_items} items processed, "
                    f"{total_updated} artwork rows so far"
                )

    return total_updated


# ---------------------------------------------------------------------------
# migrate-artwork-paths
# ---------------------------------------------------------------------------
@app.command(name="migrate-artwork-paths")
def migrate_artwork_paths(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print actions without writing."),
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """
    Migrate show-level artwork (tvshow.*, season.poster) from per-episode
    directories to canonical shows/<show_name>/ directories.

    New fetch-artwork runs already save directly to shows/. This command
    migrates files downloaded before the path change. Safe to run multiple
    times (idempotent). Use --dry-run to preview changes first.

    Old files are left in place after migration; remove them manually once
    you have confirmed the migration succeeded.
    """
    import shutil
    from sqlalchemy import or_
    from logsaga.artwork.cache import _show_title_safe, _art_type_safe
    from logsaga.models import Artwork, MediaItem

    cfg = _get_settings()
    artwork_dir = cfg.artwork_dir
    _, SessionLocal = _get_db_session(db_path)

    already_canonical = 0
    migrated = 0
    skipped_no_file = 0
    errors = 0

    with SessionLocal() as db:
        rows = (
            db.query(Artwork, MediaItem)
            .join(MediaItem, Artwork.media_item_id == MediaItem.id)
            .filter(
                or_(
                    Artwork.art_type.like("tvshow.%"),
                    Artwork.art_type == "season.poster",
                ),
                Artwork.local_path.isnot(None),
                MediaItem.show_title.isnot(None),
                MediaItem.media_type == "episode",
            )
            .all()
        )

        typer.echo(f"Rows to inspect: {len(rows)}")

        for art_row, item in rows:
            local_path: str = art_row.local_path  # type: ignore[assignment]

            # Already at canonical location
            if local_path.startswith("shows/") or local_path.startswith("shows\\"):
                already_canonical += 1
                continue

            # Compute canonical stem
            if art_row.art_type == "season.poster" and item.season is not None:
                stem = f"season_{item.season}_poster"
            else:
                stem = _art_type_safe(art_row.art_type)

            show_safe = _show_title_safe(item.show_title)
            old_abs = artwork_dir / local_path

            if not old_abs.exists():
                skipped_no_file += 1
                continue

            ext = old_abs.suffix
            new_rel = Path("shows") / show_safe / f"{stem}{ext}"
            new_abs = artwork_dir / new_rel

            # Compute new thumbnail path
            new_thumb_rel: Optional[str] = None
            new_thumb_abs: Optional[Path] = None
            if art_row.thumbnail_path:
                old_thumb = artwork_dir / art_row.thumbnail_path
                thumb_name = f"{stem}_thumb.webp"
                new_thumb_rel = str(Path("shows") / show_safe / thumb_name)
                new_thumb_abs = artwork_dir / new_thumb_rel

            if dry_run:
                typer.echo(f"  [dry-run] {local_path!r}  ->  {str(new_rel)!r}")
                migrated += 1
                continue

            try:
                new_abs.parent.mkdir(parents=True, exist_ok=True)
                if not new_abs.exists():
                    shutil.copy2(old_abs, new_abs)

                if new_thumb_abs is not None and art_row.thumbnail_path:
                    old_thumb = artwork_dir / art_row.thumbnail_path
                    if old_thumb.exists() and not new_thumb_abs.exists():
                        shutil.copy2(old_thumb, new_thumb_abs)

                art_row.local_path = str(new_rel)
                if new_thumb_rel is not None:
                    art_row.thumbnail_path = new_thumb_rel

                migrated += 1
            except Exception as exc:
                typer.echo(f"  [error] item {item.id} art_type={art_row.art_type}: {exc}", err=True)
                errors += 1

        typer.echo(f"\nResults:")
        typer.echo(f"  Already canonical:  {already_canonical}")
        typer.echo(f"  Migrated:           {migrated}")
        typer.echo(f"  Skipped (no file):  {skipped_no_file}")
        typer.echo(f"  Errors:             {errors}")

        if dry_run:
            typer.echo("\n--dry-run: not committing.")
        else:
            db.commit()
            typer.echo("\nDone. Old files left in place — remove manually after verifying.")


@app.command(name="fetch-artwork")
def fetch_artwork(
    item_id: Optional[int] = typer.Option(None, "--item-id", help="Fetch art for one item only."),
    concurrency: int = typer.Option(10, "--concurrency", help="Concurrent HTTP downloads for bulk fetch (default 10)."),
    db_path: str = typer.Option("logsaga.db", envvar="LOGSAGA_DB_PATH"),
) -> None:
    """Download artwork from Kodi and store locally for all (or one) media items."""
    cfg = _get_settings()

    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.kodi.jsonrpc import KodiJSONRPC
    from logsaga.artwork.cache import fetch_all_artwork
    from logsaga.models import MediaItem, WatchSession

    db_name = detect_db_name(cfg)
    kodi_conn = connect(cfg, db_name=db_name)
    rpc = KodiJSONRPC(cfg) if cfg.kodi_jsonrpc_url else None

    engine, SessionLocal = _get_db_session(db_path)

    try:
        if item_id is not None:
            # Single item — one transaction is fine
            with SessionLocal() as db:
                items_processed, artwork_updated = fetch_all_artwork(
                    kodi_conn, rpc, db, cfg, item_id=item_id
                )
                db.commit()
            typer.echo(f"Items processed: {items_processed}")
            typer.echo(f"Artwork rows updated: {artwork_updated}")
        else:
            # Bulk fetch — build the ordered item list, then run async downloads
            # with bounded concurrency so N HTTP requests run simultaneously.
            with SessionLocal() as db:
                # Order by most recent watch activity so recently played items
                # get artwork first. Items with no sessions sort to the end.
                item_ids = [
                    r[0] for r in
                    db.query(MediaItem.id)
                    .outerjoin(WatchSession, WatchSession.media_item_id == MediaItem.id)
                    .filter(MediaItem.kodi_media_id.isnot(None))
                    .group_by(MediaItem.id)
                    .order_by(func.max(WatchSession.last_activity_at).desc())
                    .all()
                ]

            total_items = len(item_ids)
            typer.echo(
                f"Fetching artwork for {total_items} items "
                f"(concurrency={concurrency}, progress every {_ARTWORK_BATCH_SIZE})..."
            )

            total_updated = asyncio.run(
                _bulk_fetch_async(item_ids, engine, kodi_conn, rpc, cfg, concurrency)
            )
            typer.echo(f"\nDone. {total_items} items processed, {total_updated} artwork rows updated.")
    finally:
        kodi_conn.close()
        if rpc:
            rpc.close()
