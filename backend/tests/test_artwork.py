"""
Tests for artwork/cache.py and kodi/jsonrpc.py URL helpers.

No real network calls, no real Kodi DB. All I/O is mocked or uses tmp_path.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from logsaga.artwork.cache import (
    _art_type_safe,
    _guess_extension,
    _show_title_safe,
    fetch_artwork_for_item,
)
from logsaga.config import Settings
from logsaga.kodi.db import ArtRow
from logsaga.kodi.jsonrpc import classify_art_url, _wrap_image_url
from logsaga.models import Artwork, Base, KodiState, MediaItem


# ---------------------------------------------------------------------------
# Minimal valid JPEG-ish bytes (larger than _MIN_FILE_BYTES = 100)
# ---------------------------------------------------------------------------
FAKE_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 200


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db_session(engine):
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        yield session


def _make_settings(tmp_path: Path, max_px: int = 0) -> Settings:
    return Settings(
        kodi_db_host="127.0.0.1",
        kodi_db_password="test",
        artwork_dir=tmp_path,
        artwork_thumbnail_max_px=max_px,
    )


def _make_item(db, media_type="movie", kodi_media_id=1, show_title=None, season=None) -> MediaItem:
    item = MediaItem(
        media_type=media_type,
        title="Test Item",
        show_title=show_title,
        season=season,
        kodi_media_id=kodi_media_id,
        kodi_state=KodiState.watched,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(item)
    db.flush()
    return item


def _mock_http_response(content: bytes = FAKE_IMAGE, ct: str = "image/jpeg"):
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": ct}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# classify_art_url
# ---------------------------------------------------------------------------
class TestClassifyArtUrl:
    def test_https_is_http(self):
        kind, url = classify_art_url("https://example.com/poster.jpg")
        assert kind == "http"
        assert url == "https://example.com/poster.jpg"

    def test_http_is_http(self):
        kind, _ = classify_art_url("http://example.com/poster.jpg")
        assert kind == "http"

    def test_nfs_is_vfs(self):
        kind, _ = classify_art_url("nfs://192.168.1.50/media/file.jpg")
        assert kind == "vfs"

    def test_smb_is_vfs(self):
        kind, _ = classify_art_url("smb://server/share/art.png")
        assert kind == "vfs"

    def test_plain_text_is_skip(self):
        kind, _ = classify_art_url("Season Finale")
        assert kind == "skip"

    def test_empty_is_skip(self):
        kind, _ = classify_art_url("")
        assert kind == "skip"

    def test_image_wrapped_https_decoded_to_http(self):
        # image:// wrapping of an https URL should resolve to 'http'
        import urllib.parse
        inner = urllib.parse.quote("https://example.com/poster.jpg", safe="")
        url = f"image://{inner}/"
        kind, resolved = classify_art_url(url)
        assert kind == "http"
        assert resolved == "https://example.com/poster.jpg"

    def test_image_wrapped_nfs_decoded_to_vfs(self):
        import urllib.parse
        inner = urllib.parse.quote("nfs://host/file.jpg", safe="")
        url = f"image://{inner}/"
        kind, _ = classify_art_url(url)
        assert kind == "vfs"


# ---------------------------------------------------------------------------
# _wrap_image_url
# ---------------------------------------------------------------------------
class TestWrapImageUrl:
    def test_wraps_nfs_url(self):
        result = _wrap_image_url("nfs://192.168.1.50/path/to/file.jpg")
        assert result.startswith("image://")
        assert result.endswith("/")
        assert "nfs" in result

    def test_round_trips(self):
        import urllib.parse
        original = "nfs://192.168.1.50/media/file.jpg"
        wrapped = _wrap_image_url(original)
        # Strip image:// and trailing /
        inner = wrapped[8:][:-1]
        assert urllib.parse.unquote(inner) == original


# ---------------------------------------------------------------------------
# _art_type_safe
# ---------------------------------------------------------------------------
class TestArtTypeSafe:
    def test_no_special_chars(self):
        assert _art_type_safe("poster") == "poster"

    def test_dot_replaced(self):
        assert _art_type_safe("tvshow.poster") == "tvshow_poster"

    def test_multiple_dots(self):
        assert _art_type_safe("season.1.poster") == "season_1_poster"

    def test_slash_replaced(self):
        assert _art_type_safe("show/fanart") == "show_fanart"


# ---------------------------------------------------------------------------
# _guess_extension
# ---------------------------------------------------------------------------
class TestGuessExtension:
    def test_jpg_from_url(self):
        assert _guess_extension("https://example.com/image.jpg", "") == ".jpg"

    def test_jpeg_normalized_to_jpg(self):
        assert _guess_extension("https://example.com/image.jpeg", "") == ".jpg"

    def test_png_from_url(self):
        assert _guess_extension("https://example.com/img.png", "") == ".png"

    def test_content_type_fallback(self):
        assert _guess_extension("https://example.com/image", "image/png") == ".png"

    def test_content_type_with_charset(self):
        assert _guess_extension("https://example.com/image", "image/jpeg; charset=utf-8") == ".jpg"

    def test_unknown_defaults_to_jpg(self):
        assert _guess_extension("https://example.com/image", "text/html") == ".jpg"


# ---------------------------------------------------------------------------
# _show_title_safe
# ---------------------------------------------------------------------------
class TestShowTitleSafe:
    def test_basic_spaces_lowercased(self):
        assert _show_title_safe("Breaking Bad") == "breaking_bad"

    def test_apostrophe_removed(self):
        assert _show_title_safe("It's Always Sunny in Philadelphia") == "its_always_sunny_in_philadelphia"

    def test_special_chars_removed(self):
        assert _show_title_safe("M*A*S*H") == "mash"

    def test_diacritics_normalized(self):
        # NFKD normalization strips combining diacritics so the result is pure ASCII
        assert _show_title_safe("Sh\u014dgun") == "shogun"  # ō → o

    def test_dots_removed(self):
        # dots are not word chars nor spaces, so they are stripped (not replaced with underscores)
        # "11.22.63" → remove dots → "112263" (digits run together since no surrounding spaces)
        assert _show_title_safe("11.22.63") == "112263"

    def test_hyphens_collapsed(self):
        assert _show_title_safe("Spider-Man: No Way Home") == "spider_man_no_way_home"

    def test_empty_fallback(self):
        assert _show_title_safe("   ") == "unknown_show"

    def test_leading_trailing_underscores_stripped(self):
        result = _show_title_safe("!!! Danger !!!")
        assert not result.startswith("_")
        assert not result.endswith("_")


# ---------------------------------------------------------------------------
# fetch_artwork_for_item
# ---------------------------------------------------------------------------
class TestFetchArtworkForItem:

    def test_http_url_downloaded_and_saved(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)
        art = [ArtRow(art_type="poster", url="https://example.com/poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="poster"
        ).first()
        assert row is not None
        assert row.source_url == "https://example.com/poster.jpg"
        assert row.local_path is not None
        assert row.fetch_error is None
        assert (tmp_path / row.local_path).exists()

    def test_skip_url_produces_no_row(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)
        art = [ArtRow(art_type="customtag", url="Season Finale")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 0
        assert db_session.query(Artwork).filter_by(media_item_id=item.id).first() is None

    def test_vfs_url_calls_prepare_download(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)
        art = [ArtRow(art_type="thumb", url="nfs://192.168.1.50/media/thumb.jpg")]
        mock_conn = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.prepare_download.return_value = "http://192.168.1.50:8086/vfs/nfs%3a..."

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()):
            count = fetch_artwork_for_item(item, mock_conn, mock_rpc, db_session, cfg)

        assert count == 1
        mock_rpc.prepare_download.assert_called_once_with("nfs://192.168.1.50/media/thumb.jpg")

    def test_vfs_url_without_rpc_records_error(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)
        art = [ArtRow(art_type="thumb", url="nfs://192.168.1.50/media/thumb.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="thumb"
        ).first()
        assert row is not None
        assert row.fetch_error is not None
        assert row.local_path is None

    def test_already_cached_same_url_file_exists_skipped(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)

        # Pre-create local file
        local_rel = f"movie/{item.id}/poster.jpg"
        abs_path = tmp_path / local_rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(FAKE_IMAGE)

        existing = Artwork(
            media_item_id=item.id,
            art_type="poster",
            source_url="https://example.com/poster.jpg",
            local_path=local_rel,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(existing)
        db_session.flush()

        art = [ArtRow(art_type="poster", url="https://example.com/poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get") as mock_get:
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        mock_get.assert_not_called()
        assert count == 0

    def test_url_changed_triggers_redownload(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)

        local_rel = f"movie/{item.id}/poster.jpg"
        abs_path = tmp_path / local_rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(FAKE_IMAGE)

        existing = Artwork(
            media_item_id=item.id,
            art_type="poster",
            source_url="https://example.com/OLD.jpg",
            local_path=local_rel,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(existing)
        db_session.flush()

        art = [ArtRow(art_type="poster", url="https://example.com/NEW.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="poster"
        ).first()
        assert row.source_url == "https://example.com/NEW.jpg"

    def test_thumbnail_created_when_configured(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path, max_px=400)
        item = _make_item(db_session)
        art = [ArtRow(art_type="poster", url="https://example.com/poster.jpg")]
        mock_conn = MagicMock()

        thumb_abs = tmp_path / "movie" / str(item.id) / "poster_thumb.webp"

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()), \
             patch("logsaga.artwork.cache._make_thumbnail", return_value=thumb_abs):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="poster"
        ).first()
        assert row.thumbnail_path is not None
        assert "thumb" in row.thumbnail_path

    def test_no_thumbnail_when_max_px_zero(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path, max_px=0)
        item = _make_item(db_session)
        art = [ArtRow(art_type="poster", url="https://example.com/poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()), \
             patch("logsaga.artwork.cache._make_thumbnail") as mock_thumb:
            fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        mock_thumb.assert_not_called()

    def test_download_failure_records_error(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session)
        art = [ArtRow(art_type="poster", url="https://example.com/poster.jpg")]
        mock_conn = MagicMock()

        import httpx as _httpx

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", side_effect=_httpx.ConnectError("connection refused")):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="poster"
        ).first()
        assert row is not None
        assert row.fetch_error is not None
        assert row.local_path is None

    def test_no_kodi_media_id_returns_zero(self, db_session, tmp_path):
        cfg = _make_settings(tmp_path)
        item = MediaItem(
            media_type="movie",
            title="No ID",
            kodi_media_id=None,
            kodi_state=KodiState.unwatched,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(item)
        db_session.flush()
        mock_conn = MagicMock()

        count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)
        assert count == 0

    def test_tvshow_art_saved_to_canonical_shows_dir(self, db_session, tmp_path):
        """tvshow.poster for an episode goes to shows/<show_safe>/ not episode/<id>/."""
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session, media_type="episode", show_title="Breaking Bad")
        art = [
            ArtRow(art_type="thumb", url="https://example.com/ep_thumb.jpg"),
            ArtRow(art_type="tvshow.poster", url="https://example.com/show_poster.jpg"),
        ]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 2
        poster_row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="tvshow.poster"
        ).first()
        assert poster_row is not None
        assert poster_row.local_path is not None
        # Must be in the canonical shows/ directory, not episode/<id>/
        assert poster_row.local_path.startswith("shows/") or poster_row.local_path.startswith("shows\\")
        assert "breaking_bad" in poster_row.local_path
        assert (tmp_path / poster_row.local_path).exists()

    def test_tvshow_art_no_show_title_uses_per_item_path(self, db_session, tmp_path):
        """Episode without show_title falls back to episode/<id>/ for tvshow.* art."""
        cfg = _make_settings(tmp_path)
        # No show_title set
        item = _make_item(db_session, media_type="episode")
        art = [ArtRow(art_type="tvshow.poster", url="https://example.com/show_poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="tvshow.poster"
        ).first()
        assert row is not None
        # Without show_title, falls back to per-item path
        assert row.local_path.startswith("episode/") or row.local_path.startswith("episode\\")

    def test_season_poster_saved_to_canonical_shows_dir(self, db_session, tmp_path):
        """season.poster goes to shows/<show_safe>/season_<N>_poster.<ext>."""
        cfg = _make_settings(tmp_path)
        item = _make_item(db_session, media_type="episode", show_title="Test Show", season=2)
        art = [ArtRow(art_type="season.poster", url="https://example.com/s2_poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get", return_value=_mock_http_response()):
            count = fetch_artwork_for_item(item, mock_conn, None, db_session, cfg)

        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=item.id, art_type="season.poster"
        ).first()
        assert row is not None
        assert row.local_path is not None
        assert row.local_path.startswith("shows/") or row.local_path.startswith("shows\\")
        assert "season_2_poster" in row.local_path
        assert row.fetch_error is None
        assert (tmp_path / row.local_path).exists()

    def test_tvshow_art_dedup_uses_canonical_filesystem(self, db_session, tmp_path):
        """Second episode reuses tvshow.poster from canonical shows/ dir without downloading."""
        cfg = _make_settings(tmp_path)

        # Pre-create the canonical file (as if ep1 already ran)
        canon_path = tmp_path / "shows" / "test_show" / "tvshow_poster.jpg"
        canon_path.parent.mkdir(parents=True, exist_ok=True)
        canon_path.write_bytes(FAKE_IMAGE)

        # ep2 runs, no Artwork row yet for tvshow.poster
        ep2 = _make_item(db_session, media_type="episode", kodi_media_id=2,
                         show_title="Test Show")
        art = [ArtRow(art_type="tvshow.poster", url="https://example.com/show_poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get") as mock_get:
            count = fetch_artwork_for_item(ep2, mock_conn, None, db_session, cfg)

        mock_get.assert_not_called()  # no download — canonical file found on disk
        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=ep2.id, art_type="tvshow.poster"
        ).first()
        assert row is not None
        assert "shows" in row.local_path
        assert "test_show" in row.local_path

    def test_season_poster_dedup_uses_canonical_filesystem(self, db_session, tmp_path):
        """Second episode in same show/season reuses the canonical season poster without downloading."""
        cfg = _make_settings(tmp_path)

        # Pre-create the canonical season poster (as if ep1 already downloaded it)
        canon_path = tmp_path / "shows" / "test_show" / "season_1_poster.jpg"
        canon_path.parent.mkdir(parents=True, exist_ok=True)
        canon_path.write_bytes(FAKE_IMAGE)

        # ep2 runs, no Artwork row yet for season.poster
        ep2 = _make_item(db_session, media_type="episode", kodi_media_id=2,
                         show_title="Test Show", season=1)
        art = [ArtRow(art_type="season.poster", url="https://example.com/s1_poster.jpg")]
        mock_conn = MagicMock()

        with patch("logsaga.artwork.cache.fetch_art_for_item", return_value=art), \
             patch("httpx.get") as mock_get:
            count = fetch_artwork_for_item(ep2, mock_conn, None, db_session, cfg)

        mock_get.assert_not_called()  # no download — canonical file found on disk
        assert count == 1
        row = db_session.query(Artwork).filter_by(
            media_item_id=ep2.id, art_type="season.poster"
        ).first()
        assert row is not None
        assert "season_1_poster" in row.local_path
        assert "shows" in row.local_path
