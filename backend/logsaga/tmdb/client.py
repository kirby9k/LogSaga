"""
TMDB API client with SQLite-backed caching.

Supports both TMDB authentication methods:
  - v3 API Key (short 32-char hex string) — passed as ?api_key= query param
  - Read Access Token (long JWT starting with eyJ) — passed as Bearer header

Both are available at https://www.themoviedb.org/settings/api

Cache TTLs:
  Detail responses (movie, TV show, season): 30 days
  Search results: 1 day

The client is synchronous for cache lookups (fast SQLite) and async for
HTTP calls, matching the pattern used by the rest of the codebase.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from logsaga.models import TMDBCache

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"

DETAIL_TTL_DAYS = 30
SEARCH_TTL_DAYS = 1


class TMDBClient:
    """Async TMDB API client with DB-backed caching."""

    def __init__(self, api_key: str, db: Session) -> None:
        self._db = db
        # Read Access Tokens are long JWTs (start with "eyJ").
        # v3 API Keys are short hex strings (~32 chars).
        # Use the right auth method for whichever was configured.
        if api_key.startswith("eyJ"):
            self._bearer_token: str | None = api_key
            self._api_key_param: str | None = None
        else:
            self._bearer_token = None
            self._api_key_param = api_key
        self._headers = {"Accept": "application/json"}
        if self._bearer_token:
            self._headers["Authorization"] = f"Bearer {self._bearer_token}"

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def search(self, query: str, media_type: str = "multi") -> dict:
        """Search TMDB. media_type: 'multi', 'movie', or 'tv'."""
        cache_key = f"search:{media_type}:{query.lower().strip()}"
        return await self._get_cached(
            cache_key,
            f"/search/{media_type}",
            {"query": query, "language": "en-US", "page": 1},
            SEARCH_TTL_DAYS,
        )

    async def movie_detail(self, tmdb_id: int) -> dict:
        """Fetch movie details including credits and videos."""
        cache_key = f"movie:{tmdb_id}"
        return await self._get_cached(
            cache_key,
            f"/movie/{tmdb_id}",
            {"append_to_response": "credits,videos", "language": "en-US"},
            DETAIL_TTL_DAYS,
        )

    async def tv_detail(self, tmdb_id: int) -> dict:
        """Fetch TV show details including seasons list and credits."""
        cache_key = f"tv:{tmdb_id}"
        return await self._get_cached(
            cache_key,
            f"/tv/{tmdb_id}",
            {"append_to_response": "credits", "language": "en-US"},
            DETAIL_TTL_DAYS,
        )

    async def season_detail(self, tmdb_id: int, season: int) -> dict:
        """Fetch season details including episode list."""
        cache_key = f"tv:{tmdb_id}:season:{season}"
        return await self._get_cached(
            cache_key,
            f"/tv/{tmdb_id}/season/{season}",
            {"language": "en-US"},
            DETAIL_TTL_DAYS,
        )

    async def person_detail(self, tmdb_person_id: int) -> dict:
        """Fetch person details (biography, birthday, place_of_birth, profile_path)."""
        cache_key = f"person:{tmdb_person_id}"
        return await self._get_cached(
            cache_key,
            f"/person/{tmdb_person_id}",
            {"language": "en-US"},
            DETAIL_TTL_DAYS,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def poster_url(path: str | None, size: str = "w342") -> str | None:
        """Convert a TMDB poster_path to a full URL."""
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE}/{size}{path}"

    @staticmethod
    def backdrop_url(path: str | None, size: str = "w1280") -> str | None:
        """Convert a TMDB backdrop_path to a full URL."""
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE}/{size}{path}"

    # ------------------------------------------------------------------
    # Cache internals
    # ------------------------------------------------------------------

    async def _get_cached(
        self,
        cache_key: str,
        path: str,
        params: dict,
        ttl_days: int,
    ) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=ttl_days)
        cached: TMDBCache | None = self._db.get(TMDBCache, cache_key)

        if cached is not None and cached.fetched_at > cutoff:
            return json.loads(cached.data_json)

        # Cache miss or expired — fetch from TMDB
        if self._api_key_param:
            params = {**params, "api_key": self._api_key_param}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{TMDB_BASE_URL}{path}",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            data: dict = resp.json()

        now = datetime.utcnow()
        serialised = json.dumps(data)
        if cached is not None:
            cached.data_json = serialised
            cached.fetched_at = now
        else:
            self._db.add(
                TMDBCache(cache_key=cache_key, data_json=serialised, fetched_at=now)
            )
        self._db.commit()

        return data

    def invalidate(self, cache_key: str) -> None:
        """Remove a cache entry (useful in tests)."""
        cached = self._db.get(TMDBCache, cache_key)
        if cached is not None:
            self._db.delete(cached)
            self._db.commit()
