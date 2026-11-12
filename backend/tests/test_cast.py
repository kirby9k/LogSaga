"""
Tests for the cast population service and actor API endpoint.

Covers:
  - populate_cast_for_item: movie with tmdb_cache data → CastMemberOut list
  - populate_cast_for_item: idempotency (second call reuses existing rows)
  - populate_cast_for_item: episode delegates to show
  - populate_cast_for_show: show without tmdb_cache → empty list
  - GET /api/actors/{id}: 404 when person not in DB
  - GET /api/actors/{id}: correct watched/library grouping (TV by show, movie by item)
  - EpisodeSummaryOut.last_session_at: populated by get_show_detail
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show
from logsaga.api.deps import get_db
from logsaga.main import app
from logsaga.models import (
    Base,
    CompletionReason,
    ItemCast,
    KodiShow,
    KodiState,
    MediaItem,
    MediaType,
    Person,
    SessionSource,
    SessionStatus,
    TMDBCache,
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
# Helpers
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 11, 4, 12, 0, 0)


def _make_movie(db: Session, tmdb_id: str = "12345") -> MediaItem:
    item = MediaItem(
        media_type="movie",
        title="Test Movie",
        year=2020,
        runtime_s=5400,
        kodi_state=KodiState.watched,
        tmdb_id=tmdb_id,
        kodi_media_id=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(item)
    db.flush()
    return item


def _make_show(db: Session, tmdb_id: str = "9999") -> KodiShow:
    show = KodiShow(
        title="Test Show",
        kodi_idShow=1,
        kodi_state=KodiState.watched,
        tmdb_id=tmdb_id,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(show)
    db.flush()
    return show


def _make_episode(db: Session, show: KodiShow, season: int = 1, episode: int = 1) -> MediaItem:
    item = MediaItem(
        media_type="episode",
        title=f"Episode {episode}",
        show_title=show.title,
        show_id=show.id,
        season=season,
        episode=episode,
        runtime_s=1320,
        kodi_state=KodiState.watched,
        tmdb_id=show.tmdb_id,
        kodi_media_id=episode,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(item)
    db.flush()
    return item


def _make_tmdb_movie_cache(db: Session, tmdb_id: str, cast_data: list[dict]) -> None:
    data = {"credits": {"cast": cast_data, "crew": []}}
    db.add(TMDBCache(
        cache_key=f"movie:{tmdb_id}",
        data_json=json.dumps(data),
        fetched_at=_NOW,
    ))
    db.flush()


def _make_tmdb_tv_cache(db: Session, tmdb_id: str, cast_data: list[dict]) -> None:
    data = {"name": "Test Show", "credits": {"cast": cast_data, "crew": []}}
    db.add(TMDBCache(
        cache_key=f"tv:{tmdb_id}",
        data_json=json.dumps(data),
        fetched_at=_NOW,
    ))
    db.flush()


def _sample_cast(n: int = 3) -> list[dict]:
    return [
        {
            "id": 1000 + i,
            "name": f"Actor {i}",
            "character": f"Character {i}",
            "order": i,
            "profile_path": f"/profile{i}.jpg",
        }
        for i in range(n)
    ]


def _make_session(
    db: Session,
    item: MediaItem,
    status: SessionStatus = SessionStatus.completed,
    hours_ago: float = 0,
) -> WatchSession:
    base = _NOW - timedelta(hours=hours_ago)
    ws = WatchSession(
        media_item_id=item.id,
        started_at=base - timedelta(hours=1),
        last_activity_at=base,
        ended_at=base,
        completed_at=base if status == SessionStatus.completed else None,
        status=status,
        source=SessionSource.poll,
        percent=100.0,
    )
    db.add(ws)
    db.flush()
    return ws


# ---------------------------------------------------------------------------
# Tests: populate_cast_for_item (movie)
# ---------------------------------------------------------------------------
class TestPopulateCastForItemMovie:
    def test_returns_cast_from_tmdb_cache(self, db_session):
        movie = _make_movie(db_session)
        _make_tmdb_movie_cache(db_session, "12345", _sample_cast(3))
        db_session.commit()

        result = populate_cast_for_item(movie, db_session)

        assert len(result) == 3
        assert result[0].name == "Actor 0"
        assert result[0].character == "Character 0"
        assert result[0].tmdb_person_id == 1000
        assert result[0].profile_url == "https://image.tmdb.org/t/p/w185/profile0.jpg"

    def test_empty_when_no_tmdb_cache(self, db_session):
        movie = _make_movie(db_session)
        db_session.commit()

        result = populate_cast_for_item(movie, db_session)
        assert result == []

    def test_empty_when_no_tmdb_id(self, db_session):
        movie = _make_movie(db_session, tmdb_id=None)
        db_session.commit()

        result = populate_cast_for_item(movie, db_session)
        assert result == []

    def test_idempotent_second_call_reuses_rows(self, db_session):
        movie = _make_movie(db_session)
        _make_tmdb_movie_cache(db_session, "12345", _sample_cast(2))
        db_session.commit()

        result1 = populate_cast_for_item(movie, db_session)
        # Modify cache (should be ignored on second call)
        cached = db_session.get(TMDBCache, "movie:12345")
        cached.data_json = json.dumps({"credits": {"cast": _sample_cast(5)}})
        db_session.commit()

        result2 = populate_cast_for_item(movie, db_session)

        # Second call uses DB rows, not re-parsed cache
        assert len(result1) == 2
        assert len(result2) == 2
        # DB row count unchanged
        count = db_session.query(ItemCast).filter_by(media_item_id=movie.id).count()
        assert count == 2

    def test_person_rows_created(self, db_session):
        movie = _make_movie(db_session)
        _make_tmdb_movie_cache(db_session, "12345", _sample_cast(2))
        db_session.commit()

        populate_cast_for_item(movie, db_session)

        persons = db_session.query(Person).all()
        assert len(persons) == 2
        assert {p.tmdb_person_id for p in persons} == {1000, 1001}


# ---------------------------------------------------------------------------
# Tests: populate_cast_for_item (episode)
# ---------------------------------------------------------------------------
class TestPopulateCastForItemEpisode:
    def test_episode_delegates_to_show(self, db_session):
        show = _make_show(db_session)
        ep = _make_episode(db_session, show)
        _make_tmdb_tv_cache(db_session, "9999", _sample_cast(2))
        db_session.commit()

        result = populate_cast_for_item(ep, db_session)

        assert len(result) == 2
        assert result[0].name == "Actor 0"

    def test_episode_no_show_id_returns_empty(self, db_session):
        item = MediaItem(
            media_type="episode",
            title="Orphan Episode",
            show_title="Some Show",
            show_id=None,
            season=1,
            episode=1,
            kodi_state=KodiState.unwatched,
            created_at=_NOW,
            updated_at=_NOW,
        )
        db_session.add(item)
        db_session.commit()

        result = populate_cast_for_item(item, db_session)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: populate_cast_for_show
# ---------------------------------------------------------------------------
class TestPopulateCastForShow:
    def test_returns_empty_without_cache(self, db_session):
        show = _make_show(db_session)
        db_session.commit()

        result = populate_cast_for_show(show, db_session)
        assert result == []

    def test_returns_cast_from_cache(self, db_session):
        show = _make_show(db_session)
        _make_tmdb_tv_cache(db_session, "9999", _sample_cast(4))
        db_session.commit()

        result = populate_cast_for_show(show, db_session)
        assert len(result) == 4
        assert all(r.credit_order == i for i, r in enumerate(result))

    def test_null_profile_path_gives_null_profile_url(self, db_session):
        show = _make_show(db_session)
        cast = [{"id": 2000, "name": "No Photo Actor", "character": "X", "order": 0, "profile_path": None}]
        _make_tmdb_tv_cache(db_session, "9999", cast)
        db_session.commit()

        result = populate_cast_for_show(show, db_session)
        assert len(result) == 1
        assert result[0].profile_url is None


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{tmdb_person_id}
# ---------------------------------------------------------------------------
class TestGetActor:
    def test_404_when_person_not_in_db(self, client):
        resp = client.get("/api/actors/999999")
        assert resp.status_code == 404

    def test_returns_actor_data(self, client, db_session):
        # Create person in DB (normally done by cast population)
        person = Person(
            tmdb_person_id=42,
            name="Bryan Test",
            profile_path="/bp.jpg",
            created_at=_NOW,
        )
        db_session.add(person)
        db_session.commit()

        resp = client.get("/api/actors/42")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Bryan Test"
        assert data["tmdb_person_id"] == 42
        assert data["profile_url"] == "https://image.tmdb.org/t/p/w342/bp.jpg"
        assert data["watched_items"] == []
        assert data["library_items"] == []

    def test_watched_movie_appears_in_watched_items(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie, status=SessionStatus.completed)
        person = Person(
            tmdb_person_id=99, name="Test Actor", created_at=_NOW
        )
        db_session.add(person)
        db_session.flush()
        db_session.add(ItemCast(
            person_id=99,
            media_item_id=movie.id,
            character="Hero",
            credit_order=0,
        ))
        db_session.commit()

        resp = client.get("/api/actors/99")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["watched_items"]) == 1
        assert data["watched_items"][0]["media_type"] == "movie"
        assert data["watched_items"][0]["title"] == "Test Movie"
        assert data["watched_items"][0]["watched"] is True
        assert data["library_items"] == []

    def test_unwatched_movie_appears_in_library_items(self, client, db_session):
        movie = _make_movie(db_session)
        # No watch session
        person = Person(
            tmdb_person_id=88, name="Library Actor", created_at=_NOW
        )
        db_session.add(person)
        db_session.flush()
        db_session.add(ItemCast(
            person_id=88,
            media_item_id=movie.id,
            character="Villain",
            credit_order=0,
        ))
        db_session.commit()

        resp = client.get("/api/actors/88")
        assert resp.status_code == 200
        data = resp.json()
        assert data["watched_items"] == []
        assert len(data["library_items"]) == 1
        assert data["library_items"][0]["watched"] is False

    def test_tv_show_grouped_not_per_episode(self, client, db_session):
        show = _make_show(db_session)
        ep1 = _make_episode(db_session, show, season=1, episode=1)
        ep2 = _make_episode(db_session, show, season=1, episode=2)
        _make_session(db_session, ep1)
        _make_session(db_session, ep2)
        person = Person(tmdb_person_id=77, name="Show Actor", created_at=_NOW)
        db_session.add(person)
        db_session.flush()
        db_session.add(ItemCast(
            person_id=77,
            show_id=show.id,
            character="Main Character",
            credit_order=0,
        ))
        db_session.commit()

        resp = client.get("/api/actors/77")
        assert resp.status_code == 200
        data = resp.json()
        # Should be ONE entry for the show, not two entries for the episodes
        assert len(data["watched_items"]) == 1
        assert data["watched_items"][0]["media_type"] == "tv"
        assert data["watched_items"][0]["show_id"] == show.id
        assert data["watched_items"][0]["watched_episodes"] == 2


# ---------------------------------------------------------------------------
# Tests: last_session_at in show detail
# ---------------------------------------------------------------------------
class TestShowDetailLastSessionAt:
    def test_last_session_at_populated_for_watched_episode(self, client, db_session):
        show = _make_show(db_session, tmdb_id=None)  # no TMDB cast needed
        ep = _make_episode(db_session, show)
        ws = _make_session(db_session, ep, hours_ago=5)
        db_session.commit()

        resp = client.get(f"/api/shows/{show.id}")
        assert resp.status_code == 200
        seasons = resp.json()["seasons"]
        ep_data = seasons[0]["episodes"][0]
        assert ep_data["last_session_at"] is not None

    def test_last_session_at_null_for_unwatched_episode(self, client, db_session):
        show = _make_show(db_session, tmdb_id=None)
        ep = _make_episode(db_session, show)
        # No session
        db_session.commit()

        resp = client.get(f"/api/shows/{show.id}")
        assert resp.status_code == 200
        seasons = resp.json()["seasons"]
        ep_data = seasons[0]["episodes"][0]
        assert ep_data["last_session_at"] is None
