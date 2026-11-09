"""
Poller unit tests — snapshot diff → observation kinds.

These tests use in-memory SQLite and mock Kodi connections.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from logsaga.kodi.db import FileState
from logsaga.models import (
    Base,
    CompletionReason,
    KodiFileState,
    KodiObservation,
    KodiState,
    MediaItem,
    MediaType,
    ObservationKind,
    SessionSource,
    SessionStatus,
    WatchSession,
)
from logsaga.observe.poller import diff_file_states, _update_kodi_state, _upsert_file_state
from logsaga.sessions.builder import build_incremental


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db_session():
    """In-memory SQLite session for tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# diff_file_states tests
# ---------------------------------------------------------------------------
class TestDiffFileStates:
    """Test the pure diff function that generates observation kinds."""

    def _old(
        self,
        file_id: int = 1,
        play_count=None,
        last_played=None,
        resume_pos_s=None,
        resume_total_s=None,
    ) -> KodiFileState:
        return KodiFileState(
            kodi_file_id=file_id,
            play_count=play_count,
            last_played=last_played,
            resume_pos_s=resume_pos_s,
            resume_total_s=resume_total_s,
            updated_at=datetime.utcnow(),
        )

    def _new(
        self,
        file_id: int = 1,
        play_count=None,
        last_played=None,
        resume_pos_s=None,
        resume_total_s=None,
    ) -> FileState:
        return FileState(
            file_id=file_id,
            play_count=play_count,
            last_played=last_played,
            resume_pos_s=resume_pos_s,
            resume_total_s=resume_total_s,
        )

    def test_no_change_no_observations(self):
        old = self._old(last_played="2026-01-01 10:00:00", play_count=1)
        new = self._new(last_played="2026-01-01 10:00:00", play_count=1)
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert kinds == []

    def test_new_file_with_playcount(self):
        """File appears for the first time with playCount=1 → playcount_up + lastplayed_changed."""
        new = self._new(play_count=1, last_played="2026-01-01 10:00:00")
        kinds = diff_file_states(None, new, {1: new}, 1)
        assert ObservationKind.playcount_up in kinds
        assert ObservationKind.lastplayed_changed in kinds

    def test_lastplayed_changed(self):
        old = self._old(play_count=1, last_played="2026-01-01 09:00:00")
        new = self._new(play_count=1, last_played="2026-01-01 10:00:00")
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.lastplayed_changed in kinds
        assert ObservationKind.playcount_up not in kinds

    def test_playcount_incremented(self):
        old = self._old(play_count=1, last_played="2026-01-01 09:00:00")
        new = self._new(play_count=2, last_played="2026-01-01 10:00:00")
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.playcount_up in kinds
        assert ObservationKind.lastplayed_changed in kinds

    def test_playcount_cleared(self):
        old = self._old(play_count=1, last_played="2026-01-01 09:00:00")
        new = self._new(play_count=0, last_played="2026-01-01 09:00:00")
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.playcount_cleared in kinds

    def test_resume_set(self):
        old = self._old(last_played="2026-01-01 09:00:00")
        new = self._new(last_played="2026-01-01 10:00:00", resume_pos_s=300.0, resume_total_s=1320.0)
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.resume_set in kinds
        assert ObservationKind.lastplayed_changed in kinds

    def test_resume_moved(self):
        old = self._old(last_played="2026-01-01 09:00:00", resume_pos_s=300.0, resume_total_s=1320.0)
        new = self._new(last_played="2026-01-01 09:00:00", resume_pos_s=320.0, resume_total_s=1320.0)
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.resume_moved in kinds
        assert ObservationKind.resume_set not in kinds

    def test_resume_no_move_below_threshold(self):
        """Position change < 0.5s is not a resume_moved."""
        old = self._old(resume_pos_s=300.0, resume_total_s=1320.0)
        new = self._new(resume_pos_s=300.3, resume_total_s=1320.0)
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.resume_moved not in kinds

    def test_resume_cleared(self):
        old = self._old(resume_pos_s=300.0, resume_total_s=1320.0)
        new = self._new(play_count=1, last_played="2026-01-01 10:00:00")
        kinds = diff_file_states(old, new, {1: new}, 1)
        assert ObservationKind.resume_cleared in kinds

    def test_file_removed(self):
        old = self._old(play_count=1, last_played="2026-01-01 09:00:00")
        kinds = diff_file_states(old, None, {}, 1)
        assert ObservationKind.file_removed in kinds

    def test_file_removed_no_prior_state(self):
        """File appears then disappears within the same session — no old state."""
        kinds = diff_file_states(None, None, {}, 1)
        assert kinds == []


