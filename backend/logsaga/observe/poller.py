"""
Poll loop — diffs Kodi file state and writes kodi_observation rows.

The public entry point is `poll_once(conn, db_session, settings)`.
It is synchronous (runs in a thread) and handles its own SQLite transaction.

Observation kinds emitted (see ObservationKind enum):
  resume_set       — resume point appeared for a file that had none
  resume_moved     — resume position changed by > 0.5 s
  resume_cleared   — resume point was removed
  lastplayed_changed — lastPlayed field changed (always fires on stop/completion)
  playcount_up     — playCount increased
  playcount_cleared  — playCount went to NULL or 0 (Kodi "mark as unwatched")
  file_removed     — file row disappeared from Kodi's DB (library clean)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pymysql
from sqlalchemy.orm import Session

from logsaga.config import Settings
from logsaga.kodi.db import (
    CheapCheck,
    FileState,
    cheap_check,
    fetch_file_snapshot,
    identify_file,
)
from logsaga.models import (
    KodiFileState,
    KodiObservation,
    KodiState,
    MediaItem,
    MediaType,
    ObservationKind,
    SyncState,
)


# ---------------------------------------------------------------------------
# CheapCheck cache — stored between polls to skip full snapshot when unchanged
# ---------------------------------------------------------------------------
@dataclass
class _PollState:
    last_cheap: Optional[CheapCheck] = None


_poll_state = _PollState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resume_changed(old: Optional[float], new: Optional[float]) -> bool:
    """Return True if resume position changed by more than 0.5 s."""
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(old - new) > 0.5


def _upsert_media_item(
    db: Session,
    file_id: int,
    conn: pymysql.Connection,
) -> Optional[MediaItem]:
    """
    Find or create a MediaItem for the given Kodi file_id.

    Returns the MediaItem, or None if the file is not a movie or episode.
    """
    # First check if we already have this file tracked
    item = db.query(MediaItem).filter_by(kodi_file_id=file_id).first()
    if item:
        return item

    # Try to identify the file from Kodi
    identity = identify_file(conn, file_id)
    if identity is None:
        return None  # extra / trailer / orphan

    year: Optional[int] = None
    if identity.release_date:
        try:
            year = int(identity.release_date[:4])
        except (ValueError, IndexError):
            pass

    item = MediaItem(
        media_type=identity.media_type,
        title=identity.title,
        show_title=identity.show_title,
        season=identity.season,
        episode=identity.episode,
        year=year,
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
        created_at=_now_utc(),
        updated_at=_now_utc(),
    )
    db.add(item)
    db.flush()  # get the id assigned
    return item


def _update_kodi_state(item: MediaItem, new_snap: FileState) -> None:
    """Update the item's kodi_state based on current file state."""
    if new_snap.play_count and new_snap.play_count > 0:
        if new_snap.resume_pos_s and new_snap.resume_pos_s > 0:
            item.kodi_state = KodiState.in_progress
        else:
            item.kodi_state = KodiState.watched
    elif new_snap.resume_pos_s and new_snap.resume_pos_s > 0:
        item.kodi_state = KodiState.in_progress
    else:
        item.kodi_state = KodiState.unwatched
    item.updated_at = _now_utc()


