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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

    def test_duplicate_person_in_tmdb_cast_no_crash(self, db_session):
        """TMDB sometimes lists the same person twice — INSERT OR IGNORE must not raise."""
        movie = _make_movie(db_session)
        cast_with_dup = [
            {"id": 1663786, "name": "Ellie Ricker", "character": "Role A", "order": 0, "profile_path": None},
            {"id": 1663786, "name": "Ellie Ricker", "character": "Role B", "order": 1, "profile_path": None},
        ]
        _make_tmdb_movie_cache(db_session, "12345", cast_with_dup)
        db_session.commit()

        result = populate_cast_for_item(movie, db_session)  # must not raise

        # Person inserted once, second INSERT OR IGNORE'd
        persons = db_session.query(Person).all()
        assert len(persons) == 1
        # ItemCast inserted once, second INSERT OR IGNORE'd
        item_casts = db_session.query(ItemCast).filter_by(media_item_id=movie.id).all()
        assert len(item_casts) == 1
        # Both occurrences still appear in the return list
        assert len(result) == 2


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


# ---------------------------------------------------------------------------
# Helpers for episode-level cast tests
# ---------------------------------------------------------------------------

def _make_ep_credits_cache(
    db: Session,
    tmdb_id: str,
    season: int,
    episode: int,
    cast_data: list[dict],
    guest_data: list[dict] | None = None,
) -> None:
    """Insert a TMDB episode-credits cache entry (top-level cast + guest_stars)."""
    data = {"cast": cast_data, "crew": [], "guest_stars": guest_data or []}
    db.add(TMDBCache(
        cache_key=f"tv:{tmdb_id}:ep:{season}:{episode}:credits",
        data_json=json.dumps(data),
        fetched_at=_NOW,
    ))
    db.flush()