# ---------------------------------------------------------------------------
# _upsert_file_state tests
# ---------------------------------------------------------------------------
class TestUpsertFileState:
    def test_insert_new_state(self, db_session):
        new = FileState(
            file_id=42,
            play_count=1,
            last_played="2026-01-01 10:00:00",
            resume_pos_s=None,
            resume_total_s=None,
        )
        _upsert_file_state(db_session, 42, new, None)
        db_session.flush()
        saved = db_session.get(KodiFileState, 42)
        assert saved is not None
        assert saved.play_count == 1
        assert saved.last_played == "2026-01-01 10:00:00"

    def test_update_existing_state(self, db_session):
        old = KodiFileState(
            kodi_file_id=42,
            play_count=1,
            last_played="2026-01-01 09:00:00",
            resume_pos_s=None,
            resume_total_s=None,
            updated_at=datetime.utcnow(),
        )
        db_session.add(old)
        db_session.flush()

        new = FileState(
            file_id=42,
            play_count=2,
            last_played="2026-01-01 10:00:00",
            resume_pos_s=None,
            resume_total_s=None,
        )
        _upsert_file_state(db_session, 42, new, old)
        db_session.flush()

        saved = db_session.get(KodiFileState, 42)
        assert saved.play_count == 2
        assert saved.last_played == "2026-01-01 10:00:00"

    def test_delete_on_file_removed(self, db_session):
        old = KodiFileState(
            kodi_file_id=42,
            play_count=1,
            last_played="2026-01-01 09:00:00",
            updated_at=datetime.utcnow(),
        )
        db_session.add(old)
        db_session.flush()

        _upsert_file_state(db_session, 42, None, old)
        db_session.flush()

        saved = db_session.get(KodiFileState, 42)
        assert saved is None


