"""
Backfill logic tests.

These test the session builder's rebuild and backfill paths using
in-memory SQLite. No live Kodi DB needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from logsaga.config import Settings
from logsaga.models import (
    Base,
    CompletionReason,
    KodiObservation,
    KodiState,
    MediaItem,
    MediaType,
    ObservationKind,
    SessionSource,
    SessionStatus,
    WatchSession,
)
from logsaga.sessions.builder import rebuild_all, rebuild_for_item, build_incremental
from logsaga.sessions.rules import ObsRow, SessionSettings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db_session(engine):
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        yield session


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        kodi_db_host="127.0.0.1",
        kodi_db_password="test",
        kodi_db_name="MyVideos131",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_item(db, title="Test Episode", show_title="Test Show", season=1, episode=1,
               runtime_s=1320, media_type="episode") -> MediaItem:
    item = MediaItem(
        media_type=media_type,
        title=title,
        show_title=show_title,
        season=season,
        episode=episode,
        runtime_s=runtime_s,
        kodi_state=KodiState.unwatched,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(item)
    db.flush()
    return item


def _make_obs(db, item: MediaItem, kind: ObservationKind,
              offset_minutes: int = 0,
              resume_pos_s=None, resume_total_s=None,
              play_count=None) -> KodiObservation:
    base = datetime(2026, 11, 4, 10, 0, 0)
    from datetime import timedelta
    at = base + timedelta(minutes=offset_minutes)
    obs = KodiObservation(
        media_item_id=item.id,
        kodi_file_id=item.kodi_file_id or 999,
        kind=kind,
        observed_at=at,
        kodi_last_played=at.strftime("%Y-%m-%d %H:%M:%S"),
        kodi_play_count=play_count,
        resume_pos_s=resume_pos_s,
        resume_total_s=resume_total_s,
    )
    db.add(obs)
    db.flush()
    return obs


# ---------------------------------------------------------------------------
# rebuild_for_item tests
# ---------------------------------------------------------------------------
class TestRebuildForItem:
    def test_rebuild_creates_session_from_observations(self, db_session, test_settings):
        item = _make_item(db_session)
        _make_obs(db_session, item, ObservationKind.resume_set, 0,
                  resume_pos_s=100.0, resume_total_s=1320.0)
        _make_obs(db_session, item, ObservationKind.playcount_up, 20, play_count=1)

        count = rebuild_for_item(item.id, db_session, test_settings)
        assert count == 1

        db_session.flush()
        sessions = db_session.query(WatchSession).filter_by(media_item_id=item.id).all()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].completion_reason == CompletionReason.kodi_playcount

    def test_rebuild_idempotent(self, db_session, test_settings):
        """Calling rebuild_for_item twice should produce the same result."""
        item = _make_item(db_session)
        _make_obs(db_session, item, ObservationKind.playcount_up, 0, play_count=1)

        rebuild_for_item(item.id, db_session, test_settings)
        db_session.flush()
        rebuild_for_item(item.id, db_session, test_settings)
        db_session.flush()

        sessions = db_session.query(WatchSession).filter_by(media_item_id=item.id).all()
        assert len(sessions) == 1

    def test_rebuild_no_observations_returns_zero(self, db_session, test_settings):
        item = _make_item(db_session)
        count = rebuild_for_item(item.id, db_session, test_settings)
        assert count == 0


# ---------------------------------------------------------------------------
# rebuild_all tests
# ---------------------------------------------------------------------------
class TestRebuildAll:
    def test_rebuild_all_processes_all_items(self, db_session, test_settings):
        item1 = _make_item(db_session, title="Ep1", episode=1)
        item2 = _make_item(db_session, title="Ep2", episode=2)
        _make_obs(db_session, item1, ObservationKind.playcount_up, 0, play_count=1)
        _make_obs(db_session, item2, ObservationKind.playcount_up, 0, play_count=1)
        db_session.commit()

        items_count, sessions_count = rebuild_all(db_session, test_settings)
        assert items_count == 2
        assert sessions_count == 2


# ---------------------------------------------------------------------------
# build_incremental tests
# ---------------------------------------------------------------------------
class TestBuildIncremental:
    def test_incremental_adds_to_existing_sessions(self, db_session, test_settings):
        item = _make_item(db_session)

        # Initial observation — creates one open session
        obs1 = _make_obs(db_session, item, ObservationKind.resume_set, 0,
                         resume_pos_s=100.0, resume_total_s=1320.0)
        build_incremental(item.id, [obs1], db_session, test_settings)
        db_session.flush()

        sessions = db_session.query(WatchSession).filter_by(media_item_id=item.id).all()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.open

        # New observation completes the session
        obs2 = _make_obs(db_session, item, ObservationKind.playcount_up, 20, play_count=1)
        build_incremental(item.id, [obs2], db_session, test_settings)
        db_session.flush()

        sessions = db_session.query(WatchSession).filter_by(media_item_id=item.id).all()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.completed

    def test_incremental_no_obs_returns_zero(self, db_session, test_settings):
        item = _make_item(db_session)
        result = build_incremental(item.id, [], db_session, test_settings)
        assert result == 0
