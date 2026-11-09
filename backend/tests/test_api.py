"""
FastAPI REST API tests.

Uses TestClient with dependency overrides to inject an in-memory SQLite DB.
Lifespan (background poll loop) is NOT started — we test routes in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from logsaga.api.deps import get_db
from logsaga.main import app
from logsaga.models import (
    Base,
    CompletionReason,
    KodiObservation,
    KodiShow,
    KodiState,
    MediaItem,
    ObservationKind,
    SessionSource,
    SessionStatus,
    SyncState,
    WatchSession,
)


# ---------------------------------------------------------------------------
# Fixtures
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
    """TestClient with in-memory DB injected via dependency override.

    Does NOT use the context manager so lifespan (and background poll tasks)
    are not started — we test routes in isolation.
    """
    SessionLocal = sessionmaker(bind=engine)

    def override_db():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    # Set app.state so routes that read settings work
    app.state.engine = engine
    from logsaga.config import get_settings
    app.state.settings = get_settings()

    yield TestClient(app, raise_server_exceptions=True)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime(2026, 11, 4, 12, 0, 0)


def _make_movie(db: Session, title="Test Movie", kodi_state=KodiState.watched) -> MediaItem:
    item = MediaItem(
        media_type="movie",
        title=title,
        year=2020,
        runtime_s=5400,
        kodi_state=kodi_state,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(item)
    db.flush()
    return item


def _make_or_get_show(db: Session, show_title: str) -> KodiShow:
    """Create or reuse a KodiShow row for the given show_title."""
    existing = db.query(KodiShow).filter(KodiShow.title == show_title).first()
    if existing:
        return existing
    show = KodiShow(
        title=show_title,
        kodi_idShow=hash(show_title) % 10000 + 1,
        kodi_state=KodiState.watched,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(show)
    db.flush()
    return show


def _make_episode(
    db: Session,
    title="Test Episode",
    show_title="Test Show",
    season=1,
    episode=1,
    kodi_state=KodiState.watched,
) -> MediaItem:
    show = _make_or_get_show(db, show_title)
    item = MediaItem(
        media_type="episode",
        title=title,
        show_title=show_title,
        show_id=show.id,
        season=season,
        episode=episode,
        runtime_s=1320,
        kodi_state=kodi_state,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(item)
    db.flush()
    return item


def _make_session(
    db: Session,
    item: MediaItem,
    status=SessionStatus.completed,
    offset_hours: float = 0,
    runtime_s: int = 5400,
) -> WatchSession:
    base = _now() - timedelta(hours=offset_hours)
    ws = WatchSession(
        media_item_id=item.id,
        started_at=base - timedelta(hours=1),
        last_activity_at=base,
        ended_at=base,
        completed_at=base if status == SessionStatus.completed else None,
        max_position_s=float(runtime_s),
        runtime_s=runtime_s,
        percent=100.0 if status == SessionStatus.completed else 50.0,
        status=status,
        completion_reason=CompletionReason.kodi_playcount if status == SessionStatus.completed else None,
        source=SessionSource.poll,
    )
    db.add(ws)
    db.flush()
    return ws


def _make_sync_state(db: Session, error: str = None) -> SyncState:
    sync = SyncState(
        id=1,
        kodi_db_name="MyVideos131",
        last_poll_at=_now(),
        last_success_at=_now(),
        last_error=error,
        poll_count=10,
        observation_count=25,
        error_count=0 if error is None else 1,
    )
    db.add(sync)
    db.flush()
    return sync


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_health_empty_db(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "error"  # no sync_state row

    def test_health_with_sync_state(self, client, db_session):
        _make_sync_state(db_session)
        db_session.commit()
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert data["kodi_db_name"] == "MyVideos131"
        assert data["poll_count"] == 10
        assert "settings" in data

    def test_health_with_error(self, client, db_session):
        sync = _make_sync_state(db_session, error="Connection refused")
        sync.last_success_at = None
        db_session.commit()
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert data["last_error"] == "Connection refused"


# ---------------------------------------------------------------------------
# /api/recent
# ---------------------------------------------------------------------------
class TestRecent:
    def test_recent_empty(self, client):
        resp = client.get("/api/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_recent_returns_sessions_newest_first(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, offset_hours=2)   # older
        _make_session(db_session, movie, offset_hours=0)   # newer
        db_session.commit()
        resp = client.get("/api/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        # First item should be the most recent (offset_hours=0)
        t0 = data["items"][0]["session"]["last_activity_at"]
        t1 = data["items"][1]["session"]["last_activity_at"]
        assert t0 >= t1

    def test_recent_excludes_ignored(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, status=SessionStatus.ignored)
        _make_session(db_session, movie, status=SessionStatus.completed)
        db_session.commit()
        resp = client.get("/api/recent")
        assert resp.json()["total"] == 1

    def test_recent_filter_by_type_movie(self, client, db_session):
        movie = _make_movie(db_session)
        ep = _make_episode(db_session)
        _make_session(db_session, movie)
        _make_session(db_session, ep)
        db_session.commit()
        resp = client.get("/api/recent?type=movie")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["item"]["media_type"] == "movie"

    def test_recent_filter_by_status_completed(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, status=SessionStatus.completed)
        _make_session(db_session, movie, status=SessionStatus.partial)
        db_session.commit()
        resp = client.get("/api/recent?status=completed")
        assert resp.json()["total"] == 1

    def test_recent_pagination(self, client, db_session):
        movie = _make_movie(db_session)
        for i in range(5):
            _make_session(db_session, movie, offset_hours=float(i))
        db_session.commit()
        resp = client.get("/api/recent?limit=2&offset=0")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# /api/sessions
# ---------------------------------------------------------------------------
class TestSessions:
    def test_sessions_search_by_title(self, client, db_session):
        m1 = _make_movie(db_session, title="Inception")
        m2 = _make_movie(db_session, title="Interstellar")
        _make_session(db_session, m1)
        _make_session(db_session, m2)
        db_session.commit()
        resp = client.get("/api/sessions?search=Inception")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["item"]["title"] == "Inception"

    def test_sessions_search_by_show(self, client, db_session):
        ep = _make_episode(db_session, show_title="Breaking Bad")
        _make_session(db_session, ep)
        db_session.commit()
        resp = client.get("/api/sessions?search=Breaking")
        assert resp.json()["total"] == 1

    def test_sessions_date_filter(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, offset_hours=0)   # 2026-11-04 12:00
        _make_session(db_session, movie, offset_hours=48)  # 2026-11-02 12:00
        db_session.commit()
        # Only sessions started on/after 2026-11-04
        resp = client.get("/api/sessions?date_from=2026-11-04T00:00:00")
        assert resp.json()["total"] == 1

    def test_sessions_pagination_limit_offset(self, client, db_session):
        movie = _make_movie(db_session)
        for i in range(3):
            _make_session(db_session, movie, offset_hours=float(i))
        db_session.commit()
        r1 = client.get("/api/sessions?limit=1&offset=0").json()
        r2 = client.get("/api/sessions?limit=1&offset=1").json()
        assert r1["total"] == 3
        assert len(r1["items"]) == 1
        # Different items
        assert r1["items"][0]["session"]["id"] != r2["items"][0]["session"]["id"]


# ---------------------------------------------------------------------------
# /api/in-progress
# ---------------------------------------------------------------------------
class TestInProgress:
    def test_in_progress_empty(self, client):
        resp = client.get("/api/in-progress")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_in_progress_returns_items(self, client, db_session):
        item = _make_episode(db_session, kodi_state=KodiState.in_progress)
        watched = _make_movie(db_session, kodi_state=KodiState.watched)
        db_session.commit()
        resp = client.get("/api/in-progress")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["item"]["id"] == item.id

    def test_in_progress_includes_percent(self, client, db_session):
        item = _make_episode(db_session, kodi_state=KodiState.in_progress)
        _make_session(db_session, item, status=SessionStatus.partial, runtime_s=1320)
        db_session.commit()
        resp = client.get("/api/in-progress")
        data = resp.json()
        assert data[0]["percent"] is not None


# ---------------------------------------------------------------------------
# /api/shows
# ---------------------------------------------------------------------------
class TestShows:
    def test_shows_empty(self, client):
        resp = client.get("/api/shows")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_shows_groups_episodes_by_show(self, client, db_session):
        ep1 = _make_episode(db_session, title="Ep1", show_title="Friends", episode=1)
        ep2 = _make_episode(db_session, title="Ep2", show_title="Friends", episode=2)
        ep3 = _make_episode(db_session, title="Ep1", show_title="Seinfeld", episode=1)
        _make_session(db_session, ep1)
        _make_session(db_session, ep2)
        _make_session(db_session, ep3)
        db_session.commit()
        resp = client.get("/api/shows")
        data = resp.json()
        titles = [s["show_title"] for s in data]
        assert "Friends" in titles
        assert "Seinfeld" in titles
        assert len(data) == 2

    def test_shows_watched_count(self, client, db_session):
        ep1 = _make_episode(db_session, title="E1", show_title="Fargo", episode=1)
        ep2 = _make_episode(db_session, title="E2", show_title="Fargo", episode=2)
        ep3 = _make_episode(db_session, title="E3", show_title="Fargo", episode=3,
                            kodi_state=KodiState.unwatched)
        _make_session(db_session, ep1, status=SessionStatus.completed)
        _make_session(db_session, ep2, status=SessionStatus.completed)
        _make_session(db_session, ep3, status=SessionStatus.partial)
        db_session.commit()
        resp = client.get("/api/shows?search=Fargo")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_episodes"] == 3
        assert data[0]["watched_episodes"] == 2

    def test_shows_movies_excluded(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie)
        db_session.commit()
        resp = client.get("/api/shows")
        assert resp.json() == []

    def test_shows_search_filter(self, client, db_session):
        ep = _make_episode(db_session, show_title="Game of Thrones")
        _make_session(db_session, ep)
        db_session.commit()
        assert len(client.get("/api/shows?search=Game").json()) == 1
        assert len(client.get("/api/shows?search=Stranger").json()) == 0


# ---------------------------------------------------------------------------
# /api/items/{id}
# ---------------------------------------------------------------------------
class TestItemDetail:
    def test_item_not_found_returns_404(self, client):
        resp = client.get("/api/items/9999")
        assert resp.status_code == 404

    def test_item_found_returns_detail(self, client, db_session):
        movie = _make_movie(db_session, title="Dune")
        ws = _make_session(db_session, movie)
        obs = KodiObservation(
            media_item_id=movie.id,
            kodi_file_id=1,
            kind=ObservationKind.playcount_up,
            observed_at=_now(),
            kodi_play_count=1,
        )
        db_session.add(obs)
        db_session.commit()
        resp = client.get(f"/api/items/{movie.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Dune"
        assert len(data["sessions"]) == 1
        assert len(data["observations"]) == 1
        # New metadata fields present (None when not set)
        for field in (
            "release_date", "director", "video_codec", "video_width",
            "video_height", "hdr_type", "audio_codec", "audio_channels",
        ):
            assert field in data, f"missing field: {field}"
            assert data[field] is None

    def test_item_sessions_sorted_newest_first(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, offset_hours=10)  # older
        _make_session(db_session, movie, offset_hours=0)   # newer
        db_session.commit()
        resp = client.get(f"/api/items/{movie.id}")
        sessions = resp.json()["sessions"]
        assert sessions[0]["last_activity_at"] >= sessions[1]["last_activity_at"]

    def test_item_artwork_included(self, client, db_session):
        from logsaga.models import Artwork
        from datetime import datetime
        movie = _make_movie(db_session)
        art = Artwork(
            media_item_id=movie.id,
            art_type="poster",
            source_url="https://example.com/poster.jpg",
            local_path="movie/1/poster.jpg",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(art)
        db_session.commit()
        resp = client.get(f"/api/items/{movie.id}")
        data = resp.json()
        assert len(data["artwork"]) == 1
        assert data["artwork"][0]["art_type"] == "poster"


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------
class TestStats:
    def test_stats_empty_db(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_sessions"] == 0
        assert data["completed_sessions"] == 0
        assert data["total_minutes_watched"] == 0
        assert data["completion_rate"] == 0.0
        assert data["daily"] == []
        assert data["top_shows"] == []

    def test_stats_counts_sessions(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, status=SessionStatus.completed)
        _make_session(db_session, movie, status=SessionStatus.partial)
        db_session.commit()
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["total_sessions"] == 2
        assert data["completed_sessions"] == 1
        assert data["partial_sessions"] == 1
        assert data["completion_rate"] == 0.5

    def test_stats_total_minutes(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, runtime_s=3600)  # 1 hour
        db_session.commit()
        resp = client.get("/api/stats")
        assert resp.json()["total_minutes_watched"] == 60

    def test_stats_by_media_type(self, client, db_session):
        movie = _make_movie(db_session)
        ep = _make_episode(db_session)
        _make_session(db_session, movie)
        _make_session(db_session, ep)
        db_session.commit()
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["movies_watched"] == 1
        assert data["episodes_watched"] == 1

    def test_stats_top_shows(self, client, db_session):
        # 3 eps of Show A, 2 eps of Show B
        for i in range(3):
            ep = _make_episode(db_session, show_title="Show A", episode=i + 1)
            _make_session(db_session, ep)
        for i in range(2):
            ep = _make_episode(db_session, show_title="Show B", episode=i + 1)
            _make_session(db_session, ep)
        db_session.commit()
        data = client.get("/api/stats").json()
        assert data["top_shows"][0]["show_title"] == "Show A"
        assert data["top_shows"][0]["watched_episodes"] == 3

    def test_stats_days_param(self, client, db_session):
        movie = _make_movie(db_session)
        # Recent session (within 1 day)
        _make_session(db_session, movie, offset_hours=1)
        # Old session (100 days ago — won't appear in daily for days=30)
        ws_old = _make_session(db_session, movie, offset_hours=24 * 100)
        ws_old.completed_at = _now() - timedelta(days=100)
        db_session.commit()
        data = client.get("/api/stats?days=1").json()
        # Only today's sessions in daily
        assert len(data["daily"]) <= 1

    def test_stats_total_rewatches(self, client, db_session):
        # 2 sessions on same item → 1 rewatch
        m1 = _make_movie(db_session, title="Rewatch Me")
        _make_session(db_session, m1, offset_hours=24)
        _make_session(db_session, m1, offset_hours=0)
        # 1 session on different item → not counted
        m2 = _make_movie(db_session, title="Once Only")
        _make_session(db_session, m2)
        db_session.commit()
        data = client.get("/api/stats").json()
        assert data["total_rewatches"] == 1

    def test_stats_total_rewatches_two_items(self, client, db_session):
        # 2 sessions on item A and 2 on item B → 2 rewatches
        m1 = _make_movie(db_session, title="Movie A")
        m2 = _make_movie(db_session, title="Movie B")
        for m in (m1, m2):
            _make_session(db_session, m, offset_hours=24)
            _make_session(db_session, m, offset_hours=0)
        db_session.commit()
        data = client.get("/api/stats").json()
        assert data["total_rewatches"] == 2

    def test_stats_movie_episode_minutes_split(self, client, db_session):
        movie = _make_movie(db_session)
        ep = _make_episode(db_session)
        _make_session(db_session, movie, runtime_s=3600)   # 60 min movie
        _make_session(db_session, ep, runtime_s=1800)      # 30 min episode
        db_session.commit()
        data = client.get("/api/stats").json()
        assert data["movie_minutes"] == 60
        assert data["episode_minutes"] == 30
        assert data["movie_minutes"] + data["episode_minutes"] == data["total_minutes_watched"]

    def test_stats_show_id_in_top_shows(self, client, db_session):
        ep = _make_episode(db_session, show_title="TestShow", episode=1)
        _make_session(db_session, ep)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "TestShow").first()
        data = client.get("/api/stats").json()
        assert len(data["top_shows"]) == 1
        assert data["top_shows"][0]["show_id"] == show.id

    def test_stats_streak_consecutive_days(self, client, db_session):
        from datetime import timezone
        movie = _make_movie(db_session)
        # Use real current time so streak check (ending today/yesterday) works
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for days_ago in range(3):
            ws = _make_session(db_session, movie, offset_hours=24 * days_ago)
            ws.completed_at = now - timedelta(days=days_ago)
        db_session.commit()
        data = client.get("/api/stats").json()
        assert data["current_streak_days"] >= 3
        assert data["longest_streak_days"] >= 3

    def test_stats_streak_broken(self, client, db_session):
        from datetime import timezone
        movie = _make_movie(db_session)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Day 0 and day 2 — gap on day 1 breaks the streak
        ws0 = _make_session(db_session, movie, offset_hours=0)
        ws0.completed_at = now
        ws2 = _make_session(db_session, movie, offset_hours=48)
        ws2.completed_at = now - timedelta(days=2)
        db_session.commit()
        data = client.get("/api/stats").json()
        # Current streak is 1 (only today); longest is 1 (no consecutive pair)
        assert data["current_streak_days"] == 1
        assert data["longest_streak_days"] == 1


# ---------------------------------------------------------------------------
# /api/admin/rebuild-sessions
# ---------------------------------------------------------------------------
class TestRebuildSessions:
    def test_rebuild_returns_200(self, client, db_session):
        # Seed a movie + session so rebuild has something to process
        movie = _make_movie(db_session)
        _make_session(db_session, movie)
        db_session.commit()
        resp = client.post("/api/admin/rebuild-sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "items_processed" in data
        assert "sessions_created" in data
        assert "message" in data

    def test_rebuild_empty_db(self, client):
        resp = client.post("/api/admin/rebuild-sessions")
        assert resp.status_code == 200
        assert resp.json()["items_processed"] == 0


# ---------------------------------------------------------------------------
# /api/shows — next_unwatched field
# ---------------------------------------------------------------------------
class TestShowsNextUnwatched:
    def test_next_unwatched_present_in_response(self, client, db_session):
        # 3 episodes; first 2 watched, third not
        ep1 = _make_episode(db_session, title="Pilot", show_title="TestShow",
                            season=1, episode=1)
        ep2 = _make_episode(db_session, title="Ep2", show_title="TestShow",
                            season=1, episode=2)
        ep3 = _make_episode(db_session, title="Ep3", show_title="TestShow",
                            season=1, episode=3, kodi_state=KodiState.unwatched)
        _make_session(db_session, ep1, status=SessionStatus.completed)
        _make_session(db_session, ep2, status=SessionStatus.completed)
        # ep3 has no session
        db_session.commit()
        data = client.get("/api/shows?search=TestShow").json()
        assert len(data) == 1
        nw = data[0]["next_unwatched"]
        assert nw is not None
        assert nw["season"] == 1
        assert nw["episode"] == 3
        assert nw["title"] == "Ep3"

    def test_next_unwatched_none_when_all_watched(self, client, db_session):
        ep = _make_episode(db_session, show_title="FullShow", episode=1)
        _make_session(db_session, ep, status=SessionStatus.completed)
        db_session.commit()
        data = client.get("/api/shows?search=FullShow").json()
        assert data[0]["next_unwatched"] is None


# ---------------------------------------------------------------------------
# _poster_url preference order
# ---------------------------------------------------------------------------
class TestPosterUrl:
    """Verify the artwork preference ordering returned by _poster_url()."""

    def _add_art(self, db, item_id: int, art_type: str, local_path: str):
        from logsaga.models import Artwork
        art = Artwork(
            media_item_id=item_id,
            art_type=art_type,
            source_url="https://example.com/art.jpg",
            local_path=local_path,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(art)
        db.flush()

    def test_episode_prefers_season_poster_over_tvshow_poster(self, db_session):
        from logsaga.api.routes import _poster_url
        item = _make_episode(db_session)
        self._add_art(db_session, item.id, "season.poster", f"episode/{item.id}/season_poster.jpg")
        self._add_art(db_session, item.id, "tvshow.poster", f"episode/{item.id}/tvshow_poster.jpg")
        self._add_art(db_session, item.id, "thumb", f"episode/{item.id}/thumb.jpg")
        db_session.expire(item)  # force relationship reload
        url = _poster_url(item, thumbnail=False)
        assert url == f"/art/episode/{item.id}/season_poster.jpg"

    def test_episode_falls_back_to_tvshow_poster_when_no_season_poster(self, db_session):
        from logsaga.api.routes import _poster_url
        item = _make_episode(db_session)
        self._add_art(db_session, item.id, "tvshow.poster", f"episode/{item.id}/tvshow_poster.jpg")
        self._add_art(db_session, item.id, "thumb", f"episode/{item.id}/thumb.jpg")
        db_session.expire(item)
        url = _poster_url(item, thumbnail=False)
        assert url == f"/art/episode/{item.id}/tvshow_poster.jpg"


# ---------------------------------------------------------------------------
# GET /api/movies
# ---------------------------------------------------------------------------
class TestMovies:
    def test_no_movies_returns_empty(self, client):
        resp = client.get("/api/movies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_movie_with_session_included(self, client, db_session):
        movie = _make_movie(db_session, title="Dune")
        _make_session(db_session, movie)
        db_session.commit()
        resp = client.get("/api/movies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Dune"
        assert data["items"][0]["total_sessions"] == 1

    def test_movie_without_session_excluded(self, client, db_session):
        # Movie with session — included
        m1 = _make_movie(db_session, title="Alpha")
        _make_session(db_session, m1)
        # Movie without any session — excluded
        _make_movie(db_session, title="Beta")
        db_session.commit()
        resp = client.get("/api/movies")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Alpha"

    def test_search_filter(self, client, db_session):
        m1 = _make_movie(db_session, title="Interstellar")
        _make_session(db_session, m1)
        m2 = _make_movie(db_session, title="Inception")
        _make_session(db_session, m2)
        db_session.commit()
        resp = client.get("/api/movies?search=inter")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Interstellar"

    def test_default_sort_is_alphabetical(self, client, db_session):
        for title in ("Zorro", "Alien", "Matrix"):
            m = _make_movie(db_session, title=title)
            _make_session(db_session, m)
        db_session.commit()
        resp = client.get("/api/movies")
        titles = [i["title"] for i in resp.json()["items"]]
        assert titles == sorted(titles)

    def test_sort_recent_orders_by_last_activity(self, client, db_session):
        m1 = _make_movie(db_session, title="Old Movie")
        _make_session(db_session, m1, offset_hours=10)   # older
        m2 = _make_movie(db_session, title="New Movie")
        _make_session(db_session, m2, offset_hours=0)    # more recent
        db_session.commit()
        resp = client.get("/api/movies?sort=recent")
        titles = [i["title"] for i in resp.json()["items"]]
        assert titles[0] == "New Movie"
        assert titles[1] == "Old Movie"

    def test_filter_all_includes_unwatched(self, client, db_session):
        m_watched = _make_movie(db_session, title="Watched")
        _make_session(db_session, m_watched)
        _make_movie(db_session, title="Unwatched", kodi_state=KodiState.unwatched)
        db_session.commit()
        # Default filter=history excludes unwatched
        data = client.get("/api/movies").json()
        assert data["total"] == 1
        # filter=all includes both
        data = client.get("/api/movies?filter=all").json()
        assert data["total"] == 2

    def test_filter_unwatched_returns_only_unwatched(self, client, db_session):
        m_watched = _make_movie(db_session, title="Watched")
        _make_session(db_session, m_watched)
        _make_movie(db_session, title="Unwatched", kodi_state=KodiState.unwatched)
        db_session.commit()
        data = client.get("/api/movies?filter=unwatched").json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Unwatched"


# ---------------------------------------------------------------------------
# GET /api/shows — L-07 extended (filter, show_id, kodi_state fields)
# ---------------------------------------------------------------------------
class TestShowsExtended:
    def test_shows_has_show_id_field(self, client, db_session):
        ep = _make_episode(db_session, show_title="ShowA")
        _make_session(db_session, ep)
        db_session.commit()
        data = client.get("/api/shows").json()
        assert len(data) == 1
        assert "show_id" in data[0]
        assert data[0]["show_id"] > 0

    def test_shows_has_kodi_state_field(self, client, db_session):
        ep = _make_episode(db_session, show_title="ShowB")
        _make_session(db_session, ep)
        db_session.commit()
        data = client.get("/api/shows").json()
        assert "kodi_state" in data[0]

    def test_filter_all_includes_unwatched_show(self, client, db_session):
        # Watched show
        ep_w = _make_episode(db_session, show_title="WatchedShow")
        _make_session(db_session, ep_w)
        # Unwatched show (no sessions)
        _make_episode(db_session, show_title="UnwatchedShow",
                      kodi_state=KodiState.unwatched)
        # Set the KodiShow to unwatched too (normally done by library sync)
        unwatched_show = db_session.query(KodiShow).filter(
            KodiShow.title == "UnwatchedShow"
        ).first()
        unwatched_show.kodi_state = KodiState.unwatched
        db_session.commit()
        default_data = client.get("/api/shows").json()
        all_data = client.get("/api/shows?filter=all").json()
        assert len(default_data) == 1
        assert len(all_data) == 2

    def test_filter_unwatched_returns_only_unwatched_shows(self, client, db_session):
        ep_w = _make_episode(db_session, show_title="WatchedShow")
        _make_session(db_session, ep_w)
        _make_episode(db_session, show_title="UnwatchedShow",
                      kodi_state=KodiState.unwatched)
        # Update the KodiShow for UnwatchedShow to reflect unwatched state
        # (normally done by library sync's _update_show_states)
        unwatched_show = db_session.query(KodiShow).filter(
            KodiShow.title == "UnwatchedShow"
        ).first()
        unwatched_show.kodi_state = KodiState.unwatched
        db_session.commit()
        data = client.get("/api/shows?filter=unwatched").json()
        assert len(data) == 1
        assert data[0]["show_title"] == "UnwatchedShow"


# ---------------------------------------------------------------------------
# GET /api/shows/{show_id}  (L-08)
# ---------------------------------------------------------------------------
class TestShowDetail:
    def test_404_for_unknown_show(self, client):
        resp = client.get("/api/shows/9999")
        assert resp.status_code == 404

    def test_returns_show_metadata(self, client, db_session):
        ep = _make_episode(db_session, title="Pilot", show_title="TestShow",
                           season=1, episode=1)
        _make_session(db_session, ep)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "TestShow").first()
        resp = client.get(f"/api/shows/{show.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["show_title"] == "TestShow"
        assert data["show_id"] == show.id
        assert "kodi_state" in data
        assert "total_episodes" in data
        assert "watched_episodes" in data

    def test_show_detail_has_seasons(self, client, db_session):
        ep1 = _make_episode(db_session, title="Ep1", show_title="SeasonedShow",
                            season=1, episode=1)
        ep2 = _make_episode(db_session, title="Ep2", show_title="SeasonedShow",
                            season=1, episode=2, kodi_state=KodiState.unwatched)
        ep3 = _make_episode(db_session, title="Ep3", show_title="SeasonedShow",
                            season=2, episode=1, kodi_state=KodiState.unwatched)
        _make_session(db_session, ep1)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "SeasonedShow").first()
        data = client.get(f"/api/shows/{show.id}").json()
        assert len(data["seasons"]) == 2
        s1 = next(s for s in data["seasons"] if s["season"] == 1)
        assert s1["total_episodes"] == 2
        assert s1["watched_episodes"] == 1  # only ep1 has session
        assert len(s1["episodes"]) == 2

    def test_show_detail_episode_watched_flag(self, client, db_session):
        ep_w = _make_episode(db_session, title="Watched Ep", show_title="FlagShow",
                             season=1, episode=1)
        ep_u = _make_episode(db_session, title="Unwatched Ep", show_title="FlagShow",
                             season=1, episode=2, kodi_state=KodiState.unwatched)
        _make_session(db_session, ep_w)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "FlagShow").first()
        data = client.get(f"/api/shows/{show.id}").json()
        eps = data["seasons"][0]["episodes"]
        assert eps[0]["watched"] is True
        assert eps[1]["watched"] is False

    def test_show_detail_counts_match(self, client, db_session):
        for i in range(3):
            ep = _make_episode(db_session, title=f"Ep{i}", show_title="CountShow",
                               season=1, episode=i + 1,
                               kodi_state=KodiState.watched if i < 2 else KodiState.unwatched)
            if i < 2:  # watch 2 of 3
                _make_session(db_session, ep)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "CountShow").first()
        data = client.get(f"/api/shows/{show.id}").json()
        assert data["total_episodes"] == 3
        assert data["watched_episodes"] == 2


# ---------------------------------------------------------------------------
# GET /api/shows/{show_id}/seasons/{season}  (L-09)
# ---------------------------------------------------------------------------
class TestSeasonDetail:
    def test_404_for_unknown_season(self, client, db_session):
        ep = _make_episode(db_session, show_title="SeasonTest", season=1, episode=1)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "SeasonTest").first()
        resp = client.get(f"/api/shows/{show.id}/seasons/99")
        assert resp.status_code == 404

    def test_returns_episodes_for_season(self, client, db_session):
        ep1 = _make_episode(db_session, title="S1E1", show_title="SeasonShow",
                            season=1, episode=1)
        ep2 = _make_episode(db_session, title="S1E2", show_title="SeasonShow",
                            season=1, episode=2, kodi_state=KodiState.unwatched)
        _make_episode(db_session, title="S2E1", show_title="SeasonShow",
                      season=2, episode=1, kodi_state=KodiState.unwatched)
        _make_session(db_session, ep1)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "SeasonShow").first()
        data = client.get(f"/api/shows/{show.id}/seasons/1").json()
        assert data["season"] == 1
        assert data["total_episodes"] == 2
        assert data["watched_episodes"] == 1
        episode_nums = [e["episode"] for e in data["episodes"]]
        assert episode_nums == [1, 2]

    def test_episode_watched_flag_in_season(self, client, db_session):
        ep_w = _make_episode(db_session, title="Watched", show_title="FlagSeason",
                             season=1, episode=1)
        ep_u = _make_episode(db_session, title="Unwatched", show_title="FlagSeason",
                             season=1, episode=2, kodi_state=KodiState.unwatched)
        _make_session(db_session, ep_w)
        db_session.commit()
        show = db_session.query(KodiShow).filter(KodiShow.title == "FlagSeason").first()
        data = client.get(f"/api/shows/{show.id}/seasons/1").json()
        watched_map = {e["episode"]: e["watched"] for e in data["episodes"]}
        assert watched_map[1] is True
        assert watched_map[2] is False


# ---------------------------------------------------------------------------
# GET /api/recently-added  (L-10)
# ---------------------------------------------------------------------------
class TestRecentlyAdded:
    def test_empty_returns_zero(self, client):
        resp = client.get("/api/recently-added")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_returns_items_with_date_added(self, client, db_session):
        m = MediaItem(
            media_type="movie",
            title="New Film",
            year=2026,
            kodi_state=KodiState.unwatched,
            date_added="2026-11-01 00:00:00",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(m)
        db_session.commit()
        data = client.get("/api/recently-added").json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "New Film"
        assert data["items"][0]["date_added"] == "2026-11-01 00:00:00"

    def test_type_filter_movie(self, client, db_session):
        movie = MediaItem(
            media_type="movie", title="Movie", year=2026,
            kodi_state=KodiState.unwatched,
            date_added="2026-11-01 00:00:00",
            created_at=_now(), updated_at=_now(),
        )
        db_session.add(movie)
        ep = _make_episode(db_session, title="Episode", show_title="ShowX",
                           season=1, episode=1)
        ep.date_added = "2026-11-02 00:00:00"
        db_session.commit()
        movie_data = client.get("/api/recently-added?type=movie").json()
        ep_data = client.get("/api/recently-added?type=episode").json()
        assert movie_data["total"] == 1
        assert ep_data["total"] == 1

    def test_sentinel_date_sorts_last(self, client, db_session):
        from logsaga.library.sync import DATE_ADDED_SENTINEL
        m1 = MediaItem(
            media_type="movie", title="Recent Movie",
            kodi_state=KodiState.unwatched,
            date_added="2026-11-01 00:00:00",
            created_at=_now(), updated_at=_now(),
        )
        m2 = MediaItem(
            media_type="movie", title="Old Backfill",
            kodi_state=KodiState.unwatched,
            date_added=DATE_ADDED_SENTINEL,
            created_at=_now(), updated_at=_now(),
        )
        db_session.add_all([m1, m2])
        db_session.commit()
        data = client.get("/api/recently-added").json()
        titles = [item["title"] for item in data["items"]]
        assert titles[0] == "Recent Movie"
        assert titles[-1] == "Old Backfill"

    def test_removed_items_excluded(self, client, db_session):
        active = MediaItem(
            media_type="movie", title="Active",
            kodi_state=KodiState.unwatched,
            date_added="2026-11-01 00:00:00",
            created_at=_now(), updated_at=_now(),
        )
        removed = MediaItem(
            media_type="movie", title="Removed",
            kodi_state=KodiState.removed,
            date_added="2026-11-02 00:00:00",
            created_at=_now(), updated_at=_now(),
        )
        db_session.add_all([active, removed])
        db_session.commit()
        data = client.get("/api/recently-added").json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Active"

    def test_show_id_field_set_for_episodes(self, client, db_session):
        ep = _make_episode(db_session, title="S1E1", show_title="ShowForRecent",
                           season=1, episode=1)
        ep.date_added = "2026-11-01 00:00:00"
        db_session.commit()
        data = client.get("/api/recently-added?type=episode").json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["show_id"] is not None
        assert item["show_id"] > 0
        assert item["show_title"] == "ShowForRecent"


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{session_id}  — permanent delete (F-04)
# ---------------------------------------------------------------------------
class TestSessionDelete:
    def test_delete_returns_204(self, client, db_session):
        movie = _make_movie(db_session)
        ws = _make_session(db_session, movie)
        db_session.commit()
        resp = client.delete(f"/api/sessions/{ws.id}")
        assert resp.status_code == 204

    def test_delete_removes_row(self, client, db_session):
        movie = _make_movie(db_session)
        ws = _make_session(db_session, movie)
        ws_id = ws.id
        db_session.commit()
        client.delete(f"/api/sessions/{ws_id}")
        db_session.expire_all()
        assert db_session.get(WatchSession, ws_id) is None

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/sessions/99999")
        assert resp.status_code == 404

    def test_deleted_session_excluded_from_recent(self, client, db_session):
        movie = _make_movie(db_session)
        ws = _make_session(db_session, movie)
        db_session.commit()
        data = client.get("/api/recent").json()
        assert data["total"] == 1
        client.delete(f"/api/sessions/{ws.id}")
        data = client.get("/api/recent").json()
        assert data["total"] == 0

    def test_deleted_session_not_counted_in_stats(self, client, db_session):
        movie = _make_movie(db_session)
        ws = _make_session(db_session, movie)
        db_session.commit()
        stats = client.get("/api/stats").json()
        assert stats["completed_sessions"] == 1
        client.delete(f"/api/sessions/{ws.id}")
        stats = client.get("/api/stats").json()
        assert stats["completed_sessions"] == 0

    def test_deleted_session_excluded_from_item_detail(self, client, db_session):
        movie = _make_movie(db_session)
        ws1 = _make_session(db_session, movie, offset_hours=2)
        ws2 = _make_session(db_session, movie, offset_hours=0)
        db_session.commit()
        data = client.get(f"/api/items/{movie.id}").json()
        assert len(data["sessions"]) == 2
        client.delete(f"/api/sessions/{ws1.id}")
        data = client.get(f"/api/items/{movie.id}").json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == ws2.id