# ---------------------------------------------------------------------------
# Tests: populate_cast_for_item — episode-level credits
# ---------------------------------------------------------------------------
class TestPopulateCastForItemEpisodeLevel:
    def test_episode_uses_episode_level_cache_when_present(self, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        ep = _make_episode(db_session, show, season=1, episode=1)
        # Episode-specific credits in cache (cast only, no guest stars)
        _make_ep_credits_cache(db_session, "9999", 1, 1, _sample_cast(2))
        db_session.commit()

        result = populate_cast_for_item(ep, db_session)

        assert len(result) == 2
        assert result[0].name == "Actor 0"

    def test_episode_includes_guest_stars(self, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        ep = _make_episode(db_session, show, season=1, episode=1)
        regular_cast = [{"id": 2001, "name": "Regular", "character": "Main", "order": 0, "profile_path": None}]
        guest_cast = [{"id": 2002, "name": "Guest Star", "character": "Villain", "order": 0, "profile_path": None}]
        _make_ep_credits_cache(db_session, "9999", 1, 1, regular_cast, guest_cast)
        db_session.commit()

        result = populate_cast_for_item(ep, db_session)

        names = {r.name for r in result}
        assert "Regular" in names
        assert "Guest Star" in names

    def test_episode_returns_existing_episode_level_rows(self, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        ep = _make_episode(db_session, show, season=1, episode=1)
        # Pre-insert a person and episode-level item_cast row
        db_session.execute(
            sqlite_insert(Person.__table__).values(
                tmdb_person_id=5555, name="Pre-existing", profile_path=None, created_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["tmdb_person_id"])
        )
        db_session.execute(
            sqlite_insert(ItemCast.__table__).values(
                person_id=5555, media_item_id=ep.id, show_id=None, character="Existing Role", credit_order=0,
            ).on_conflict_do_nothing()
        )
        db_session.commit()

        result = populate_cast_for_item(ep, db_session)

        assert len(result) == 1
        assert result[0].name == "Pre-existing"

    def test_episode_falls_back_to_show_level_when_no_episode_cache(self, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        ep = _make_episode(db_session, show, season=1, episode=1)
        # Only show-level cache, no episode-level cache
        _make_tmdb_tv_cache(db_session, "9999", _sample_cast(3))
        db_session.commit()

        result = populate_cast_for_item(ep, db_session)

        # Should fall through to show-level and return 3 actors
        assert len(result) == 3

    def test_season_detail_includes_show_tmdb_id(self, client, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        _make_episode(db_session, show, season=1, episode=1)
        db_session.commit()

        resp = client.get(f"/api/shows/{show.id}/seasons/1")
        assert resp.status_code == 200
        assert resp.json()["show_tmdb_id"] == "9999"

    def test_season_detail_show_tmdb_id_null_when_not_set(self, client, db_session):
        show = _make_show(db_session, tmdb_id=None)
        _make_episode(db_session, show, season=1, episode=1)
        db_session.commit()

        resp = client.get(f"/api/shows/{show.id}/seasons/1")
        assert resp.status_code == 200
        assert resp.json()["show_tmdb_id"] is None


# ---------------------------------------------------------------------------
# Tests: populate_cast_for_show — episode aggregation
# ---------------------------------------------------------------------------
class TestPopulateCastForShowAggregation:
    def test_aggregates_from_episode_cast_sorted_by_appearances(self, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        ep1 = _make_episode(db_session, show, season=1, episode=1)
        ep2 = _make_episode(db_session, show, season=1, episode=2)
        ep3 = _make_episode(db_session, show, season=1, episode=3)

        # Person A (id 3001): appears in ep1 + ep2 (2 episodes)
        # Person B (id 3002): appears in ep1 + ep2 + ep3 (3 episodes)
        # Person C (id 3003): appears in ep1 only (1 episode)
        for pid, name in [(3001, "Person A"), (3002, "Person B"), (3003, "Person C")]:
            db_session.execute(
                sqlite_insert(Person.__table__).values(
                    tmdb_person_id=pid, name=name, profile_path=None, created_at=_NOW,
                ).on_conflict_do_nothing(index_elements=["tmdb_person_id"])
            )
        # ep1: A, B, C
        for order, (pid, ep) in enumerate([(3001, ep1), (3002, ep1), (3003, ep1)]):
            db_session.execute(
                sqlite_insert(ItemCast.__table__).values(
                    person_id=pid, media_item_id=ep.id, show_id=None, character=None, credit_order=order,
                ).on_conflict_do_nothing()
            )
        # ep2: A, B
        for order, (pid, ep) in enumerate([(3001, ep2), (3002, ep2)]):
            db_session.execute(
                sqlite_insert(ItemCast.__table__).values(
                    person_id=pid, media_item_id=ep.id, show_id=None, character=None, credit_order=order,
                ).on_conflict_do_nothing()
            )
        # ep3: B only
        db_session.execute(
            sqlite_insert(ItemCast.__table__).values(
                person_id=3002, media_item_id=ep3.id, show_id=None, character=None, credit_order=0,
            ).on_conflict_do_nothing()
        )
        db_session.commit()

        from logsaga.api.cast import populate_cast_for_show
        result = populate_cast_for_show(show, db_session)

        names = [r.name for r in result]
        # B (3 eps) > A (2 eps) > C (1 ep)
        assert names[0] == "Person B"
        assert names[1] == "Person A"
        assert names[2] == "Person C"

    def test_fallback_to_show_level_when_no_episode_cast(self, db_session):
        show = _make_show(db_session, tmdb_id="9999")
        # No episodes in DB → no episode-level rows
        _make_tmdb_tv_cache(db_session, "9999", _sample_cast(3))
        db_session.commit()

        from logsaga.api.cast import populate_cast_for_show
        result = populate_cast_for_show(show, db_session)

        # Falls through to show-level TMDB cache
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Tests: GET /api/admin/cast-status
# ---------------------------------------------------------------------------
class TestGetCastStatus:
    def test_returns_zeros_when_empty(self, client):
        resp = client.get("/api/admin/cast-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_items"] == 0
        assert data["items_with_cast"] == 0
        assert data["total_shows"] == 0
        assert data["shows_with_cast"] == 0
        assert data["pending_items"] == 0
        assert data["pending_shows"] == 0

    def test_counts_movies_and_shows(self, client, db_session):
        movie = _make_movie(db_session, tmdb_id="12345")
        show = _make_show(db_session, tmdb_id="9999")
        db_session.commit()

        resp = client.get("/api/admin/cast-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_items"] == 1   # 1 movie with tmdb_id
        assert data["total_shows"] == 1   # 1 show with tmdb_id
        assert data["pending_items"] == 1
        assert data["pending_shows"] == 1

    def test_items_with_cast_decrements_pending(self, client, db_session):
        movie = _make_movie(db_session, tmdb_id="12345")
        _make_tmdb_movie_cache(db_session, "12345", _sample_cast(2))
        db_session.commit()

        populate_cast_for_item(movie, db_session)

        resp = client.get("/api/admin/cast-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items_with_cast"] == 1
        assert data["pending_items"] == 0
