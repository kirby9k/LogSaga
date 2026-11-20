"""
Tests for backup/manager.py — backup, verify, restore, stripped-copy.

All tests use temporary SQLite files via pytest's tmp_path fixture.
No live database is ever touched.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from logsaga.backup.manager import (
    BackupInfo,
    backup_dir,
    create_backup,
    list_backups,
    make_stripped_copy,
    restore_from_zip,
    verify_backup,
)

SCHEMA_VERSION = "0009_ratings"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> str:
    """Create a minimal SQLite database that backup/restore can work with."""
    db_path = str(path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('{SCHEMA_VERSION}');
            CREATE TABLE watch_session (id INTEGER PRIMARY KEY);
            CREATE TABLE media_item (id INTEGER PRIMARY KEY);
            CREATE TABLE kodi_show (id INTEGER PRIMARY KEY);
            CREATE TABLE tmdb_cache (id INTEGER PRIMARY KEY, data TEXT);
            INSERT INTO tmdb_cache VALUES (1, 'should_be_excluded');
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO settings VALUES ('kodi_db_password', 'topsecret');
            INSERT INTO settings VALUES ('poll_seconds', '30');
            """
        )
    return db_path


def _extract_db_from_zip(zip_path: Path, extract_to: Path) -> Path:
    """Extract logsaga.db from a backup ZIP into extract_to, return its path."""
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as zf:
        zf.extract("logsaga.db", str(extract_to))
    return extract_to / "logsaga.db"


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return _make_db(tmp_path / "logsaga.db")


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------

