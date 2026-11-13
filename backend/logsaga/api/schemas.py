"""
Pydantic v2 response schemas for the LogSAGA REST API.

All models use from_attributes=True so they can be built directly from
SQLAlchemy ORM objects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, PlainSerializer

# Naive datetimes stored in SQLite are always UTC.
# Append Z so JavaScript (date-fns parseISO) treats them as UTC, not local time.
UTCDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda v: v.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        return_type=str,
        when_used="json",
    ),
]


# ---------------------------------------------------------------------------
# Base config
# ---------------------------------------------------------------------------
class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Artwork
# ---------------------------------------------------------------------------
class ArtworkOut(_Base):
    art_type: str
    local_path: Optional[str] = None
    thumbnail_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
class SessionOut(_Base):
    id: int
    started_at: UTCDatetime
    last_activity_at: UTCDatetime
    ended_at: Optional[UTCDatetime] = None
    completed_at: Optional[UTCDatetime] = None
    max_position_s: Optional[float] = None
    runtime_s: Optional[int] = None
    percent: Optional[float] = None
    status: str
    completion_reason: Optional[str] = None
    source: str


# ---------------------------------------------------------------------------
# Observations (shown in item-detail "debug" toggle)
# ---------------------------------------------------------------------------
class ObservationOut(_Base):
    id: int
    kind: str
    observed_at: UTCDatetime
    kodi_play_count: Optional[int] = None
    resume_pos_s: Optional[float] = None
    resume_total_s: Optional[float] = None


# ---------------------------------------------------------------------------
# Media items
# ---------------------------------------------------------------------------
class MediaItemSummaryOut(_Base):
    """Compact representation used in list/recent responses."""
    id: int
    media_type: str
    title: str
    show_title: Optional[str] = None
    show_id: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    runtime_s: Optional[int] = None
    kodi_state: str
    # Computed artwork URLs — not ORM fields; set by route handlers
    poster_url: Optional[str] = None
    thumbnail_url: Optional[str] = None


class MediaItemDetailOut(_Base):
    """Full representation used in /api/items/{id}."""
    id: int
    media_type: str
    title: str
    show_title: Optional[str] = None
    show_id: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    plot: Optional[str] = None
    runtime_s: Optional[int] = None
    genres: Optional[str] = None
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    release_date: Optional[str] = None
    director: Optional[str] = None
    video_codec: Optional[str] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    hdr_type: Optional[str] = None
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    kodi_state: str
    created_at: UTCDatetime
    updated_at: UTCDatetime
    artwork: list[ArtworkOut] = []
    sessions: list[SessionOut] = []         # newest first
    observations: list[ObservationOut] = [] # newest first
    cast: list[CastMemberOut] = []          # top 20 cast members (lazy-populated)


# ---------------------------------------------------------------------------
# Lists / paginated responses
# ---------------------------------------------------------------------------
class RecentItemOut(BaseModel):
    """One entry in /api/recent and /api/sessions — a session + its item."""
    session: SessionOut
    item: MediaItemSummaryOut


class SessionListOut(BaseModel):
    total: int
    items: list[RecentItemOut]


# ---------------------------------------------------------------------------
# In-progress
# ---------------------------------------------------------------------------
class InProgressOut(BaseModel):
    item: MediaItemSummaryOut
    resume_pos_s: Optional[float] = None
    resume_total_s: Optional[float] = None
    percent: Optional[float] = None


# ---------------------------------------------------------------------------
# TV shows
# ---------------------------------------------------------------------------
class NextUnwatchedOut(BaseModel):
    """The next episode to watch for a TV show (lowest unwatched S/E)."""
    item_id: int
    season: int
    episode: int
    title: str


class ShowSummaryOut(BaseModel):
    show_id: int                           # kodi_show.id (integer URL key)
    show_title: str
    total_episodes: int
    watched_episodes: int
    kodi_state: str
    last_activity_at: Optional[UTCDatetime] = None
    poster_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    next_unwatched: Optional[NextUnwatchedOut] = None


# ---------------------------------------------------------------------------
# Show detail (L-08)
# ---------------------------------------------------------------------------
class EpisodeSummaryOut(BaseModel):
    """One episode in a season listing."""
    id: int                              # media_item.id
    episode: int
    title: str
    release_date: Optional[str] = None
    runtime_s: Optional[int] = None
    kodi_state: str
    watched: bool                        # has at least one completed session
    plot: Optional[str] = None
    thumb_url: Optional[str] = None
    last_session_at: Optional[UTCDatetime] = None  # most recent completed session time


class SeasonSummaryOut(BaseModel):
    season: int
    total_episodes: int
    watched_episodes: int
    episodes: list[EpisodeSummaryOut] = []
    show_tmdb_id: Optional[str] = None


class ShowDetailOut(BaseModel):
    show_id: int
    show_title: str
    plot: Optional[str] = None
    year: Optional[int] = None
    premiered: Optional[str] = None
    studio: Optional[str] = None
    status: Optional[str] = None
    genres: Optional[str] = None
    tvdb_id: Optional[str] = None
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    kodi_state: str
    total_episodes: int
    watched_episodes: int
    poster_url: Optional[str] = None
    fanart_url: Optional[str] = None
    logo_url: Optional[str] = None
    seasons: list[SeasonSummaryOut] = []
    cast: list[CastMemberOut] = []          # top 20 cast members (lazy-populated)


# ---------------------------------------------------------------------------
# Recently added (L-10)
# ---------------------------------------------------------------------------
class RecentlyAddedItemOut(BaseModel):
    id: int
    media_type: str
    title: str
    show_title: Optional[str] = None
    show_id: Optional[int] = None        # kodi_show.id — for episode show links
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    date_added: Optional[str] = None
    kodi_state: str
    poster_url: Optional[str] = None
    thumbnail_url: Optional[str] = None


class RecentlyAddedListOut(BaseModel):
    total: int
    items: list[RecentlyAddedItemOut]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
class DailyStatOut(BaseModel):
    date: str           # YYYY-MM-DD
    minutes_watched: int
    sessions_count: int


class ShowStatOut(BaseModel):
    show_title: str
    watched_episodes: int
    total_minutes: int
    show_id: Optional[int] = None   # kodi_show.id — for navigation


class StatsOut(BaseModel):
    total_sessions: int
    completed_sessions: int
    partial_sessions: int
    total_minutes_watched: int
    movies_watched: int
    episodes_watched: int
    completion_rate: float
    daily: list[DailyStatOut]       # last N days (default 30)
    top_shows: list[ShowStatOut]    # top 10 by watched episodes
    total_rewatches: int = 0        # items with >= 2 completed sessions
    movie_minutes: int = 0          # watch time for movies
    episode_minutes: int = 0        # watch time for episodes
    longest_session_minutes: int = 0
    current_streak_days: int = 0    # consecutive days ending today/yesterday
    longest_streak_days: int = 0    # all-time longest streak


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------
class MovieSummaryOut(BaseModel):
    id: int
    title: str
    year: Optional[int] = None
    runtime_s: Optional[int] = None
    genres: Optional[str] = None
    kodi_state: str
    poster_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    total_sessions: int
    last_watched_at: Optional[UTCDatetime] = None


class MovieListOut(BaseModel):
    total: int
    items: list[MovieSummaryOut]


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class ArtworkStatusOut(BaseModel):
    total_items: int
    items_with_artwork: int
    total_artwork_rows: int
    pending: int


class CastStatusOut(BaseModel):
    total_items: int
    items_with_cast: int
    total_shows: int
    shows_with_cast: int
    pending_items: int
    pending_shows: int


class RebuildSessionsOut(BaseModel):
    items_processed: int
    sessions_created: int
    message: str


class AdminSettingsOut(BaseModel):
    min_started_seconds: int
    session_gap_hours: float
    watched_percent: int
    watched_remaining_minutes_movie: int
    watched_remaining_minutes_episode: int
    poll_seconds: int
    library_sync_hours: float


class AdminSettingsIn(BaseModel):
    min_started_seconds: Optional[int] = None
    session_gap_hours: Optional[float] = None
    watched_percent: Optional[int] = None
    watched_remaining_minutes_movie: Optional[int] = None
    watched_remaining_minutes_episode: Optional[int] = None
    poll_seconds: Optional[int] = None
    library_sync_hours: Optional[float] = None


class JobStateOut(BaseModel):
    name: str
    running: bool
    last_run: Optional[UTCDatetime] = None
    last_result: Optional[str] = None


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str         # "ok" | "degraded" | "error"
    version: str = "dev"
    kodi_db_name: Optional[str] = None
    last_poll_at: Optional[UTCDatetime] = None
    last_success_at: Optional[UTCDatetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[UTCDatetime] = None
    poll_count: int = 0
    observation_count: int = 0
    error_count: int = 0
    settings: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
class SearchResultOut(BaseModel):
    """One entry in GET /api/search results."""
    source: str                      # "local" or "tmdb"
    media_type: str                  # "movie", "episode", or "tv" (TMDB shows)
    tmdb_id: Optional[str] = None
    local_id: Optional[int] = None   # MediaItem.id if in local DB
    show_id: Optional[int] = None    # KodiShow.id for local episodes/shows
    title: str
    show_title: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    poster_url: Optional[str] = None  # local thumb if available, else TMDB URL
    has_history: bool = False          # True if item has at least one WatchSession


class SearchOut(BaseModel):
    results: list[SearchResultOut]
    tmdb_enabled: bool


# ---------------------------------------------------------------------------
# TMDB detail responses (sourced from TMDB API, cached in tmdb_cache)
# ---------------------------------------------------------------------------
class CastMemberOut(BaseModel):
    """Cast member on a local item or show (populated from tmdb_cache)."""
    tmdb_person_id: int
    name: str
    character: Optional[str] = None
    credit_order: int
    profile_url: Optional[str] = None  # TMDB CDN URL


class ActorAppearanceOut(BaseModel):
    """One movie or show entry on an actor's profile page."""
    media_type: str                    # "movie" or "tv"
    title: str
    item_id: Optional[int] = None      # media_item.id for movies
    show_id: Optional[int] = None      # kodi_show.id for TV shows
    character: Optional[str] = None
    # TV shows
    total_episodes: Optional[int] = None
    watched_episodes: Optional[int] = None
    # Movies
    watched: bool = False
    last_watched_at: Optional[UTCDatetime] = None
    poster_url: Optional[str] = None


