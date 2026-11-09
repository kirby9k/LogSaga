"""
Session rules — pure functions, no I/O, no database access.

The main entry point is `apply_observations()`. It takes a sorted list of
observations for a single media item and the existing sessions for that item,
and returns an updated list of sessions.

All rules are derived from confirmed Kodi behavior documented in
notes/kodi-behavior.md and CLAUDE.md "Session rules confirmed (2026-11-04)".

Rule summary:
  Open    — any activity starts a session
  Merge   — activity within SESSION_GAP_HOURS merges into the same session
  Complete (position)      — max_position >= WATCHED_PERCENT% of runtime
                             OR within credits window (WATCHED_REMAINING_MINUTES)
  Complete (kodi_playcount) — playcount_up seen during an active session
  Ignore  — session closed with max_position < MIN_STARTED_SECONDS and no completion
  Partial — session closed without reaching watched threshold
  Rewatch — new session only after SESSION_GAP_HOURS gap from last session
  Mark unwatched — playcount_cleared is recorded but never modifies sessions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from logsaga.models import (
    CompletionReason,
    ObservationKind,
    SessionSource,
    SessionStatus,
)


# ---------------------------------------------------------------------------
# Data structures (plain dataclasses, no SQLAlchemy dependency)
# ---------------------------------------------------------------------------
@dataclass
class ObsRow:
    """Lightweight representation of a kodi_observation row for rule processing."""
    id: int
    kind: ObservationKind
    observed_at: datetime
    kodi_play_count: Optional[int]
    resume_pos_s: Optional[float]
    resume_total_s: Optional[float]


@dataclass
class SessionRow:
    """Lightweight representation of a watch_session row for rule processing."""
    id: Optional[int]  # None for new sessions not yet persisted
    started_at: datetime
    last_activity_at: datetime
    ended_at: Optional[datetime]
    completed_at: Optional[datetime]
    max_position_s: Optional[float]
    runtime_s: Optional[int]
    percent: Optional[float]
    status: SessionStatus
    completion_reason: Optional[CompletionReason]
    source: SessionSource
    dirty: bool = field(default=True, compare=False)  # True = needs write


@dataclass
class SessionSettings:
    min_started_seconds: int = 120
    session_gap_hours: int = 6
    watched_percent: int = 90
    watched_remaining_minutes_movie: int = 8
    watched_remaining_minutes_episode: int = 3
    is_movie: bool = False  # determines which credits window to use


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _gap_hours(a: datetime, b: datetime) -> float:
    return abs((b - a).total_seconds()) / 3600.0


def _is_within_session_gap(session: SessionRow, observed_at: datetime, gap_hours: int) -> bool:
    """Return True if observed_at is within gap_hours of the session's last activity."""
    return _gap_hours(session.last_activity_at, observed_at) <= gap_hours


def _compute_percent(position_s: Optional[float], runtime_s: Optional[int]) -> Optional[float]:
    if position_s is None or runtime_s is None or runtime_s <= 0:
        return None
    return (position_s / runtime_s) * 100.0


def _advance_position(session: SessionRow, resume_pos_s: Optional[float]) -> None:
    """Move max_position_s forward (never backward)."""
    if resume_pos_s is not None and resume_pos_s > 0:
        if session.max_position_s is None or resume_pos_s > session.max_position_s:
            session.max_position_s = resume_pos_s
            session.percent = _compute_percent(resume_pos_s, session.runtime_s)


def _check_position_completion(
    session: SessionRow,
    settings: SessionSettings,
    at: datetime,
) -> bool:
    """
    Check if max_position qualifies the session as watched by our rules.

    Returns True and marks the session completed if the threshold is met.
    Does nothing and returns False if not.
    """
    if session.status == SessionStatus.completed:
        return True  # already completed

    pos = session.max_position_s
    runtime = session.runtime_s

    if pos is None or runtime is None or runtime <= 0:
        return False

    percent = (pos / runtime) * 100.0
    credits_minutes = (
        settings.watched_remaining_minutes_movie
        if settings.is_movie
        else settings.watched_remaining_minutes_episode
    )
    remaining_s = runtime - pos
    within_credits = remaining_s <= (credits_minutes * 60)

    if percent >= settings.watched_percent or within_credits:
        _mark_completed(session, CompletionReason.position, at)
        return True

    return False


def _mark_completed(
    session: SessionRow,
    reason: CompletionReason,
    at: datetime,
) -> None:
    if session.status != SessionStatus.completed:
        session.status = SessionStatus.completed
        session.completion_reason = reason
        session.completed_at = at
        session.ended_at = at
        session.dirty = True


def _close_session(session: SessionRow, at: datetime, settings: SessionSettings) -> None:
    """
    Close a session that has timed out (no activity for SESSION_GAP_HOURS).

    Final status:
      completed — if max_position met the threshold
      partial   — if started but not finished
      ignored   — if max_position < MIN_STARTED_SECONDS and never completed
    """
    if session.status == SessionStatus.completed:
        return  # already done

    # Check position threshold one last time
    if _check_position_completion(session, settings, at):
        return

    # Still open — decide final status
    pos = session.max_position_s or 0.0
    if pos < settings.min_started_seconds:
        session.status = SessionStatus.ignored
    else:
        session.status = SessionStatus.partial

    session.ended_at = at
    session.dirty = True