class TestCreateBackup:
    def test_returns_backup_info(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        assert isinstance(info, BackupInfo)
        assert info.filename.startswith("logsaga-backup-")
        assert info.filename.endswith(".zip")
        assert info.size_bytes > 0
        assert info.schema_version == SCHEMA_VERSION

    def test_backup_file_exists_on_disk(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        assert info.path.exists()

    def test_backup_stored_in_backups_subdir(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        assert info.path.parent == backup_dir(tmp_db)

    def test_backup_is_valid_zip(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        assert zipfile.is_zipfile(str(info.path))

    def test_tmdb_cache_excluded(self, tmp_db: str, tmp_path: Path) -> None:
        """tmdb_cache rows must not appear inside the backup's database."""
        info = create_backup(tmp_db)
        extracted = _extract_db_from_zip(info.path, tmp_path / "extracted")
        with sqlite3.connect(str(extracted)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tmdb_cache").fetchone()[0]
        assert count == 0

    def test_source_db_tmdb_cache_untouched(self, tmp_db: str) -> None:
        """Creating a backup must not remove rows from the source database."""
        create_backup(tmp_db)
        with sqlite3.connect(tmp_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tmdb_cache").fetchone()[0]
        assert count == 1

    def test_meta_json_contains_schema_version(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        with zipfile.ZipFile(str(info.path)) as zf:
            meta = json.loads(zf.read("meta.json"))
        assert meta["schema_version"] == SCHEMA_VERSION
        assert meta["credentials_stripped"] is False


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------

class TestListBackups:
    def test_empty_list_when_no_backups(self, tmp_db: str) -> None:
        assert list_backups(tmp_db) == []

    def test_returns_backup_after_create(self, tmp_db: str) -> None:
        create_backup(tmp_db)
        assert len(list_backups(tmp_db)) == 1

    def test_multiple_backups_all_listed(self, tmp_db: str) -> None:
        # Place two dummy ZIP files with distinct timestamps in the backup dir
        # (create_backup uses second-level filenames; calling it twice in the same
        # second produces the same filename and overwrites).
        bdir = backup_dir(tmp_db)
        bdir.mkdir(parents=True, exist_ok=True)
        for name in (
            "logsaga-backup-2026-01-01T10-00-00.zip",
            "logsaga-backup-2026-01-01T10-00-01.zip",
        ):
            with zipfile.ZipFile(str(bdir / name), "w") as zf:
                zf.writestr("placeholder.txt", "test")
        assert len(list_backups(tmp_db)) == 2


# ---------------------------------------------------------------------------
# verify_backup
# ---------------------------------------------------------------------------

class TestVerifyBackup:
    def test_valid_backup_ok(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        result = verify_backup(info.path, SCHEMA_VERSION)
        assert result.ok is True
        assert result.error is None
        assert result.app_compatible is True
        assert result.schema_version == SCHEMA_VERSION

    def test_nonexistent_file_returns_error(self, tmp_path: Path) -> None:
        result = verify_backup(tmp_path / "ghost.zip", SCHEMA_VERSION)
        assert result.ok is False
        assert result.error is not None

    def test_incompatible_schema_flagged(self, tmp_db: str) -> None:
        """A backup with an older schema version should report app_compatible=False."""
        info = create_backup(tmp_db)
        result = verify_backup(info.path, "0001_initial")
        assert result.ok is True          # still a valid, intact backup
        assert result.app_compatible is False

    def test_invalid_zip_returns_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip file")
        result = verify_backup(bad, SCHEMA_VERSION)
        assert result.ok is False


# ---------------------------------------------------------------------------
# restore_from_zip
# ---------------------------------------------------------------------------

class TestRestoreFromZip:
    def test_restores_to_new_path(self, tmp_db: str, tmp_path: Path) -> None:
        """
        Restore targets a caller-specified path.
        This proves we never implicitly touch the source or any other file.
        """
        info = create_backup(tmp_db)
        restore_target = str(tmp_path / "restored.db")

        restore_from_zip(info.path, restore_target)

        assert Path(restore_target).exists()
        with sqlite3.connect(restore_target) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row[0] == SCHEMA_VERSION

    def test_restored_db_passes_integrity_check(self, tmp_db: str, tmp_path: Path) -> None:
        info = create_backup(tmp_db)
        restore_target = str(tmp_path / "restored.db")
        restore_from_zip(info.path, restore_target)
        with sqlite3.connect(restore_target) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
        assert result[0] == "ok"

    def test_source_db_unchanged_after_restore(self, tmp_db: str, tmp_path: Path) -> None:
        """Source DB must not be modified when restoring to a different path."""
        info = create_backup(tmp_db)
        restore_from_zip(info.path, str(tmp_path / "restored.db"))
        with sqlite3.connect(tmp_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tmdb_cache").fetchone()[0]
        assert count == 1  # source still has its original data

    def test_source_zip_not_modified(self, tmp_db: str, tmp_path: Path) -> None:
        """The backup ZIP itself must not be modified by a restore."""
        info = create_backup(tmp_db)
        mtime_before = info.path.stat().st_mtime
        restore_from_zip(info.path, str(tmp_path / "restored.db"))
        assert info.path.stat().st_mtime == mtime_before

    def test_raises_on_nonexistent_zip(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            restore_from_zip(tmp_path / "ghost.zip", str(tmp_path / "out.db"))

    def test_raises_on_invalid_zip(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"garbage bytes")
        with pytest.raises(ValueError):
            restore_from_zip(bad, str(tmp_path / "out.db"))


# ---------------------------------------------------------------------------
# make_stripped_copy
# ---------------------------------------------------------------------------

class TestMakeStrippedCopy:
    def test_credentials_stripped(self, tmp_db: str, tmp_path: Path) -> None:
        info = create_backup(tmp_db)
        stripped = make_stripped_copy(info.path)
        try:
            extracted = _extract_db_from_zip(stripped, tmp_path / "stripped")
            with sqlite3.connect(str(extracted)) as conn:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key = 'kodi_db_password'"
                ).fetchone()
            assert row is None
        finally:
            stripped.unlink(missing_ok=True)

    def test_non_credentials_retained(self, tmp_db: str, tmp_path: Path) -> None:
        info = create_backup(tmp_db)
        stripped = make_stripped_copy(info.path)
        try:
            extracted = _extract_db_from_zip(stripped, tmp_path / "stripped2")
            with sqlite3.connect(str(extracted)) as conn:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key = 'poll_seconds'"
                ).fetchone()
            assert row is not None
            assert row[0] == "30"
        finally:
            stripped.unlink(missing_ok=True)

    def test_meta_json_credentials_stripped_flag(self, tmp_db: str) -> None:
        info = create_backup(tmp_db)
        stripped = make_stripped_copy(info.path)
        try:
            with zipfile.ZipFile(str(stripped)) as zf:
                meta = json.loads(zf.read("meta.json"))
            assert meta["credentials_stripped"] is True
        finally:
            stripped.unlink(missing_ok=True)

    def test_source_zip_not_modified(self, tmp_db: str) -> None:
        """make_stripped_copy must not alter the original backup ZIP."""
        info = create_backup(tmp_db)
        mtime_before = info.path.stat().st_mtime
        stripped = make_stripped_copy(info.path)
        try:
            assert info.path.stat().st_mtime == mtime_before
        finally:
            stripped.unlink(missing_ok=True)
