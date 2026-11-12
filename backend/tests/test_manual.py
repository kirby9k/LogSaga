"""
Tests for manual watch history (POST /api/watched) and related endpoints:
- GET /api/search  (local-only mode; no TMDB key)
- GET /api/recently-viewed
- POST /api/watched  (existing local items, duplicate skipping, missing TMDB)
- _has_duplicate_session dedup logic
- Batch season add: items without air_date are handled correctly
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from logsaga.api.deps import get_db, get_tmdb_client
from logsaga.api.manual import _has_duplicate_session, create_manual_sessions
from logsaga.api.schemas import WatchedItemIn
from logsaga.main import app
from logsaga.models import (
    Base,
    CompletionReason,
    KodiShow,
    KodiState,
    MediaItem,
    MediaType,
    RecentlyViewed,
    SessionSource,
    SessionStatus,
    WatchSession,
)


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite
# ---------------------------------------------------------------------------
@pytest.fixture(scope="function")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="function")
def db_session(engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        yield session


@pytest.fixture(scope="function")
def client(engine, db_session) -> Generator[TestClient, None, None]:
    SessionLocal = sessionmaker(bind=engine)

    def override_db():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.state.engine = engine
    from logsaga.config import get_settings
    app.state.settings = get_settings()

    yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 11, 12, 15, 0, 0)


def _make_movie(
    db: Session,
    title: str = "Test Movie",
    tmdb_id: str = "12345",
    kodi_state: KodiState = KodiState.watched,
) -> MediaItem:
    item = MediaItem(
        media_type=MediaType.movie,
        title=title,
        year=2020,
        runtime_s=5400,
        tmdb_id=tmdb_id,
        kodi_state=kodi_state,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(item)
    db.flush()
    return item


def _make_show(
    db: Session,
    title: str = "Test Show",
    tmdb_id: str = "99999",
) -> KodiShow:
    show = KodiShow(
        title=title,
        kodi_idShow=1,
        tmdb_id=tmdb_id,
        kodi_state=KodiState.watched,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(show)
    db.flush()
    return show


def _make_episode(
    db: Session,
    show: KodiShow,
    season: int = 1,
    episode: int = 1,
    title: str = "Test Episode",
    release_date: str | None = "2020-01-15",
) -> MediaItem:
    item = MediaItem(
        media_type=MediaType.episode,
        title=title,
        show_title=show.title,
        show_id=show.id,
        season=season,
        episode=episode,
        tmdb_id=show.tmdb_id,
        release_date=release_date,
        runtime_s=1320,
        kodi_state=KodiState.unwatched,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(item)
    db.flush()
    return item


def _make_completed_session(db: Session, item: MediaItem, offset_hours: float = 0) -> WatchSession:
    base = _NOW - timedelta(hours=offset_hours)
    ws = WatchSession(
        media_item_id=item.id,
        started_at=base - timedelta(hours=1),
        last_activity_at=base,
        ended_at=base,
        completed_at=base,
        runtime_s=item.runtime_s,
        percent=100.0,
        status=SessionStatus.completed,
        completion_reason=CompletionReason.kodi_playcount,
        source=SessionSource.poll,
    )
    db.add(ws)
    db.flush()
    return ws


# ---------------------------------------------------------------------------
# _has_duplicate_session unit tests
# ---------------------------------------------------------------------------
class TestHasDuplicateSession:
    def test_no_session_returns_false(self, db_session):
        movie = _make_movie(db_session)
        db_session.commit()
        assert not _has_duplicate_session(db_session, movie.id, _NOW)

    def test_session_within_window_returns_true(self, db_session):
        movie = _make_movie(db_session)
        _make_completed_session(db_session, movie, offset_hours=0)
        db_session.commit()
        # Watch at same time as existing session
        assert _has_duplicate_session(db_session, movie.id, _NOW)

    def test_session_outside_window_returns_false(self, db_session):
        movie = _make_movie(db_session)
        _make_completed_session(db_session, movie, offset_hours=0)
        db_session.commit()
        # Watch 3 hours later — outside the 1-hour window
        future = _NOW + timedelta(hours=3)
        assert not _has_duplicate_session(db_session, movie.id, future)

    def test_partial_session_not_counted_as_duplicate(self, db_session):
        movie = _make_movie(db_session)
        ws = _make_completed_session(db_session, movie)
        ws.status = SessionStatus.partial
        ws.completed_at = None
        db_session.commit()
        assert not _has_duplicate_session(db_session, movie.id, _NOW)

    def test_timezone_aware_watched_at(self, db_session):
        movie = _make_movie(db_session)
        _make_completed_session(db_session, movie, offset_hours=0)
        db_session.commit()
        # Pass a timezone-aware datetime — should be normalised to UTC
        aware = _NOW.replace(tzinfo=timezone.utc)
        assert _has_duplicate_session(db_session, movie.id, aware)


# ---------------------------------------------------------------------------
# create_manual_sessions unit tests
# ---------------------------------------------------------------------------
class TestCreateManualSessions:
    @pytest.mark.asyncio
    async def test_creates_session_for_existing_movie(self, db_session):
        movie = _make_movie(db_session, tmdb_id="111")
        db_session.commit()

        req = WatchedItemIn(media_type="movie", tmdb_id=111, watched_at=_NOW)
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 1
        assert result.skipped == 0
        assert result.errors == []

        sessions = db_session.query(WatchSession).all()
        assert len(sessions) == 1
        assert sessions[0].source == SessionSource.manual
        assert sessions[0].completion_reason == CompletionReason.manual
        assert sessions[0].status == SessionStatus.completed
        assert sessions[0].percent == 100.0

    @pytest.mark.asyncio
    async def test_skips_duplicate_movie_session(self, db_session):
        movie = _make_movie(db_session, tmdb_id="222")
        _make_completed_session(db_session, movie)
        db_session.commit()

        req = WatchedItemIn(media_type="movie", tmdb_id=222, watched_at=_NOW)
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 0
        assert result.skipped == 1
        # Still only 1 session (the original)
        assert db_session.query(WatchSession).count() == 1

    @pytest.mark.asyncio
    async def test_missing_movie_without_tmdb_client_adds_error(self, db_session):
        db_session.commit()

        req = WatchedItemIn(media_type="movie", tmdb_id=9999, watched_at=_NOW)
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 0
        assert result.skipped == 1
        assert len(result.errors) == 1
        assert "TMDB not configured" in result.errors[0]

    @pytest.mark.asyncio
    async def test_creates_session_for_existing_episode(self, db_session):
        show = _make_show(db_session, tmdb_id="5050")
        ep = _make_episode(db_session, show, season=1, episode=3)
        db_session.commit()

        req = WatchedItemIn(
            media_type="episode",
            tmdb_id=5050,
            season=1,
            episode=3,
            watched_at=_NOW,
        )
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 1
        assert result.errors == []
        ws = db_session.query(WatchSession).filter_by(media_item_id=ep.id).first()
        assert ws is not None
        assert ws.source == SessionSource.manual

    @pytest.mark.asyncio
    async def test_episode_missing_season_raises_error(self, db_session):
        db_session.commit()

        req = WatchedItemIn(media_type="episode", tmdb_id=1234, watched_at=_NOW)
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 0
        assert result.skipped == 1
        assert "season and episode are required" in result.errors[0]

    @pytest.mark.asyncio
    async def test_unknown_media_type_adds_error(self, db_session):
        db_session.commit()

        req = WatchedItemIn(media_type="audiobook", tmdb_id=1, watched_at=_NOW)
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 0
        assert result.skipped == 1
        assert "Unknown media_type" in result.errors[0]

    @pytest.mark.asyncio
    async def test_batch_multiple_episodes(self, db_session):
        show = _make_show(db_session, tmdb_id="7070")
        eps = [_make_episode(db_session, show, season=1, episode=i) for i in range(1, 6)]
        db_session.commit()

        reqs = [
            WatchedItemIn(media_type="episode", tmdb_id=7070, season=1, episode=i, watched_at=_NOW)
            for i in range(1, 6)
        ]
        result = await create_manual_sessions(
            db=db_session,
            items=reqs,
            tmdb_client=None,
            schedule_artwork=None,
        )

        assert result.created == 5
        assert result.skipped == 0
        assert db_session.query(WatchSession).count() == 5

    @pytest.mark.asyncio
    async def test_calls_schedule_artwork_for_new_items(self, db_session):
        """When a new MediaItem is created via TMDB, schedule_artwork should be called."""
        db_session.commit()

        mock_tmdb = AsyncMock()
        mock_tmdb.movie_detail.return_value = {
            "title": "TMDB Movie",
            "overview": "A test movie",
            "release_date": "2020-05-01",
            "runtime": 120,
            "genres": [],
            "credits": {"crew": []},
        }

        artwork_calls = []
        def track_artwork(item_id):
            artwork_calls.append(item_id)

        req = WatchedItemIn(media_type="movie", tmdb_id=55555, watched_at=_NOW)
        result = await create_manual_sessions(
            db=db_session,
            items=[req],
            tmdb_client=mock_tmdb,
            schedule_artwork=track_artwork,
        )

        assert result.created == 1
        assert len(artwork_calls) == 1


# ---------------------------------------------------------------------------
# GET /api/search
# ---------------------------------------------------------------------------
class TestSearch:
    def test_search_local_only(self, client, db_session):
        """With no TMDB key, search returns only local library results."""
        movie = _make_movie(db_session, title="Inception", tmdb_id="27205")
        db_session.commit()

        resp = client.get("/api/search", params={"q": "inception"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tmdb_enabled"] is False
        assert len(data["results"]) >= 1
        titles = [r["title"] for r in data["results"]]
        assert "Inception" in titles
        # All results should be local
        for r in data["results"]:
            assert r["source"] == "local"

    def test_search_no_match(self, client, db_session):
        db_session.commit()
        resp = client.get("/api/search", params={"q": "xqzjklmno123"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []

    def test_search_returns_episodes_and_movies(self, client, db_session):
        movie = _make_movie(db_session, title="Test Movie", tmdb_id="1111")
        show = _make_show(db_session, title="Test Show", tmdb_id="2222")
        ep = _make_episode(db_session, show, title="Test Episode")
        db_session.commit()

        resp = client.get("/api/search", params={"q": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) >= 2

    def test_search_requires_q_param(self, client):
        resp = client.get("/api/search")
        assert resp.status_code == 422

    def test_search_has_history_flag(self, client, db_session):
        movie = _make_movie(db_session, tmdb_id="3333")
        _make_completed_session(db_session, movie)
        db_session.commit()

        resp = client.get("/api/search", params={"q": "test"})
        assert resp.status_code == 200
        result = next(
            (r for r in resp.json()["results"] if r.get("tmdb_id") == "3333"),
            None,
        )
        # has_history should reflect that a session exists
        assert result is not None
        assert result["has_history"] is True


# ---------------------------------------------------------------------------
# GET /api/recently-viewed
# ---------------------------------------------------------------------------
class TestRecentlyViewed:
    def test_empty(self, client):
        resp = client.get("/api/recently-viewed")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_items(self, client, db_session):
        db_session.add(RecentlyViewed(
            media_type="movie",
            tmdb_id=12345,
            title="Interstellar",
            poster_url=None,
            viewed_at=_NOW,
        ))
        db_session.commit()

        resp = client.get("/api/recently-viewed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Interstellar"
        assert data[0]["media_type"] == "movie"
        assert data[0]["tmdb_id"] == 12345

    def test_ordered_newest_first(self, client, db_session):
        db_session.add(RecentlyViewed(
            media_type="movie", tmdb_id=1, title="Older",
            poster_url=None, viewed_at=_NOW - timedelta(hours=5),
        ))
        db_session.add(RecentlyViewed(
            media_type="tv", tmdb_id=2, title="Newer",
            poster_url=None, viewed_at=_NOW,
        ))
        db_session.commit()

        resp = client.get("/api/recently-viewed")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["title"] == "Newer"
        assert data[1]["title"] == "Older"

    def test_limit_param(self, client, db_session):
        for i in range(10):
            db_session.add(RecentlyViewed(
                media_type="movie", tmdb_id=i + 1, title=f"Movie {i}",
                poster_url=None, viewed_at=_NOW - timedelta(hours=i),
            ))
        db_session.commit()

        resp = client.get("/api/recently-viewed", params={"limit": 3})
        assert resp.status_code == 200
        assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# POST /api/watched
# ---------------------------------------------------------------------------
class TestPostWatched:
    def test_add_existing_movie(self, client, db_session):
        movie = _make_movie(db_session, title="Dune", tmdb_id="438631")
        db_session.commit()

        resp = client.post("/api/watched", json={
            "items": [{"media_type": "movie", "tmdb_id": 438631, "watched_at": "2026-11-01T20:00:00Z"}]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["skipped"] == 0

    def test_skip_duplicate(self, client, db_session):
        movie = _make_movie(db_session, title="Dune", tmdb_id="438631")
        _make_completed_session(db_session, movie)
        db_session.commit()

        resp = client.post("/api/watched", json={
            "items": [{"media_type": "movie", "tmdb_id": 438631, "watched_at": _NOW.isoformat() + "Z"}]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["skipped"] == 1

    def test_missing_movie_without_tmdb_key(self, client, db_session):
        db_session.commit()

        resp = client.post("/api/watched", json={
            "items": [{"media_type": "movie", "tmdb_id": 99999, "watched_at": "2026-11-01T20:00:00Z"}]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["skipped"] == 1
        assert len(data["errors"]) == 1

    def test_add_existing_episode(self, client, db_session):
        show = _make_show(db_session, tmdb_id="1399")
        ep = _make_episode(db_session, show, season=1, episode=1)
        db_session.commit()

        resp = client.post("/api/watched", json={
            "items": [{
                "media_type": "episode",
                "tmdb_id": 1399,
                "season": 1,
                "episode": 1,
                "watched_at": "2026-11-01T20:00:00Z",
            }]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["skipped"] == 0

    def test_batch_multiple_episodes(self, client, db_session):
        show = _make_show(db_session, tmdb_id="8888")
        for i in range(1, 7):
            _make_episode(db_session, show, season=1, episode=i)
        db_session.commit()

        reqs = [
            {
                "media_type": "episode",
                "tmdb_id": 8888,
                "season": 1,
                "episode": i,
                "watched_at": f"2026-01-0{i}T20:00:00Z",
            }
            for i in range(1, 7)
        ]
        resp = client.post("/api/watched", json={"items": reqs})
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 6
        assert data["skipped"] == 0

    def test_session_has_manual_source(self, client, db_session):
        movie = _make_movie(db_session, tmdb_id="77777")
        db_session.commit()

        client.post("/api/watched", json={
            "items": [{"media_type": "movie", "tmdb_id": 77777, "watched_at": "2026-11-01T20:00:00Z"}]
        })

        db_session.expire_all()
        ws = db_session.query(WatchSession).filter_by(media_item_id=movie.id).first()
        assert ws is not None
        assert ws.source == SessionSource.manual
        assert ws.completion_reason == CompletionReason.manual