# ---------------------------------------------------------------------------
# History preservation regression tests
# ---------------------------------------------------------------------------
class TestHistoryPreservation:
    """
    Regression guard: existing WatchSessions must survive playcount_cleared
    and file_removed observations at the poller+builder pipeline level.

    Tests operate against in-memory SQLite + the full builder pipeline so
    any future change that accidentally deletes or rewrites sessions fails
    immediately.

    Timing:
      _SESSION_START  10:00  — session opened
      _SESSION_END    10:22  — last session activity (22-min episode)
      _EVENT_TIME     15:30  — observation arrives (5h 8m later, within 6h gap)
    """

    _FILE_ID = 99
    _SESSION_START = datetime(2026, 11, 4, 10, 0, 0)
    _SESSION_END   = datetime(2026, 11, 4, 10, 22, 0)
    _EVENT_TIME    = datetime(2026, 11, 4, 15, 30, 0)

    # --- helpers ---

    def _item(
        self,
        db,
        kodi_state: KodiState = KodiState.watched,
    ) -> MediaItem:
        now = datetime(2026, 11, 4, 9, 0, 0)
        item = MediaItem(
            media_type=MediaType.episode,
            title="Test Episode",
            show_title="Test Show",
            season=1,
            episode=1,
            runtime_s=1320,
            kodi_file_id=self._FILE_ID,
            kodi_state=kodi_state,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        db.flush()
        return item

    def _completed_session(self, db, media_item_id: int) -> WatchSession:
        ws = WatchSession(
            media_item_id=media_item_id,
            started_at=self._SESSION_START,
            last_activity_at=self._SESSION_END,
            ended_at=self._SESSION_END,
            completed_at=self._SESSION_END,
            max_position_s=1320.0,
            runtime_s=1320,
            percent=100.0,
            status=SessionStatus.completed,
            completion_reason=CompletionReason.kodi_playcount,
            source=SessionSource.poll,
        )
        db.add(ws)
        db.flush()
        return ws

    def _open_session(self, db, media_item_id: int, max_pos: float = 600.0) -> WatchSession:
        """Open session with last_activity at 10:10 — 5h20m before _EVENT_TIME, within 6h gap."""
        ws = WatchSession(
            media_item_id=media_item_id,
            started_at=self._SESSION_START,
            last_activity_at=datetime(2026, 11, 4, 10, 10, 0),
            max_position_s=max_pos,
            runtime_s=1320,
            status=SessionStatus.open,
            source=SessionSource.poll,
        )
        db.add(ws)
        db.flush()
        return ws

    def _obs(self, db, media_item_id: int, kind: ObservationKind) -> KodiObservation:
        obs = KodiObservation(
            media_item_id=media_item_id,
            kodi_file_id=self._FILE_ID,
            kind=kind,
            observed_at=self._EVENT_TIME,
        )
        db.add(obs)
        db.flush()
        return obs

    # --- playcount_cleared ---

    def test_playcount_cleared_kodi_state_updated_to_unwatched(self, db_session):
        """_update_kodi_state sets kodi_state=unwatched when play_count=0."""
        item = self._item(db_session)
        self._completed_session(db_session, item.id)

        new_snap = FileState(
            file_id=self._FILE_ID,
            play_count=0,
            last_played="2026-11-04 10:22:00",
            resume_pos_s=None,
            resume_total_s=None,
        )
        _update_kodi_state(item, new_snap)

        assert item.kodi_state == KodiState.unwatched

    def test_playcount_cleared_session_count_unchanged(self, db_session, test_settings):
        """build_incremental with playcount_cleared must not create or delete sessions."""
        item = self._item(db_session)
        self._completed_session(db_session, item.id)

        obs = self._obs(db_session, item.id, ObservationKind.playcount_cleared)
        build_incremental(item.id, [obs], db_session, test_settings)

        count = db_session.query(WatchSession).filter_by(media_item_id=item.id).count()
        assert count == 1

    def test_playcount_cleared_session_status_unchanged(self, db_session, test_settings):
        """Completed session keeps status=completed after playcount_cleared."""
        item = self._item(db_session)
        self._completed_session(db_session, item.id)

        obs = self._obs(db_session, item.id, ObservationKind.playcount_cleared)
        build_incremental(item.id, [obs], db_session, test_settings)

        ws = db_session.query(WatchSession).filter_by(media_item_id=item.id).first()
        assert ws.status == SessionStatus.completed
        assert ws.completion_reason == CompletionReason.kodi_playcount

    def test_playcount_cleared_repeated_does_not_duplicate_sessions(
        self, db_session, test_settings
    ):
        """Three successive mark-unwatched events leave exactly one session."""
        item = self._item(db_session)
        self._completed_session(db_session, item.id)

        for delta in range(3):
            obs = KodiObservation(
                media_item_id=item.id,
                kodi_file_id=self._FILE_ID,
                kind=ObservationKind.playcount_cleared,
                observed_at=self._EVENT_TIME + timedelta(hours=delta),
            )
            db_session.add(obs)
            db_session.flush()
            build_incremental(item.id, [obs], db_session, test_settings)

        count = db_session.query(WatchSession).filter_by(media_item_id=item.id).count()
        assert count == 1

    # --- file_removed ---

    def test_file_removed_kodi_state_updated_to_removed(self, db_session):
        """Poller sets kodi_state=removed on file_removed (direct assignment)."""
        item = self._item(db_session)
        self._completed_session(db_session, item.id)

        item.kodi_state = KodiState.removed
        db_session.flush()

        assert item.kodi_state == KodiState.removed

    def test_file_removed_preserves_completed_session(self, db_session, test_settings):
        """build_incremental with file_removed must not touch completed sessions."""
        item = self._item(db_session)
        self._completed_session(db_session, item.id)

        item.kodi_state = KodiState.removed
        obs = self._obs(db_session, item.id, ObservationKind.file_removed)
        build_incremental(item.id, [obs], db_session, test_settings)

        count = db_session.query(WatchSession).filter_by(media_item_id=item.id).count()
        assert count == 1
        ws = db_session.query(WatchSession).filter_by(media_item_id=item.id).first()
        assert ws.status == SessionStatus.completed

    def test_file_removed_closes_open_session_without_deleting(
        self, db_session, test_settings
    ):
        """file_removed closes an open session (→ partial) but does not delete it."""
        item = self._item(db_session, kodi_state=KodiState.in_progress)
        self._open_session(db_session, item.id, max_pos=600.0)

        item.kodi_state = KodiState.removed
        obs = self._obs(db_session, item.id, ObservationKind.file_removed)
        build_incremental(item.id, [obs], db_session, test_settings)
        db_session.flush()

        sessions = db_session.query(WatchSession).filter_by(media_item_id=item.id).all()
        assert len(sessions) == 1                           # row kept, not deleted
        assert sessions[0].status == SessionStatus.partial  # closed, not open


# ---------------------------------------------------------------------------
# _upsert_media_item field-population tests
# ---------------------------------------------------------------------------
class TestUpsertMediaItemFields:
    """
    Verify that _upsert_media_item stores all metadata fields (including
    the new T-02/T-03 ones) on the MediaItem it creates.
    """

    def test_all_metadata_fields_stored(self, db_session):
        from logsaga.kodi.db import MediaIdentity
        from logsaga.observe.poller import _upsert_media_item

        identity = MediaIdentity(
            media_type="movie",
            media_id=1,
            title="Test Movie",
            show_title=None,
            season=None,
            episode=None,
            runtime_s=5400,
            release_date="2024-06-15",
            imdb_id="tt1234567",
            tmdb_id="12345",
            tvdb_id=None,
            plot="A test movie about testing.",
            genres="Action / Comedy",
            director="Jane Director",
            video_codec="h264",
            video_width=1920,
            video_height=1080,
            hdr_type=None,
            audio_codec="aac",
            audio_channels=6,
        )

        mock_conn = MagicMock()

        with patch("logsaga.observe.poller.identify_file", return_value=identity):
            item = _upsert_media_item(db_session, 100, mock_conn)

        db_session.flush()

        assert item is not None
        assert item.title == "Test Movie"
        assert item.plot == "A test movie about testing."
        assert item.genres == "Action / Comedy"
        assert item.year == 2024
        assert item.release_date == "2024-06-15"
        assert item.director == "Jane Director"
        assert item.video_codec == "h264"
        assert item.video_width == 1920
        assert item.video_height == 1080
        assert item.hdr_type is None
        assert item.audio_codec == "aac"
        assert item.audio_channels == 6

    def test_missing_metadata_stored_as_none(self, db_session):
        """Fields absent in Kodi (empty plot, no director) are stored as None."""
        from logsaga.kodi.db import MediaIdentity
        from logsaga.observe.poller import _upsert_media_item

        identity = MediaIdentity(
            media_type="episode",
            media_id=2,
            title="Bare Episode",
            show_title="Some Show",
            season=1,
            episode=3,
            runtime_s=1320,
            release_date=None,
            imdb_id=None,
            tmdb_id=None,
            tvdb_id=None,
        )

        mock_conn = MagicMock()

        with patch("logsaga.observe.poller.identify_file", return_value=identity):
            item = _upsert_media_item(db_session, 101, mock_conn)

        db_session.flush()

        assert item is not None
        assert item.plot is None
        assert item.genres is None
        assert item.year is None
        assert item.release_date is None
        assert item.director is None
        assert item.video_codec is None
        assert item.video_width is None
        assert item.hdr_type is None
        assert item.audio_codec is None
        assert item.audio_channels is None


# ---------------------------------------------------------------------------
# enrich_item() tests
# ---------------------------------------------------------------------------
class TestEnrichItem:
    """
    Tests for kodi/db.py enrich_item() — queries Kodi by media_id and returns
    a fully populated MediaIdentity.  Uses a mock connection so no real Kodi
    DB is needed.
    """

    def _make_conn(self, main_row, stream_rows=None, id_rows=None):
        """Build a mock pymysql connection whose cursor returns canned results."""
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = main_row
        # fetchall is called once for streamdetails (if file_id given) and once
        # for unique_ids — supply them in order via side_effect.
        fetchall_results = []
        if stream_rows is not None:
            fetchall_results.append(stream_rows)
        if id_rows is not None:
            fetchall_results.append(id_rows)
        cursor.fetchall.side_effect = fetchall_results or [[]]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    def test_enrich_movie_returns_full_identity(self):
        from logsaga.kodi.db import enrich_item

        main_row = {
            "media_type": "movie", "media_id": 1,
            "title": "Test Movie", "show_title": None,
            "season": None, "episode": None,
            "runtime_s": "5400", "release_date": "2024-06-15",
            "plot": "A test plot.", "genres": "Action", "director": "Jane Director",
        }
        stream_rows = [
            {"iStreamType": 0, "strVideoCodec": "h264", "iVideoWidth": 1920,
             "iVideoHeight": 1080, "strHdrType": None, "iVideoDuration": 5400,
             "strAudioCodec": None, "iAudioChannels": None},
            {"iStreamType": 1, "strVideoCodec": None, "iVideoWidth": None,
             "iVideoHeight": None, "strHdrType": None, "iVideoDuration": None,
             "strAudioCodec": "aac", "iAudioChannels": 6},
        ]
        id_rows = [{"type": "imdb", "value": "tt1234567"}]

        conn = self._make_conn(main_row, stream_rows, id_rows)
        result = enrich_item(conn, "movie", 1, 100)

        assert result is not None
        assert result.title == "Test Movie"
        assert result.plot == "A test plot."
        assert result.genres == "Action"
        assert result.director == "Jane Director"
        assert result.release_date == "2024-06-15"
        assert result.video_codec == "h264"
        assert result.video_width == 1920
        assert result.video_height == 1080
        assert result.hdr_type is None
        assert result.audio_codec == "aac"
        assert result.audio_channels == 6
        assert result.imdb_id == "tt1234567"

    def test_enrich_item_not_found_returns_none(self):
        from logsaga.kodi.db import enrich_item

        conn = self._make_conn(main_row=None, id_rows=[])
        result = enrich_item(conn, "movie", 999, 100)
        assert result is None

    def test_enrich_item_no_file_id_skips_streamdetails(self):
        """kodi_file_id=None → streamdetails not fetched; metadata fields still set."""
        from logsaga.kodi.db import enrich_item

        main_row = {
            "media_type": "episode", "media_id": 5,
            "title": "An Episode", "show_title": "Show",
            "season": 1, "episode": 2,
            "runtime_s": None, "release_date": "2023-03-01",
            "plot": "Episode plot.", "genres": "Drama", "director": None,
        }
        id_rows = [{"type": "tvdb", "value": "12345"}]

        # No stream_rows passed — only id_rows in side_effect
        conn = self._make_conn(main_row, stream_rows=None, id_rows=id_rows)
        result = enrich_item(conn, "episode", 5, None)

        assert result is not None
        assert result.plot == "Episode plot."
        assert result.video_codec is None   # skipped
        assert result.tvdb_id == "12345"
