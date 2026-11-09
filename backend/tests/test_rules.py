"""
Session rule tests — all scenarios from CLAUDE.md.

Each test builds a timeline of ObsRow objects and asserts the resulting
SessionRow list matches expected behavior. No I/O, no database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytest

from logsaga.models import (
    CompletionReason,
    ObservationKind,
    SessionSource,
    SessionStatus,
)
from logsaga.sessions.rules import (
    ObsRow,
    SessionRow,
    SessionSettings,
    apply_observations,
    close_timed_out_sessions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_BASE = datetime(2026, 11, 4, 10, 0, 0)  # 2026-11-04 10:00:00


def _t(offset_minutes: int) -> datetime:
    return _BASE + timedelta(minutes=offset_minutes)


def _obs(
    kind: ObservationKind,
    offset_minutes: int,
    resume_pos_s: Optional[float] = None,
    resume_total_s: Optional[float] = None,
    play_count: Optional[int] = None,
    obs_id: int = 1,
) -> ObsRow:
    return ObsRow(
        id=obs_id,
        kind=kind,
        observed_at=_t(offset_minutes),
        kodi_play_count=play_count,
        resume_pos_s=resume_pos_s,
        resume_total_s=resume_total_s,
    )


_EPISODE_SETTINGS = SessionSettings(
    min_started_seconds=120,
    session_gap_hours=6,
    watched_percent=90,
    watched_remaining_minutes_movie=8,
    watched_remaining_minutes_episode=3,
    is_movie=False,
)

_MOVIE_SETTINGS = SessionSettings(
    min_started_seconds=120,
    session_gap_hours=6,
    watched_percent=90,
    watched_remaining_minutes_movie=8,
    watched_remaining_minutes_episode=3,
    is_movie=True,
)

EPISODE_RUNTIME = 1320  # 22 min episode
MOVIE_RUNTIME = 5400    # 90 min movie


# ---------------------------------------------------------------------------
# Scenario 1: Crash mid-episode, resume within 6 h → 1 session, stays open
# ---------------------------------------------------------------------------
class TestCrashAndResume:
    def test_crash_mid_episode_resume_within_gap(self):
        """
        Crash/resume addon writes position every 20s during play.
        Stop (crash): only lastPlayed. Resume shortly after: new resume_set.
        All activity within 6h → 1 session.
        """
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 1, resume_pos_s=120.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 2, resume_pos_s=140.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.lastplayed_changed, 3),  # stop / crash
            _obs(ObservationKind.resume_set, 60, resume_pos_s=140.0, resume_total_s=EPISODE_RUNTIME),  # resume
            _obs(ObservationKind.resume_moved, 61, resume_pos_s=200.0, resume_total_s=EPISODE_RUNTIME),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.open
        assert sessions[0].max_position_s == 200.0

    def test_crash_resume_max_position_advances(self):
        """max_position_s should be the highest position seen across all resume writes."""
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=50.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 1, resume_pos_s=70.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 2, resume_pos_s=90.0, resume_total_s=EPISODE_RUNTIME),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert sessions[0].max_position_s == 90.0


# ---------------------------------------------------------------------------
# Scenario 2: Stop at 50%, never return (>6h timeout) → 1 partial session
# ---------------------------------------------------------------------------
class TestStopAndTimeout:
    def test_stop_at_50_timeout(self):
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 1, resume_pos_s=660.0, resume_total_s=EPISODE_RUNTIME),  # 50%
            _obs(ObservationKind.lastplayed_changed, 2),  # stop
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)

        # Session is still open (hasn't timed out yet)
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.open

        # Simulate time passing — close timed-out sessions
        now = _t(2 + 6 * 60 + 1)  # 6h1m after the stop
        sessions = close_timed_out_sessions(sessions, now, _EPISODE_SETTINGS)
        assert sessions[0].status == SessionStatus.partial
        assert sessions[0].max_position_s == 660.0

    def test_short_play_becomes_ignored(self):
        """Play for <MIN_STARTED_SECONDS then stop → session becomes 'ignored'."""
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=30.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.lastplayed_changed, 1),  # stop at ~30s
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        now = _t(1 + 6 * 60 + 1)
        sessions = close_timed_out_sessions(sessions, now, _EPISODE_SETTINGS)
        assert sessions[0].status == SessionStatus.ignored


# ---------------------------------------------------------------------------
# Scenario 3: Watch to end via playcount_up → 1 completed session
# ---------------------------------------------------------------------------
class TestWatchToEnd:
    def test_playcount_up_completes_session(self):
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 5, resume_pos_s=600.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.lastplayed_changed, 20),
            _obs(ObservationKind.playcount_up, 20, play_count=1),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].completion_reason == CompletionReason.kodi_playcount

    def test_completion_via_position_threshold(self):
        """Reaching 90% of runtime (without playcount_up) counts as watched."""
        pos_90pct = int(EPISODE_RUNTIME * 0.9) + 1
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 10, resume_pos_s=float(pos_90pct),
                 resume_total_s=EPISODE_RUNTIME),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].completion_reason == CompletionReason.position

    def test_completion_via_credits_window_episode(self):
        """Within 3 min of end for an episode = completed."""
        credits_pos = EPISODE_RUNTIME - (3 * 60) + 10  # 10s inside the window
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.resume_moved, 10, resume_pos_s=float(credits_pos),
                 resume_total_s=EPISODE_RUNTIME),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].completion_reason == CompletionReason.position

    def test_completion_via_credits_window_movie(self):
        """Within 8 min of end for a movie = completed."""
        credits_pos = MOVIE_RUNTIME - (8 * 60) + 10
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=MOVIE_RUNTIME),
            _obs(ObservationKind.resume_moved, 10, resume_pos_s=float(credits_pos),
                 resume_total_s=MOVIE_RUNTIME),
        ]
        sessions = apply_observations(obs, [], _MOVIE_SETTINGS, runtime_s=MOVIE_RUNTIME)
        assert sessions[0].status == SessionStatus.completed


# ---------------------------------------------------------------------------
# Scenario 4: Finish, then Kodi marks watched again → still 1 session
# ---------------------------------------------------------------------------
class TestCompletedThenMarkedWatched:
    def test_playcount_after_completion_merges(self):
        """
        If Kodi writes playcount_up shortly after the episode finishes (within 6h),
        it should merge into the same session and not create a second one.
        """
        obs = [
            _obs(ObservationKind.resume_moved, 0, resume_pos_s=1200.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.lastplayed_changed, 1),
            _obs(ObservationKind.playcount_up, 1, play_count=1),
            # Kodi sometimes writes the playcount slightly after lastPlayed
            _obs(ObservationKind.lastplayed_changed, 2),
            _obs(ObservationKind.playcount_up, 2, play_count=1),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed


# ---------------------------------------------------------------------------
# Scenario 5: Mark watched from menu (playcount_up, no prior activity)
# ---------------------------------------------------------------------------
class TestMarkAsWatched:
    def test_mark_as_watched_creates_completed_session(self):
        """
        playcount_up with no resume activity = mark as watched from Kodi menu.
        (Also covers Device 2's natural completion with no crash/resume addon.)
        Should create one completed session with reason=kodi_playcount.
        """
        obs = [
            _obs(ObservationKind.lastplayed_changed, 0),
            _obs(ObservationKind.playcount_up, 0, play_count=1),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].completion_reason == CompletionReason.kodi_playcount

    def test_mark_as_watched_no_runtime_still_completed(self):
        """Even without runtime, playcount_up = completed."""
        obs = [_obs(ObservationKind.playcount_up, 0, play_count=1)]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=None)
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].completion_reason == CompletionReason.kodi_playcount


# ---------------------------------------------------------------------------
# Scenario 6: Mark unwatched after watching → sessions unchanged
# ---------------------------------------------------------------------------
class TestMarkAsUnwatched:
    def test_playcount_cleared_does_not_modify_sessions(self):
        """
        playcount_cleared (mark as unwatched) is recorded as an observation
        but must NOT change, reopen, or delete existing completed sessions.
        """
        # Existing completed session
        existing = [
            SessionRow(
                id=1,
                started_at=_t(0),
                last_activity_at=_t(20),
                ended_at=_t(20),
                completed_at=_t(20),
                max_position_s=1200.0,
                runtime_s=EPISODE_RUNTIME,
                percent=90.9,
                status=SessionStatus.completed,
                completion_reason=CompletionReason.kodi_playcount,
                source=SessionSource.poll,
                dirty=False,
            )
        ]

        # Kodi marks as unwatched later
        obs = [
            _obs(ObservationKind.playcount_cleared, 60, play_count=0),
            _obs(ObservationKind.resume_cleared, 60),
        ]
        sessions = apply_observations(obs, existing, _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)

        # Session count unchanged, still completed
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].id == 1  # same session, not replaced


# ---------------------------------------------------------------------------
# Scenario 7: Rewatch next day → 2 sessions
# ---------------------------------------------------------------------------
class TestRewatch:
    def test_rewatch_creates_new_session(self):
        """Activity >6h after a completed session should open a second session."""
        obs = [
            # First watch — completes
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.playcount_up, 20, play_count=1),
            # Rewatch the next day (>6h gap)
            _obs(ObservationKind.resume_set, 7 * 60, resume_pos_s=50.0,
                 resume_total_s=EPISODE_RUNTIME),  # 7h later
            _obs(ObservationKind.playcount_up, 7 * 60 + 20, play_count=2),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 2
        assert sessions[0].status == SessionStatus.completed
        assert sessions[1].status == SessionStatus.completed

    def test_no_rewatch_within_gap(self):
        """Activity within 6h of a completed session merges into it."""
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=100.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.playcount_up, 20, play_count=1),
            # Another activity 2h later — within the gap
            _obs(ObservationKind.lastplayed_changed, 120),  # 2h
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1


# ---------------------------------------------------------------------------
# Scenario 8: Resume tool writes same position every 20s — no duplicate obs
# ---------------------------------------------------------------------------
class TestResumeToolWrites:
    def test_many_resume_moved_fold_into_one_session(self):
        """
        20-second bookmark writes from crash/resume addon should all fold into
        one session. max_position_s advances; no extra sessions created.
        """
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=200.0,
                 resume_total_s=EPISODE_RUNTIME, obs_id=1),
        ]
        # Simulate 30 writes over 10 minutes (every 20s)
        for i in range(1, 31):
            obs.append(_obs(
                ObservationKind.resume_moved,
                i,  # 1-minute offsets for testing (actual: 20s)
                resume_pos_s=200.0 + i * 21.0,
                resume_total_s=EPISODE_RUNTIME,
                obs_id=i + 1,
            ))

        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1
        # max_position should be the last written value
        expected_max = 200.0 + 30 * 21.0
        assert sessions[0].max_position_s == expected_max


# ---------------------------------------------------------------------------
# Scenario 9: Short play <MIN_STARTED_SECONDS → session becomes ignored
# ---------------------------------------------------------------------------
class TestShortPlay:
    def test_very_short_play_ignored(self):
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=10.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.lastplayed_changed, 0),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        # Still open — hasn't timed out yet
        now = _t(0 + 6 * 60 + 1)
        sessions = close_timed_out_sessions(sessions, now, _EPISODE_SETTINGS)
        assert sessions[0].status == SessionStatus.ignored

    def test_short_play_with_no_resume_ignored(self):
        """lastplayed_changed alone (no resume) with max_pos=None → ignored after timeout."""
        obs = [_obs(ObservationKind.lastplayed_changed, 0)]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        now = _t(0 + 6 * 60 + 1)
        sessions = close_timed_out_sessions(sessions, now, _EPISODE_SETTINGS)
        assert sessions[0].status == SessionStatus.ignored


# ---------------------------------------------------------------------------
# Scenario 10: file_removed after watching → history kept
# ---------------------------------------------------------------------------
class TestFileRemoved:
    def test_file_removed_does_not_affect_completed_session(self):
        """When a file is removed from Kodi's library, existing sessions are kept."""
        existing = [
            SessionRow(
                id=1,
                started_at=_t(0),
                last_activity_at=_t(20),
                ended_at=_t(20),
                completed_at=_t(20),
                max_position_s=float(EPISODE_RUNTIME),
                runtime_s=EPISODE_RUNTIME,
                percent=100.0,
                status=SessionStatus.completed,
                completion_reason=CompletionReason.kodi_playcount,
                source=SessionSource.poll,
                dirty=False,
            )
        ]
        obs = [_obs(ObservationKind.file_removed, 100)]
        sessions = apply_observations(obs, existing, _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        # Session still exists and is still completed
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed

    def test_file_removed_closes_open_session(self):
        """An open session is closed when the file is removed."""
        obs = [
            _obs(ObservationKind.resume_set, 0, resume_pos_s=200.0, resume_total_s=EPISODE_RUNTIME),
            _obs(ObservationKind.file_removed, 10),
        ]
        sessions = apply_observations(obs, [], _EPISODE_SETTINGS, runtime_s=EPISODE_RUNTIME)
        assert len(sessions) == 1
        assert sessions[0].status in (
            SessionStatus.partial, SessionStatus.ignored, SessionStatus.completed
        )


# ---------------------------------------------------------------------------
# Scenario 11: Sequential binge (auto-advance) → N separate sessions
# ---------------------------------------------------------------------------
class TestSequentialBinge:
    def test_binge_creates_one_session_per_episode(self):
        """
        Auto-advancing through 5 episodes, each completes via playcount_up.
        Each episode should get its own session.
        """
        # Each episode is 1440s (24 min). Gaps between completions = ~1296s (~90%).
        all_obs = []
        t = 0
        for ep in range(5):
            # Each episode: lastplayed + playcount_up
            all_obs.append(_obs(ObservationKind.lastplayed_changed, t, obs_id=ep * 2 + 1))
            all_obs.append(_obs(ObservationKind.playcount_up, t, play_count=ep + 1,
                                obs_id=ep * 2 + 2))
            t += 1296 // 60  # ~21 min gap (convert to minutes for _t)

        # Apply to a fresh session list; each episode is a separate media_item
        # so we apply per-episode (each call gets a fresh sessions=[])
        total_sessions = []
        for ep in range(5):
            ep_obs = all_obs[ep * 2: ep * 2 + 2]
            sessions = apply_observations(ep_obs, [], _EPISODE_SETTINGS, runtime_s=1440)
            total_sessions.extend(sessions)

        assert len(total_sessions) == 5
        for s in total_sessions:
            assert s.status == SessionStatus.completed