def _find_open_session(sessions: list[SessionRow]) -> Optional[SessionRow]:
    """Return the most recently active open session, or None."""
    open_sessions = [s for s in sessions if s.status == SessionStatus.open]
    if not open_sessions:
        return None
    return max(open_sessions, key=lambda s: s.last_activity_at)


def _find_mergeable_session(
    sessions: list[SessionRow],
    observed_at: datetime,
    gap_hours: int,
) -> Optional[SessionRow]:
    """
    Return an existing session that this activity should merge into.

    Per CLAUDE.md: any activity within SESSION_GAP_HOURS of a session's
    last_activity_at belongs to that session. This applies to open, completed,
    and partial sessions. Ignored sessions are never merged into.

    Why completed sessions: Kodi sometimes writes a playcount_up after the
    lastplayed_changed from the same watch; crash/resume may write a resume_cleared
    just after completion; lastplayed_changed may fire again on the same stop event.
    All these belong to the same viewing, not a new one.
    """
    candidates = [
        s for s in sessions
        if s.status != SessionStatus.ignored  # never merge into ignored sessions
        and _is_within_session_gap(s, observed_at, gap_hours)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.last_activity_at)


def _new_session(obs: ObsRow, source: SessionSource, runtime_s: Optional[int]) -> SessionRow:
    """Create a new open session from the first observation."""
    pos = obs.resume_pos_s if obs.resume_pos_s and obs.resume_pos_s > 0 else None
    pct = _compute_percent(pos, runtime_s)
    return SessionRow(
        id=None,
        started_at=obs.observed_at,
        last_activity_at=obs.observed_at,
        ended_at=None,
        completed_at=None,
        max_position_s=pos,
        runtime_s=runtime_s,
        percent=pct,
        status=SessionStatus.open,
        completion_reason=None,
        source=source,
        dirty=True,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def apply_observations(
    observations: list[ObsRow],
    existing_sessions: list[SessionRow],
    settings: SessionSettings,
    runtime_s: Optional[int] = None,
    source: SessionSource = SessionSource.poll,
) -> list[SessionRow]:
    """
    Apply a sorted (by observed_at ASC) list of observations for one item
    to the existing sessions for that item.

    Returns the full updated list of sessions (new + modified).
    Call the builder to persist any sessions with `dirty=True`.

    Does not modify observations — they are append-only.
    """
    # Work on a copy so callers can compare before/after if needed
    sessions = list(existing_sessions)
    if runtime_s is None and sessions:
        # Try to inherit runtime from existing sessions
        for s in sessions:
            if s.runtime_s:
                runtime_s = s.runtime_s
                break

    # Sort observations oldest-first
    obs_sorted = sorted(observations, key=lambda o: o.observed_at)

    for obs in obs_sorted:
        kind = obs.kind

        # Mark-unwatched observations: record only, never touch sessions
        if kind == ObservationKind.playcount_cleared:
            continue

        # File removed: close any open sessions, don't create new ones
        if kind == ObservationKind.file_removed:
            for s in sessions:
                if s.status == SessionStatus.open:
                    _close_session(s, obs.observed_at, settings)
            continue

        # --- Activity observation ---
        # First, close any sessions that have timed out before this observation
        for s in sessions:
            if s.status == SessionStatus.open:
                if not _is_within_session_gap(s, obs.observed_at, settings.session_gap_hours):
                    _close_session(s, s.last_activity_at, settings)

        # Try to merge into an existing open session
        session = _find_mergeable_session(sessions, obs.observed_at, settings.session_gap_hours)

        if session is None:
            # No mergeable session — open a new one
            session = _new_session(obs, source, runtime_s)
            sessions.append(session)
        else:
            session.dirty = True

        # Update position (resume observations carry the current position)
        if kind in (
            ObservationKind.resume_set,
            ObservationKind.resume_moved,
            ObservationKind.resume_cleared,
        ):
            _advance_position(session, obs.resume_pos_s)

        # Update last activity time
        if obs.observed_at > session.last_activity_at:
            session.last_activity_at = obs.observed_at

        # Update runtime if we now have a better value
        if obs.resume_total_s and obs.resume_total_s > 0:
            new_runtime = int(obs.resume_total_s)
            if session.runtime_s is None or new_runtime > session.runtime_s:
                session.runtime_s = new_runtime
                runtime_s = new_runtime

        # playcount_up during an active session = Kodi says it's watched
        if kind == ObservationKind.playcount_up:
            _mark_completed(session, CompletionReason.kodi_playcount, obs.observed_at)
        else:
            # Check position-based completion after each activity
            _check_position_completion(session, settings, obs.observed_at)

    return sessions


def close_timed_out_sessions(
    sessions: list[SessionRow],
    now: datetime,
    settings: SessionSettings,
) -> list[SessionRow]:
    """
    Close any sessions that have been open with no activity for SESSION_GAP_HOURS.

    Call this periodically (e.g. once per poll) to finalise old sessions.
    Returns the same list (modified in place for dirty sessions).
    """
    for s in sessions:
        if s.status == SessionStatus.open:
            if _gap_hours(s.last_activity_at, now) > settings.session_gap_hours:
                _close_session(s, s.last_activity_at, settings)
    return sessions
