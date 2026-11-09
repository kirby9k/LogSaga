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


class SeasonSummaryOut(BaseModel):
    season: int
    total_episodes: int
    watched_episodes: int
    episodes: list[EpisodeSummaryOut] = []


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
    kodi_db_name: Optional[str] = None
    last_poll_at: Optional[UTCDatetime] = None
    last_success_at: Optional[UTCDatetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[UTCDatetime] = None
    poll_count: int = 0
    observation_count: int = 0
    error_count: int = 0
    settings: dict[str, Any] = {}