# ---------------------------------------------------------------------------
# Diff logic — pure, no I/O (also used by tests)
# ---------------------------------------------------------------------------
def diff_file_states(
    old: Optional[KodiFileState],
    new: Optional[FileState],
    old_snap_dict: dict[int, FileState],
    file_id: int,
) -> list[ObservationKind]:
    """
    Compare old (SQLite snapshot) vs new (Kodi poll) state for one file.

    Returns a list of ObservationKind values describing what changed.
    The list is empty if nothing changed.
    """
    kinds: list[ObservationKind] = []

    if new is None:
        # File disappeared from Kodi (library clean)
        if old is not None:
            kinds.append(ObservationKind.file_removed)
        return kinds

    old_play_count = old.play_count if old else None
    old_last_played = old.last_played if old else None
    old_resume_pos = old.resume_pos_s if old else None
    old_resume_total = old.resume_total_s if old else None

    new_play_count = new.play_count
    new_last_played = new.last_played
    new_resume_pos = new.resume_pos_s
    new_resume_total = new.resume_total_s

    # playCount changes
    if new_play_count is not None and old_play_count is None:
        # File appeared with a playCount — counts as a new play
        if new_play_count > 0:
            kinds.append(ObservationKind.playcount_up)
    elif new_play_count is not None and old_play_count is not None:
        if new_play_count > old_play_count:
            kinds.append(ObservationKind.playcount_up)
        elif new_play_count == 0 and old_play_count > 0:
            kinds.append(ObservationKind.playcount_cleared)
    elif new_play_count is None and old_play_count is not None and old_play_count > 0:
        kinds.append(ObservationKind.playcount_cleared)

    # lastPlayed changes
    if new_last_played != old_last_played and new_last_played:
        kinds.append(ObservationKind.lastplayed_changed)

    # Resume point changes
    had_resume = old_resume_pos is not None and old_resume_pos > 0
    has_resume = new_resume_pos is not None and new_resume_pos > 0

    if not had_resume and has_resume:
        kinds.append(ObservationKind.resume_set)
    elif had_resume and not has_resume:
        kinds.append(ObservationKind.resume_cleared)
    elif had_resume and has_resume:
        if _resume_changed(old_resume_pos, new_resume_pos) or _resume_changed(
            old_resume_total, new_resume_total
        ):
            kinds.append(ObservationKind.resume_moved)

    return kinds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def poll_once(
    conn: pymysql.Connection,
    db: Session,
    settings: Settings,
) -> list[KodiObservation]:
    """
    Run one poll cycle:
      1. Cheap check — skip full snapshot if nothing changed.
      2. Fetch full file snapshot from Kodi.
      3. Diff against kodi_file_state in SQLite.
      4. For new files: identify and upsert media_item.
      5. Append kodi_observation rows.
      6. Update kodi_file_state snapshot.
      7. Update sync_state.

    Returns the list of KodiObservation rows written.
    All writes happen in one SQLite transaction (committed on return).
    """
    observed_at = _now_utc()

    # Step 1: cheap check
    try:
        current_cheap = cheap_check(conn)
    except Exception:
        current_cheap = None

    if current_cheap is not None and _poll_state.last_cheap is not None:
        if (
            current_cheap.max_played == _poll_state.last_cheap.max_played
            and current_cheap.played_count == _poll_state.last_cheap.played_count
            and current_cheap.bookmark_count == _poll_state.last_cheap.bookmark_count
            and current_cheap.max_resume == _poll_state.last_cheap.max_resume
        ):
            # Nothing changed — update poll time and return
            _update_sync_state(db, observed_at, success=True, observations=0)
            db.commit()
            return []

    _poll_state.last_cheap = current_cheap

    # Step 2: fetch full snapshot
    new_snapshot = fetch_file_snapshot(conn)

    # Step 3: load old snapshot from SQLite
    old_states: dict[int, KodiFileState] = {
        row.kodi_file_id: row
        for row in db.query(KodiFileState).all()
    }

    # Find all file IDs to process (union of old and new)
    all_ids = set(old_states.keys()) | set(new_snapshot.keys())

    new_observations: list[KodiObservation] = []

    for file_id in all_ids:
        old = old_states.get(file_id)
        new = new_snapshot.get(file_id)

        kinds = diff_file_states(old, new, new_snapshot, file_id)
        if not kinds:
            continue

        # Step 4: identify file and upsert media_item
        item = _upsert_media_item(db, file_id, conn)
        if item is None:
            # Not a movie or episode — skip observations but still update state
            _upsert_file_state(db, file_id, new, old)
            continue

        # Update kodi_state on the media_item
        if new is not None:
            _update_kodi_state(item, new)
        elif ObservationKind.file_removed in kinds:
            item.kodi_state = KodiState.removed
            item.updated_at = _now_utc()

        # Step 5: write observations
        for kind in kinds:
            obs = KodiObservation(
                media_item_id=item.id,
                kodi_file_id=file_id,
                kind=kind,
                observed_at=observed_at,
                kodi_last_played=new.last_played if new else None,
                kodi_play_count=new.play_count if new else None,
                resume_pos_s=new.resume_pos_s if new else None,
                resume_total_s=new.resume_total_s if new else None,
            )
            db.add(obs)
            new_observations.append(obs)

        # Step 6: update kodi_file_state
        _upsert_file_state(db, file_id, new, old)

    # Step 7: update sync_state
    _update_sync_state(db, observed_at, success=True, observations=len(new_observations))

    db.commit()
    return new_observations


def _upsert_file_state(
    db: Session,
    file_id: int,
    new: Optional[FileState],
    old: Optional[KodiFileState],
) -> None:
    """Update or delete the kodi_file_state row for a file."""
    if new is None:
        # File removed from Kodi — remove from our snapshot too
        if old is not None:
            db.delete(old)
        return

    if old is None:
        state = KodiFileState(
            kodi_file_id=file_id,
            play_count=new.play_count,
            last_played=new.last_played,
            resume_pos_s=new.resume_pos_s,
            resume_total_s=new.resume_total_s,
            updated_at=_now_utc(),
        )
        db.add(state)
    else:
        old.play_count = new.play_count
        old.last_played = new.last_played
        old.resume_pos_s = new.resume_pos_s
        old.resume_total_s = new.resume_total_s
        old.updated_at = _now_utc()


def _update_sync_state(
    db: Session,
    poll_at: datetime,
    success: bool,
    observations: int,
    error: Optional[str] = None,
) -> None:
    """Upsert the single sync_state row."""
    state = db.get(SyncState, 1)
    if state is None:
        state = SyncState(
            id=1,
            poll_count=0,
            observation_count=0,
            error_count=0,
        )
        db.add(state)

    state.last_poll_at = poll_at
    state.poll_count += 1
    state.observation_count += observations

    if success:
        state.last_success_at = poll_at
    else:
        state.last_error = error
        state.last_error_at = poll_at
        state.error_count += 1