class ActorDetailOut(BaseModel):
    """Full actor profile (GET /api/actors/{tmdb_person_id})."""
    tmdb_person_id: int
    name: str
    biography: Optional[str] = None
    birthday: Optional[str] = None
    birthplace: Optional[str] = None
    profile_url: Optional[str] = None
    watched_items: list[ActorAppearanceOut] = []
    library_items: list[ActorAppearanceOut] = []


class TMDBPersonOut(BaseModel):
    name: str
    job: Optional[str] = None           # for crew
    character: Optional[str] = None     # for cast
    tmdb_person_id: Optional[int] = None   # TMDB person ID
    profile_url: Optional[str] = None      # TMDB CDN portrait URL


class TMDBEpisodeOut(BaseModel):
    season: int
    episode: int
    title: str
    overview: Optional[str] = None
    air_date: Optional[str] = None   # "YYYY-MM-DD" or None
    runtime: Optional[int] = None    # minutes
    still_url: Optional[str] = None  # TMDB still image URL


class TMDBSeasonOut(BaseModel):
    tmdb_id: int            # TV show TMDB ID
    season: int
    title: str
    overview: Optional[str] = None
    air_date: Optional[str] = None
    poster_url: Optional[str] = None
    episodes: list[TMDBEpisodeOut] = []
    # local_id is populated if the show exists in the local DB
    local_show_id: Optional[int] = None


