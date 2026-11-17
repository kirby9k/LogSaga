"""
Backup, restore, and verify logic for LogSAGA's SQLite database.

Design decisions:
- Backup format: ZIP containing logsaga.db + meta.json
- tmdb_cache is excluded from all backups (large cache; auto-rebuilt)
- Server-side backups are full (include credentials)
- Downloaded backups have credentials stripped from the settings table
- Restore is a full atomic overwrite — never a merge
- sqlite3.backup() is used for a live consistent snapshot without locking the DB
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Settings keys that contain credentials — stripped from downloaded backups
CREDENTIAL_KEYS: frozenset[str] = frozenset({
    "kodi_db_password",
    "kodi_jsonrpc_password",
    "tmdb_api_key",
    "mqtt_password",
})

# Tables excluded from all backups (caches that auto-rebuild)
EXCLUDED_TABLES: frozenset[str] = frozenset({"tmdb_cache"})

# Name of the SQLite file inside the ZIP
_DB_FILENAME = "logsaga.db"
_META_FILENAME = "meta.json"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BackupInfo:
    filename: str
    path: Path
    size_bytes: int
    created_at: str          # ISO 8601 UTC string
    sessions: Optional[int]
    media_items: Optional[int]
    shows: Optional[int]
    schema_version: Optional[str]


@dataclass
class VerifyResult:
    ok: bool
    error: Optional[str]
    schema_version: Optional[str]
    app_compatible: bool          # schema_version == current alembic version
    sessions: Optional[int]
    media_items: Optional[int]
    shows: Optional[int]
    created_at: Optional[str]
    credentials_stripped: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def backup_dir(db_path: str) -> Path:
    """Return the backup directory (parent of db_path / 'backups')."""
    return Path(db_path).parent / "backups"


def _current_schema(db_path: str) -> Optional[str]:
    """Read the current alembic_version from the live database."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _read_meta(zip_path: Path) -> Optional[dict]:
    """Read and parse meta.json from a backup ZIP. Returns None on any error."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if _META_FILENAME not in zf.namelist():
                return None
            return json.loads(zf.read(_META_FILENAME))
    except Exception:
        return None


def _zip_info_to_backup_info(zip_path: Path) -> BackupInfo:
    """Build a BackupInfo from a zip file on disk."""
    meta = _read_meta(zip_path) or {}
    return BackupInfo(
        filename=zip_path.name,
        path=zip_path,
        size_bytes=zip_path.stat().st_size,
        created_at=meta.get("created_at", ""),
        sessions=meta.get("sessions"),
        media_items=meta.get("media_items"),
        shows=meta.get("shows"),
        schema_version=meta.get("schema_version"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_backup(db_path: str, app_version: str = "dev") -> BackupInfo:
    """
    Create a full backup of the live SQLite database.

    Uses sqlite3.backup() for a consistent snapshot without taking an
    exclusive lock on the source database. After snapshotting, deletes
    tmdb_cache rows from the copy to keep the backup lean.

    Returns a BackupInfo for the created ZIP file.
    Raises OSError or sqlite3.Error on failure.
    """
    bdir = backup_dir(db_path)
    bdir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    zip_name = f"logsaga-backup-{ts}.zip"
    zip_path = bdir / zip_name

    # Count records for meta.json (read from live DB)
    sessions = media_items = shows = None
    schema_version = None
    try:
        with sqlite3.connect(db_path) as src:
            sessions   = src.execute("SELECT COUNT(*) FROM watch_session").fetchone()[0]
            media_items = src.execute("SELECT COUNT(*) FROM media_item").fetchone()[0]
            shows      = src.execute("SELECT COUNT(*) FROM kodi_show").fetchone()[0]
            row = src.execute("SELECT version_num FROM alembic_version").fetchone()
            schema_version = row[0] if row else None
    except Exception as exc:
        logger.warning("backup_count_error", error=str(exc))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / _DB_FILENAME

        # --- Live consistent snapshot via sqlite3.backup() ---
        with sqlite3.connect(db_path) as src, sqlite3.connect(str(tmp_db)) as dst:
            src.backup(dst)

        # --- Remove excluded tables from the copy ---
        with sqlite3.connect(str(tmp_db)) as conn:
            for table in EXCLUDED_TABLES:
                try:
                    conn.execute(f"DELETE FROM {table}")  # noqa: S608 — table names are constants
                except sqlite3.OperationalError:
                    pass  # table doesn't exist in this schema version
            conn.execute("VACUUM")

        # --- Write meta.json ---
        meta = {
            "created_at": now.isoformat(),
            "schema_version": schema_version,
            "sessions": sessions,
            "media_items": media_items,
            "shows": shows,
            "app_version": app_version,
            "credentials_stripped": False,
        }
        meta_path = Path(tmp) / _META_FILENAME
        meta_path.write_text(json.dumps(meta, indent=2))

        # --- Zip ---
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(tmp_db), _DB_FILENAME)
            zf.write(str(meta_path), _META_FILENAME)

    logger.info(
        "backup_created",
        filename=zip_name,
        size_bytes=zip_path.stat().st_size,
        sessions=sessions,
    )
    return _zip_info_to_backup_info(zip_path)


def list_backups(db_path: str) -> list[BackupInfo]:
    """
    Return all backup ZIPs in the backup directory, newest first.
    Returns an empty list if the directory doesn't exist yet.
    """
    bdir = backup_dir(db_path)
    if not bdir.exists():
        return []

    zips = sorted(
        bdir.glob("logsaga-backup-*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    result = []
    for zip_path in zips:
        try:
            result.append(_zip_info_to_backup_info(zip_path))
        except Exception as exc:
            logger.warning("backup_list_skip", filename=zip_path.name, error=str(exc))
    return result


def verify_backup(zip_path: Path, current_schema: str) -> VerifyResult:
    """
    Verify a backup ZIP without modifying anything.

    Checks:
    - ZIP is valid and contains logsaga.db
    - SQLite integrity_check passes
    - alembic_version matches current_schema
    - meta.json is readable

    Returns a VerifyResult. Never raises.
    """
    try:
        if not zip_path.exists():
            return VerifyResult(ok=False, error="File not found", schema_version=None,
                                app_compatible=False, sessions=None, media_items=None,
                                shows=None, created_at=None, credentials_stripped=False)

        if not zipfile.is_zipfile(str(zip_path)):
            return VerifyResult(ok=False, error="Not a valid ZIP file", schema_version=None,
                                app_compatible=False, sessions=None, media_items=None,
                                shows=None, created_at=None, credentials_stripped=False)

        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                names = zf.namelist()
                if _DB_FILENAME not in names:
                    return VerifyResult(ok=False, error="Backup ZIP missing logsaga.db",
                                        schema_version=None, app_compatible=False,
                                        sessions=None, media_items=None, shows=None,
                                        created_at=None, credentials_stripped=False)
                zf.extract(_DB_FILENAME, tmp)
                meta_raw = zf.read(_META_FILENAME) if _META_FILENAME in names else b"{}"

            tmp_db = Path(tmp) / _DB_FILENAME
            meta = json.loads(meta_raw) if meta_raw else {}

            with sqlite3.connect(str(tmp_db)) as conn:
                # Integrity check
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] != "ok":
                    return VerifyResult(ok=False, error=f"Integrity check failed: {result[0]}",
                                        schema_version=None, app_compatible=False,
                                        sessions=None, media_items=None, shows=None,
                                        created_at=None, credentials_stripped=False)

                # Schema version
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                schema_version = row[0] if row else None

        app_compatible = schema_version == current_schema
        return VerifyResult(
            ok=True,
            error=None,
            schema_version=schema_version,
            app_compatible=app_compatible,
            sessions=meta.get("sessions"),
            media_items=meta.get("media_items"),
            shows=meta.get("shows"),
            created_at=meta.get("created_at"),
            credentials_stripped=meta.get("credentials_stripped", False),
        )

    except Exception as exc:
        return VerifyResult(ok=False, error=str(exc), schema_version=None,
                            app_compatible=False, sessions=None, media_items=None,
                            shows=None, created_at=None, credentials_stripped=False)


def restore_from_zip(zip_path: Path, db_path: str) -> None:
    """
    Extract logsaga.db from zip_path and atomically replace db_path.

    Does NOT reload the SQLAlchemy engine — the caller (route handler) must
    do that after this returns.

    Raises ValueError on validation failure, OSError on filesystem failure.
    """
    if not zip_path.exists():
        raise ValueError("Backup file not found")

    if not zipfile.is_zipfile(str(zip_path)):
        raise ValueError("Not a valid ZIP file")

    pending = Path(db_path + ".restore_pending")

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            if _DB_FILENAME not in zf.namelist():
                raise ValueError("Backup ZIP missing logsaga.db")
            zf.extract(_DB_FILENAME, tmp)

        tmp_db = Path(tmp) / _DB_FILENAME

        # Quick integrity check before we commit to replacing
        with sqlite3.connect(str(tmp_db)) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                raise ValueError(f"Backup database failed integrity check: {result[0]}")

        # Copy to pending location (same filesystem as db_path for atomic replace)
        shutil.copy2(str(tmp_db), str(pending))

    # Atomic swap
    os.replace(str(pending), db_path)
    logger.info("restore_complete", db_path=db_path, source=str(zip_path))


def make_stripped_copy(zip_path: Path) -> Path:
    """
    Create a copy of the backup ZIP with credential keys removed from the
    settings table. Returns the path to a temporary file.

    The caller is responsible for deleting the temp file after use.
    """
    # Create a temp file that persists (not in a TemporaryDirectory so caller can serve it)
    fd, tmp_path_str = tempfile.mkstemp(suffix=".zip", prefix="logsaga-download-")
    os.close(fd)
    tmp_zip = Path(tmp_path_str)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            # Extract full backup
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(tmp)

            tmp_db = Path(tmp) / _DB_FILENAME
            meta_path = Path(tmp) / _META_FILENAME

            # Strip credentials from settings table
            with sqlite3.connect(str(tmp_db)) as conn:
                placeholders = ",".join("?" * len(CREDENTIAL_KEYS))
                conn.execute(
                    f"DELETE FROM settings WHERE key IN ({placeholders})",  # noqa: S608
                    list(CREDENTIAL_KEYS),
                )
                conn.execute("VACUUM")

            # Update meta.json to reflect stripping
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                meta["credentials_stripped"] = True
                meta_path.write_text(json.dumps(meta, indent=2))

            # Rezip
            with zipfile.ZipFile(str(tmp_zip), "w", zipfile.ZIP_DEFLATED) as zf:
                if tmp_db.exists():
                    zf.write(str(tmp_db), _DB_FILENAME)
                if meta_path.exists():
                    zf.write(str(meta_path), _META_FILENAME)

    except Exception:
        tmp_zip.unlink(missing_ok=True)
        raise

    return tmp_zip
