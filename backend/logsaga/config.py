"""
Application configuration loaded from environment variables / .env file.

All session rule values are stored here so that the settings table in SQLite
can snapshot the active values at rebuild time, making rebuilds reproducible.
"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Kodi MariaDB (read-only) ---
    kodi_db_host: str = ""
    kodi_db_port: int = 3306
    kodi_db_user: str = "logsaga"
    kodi_db_password: str
    kodi_db_name: str = ""  # empty = auto-detect highest MyVideos%

    # --- Kodi JSON-RPC ---
    kodi_jsonrpc_url: str = ""
    kodi_jsonrpc_user: str = ""
    kodi_jsonrpc_password: str = ""

    # --- Polling ---
    poll_seconds: int = 30
    library_sync_hours: float = 2.0  # interval between full library syncs

    # --- Timezone ---
    # Must match the Kodi client's local timezone (lastPlayed is written in client local time).
    tz: str = "America/Los_Angeles"

    # --- MQTT (Home Assistant discovery) ---
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_discovery_prefix: str = "homeassistant"

    # --- TMDB (optional — search and manual history features; leave blank to disable) ---
    tmdb_api_key: str = ""

    # --- App ---
    public_base_url: str = "http://localhost:8790"
    log_level: str = "INFO"
    build_version: str = "dev"  # set at build time via BUILD_VERSION env/ARG
    # SQLite database path (bare file path, not a URL)
    logsaga_db_path: str = "logsaga.db"

    # --- Artwork ---
    # Where downloaded artwork is stored. Inside the Docker container this is
    # /config/artwork (on the /config volume). For local dev, override as needed.
    artwork_dir: Path = Path("/config/artwork")
    # Maximum pixels on the longest side for WebP thumbnails (0 = no thumbnail)
    artwork_thumbnail_max_px: int = 400

    # --- Session rules (confirmed 2026-11-04) ---
    min_started_seconds: int = 120       # below this + no completion = ignored
    session_gap_hours: int = 6           # activity within this window merges into one session
    watched_percent: int = 90            # % of runtime that counts as watched
    watched_remaining_minutes_movie: int = 8    # credits window for movies
    watched_remaining_minutes_episode: int = 3  # credits window for episodes

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.mqtt_host)

    @property
    def tmdb_enabled(self) -> bool:
        return bool(self.tmdb_api_key)


def get_settings() -> Settings:
    """Return a Settings instance. Import and call this wherever config is needed."""
    return Settings()