class TMDBSeasonSummaryOut(BaseModel):
    season: int
    title: str
    episode_count: int
    air_date: Optional[str] = None
    poster_url: Optional[str] = None


class TMDBTVOut(BaseModel):
    tmdb_id: int
    title: str
    overview: Optional[str] = None
    first_air_date: Optional[str] = None
    status: Optional[str] = None
    genres: list[str] = []
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    vote_average: Optional[float] = None
    seasons: list[TMDBSeasonSummaryOut] = []
    cast: list[TMDBPersonOut] = []
    # local_show_id is set if this show exists in the local KodiShow table
    local_show_id: Optional[int] = None


class TMDBMovieOut(BaseModel):
    tmdb_id: int
    title: str
    overview: Optional[str] = None
    release_date: Optional[str] = None   # "YYYY-MM-DD"
    runtime: Optional[int] = None        # minutes
    genres: list[str] = []
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    vote_average: Optional[float] = None
    director: Optional[str] = None
    cast: list[TMDBPersonOut] = []
    # local_id is set if this movie exists in the local MediaItem table
    local_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Recently viewed (TMDB browse history)
# ---------------------------------------------------------------------------
class RecentlyViewedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_type: str       # "movie" or "tv"
    tmdb_id: int
    title: str
    poster_url: Optional[str] = None
    viewed_at: UTCDatetime


# ---------------------------------------------------------------------------
# Manual watch history (POST /api/watched)
# ---------------------------------------------------------------------------
class WatchedItemIn(BaseModel):
    """One item to mark as watched."""
    media_type: str              # "movie" or "episode"
    tmdb_id: int                 # For episodes: the TV show's TMDB ID
    season: Optional[int] = None   # Episodes only
    episode: Optional[int] = None  # Episodes only
    watched_at: datetime           # UTC ISO 8601


class PostWatchedIn(BaseModel):
    items: list[WatchedItemIn]


class PostWatchedOut(BaseModel):
    created: int
    skipped: int
    errors: list[str] = []


# ---------------------------------------------------------------------------
# Actor TMDB recommendations
# ---------------------------------------------------------------------------
class TMDBDiscoveryItem(BaseModel):
    """A TMDB movie or TV show surfaced as a recommendation (not in local library)."""
    tmdb_id: int
    media_type: str          # "movie" or "tv"
    title: str
    poster_url: Optional[str] = None
    year: Optional[int] = None
    popularity: Optional[float] = None


class ActorRecommendationsOut(BaseModel):
    """TMDB credits for an actor filtered to items not in the local library."""
    most_popular: list[TMDBDiscoveryItem] = []
    most_recent: list[TMDBDiscoveryItem] = []


# ---------------------------------------------------------------------------
# Dashboard summary stats
# ---------------------------------------------------------------------------
class DashboardStatsOut(BaseModel):
    """Lightweight library + history counts for the dashboard page."""
    total_movies: int = 0
    total_shows: int = 0
    total_episodes: int = 0
    movie_plays: int = 0      # completed sessions with media_type=movie
    episode_plays: int = 0    # completed sessions with media_type=episode
