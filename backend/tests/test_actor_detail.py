"""
Tests for actor detail page improvements:

  Filmography endpoint (GET /api/actors/{id}/filmography):
    - Credit type classification (cast → acting/self/unknown, crew → production)
    - Talk-show / news / reality genre exclusion (TMDB genre IDs 10767, 10763, 10764)
    - Library items appear with in_library=True
    - Same title appearing in both cast and crew → two separate items (different credit_type)

  Recommendations endpoint (GET /api/actors/{id}/recommendations):
    - Library items are included (not excluded) and have in_library=True
    - Each list is capped at 12 items

  Recently-played endpoint (GET /api/actors/{id}/recently-played):
    - Ordered by completed_at DESC
    - session_id is unique across results even when show-level item_cast could match
      multiple episodes
    - Episode rows include show_title, season_number, episode_number
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from logsaga.api.deps import get_db, get_tmdb_client_optional
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
_NOW = datetime(2026, 11, 4, 12, 0, 0)


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


@pytest.fixture(scope="function")
def client_with_tmdb(engine, db_session) -> Generator[tuple[TestClient, AsyncMock], None, None]:
    """TestClient where get_tmdb_client_optional returns a controllable AsyncMock."""
    SessionLocal = sessionmaker(bind=engine)

    def override_db():
        with SessionLocal() as db:
            yield db

    mock_tmdb = AsyncMock()

    def override_tmdb():
        return mock_tmdb

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_tmdb_client_optional] = override_tmdb
    app.state.engine = engine
    from logsaga.config import get_settings
    app.state.settings = get_settings()

    yield TestClient(app, raise_server_exceptions=True), mock_tmdb

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _make_movie(db: Session, tmdb_id: str = "12345", title: str = "Test Movie") -> MediaItem:
    item = MediaItem(
        media_type="movie",
        title=title,
        year=2020,
        runtime_s=5400,
        kodi_state=KodiState.unwatched,
        tmdb_id=tmdb_id,
        kodi_media_id=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(item)
    db.flush()
    return item


def _make_show(db: Session, tmdb_id: str = "9999", title: str = "Test Show") -> KodiShow:
    show = KodiShow(
        title=title,
        kodi_idShow=1,
        kodi_state=KodiState.unwatched,
        tmdb_id=tmdb_id,
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
    title: str | None = None,
) -> MediaItem:
    item = MediaItem(
        media_type="episode",
        title=title or f"Episode S{season}E{episode}",
        show_title=show.title,
        show_id=show.id,
        season=season,
        episode=episode,
        runtime_s=1320,
        kodi_state=KodiState.unwatched,
        tmdb_id=show.tmdb_id,
        kodi_media_id=episode,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(item)
    db.flush()
    return item


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


def _add_person_and_movie_cast(db: Session, person_id: int, item: MediaItem) -> None:
    db.execute(
        sqlite_insert(Person.__table__).values(
            tmdb_person_id=person_id, name=f"Person {person_id}",
            profile_path=None, created_at=_NOW,
        ).on_conflict_do_nothing(index_elements=["tmdb_person_id"])
    )
    db.execute(
        sqlite_insert(ItemCast.__table__).values(
            person_id=person_id, media_item_id=item.id, show_id=None,
            character="Hero", credit_order=0,
        ).on_conflict_do_nothing()
    )
    db.flush()


def _add_person_and_show_cast(db: Session, person_id: int, show: KodiShow) -> None:
    db.execute(
        sqlite_insert(Person.__table__).values(
            tmdb_person_id=person_id, name=f"Person {person_id}",
            profile_path=None, created_at=_NOW,
        ).on_conflict_do_nothing(index_elements=["tmdb_person_id"])
    )
    db.execute(
        sqlite_insert(ItemCast.__table__).values(
            person_id=person_id, media_item_id=None, show_id=show.id,
            character="Main Character", credit_order=0,
        ).on_conflict_do_nothing()
    )
    db.flush()


def _combined_credits(cast: list[dict], crew: list[dict] | None = None) -> dict:
    return {"cast": cast, "crew": crew or []}


def _cast_entry(
    tmdb_id: int,
    media_type: str = "movie",
    title: str = "Test Title",
    character: str | None = "Hero",
    year: int = 2020,
    popularity: float = 10.0,
    genre_ids: list[int] | None = None,
) -> dict:
    entry: dict = {
        "id": tmdb_id,
        "media_type": media_type,
        "character": character,
        "popularity": popularity,
        "genre_ids": genre_ids or [],
    }
    if media_type == "movie":
        entry["title"] = title
        entry["release_date"] = f"{year}-01-01"
    else:
        entry["name"] = title
        entry["first_air_date"] = f"{year}-01-01"
    return entry


def _crew_entry(
    tmdb_id: int,
    media_type: str = "movie",
    title: str = "Test Title",
    job: str = "Director",
    year: int = 2020,
    genre_ids: list[int] | None = None,
) -> dict:
    entry: dict = {
        "id": tmdb_id,
        "media_type": media_type,
        "job": job,
        "popularity": 5.0,
        "genre_ids": genre_ids or [],
    }
    if media_type == "movie":
        entry["title"] = title
        entry["release_date"] = f"{year}-01-01"
    else:
        entry["name"] = title
        entry["first_air_date"] = f"{year}-01-01"
    return entry


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/filmography — credit classification
# ---------------------------------------------------------------------------
class TestFilmographyCreditClassification:
    def test_cast_with_character_is_acting(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(101, character="John Smith")]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["credit_type"] == "acting"
        assert movies[0]["character"] == "John Smith"

    def test_cast_with_self_prefix_is_self(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(102, character="Self - Host")]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["credit_type"] == "self"

    def test_cast_with_no_character_is_unknown(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(103, character=None)]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["credit_type"] == "unknown"

    def test_crew_entry_is_production(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[],
            crew=[_crew_entry(104, job="Director")]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["credit_type"] == "production"
        assert movies[0]["job"] == "Director"


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/filmography — genre exclusions
# ---------------------------------------------------------------------------
class TestFilmographyGenreExclusions:
    def test_excludes_talk_show_genre_10767(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[
                _cast_entry(201, genre_ids=[10767]),   # talk show → excluded
                _cast_entry(202, genre_ids=[]),         # regular → included
            ]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["tmdb_id"] == 202

    def test_excludes_news_genre_10763(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(203, genre_ids=[10763])]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        assert resp.json()["movies"] == []

    def test_excludes_reality_tv_genre_10764(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(204, genre_ids=[10764])]
        )
        db_session.commit()

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        assert resp.json()["movies"] == []


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/filmography — library membership
# ---------------------------------------------------------------------------
class TestFilmographyLibraryItems:
    def test_library_movie_has_in_library_true(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        movie = _make_movie(db_session, tmdb_id="301")
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(301)]
        )

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["in_library"] is True
        assert movies[0]["item_id"] == movie.id

    def test_non_library_movie_has_in_library_false(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(302)]
        )

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["in_library"] is False
        assert movies[0]["item_id"] is None

    def test_watched_movie_has_watched_true(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        movie = _make_movie(db_session, tmdb_id="303")
        _make_session(db_session, movie, status=SessionStatus.completed)
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(303)]
        )

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["watched"] is True

    def test_library_show_has_in_library_true(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        show = _make_show(db_session, tmdb_id="9001")
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(9001, media_type="tv")]
        )

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        tv_shows = resp.json()["tv_shows"]
        assert len(tv_shows) == 1
        assert tv_shows[0]["in_library"] is True
        assert tv_shows[0]["show_id"] == show.id


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/filmography — deduplication
# ---------------------------------------------------------------------------
class TestFilmographyDedup:
    def test_same_title_in_cast_and_crew_produces_two_items(self, client_with_tmdb, db_session):
        """
        When the same TMDB title appears in both cast (acting) and crew (production),
        the two entries must be preserved as separate items (different credit_type).
        """
        client, mock_tmdb = client_with_tmdb
        db_session.commit()

        tmdb_id = 401
        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(tmdb_id, character="Hero")],
            crew=[_crew_entry(tmdb_id, job="Director")],
        )

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 2
        credit_types = {m["credit_type"] for m in movies}
        assert "acting" in credit_types
        assert "production" in credit_types

    def test_duplicate_cast_entries_deduplicated(self, client_with_tmdb, db_session):
        """Same (tmdb_id, media_type, credit_type) key → keep only the first occurrence."""
        client, mock_tmdb = client_with_tmdb
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[
                _cast_entry(402, character="Role A"),
                _cast_entry(402, character="Role B"),  # duplicate id+type
            ]
        )

        resp = client.get("/api/actors/1/filmography")
        assert resp.status_code == 200
        movies = resp.json()["movies"]
        assert len(movies) == 1
        assert movies[0]["character"] == "Role A"


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/recommendations — library inclusion
# ---------------------------------------------------------------------------
class TestRecommendationsLibraryInclusion:
    def test_library_movie_included_with_in_library_true(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        movie = _make_movie(db_session, tmdb_id="501")
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(501, popularity=99.0)]
        )

        resp = client.get("/api/actors/1/recommendations")
        assert resp.status_code == 200
        popular = resp.json()["most_popular"]
        # Library item should appear, not be filtered out
        tmdb_ids = [i["tmdb_id"] for i in popular]
        assert 501 in tmdb_ids
        item = next(i for i in popular if i["tmdb_id"] == 501)
        assert item["in_library"] is True

    def test_watched_movie_has_watched_true(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        movie = _make_movie(db_session, tmdb_id="502")
        _make_session(db_session, movie, status=SessionStatus.completed)
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(502, popularity=99.0)]
        )

        resp = client.get("/api/actors/1/recommendations")
        assert resp.status_code == 200
        popular = resp.json()["most_popular"]
        item = next((i for i in popular if i["tmdb_id"] == 502), None)
        assert item is not None
        assert item["watched"] is True

    def test_non_library_movie_has_in_library_false(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        db_session.commit()

        mock_tmdb.person_credits.return_value = _combined_credits(
            cast=[_cast_entry(503, popularity=50.0)]
        )

        resp = client.get("/api/actors/1/recommendations")
        assert resp.status_code == 200
        popular = resp.json()["most_popular"]
        item = next((i for i in popular if i["tmdb_id"] == 503), None)
        assert item is not None
        assert item["in_library"] is False
        assert item["watched"] is False


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/recommendations — limit 12
# ---------------------------------------------------------------------------
class TestRecommendationsLimit:
    def test_most_popular_capped_at_12(self, client_with_tmdb, db_session):
        client, mock_tmdb = client_with_tmdb
        db_session.commit()

        # 20 entries — should be capped to 12 in most_popular
        entries = [
            _cast_entry(600 + i, popularity=float(100 - i))
            for i in range(20)
        ]
        mock_tmdb.person_credits.return_value = _combined_credits(cast=entries)

        resp = client.get("/api/actors/1/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["most_popular"]) <= 12
        assert len(data["most_recent"]) <= 12


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/recently-played — ordering
# ---------------------------------------------------------------------------
class TestRecentlyPlayedOrdering:
    def test_ordered_by_completed_at_desc(self, client, db_session):
        show = _make_show(db_session)
        ep1 = _make_episode(db_session, show, episode=1, title="First Episode")
        ep2 = _make_episode(db_session, show, episode=2, title="Second Episode")

        # ep2 was watched more recently
        s1 = _make_session(db_session, ep1, hours_ago=10)
        s2 = _make_session(db_session, ep2, hours_ago=1)

        _add_person_and_show_cast(db_session, 700, show)
        db_session.commit()

        resp = client.get("/api/actors/700/recently-played")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) >= 2
        # Most recent first
        assert sessions[0]["title"] == "Second Episode"
        assert sessions[1]["title"] == "First Episode"


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/recently-played — no duplicate sessions
# ---------------------------------------------------------------------------
class TestRecentlyPlayedNoDuplicates:
    def test_session_id_unique_even_with_show_cast_multiple_episodes(self, client, db_session):
        """
        Actor has show-level item_cast. Watching two episodes of the show produces
        two distinct sessions — both should appear, with unique session_ids.
        The dedup guard (by session.id) ensures no session is listed twice.
        """
        show = _make_show(db_session)
        ep1 = _make_episode(db_session, show, episode=1)
        ep2 = _make_episode(db_session, show, episode=2)
        s1 = _make_session(db_session, ep1, hours_ago=5)
        s2 = _make_session(db_session, ep2, hours_ago=1)

        _add_person_and_show_cast(db_session, 701, show)
        db_session.commit()

        resp = client.get("/api/actors/701/recently-played")
        assert resp.status_code == 200
        sessions = resp.json()

        session_ids = [s["session_id"] for s in sessions]
        assert len(session_ids) == len(set(session_ids)), "Duplicate session_ids in response"

    def test_movie_and_episode_sessions_both_returned(self, client, db_session):
        movie = _make_movie(db_session, tmdb_id="800")
        show = _make_show(db_session, tmdb_id="8001")
        ep = _make_episode(db_session, show, episode=1)

        _make_session(db_session, movie, hours_ago=3)
        _make_session(db_session, ep, hours_ago=1)

        # Same person in both movie (item-level) and show (show-level)
        _add_person_and_movie_cast(db_session, 702, movie)
        _add_person_and_show_cast(db_session, 702, show)
        db_session.commit()

        resp = client.get("/api/actors/702/recently-played")
        assert resp.status_code == 200
        sessions = resp.json()
        media_types = {s["media_type"] for s in sessions}
        assert "movie" in media_types
        assert "tv" in media_types


# ---------------------------------------------------------------------------
# Tests: GET /api/actors/{id}/recently-played — episode info
# ---------------------------------------------------------------------------
class TestRecentlyPlayedEpisodeInfo:
    def test_episode_includes_show_title(self, client, db_session):
        show = _make_show(db_session, title="Breaking Bad")
        ep = _make_episode(db_session, show, season=3, episode=7)
        _make_session(db_session, ep)
        _add_person_and_show_cast(db_session, 703, show)
        db_session.commit()

        resp = client.get("/api/actors/703/recently-played")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1
        assert sessions[0]["show_title"] == "Breaking Bad"

    def test_episode_includes_season_and_episode_number(self, client, db_session):
        show = _make_show(db_session, title="Succession")
        ep = _make_episode(db_session, show, season=4, episode=2)
        _make_session(db_session, ep)
        _add_person_and_show_cast(db_session, 704, show)
        db_session.commit()

        resp = client.get("/api/actors/704/recently-played")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1
        assert sessions[0]["season_number"] == 4
        assert sessions[0]["episode_number"] == 2

    def test_movie_session_has_null_show_fields(self, client, db_session):
        movie = _make_movie(db_session, tmdb_id="900")
        _make_session(db_session, movie)
        _add_person_and_movie_cast(db_session, 705, movie)
        db_session.commit()

        resp = client.get("/api/actors/705/recently-played")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1
        assert sessions[0]["media_type"] == "movie"
        assert sessions[0]["show_title"] is None
        assert sessions[0]["season_number"] is None
        assert sessions[0]["episode_number"] is None

    def test_no_sessions_for_unrelated_actor(self, client, db_session):
        movie = _make_movie(db_session)
        _make_session(db_session, movie)
        _add_person_and_movie_cast(db_session, 706, movie)
        db_session.commit()

        # Ask for a different actor who has no cast entries
        resp = client.get("/api/actors/999/recently-played")
        assert resp.status_code == 200
        assert resp.json() == []
