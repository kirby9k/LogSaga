"""
SQLAlchemy 2.x declarative models for the LogSAGA SQLite database.

Tables:
  media_item       — stable item identity (movie or episode)
  kodi_file_state  — last-seen Kodi state per file (snapshot for diff)
  kodi_observation — append-only raw change events from the poller
  watch_session    — derived sessions (rebuildable from observations)
  artwork          — cached artwork: source URL, local file, thumbnail
  sync_state       — single-row health/status record (id always = 1)
  settings         — active session-rule values (key/value store)

All timestamps are stored in UTC.
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.engine import Engine


# ---------------------------------------------------------------------------
# Enable WAL mode for SQLite on every new connection
# ---------------------------------------------------------------------------
@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")  # wait up to 30s for write lock
    cursor.close()


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class MediaType(str, enum.Enum):
    movie = "movie"
    episode = "episode"


class KodiState(str, enum.Enum):
    watched = "watched"
    unwatched = "unwatched"
    in_progress = "in_progress"
    removed = "removed"


class ObservationKind(str, enum.Enum):
    resume_set = "resume_set"
    resume_moved = "resume_moved"
    resume_cleared = "resume_cleared"
    lastplayed_changed = "lastplayed_changed"
    playcount_up = "playcount_up"
    playcount_cleared = "playcount_cleared"
    file_removed = "file_removed"


class SessionStatus(str, enum.Enum):
    open = "open"
    completed = "completed"
    partial = "partial"
    ignored = "ignored"


class CompletionReason(str, enum.Enum):
    position = "position"           # reached WATCHED_PERCENT or credits window
    kodi_playcount = "kodi_playcount"  # Kodi bumped playCount (natural end or Device 2)
    kodi_marked = "kodi_marked"    # "Mark as watched" from Kodi menu (future: distinguishable)
    manual = "manual"              # manually added via POST /api/watched


class SessionSource(str, enum.Enum):
    poll = "poll"
    backfill = "backfill"
    backfill_prior = "backfill_prior"  # placeholder for plays before backfill with unknown date
    manual = "manual"              # manually added via POST /api/watched


# ---------------------------------------------------------------------------
# kodi_show — TV show identity and metadata (one row per show)
# ---------------------------------------------------------------------------
class KodiShow(Base):
    __tablename__ = "kodi_show"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Kodi's internal show ID (not stable across library rebuilds — used for joins only)
    kodi_idShow: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Metadata
    title: Mapped[str] = mapped_column(Text, nullable=False)
    plot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    premiered: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # YYYY-MM-DD
    studio: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)   # network/studio
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)    # Ended/Continuing
    genres: Mapped[Optional[str]] = mapped_column(Text, nullable=True)          # slash-separated

    # Stable external IDs
    tvdb_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    imdb_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tmdb_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Derived state (updated by library sync)
    kodi_state: Mapped[str] = mapped_column(
        Enum(KodiState), nullable=False, default=KodiState.unwatched
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    episodes: Mapped[list["MediaItem"]] = relationship(
        back_populates="kodi_show", foreign_keys="MediaItem.show_id"
    )

    __table_args__ = (
        UniqueConstraint("kodi_idShow", name="uq_kodi_show_kodi_idShow"),
        Index("ix_kodi_show_title", "title"),
    )


# ---------------------------------------------------------------------------
# media_item — stable item identity
# ---------------------------------------------------------------------------
class MediaItem(Base):
    __tablename__ = "media_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_type: Mapped[str] = mapped_column(Enum(MediaType), nullable=False)

    # Human-readable labels
    title: Mapped[str] = mapped_column(Text, nullable=False)
    show_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # episodes only
    season: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)    # episodes only
    episode: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # episodes only
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # seconds
    genres: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # slash-separated

    # Technical metadata (populated on first seen and by enrich-metadata)
    release_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    director: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    video_codec: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    video_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    video_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hdr_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    audio_channels: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Library fields (added in migration 0004)
    date_added: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )  # 'YYYY-MM-DD HH:MM:SS' as stored by Kodi; sentinel '1951-09-25 00:00:00' when NULL
    show_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("kodi_show.id"), nullable=True
    )  # FK to kodi_show for episodes; NULL for movies

    # Stable external IDs (from Kodi's uniqueid table)
    imdb_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tmdb_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tvdb_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Last-known Kodi internal IDs (unstable across rebuilds — do not rely on for identity)
    kodi_file_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kodi_media_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Kodi's current view of this item (updated by the poller)
    kodi_state: Mapped[str] = mapped_column(
        Enum(KodiState), nullable=False, default=KodiState.unwatched
    )

    # Hash of scraped metadata fields — used to detect when a re-scrape changes the data
    metadata_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    kodi_show: Mapped[Optional["KodiShow"]] = relationship(
        back_populates="episodes", foreign_keys=[show_id]
    )
    observations: Mapped[list["KodiObservation"]] = relationship(
        back_populates="media_item", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["WatchSession"]] = relationship(
        back_populates="media_item", cascade="all, delete-orphan"
    )
    artwork: Mapped[list["Artwork"]] = relationship(
        back_populates="media_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_media_item_kodi_file_id", "kodi_file_id"),
        Index("ix_media_item_imdb_id", "imdb_id"),
        Index("ix_media_item_tmdb_id", "tmdb_id"),
        Index("ix_media_item_tvdb_id", "tvdb_id"),
        Index("ix_media_item_show_id", "show_id"),
    )


# ---------------------------------------------------------------------------
# kodi_file_state — snapshot of last-seen Kodi state per file
# ---------------------------------------------------------------------------
class KodiFileState(Base):
    __tablename__ = "kodi_file_state"

    kodi_file_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    play_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_played: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )  # 'YYYY-MM-DD HH:MM:SS' as Kodi wrote it
    resume_pos_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resume_total_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# kodi_observation — append-only raw change events
# ---------------------------------------------------------------------------
class KodiObservation(Base):
    __tablename__ = "kodi_observation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_item.id"), nullable=False
    )
    kodi_file_id: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[str] = mapped_column(Enum(ObservationKind), nullable=False)

    # UTC timestamp of when the poller observed this change
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Kodi state values at time of observation (from the new snapshot)
    kodi_last_played: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    kodi_play_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resume_pos_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resume_total_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    media_item: Mapped["MediaItem"] = relationship(back_populates="observations")

    __table_args__ = (
        Index("ix_kodi_observation_media_item_id", "media_item_id"),
        Index("ix_kodi_observation_observed_at", "observed_at"),
        Index("ix_kodi_observation_kodi_file_id", "kodi_file_id"),
    )


# ---------------------------------------------------------------------------
# watch_session — derived sessions (rebuildable)
# ---------------------------------------------------------------------------
class WatchSession(Base):
    __tablename__ = "watch_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_item.id"), nullable=False
    )

    # Timestamps (all UTC)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Position data
    max_position_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    runtime_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Classification
    status: Mapped[str] = mapped_column(
        Enum(SessionStatus), nullable=False, default=SessionStatus.open
    )
    completion_reason: Mapped[Optional[str]] = mapped_column(
        Enum(CompletionReason), nullable=True
    )
    source: Mapped[str] = mapped_column(
        Enum(SessionSource), nullable=False, default=SessionSource.poll
    )
    device: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    media_item: Mapped["MediaItem"] = relationship(back_populates="sessions")

    __table_args__ = (
        Index("ix_watch_session_media_item_id", "media_item_id"),
        Index("ix_watch_session_started_at", "started_at"),
        Index("ix_watch_session_status", "status"),
    )


# ---------------------------------------------------------------------------
# sync_state — single-row health record (id always = 1)
# ---------------------------------------------------------------------------
class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    kodi_db_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# settings — active rule values (key/value, snapshotted at rebuild time)
# ---------------------------------------------------------------------------
class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# artwork — cached downloaded artwork per media item
# ---------------------------------------------------------------------------
class Artwork(Base):
    __tablename__ = "artwork"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_item.id"), nullable=False
    )

    # Art type as stored in Kodi's art table, e.g. 'poster', 'fanart', 'thumb',
    # 'tvshow.poster', 'season.poster', 'clearlogo', etc.
    art_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Raw URL as stored in Kodi's art.url column (before any image:// wrapping)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Path to the downloaded file, relative to artwork_dir
    # e.g. "movie/42/poster.jpg"
    local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Path to the WebP thumbnail, relative to artwork_dir
    # e.g. "movie/42/poster_thumb.webp"
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # File size in bytes
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Last successful fetch time (UTC)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Last fetch error message (None if last fetch succeeded)
    fetch_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    media_item: Mapped["MediaItem"] = relationship(back_populates="artwork")

    __table_args__ = (
        Index("ix_artwork_media_item_id", "media_item_id"),
        # One row per (item, art_type) — a media item has at most one of each art type
        UniqueConstraint("media_item_id", "art_type", name="uq_artwork_item_type"),
    )


# ---------------------------------------------------------------------------
# tmdb_cache — 30-day TTL JSON cache for TMDB API responses
# ---------------------------------------------------------------------------
class TMDBCache(Base):
    __tablename__ = "tmdb_cache"

    # Composite key: "movie:{id}", "tv:{id}", "tv:{id}:season:{n}", "search:{q}:{type}"
    cache_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ---------------------------------------------------------------------------
# recently_viewed — server-side log of TMDB pages browsed (not yet added)
# ---------------------------------------------------------------------------
class RecentlyViewed(Base):
    __tablename__ = "recently_viewed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "movie" or "tv"
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    poster_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # TMDB poster URL
    viewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_recently_viewed_viewed_at", "viewed_at"),
        UniqueConstraint("media_type", "tmdb_id", name="uq_recently_viewed_tmdb"),
    )
