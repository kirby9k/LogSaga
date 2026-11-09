"""
Session builder — applies session rules against observation history.

Three entry points:
  rebuild_for_item   — delete all sessions for one item, re-apply rules
  rebuild_all        — rebuild every item (used by `logsaga rebuild-sessions`)
  build_incremental  — add new observations to existing sessions (used by poll loop)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from logsaga.config import Settings
from logsaga.models import (
    KodiObservation,
    MediaItem,
    ObservationKind,
    SessionSource,
    SessionStatus,
    WatchSession,
)
from logsaga.sessions.rules import (
    ObsRow,
    SessionRow,
    SessionSettings,
    apply_observations,
    close_timed_out_sessions,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _settings_from_config(cfg: Settings, item: MediaItem) -> SessionSettings:
    return SessionSettings(
        min_started_seconds=cfg.min_started_seconds,
        session_gap_hours=cfg.session_gap_hours,
        watched_percent=cfg.watched_percent,
        watched_remaining_minutes_movie=cfg.watched_remaining_minutes_movie,
        watched_remaining_minutes_episode=cfg.watched_remaining_minutes_episode,
        is_movie=(item.media_type == "movie"),
    )


def _obs_to_row(obs: KodiObservation) -> ObsRow:
    return ObsRow(
        id=obs.id,
        kind=obs.kind,
        observed_at=obs.observed_at,
        kodi_play_count=obs.kodi_play_count,
        resume_pos_s=obs.resume_pos_s,
        resume_total_s=obs.resume_total_s,
    )


def _session_row_to_model(
    row: SessionRow,
    media_item_id: int,
    existing_model: Optional[WatchSession] = None,
) -> WatchSession:
    """Convert a SessionRow to a WatchSession ORM model."""
    if existing_model is not None:
        ws = existing_model
    else:
        ws = WatchSession(media_item_id=media_item_id)

    ws.started_at = row.started_at
    ws.last_activity_at = row.last_activity_at
    ws.ended_at = row.ended_at
    ws.completed_at = row.completed_at
    ws.max_position_s = row.max_position_s
    ws.runtime_s = row.runtime_s
    ws.percent = row.percent
    ws.status = row.status
    ws.completion_reason = row.completion_reason
    ws.source = row.source
    return ws


def _load_session_rows(db: Session, media_item_id: int) -> list[SessionRow]:
    """Load existing WatchSession rows as SessionRow dataclasses."""
    models = (
        db.query(WatchSession)
        .filter_by(media_item_id=media_item_id)
        .order_by(WatchSession.started_at)
        .all()
    )
    rows = []
    for ws in models:
        rows.append(
            SessionRow(
                id=ws.id,
                started_at=ws.started_at,
                last_activity_at=ws.last_activity_at,
                ended_at=ws.ended_at,
                completed_at=ws.completed_at,
                max_position_s=ws.max_position_s,
                runtime_s=ws.runtime_s,
                percent=ws.percent,
                status=ws.status,
                completion_reason=ws.completion_reason,
                source=ws.source,
                dirty=False,
            )
        )
    return rows


def _persist_sessions(
    db: Session,
    media_item_id: int,
    session_rows: list[SessionRow],
) -> None:
    """
    Write session_rows back to the database.

    - Rows with id=None are new and will be inserted.
    - Rows with dirty=True (and existing id) will be updated.
    - Rows not in session_rows that exist in DB are left alone (rebuild handles deletion).
    """
    # Map existing DB rows by id for fast lookup
    existing: dict[int, WatchSession] = {
        ws.id: ws
        for ws in db.query(WatchSession).filter_by(media_item_id=media_item_id).all()
    }

    for row in session_rows:
        if not row.dirty:
            continue
        if row.id is None:
            # New session
            ws = _session_row_to_model(row, media_item_id)
            db.add(ws)
        else:
            ws = existing.get(row.id)
            if ws:
                _session_row_to_model(row, media_item_id, ws)


def rebuild_for_item(
    media_item_id: int,
    db: Session,
    cfg: Settings,
) -> int:
    """
    Rebuild all sessions for a single media item.

    Deletes existing sessions, re-applies rules against all observations,
    then closes any still-open sessions.

    Returns the number of sessions created.
    """
    item = db.get(MediaItem, media_item_id)
    if item is None:
        return 0

    # Delete existing sessions
    db.query(WatchSession).filter_by(media_item_id=media_item_id).delete()

    # Load all observations, sorted oldest first
    observations = (
        db.query(KodiObservation)
        .filter_by(media_item_id=media_item_id)
        .order_by(KodiObservation.observed_at)
        .all()
    )
    if not observations:
        return 0

    obs_rows = [_obs_to_row(o) for o in observations]
    settings = _settings_from_config(cfg, item)
    runtime_s = item.runtime_s

    session_rows = apply_observations(
        obs_rows,
        [],
        settings,
        runtime_s=runtime_s,
        source=SessionSource.poll,
    )

    # Close any still-open sessions (timed out)
    close_timed_out_sessions(session_rows, _now_utc(), settings)

    for row in session_rows:
        row.dirty = True  # all are new after rebuild
        ws = _session_row_to_model(row, media_item_id)
        db.add(ws)

    return len(session_rows)


def rebuild_all(db: Session, cfg: Settings) -> tuple[int, int]:
    """
    Rebuild sessions for all media items.
    Returns (items_processed, sessions_created).
    """
    item_ids = [row[0] for row in db.query(MediaItem.id).all()]
    total_sessions = 0
    for item_id in item_ids:
        total_sessions += rebuild_for_item(item_id, db, cfg)
        db.flush()
    db.commit()
    return len(item_ids), total_sessions


def build_incremental(
    media_item_id: int,
    new_observations: list[KodiObservation],
    db: Session,
    cfg: Settings,
) -> int:
    """
    Add new observations to existing sessions for an item.

    Used by the poll loop — avoids a full rebuild on every poll.
    Returns the number of sessions modified or created.
    """
    if not new_observations:
        return 0

    item = db.get(MediaItem, media_item_id)
    if item is None:
        return 0

    settings = _settings_from_config(cfg, item)
    existing_rows = _load_session_rows(db, media_item_id)

    obs_rows = [_obs_to_row(o) for o in new_observations]

    # Close any sessions that timed out before the earliest new observation.
    # Use the first observation's timestamp as reference so historical/test
    # timestamps don't get incorrectly closed by actual current time.
    if obs_rows:
        earliest_obs_time = min(o.observed_at for o in obs_rows)
        close_timed_out_sessions(existing_rows, earliest_obs_time, settings)
    runtime_s = item.runtime_s

    updated_rows = apply_observations(
        obs_rows,
        existing_rows,
        settings,
        runtime_s=runtime_s,
        source=SessionSource.poll,
    )

    dirty_count = sum(1 for r in updated_rows if r.dirty)
    _persist_sessions(db, media_item_id, updated_rows)

    return dirty_count
