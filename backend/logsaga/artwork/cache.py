"""
Artwork cache — downloads and stores artwork for media items.

For each media item this module:
  1. Looks up art URLs from Kodi's MariaDB art table
  2. Classifies each URL (plain HTTP, Kodi VFS, or non-image text)
  3. Downloads the image (directly for http/https; via JSON-RPC for VFS paths)
  4. Saves the file under artwork_dir/<media_type>/<item_id>/<art_type>.<ext>
     Show-level art (tvshow.*, season.poster) goes to the canonical location:
       artwork_dir/shows/<show_name>/<art_type>.<ext>
     This means all episodes of the same show share one file on disk.
  5. Creates a WebP thumbnail if artwork_thumbnail_max_px > 0
  6. Upserts the Artwork row in SQLite

Re-fetch is triggered only when the source URL changes or the local file is missing.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from logsaga.config import Settings
from logsaga.kodi.db import fetch_art_for_item
from logsaga.kodi.jsonrpc import KodiJSONRPC, classify_art_url
from logsaga.models import Artwork, MediaItem  # MediaItem used by fetch_all_artwork

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0
_MIN_FILE_BYTES = 100  # sanity: skip empty/tiny responses

# Only download art types the UI actually displays.
# Movies use: poster > fanart > clearlogo
# Episodes use: season.poster > tvshow.poster > tvshow.fanart (portrait slots)
#               thumb (episode screencap, 16:9 slot)
# (tvshow.* names are synthesized by fetch_art_for_item from the parent show's rows)
# Show-level art (tvshow.*, season.poster) is stored in shows/<show_name>/
_ALLOWED_ART_TYPES: frozenset[str] = frozenset({
    "poster", "fanart", "clearlogo",          # movies (and show-level posters)
    "thumb", "tvshow.poster", "tvshow.fanart", # episodes
    "season.poster",                           # episodes — per-season portrait poster
})


# ---------------------------------------------------------------------------
# Internal helpers (also exported for unit tests)
# ---------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _art_type_safe(art_type: str) -> str:
    """Convert art_type to a filesystem-safe stem: 'tvshow.poster' → 'tvshow_poster'."""
    return art_type.replace(".", "_").replace("/", "_").replace("\\", "_")


def _show_title_safe(title: str) -> str:
    """Convert a show title to a filesystem-safe ASCII directory name.

    'Breaking Bad'                        → 'breaking_bad'
    "It's Always Sunny in Philadelphia"   → 'its_always_sunny_in_philadelphia'
    'M*A*S*H'                             → 'mash'
    'Shōgun'                              → 'shogun'  (NFKD strips diacritics)
    """
    s = title.strip().lower()
    # NFKD normalization decomposes accented chars (e.g. ō → o + combining macron);
    # encoding to ASCII and ignoring non-ASCII bytes strips the combining chars,
    # giving a pure ASCII string that's safe on any filesystem and terminal.
    s = unicodedata.normalize("NFKD", s).encode("ascii", errors="ignore").decode("ascii")
    # Remove characters that aren't ASCII alphanumeric, spaces, underscores, or hyphens
    s = re.sub(r"[^\w\s-]", "", s)
    # Collapse any sequence of whitespace, underscores, or hyphens to a single underscore
    s = re.sub(r"[\s_-]+", "_", s)
    s = s.strip("_")
    return s or "unknown_show"


def _guess_extension(url: str, content_type: str) -> str:
    """Guess a file extension from URL path, then Content-Type header."""
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext

    ct = content_type.lower().split(";")[0].strip()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(ct, ".jpg")


def _make_thumbnail(data: bytes, dest_path: Path, max_px: int) -> Optional[Path]:
    """
    Create a WebP thumbnail from image bytes, resized to fit within max_px × max_px.
    Returns dest_path on success, None on any error.
    """
    try:
        from PIL import Image  # import here so PIL absence only affects thumbnails

        img = Image.open(io.BytesIO(data))
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest_path, "WEBP", quality=85)
        return dest_path
    except Exception as exc:
        logger.warning("Thumbnail creation failed for %s: %s", dest_path, exc)
        return None


def _upsert_artwork(
    db: Session,
    media_item_id: int,
    art_type: str,
    source_url: str,
    local_path: Optional[str],
    thumbnail_path: Optional[str],
    file_size: Optional[int],
    fetch_error: Optional[str],
) -> Artwork:
    """Insert or update an Artwork row for (media_item_id, art_type)."""
    row = (
        db.query(Artwork)
        .filter_by(media_item_id=media_item_id, art_type=art_type)
        .first()
    )
    now = _now_utc()
    if row is None:
        row = Artwork(
            media_item_id=media_item_id,
            art_type=art_type,
            created_at=now,
            updated_at=now,
        )
        db.add(row)

    row.source_url = source_url
    row.local_path = local_path
    row.thumbnail_path = thumbnail_path
    row.file_size = file_size
    row.fetch_error = fetch_error
    row.updated_at = now
    if fetch_error is None and local_path is not None:
        row.fetched_at = now

    return row


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_artwork_for_item(
    item: MediaItem,
    kodi_conn,
    rpc: Optional[KodiJSONRPC],
    db: Session,
    cfg: Settings,
) -> int:
    """
    Fetch and cache artwork for one media item.

    Returns the number of Artwork rows created or updated.
    Skips art already on disk with an unchanged source URL.

    Show-level art (tvshow.*, season.poster) is stored once per show (or per
    season) in shows/<show_name>/ so that all episodes share one file on disk.
    Deduplication is filesystem-based: if the canonical file already exists,
    no download is performed.
    """
    if item.kodi_media_id is None:
        return 0

    art_rows = fetch_art_for_item(kodi_conn, item.media_type, item.kodi_media_id)
    if not art_rows:
        return 0

    artwork_dir: Path = cfg.artwork_dir
    max_px: int = cfg.artwork_thumbnail_max_px
    updated = 0

    for art in art_rows:
        # Skip art types the UI doesn't use
        if art.art_type not in _ALLOWED_ART_TYPES:
            continue

        # Skip if already cached with same URL and local file exists
        existing = (
            db.query(Artwork)
            .filter_by(media_item_id=item.id, art_type=art.art_type)
            .first()
        )
        if (
            existing
            and existing.source_url == art.url
            and existing.local_path
            and (artwork_dir / existing.local_path).exists()
        ):
            continue

        # Determine whether this is show-level art that belongs in shows/<show_name>/
        is_show_art = (
            (
                (art.art_type.startswith("tvshow.") and item.media_type == "episode")
                or (
                    art.art_type == "season.poster"
                    and item.media_type == "episode"
                    and item.season is not None
                )
            )
            and bool(item.show_title)
        )

        # Compute the filesystem stem (used for canonical check and save path)
        if is_show_art:
            show_safe: Optional[str] = _show_title_safe(item.show_title)  # type: ignore[arg-type]
            if art.art_type == "season.poster":
                stem = f"season_{item.season}_poster"
            else:
                stem = _art_type_safe(art.art_type)  # "tvshow_poster", "tvshow_fanart"

            # Filesystem dedup: if the canonical file already exists on disk
            # (another episode of the same show downloaded it first), point to it
            # without downloading again.
            canonical_dir = artwork_dir / "shows" / show_safe
            if canonical_dir.exists():
                matches = [
                    f for f in canonical_dir.glob(f"{stem}.*")
                    if "_thumb" not in f.name
                ]
                if matches:
                    canon_abs = matches[0]
                    thumb_name = f"{stem}_thumb.webp"
                    thumb_path = canonical_dir / thumb_name
                    _upsert_artwork(
                        db, item.id, art.art_type, art.url,
                        local_path=str(canon_abs.relative_to(artwork_dir)),
                        thumbnail_path=str(Path("shows") / show_safe / thumb_name) if thumb_path.exists() else None,
                        file_size=canon_abs.stat().st_size,
                        fetch_error=None,
                    )
                    updated += 1
                    continue
        else:
            show_safe = None
            stem = _art_type_safe(art.art_type)

        kind, url = classify_art_url(art.url)
        if kind == "skip":
            continue

        # Resolve the HTTP download URL
        download_url: Optional[str] = None
        fetch_error: Optional[str] = None

        if kind == "http":
            download_url = url
        elif kind == "vfs":
            if rpc is None:
                fetch_error = "VFS art requires JSON-RPC but no client was provided"
            else:
                try:
                    download_url = rpc.prepare_download(url)
                except Exception as exc:
                    fetch_error = f"PrepareDownload failed: {exc}"

        if fetch_error or download_url is None:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None, fetch_error)
            updated += 1
            continue

        # Download
        try:
            resp = httpx.get(download_url, timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            data = resp.content
        except Exception as exc:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None, str(exc))
            updated += 1
            continue

        if len(data) < _MIN_FILE_BYTES:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None,
                            f"Response too small ({len(data)} bytes)")
            updated += 1
            continue

        # Determine save path
        ct = resp.headers.get("content-type", "")
        ext = _guess_extension(download_url, ct)

        if is_show_art and show_safe is not None:
            rel_path = Path("shows") / show_safe / f"{stem}{ext}"
        else:
            rel_path = Path(item.media_type) / str(item.id) / f"{stem}{ext}"
        abs_path = artwork_dir / rel_path

        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(data)
        except OSError as exc:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None, f"Write failed: {exc}")
            updated += 1
            continue

        # Thumbnail
        thumb_rel: Optional[str] = None
        if max_px > 0:
            thumb_name = f"{stem}_thumb.webp"
            if is_show_art and show_safe is not None:
                thumb_abs = artwork_dir / "shows" / show_safe / thumb_name
                if _make_thumbnail(data, thumb_abs, max_px):
                    thumb_rel = str(Path("shows") / show_safe / thumb_name)
            else:
                thumb_abs = artwork_dir / item.media_type / str(item.id) / thumb_name
                if _make_thumbnail(data, thumb_abs, max_px):
                    thumb_rel = str(Path(item.media_type) / str(item.id) / thumb_name)

        _upsert_artwork(
            db, item.id, art.art_type, art.url,
            local_path=str(rel_path),
            thumbnail_path=thumb_rel,
            file_size=len(data),
            fetch_error=None,
        )
        updated += 1

    return updated


async def fetch_artwork_for_item_async(
    item: MediaItem,
    kodi_conn,
    rpc: Optional[KodiJSONRPC],
    db: Session,
    cfg: Settings,
    client: httpx.AsyncClient,
) -> int:
    """
    Async version of fetch_artwork_for_item.

    Identical logic but uses ``await client.get()`` for downloads so that
    multiple items can have HTTP requests in-flight simultaneously.
    Pass a shared ``httpx.AsyncClient`` (with timeout already set) from the caller.
    """
    if item.kodi_media_id is None:
        return 0

    art_rows = fetch_art_for_item(kodi_conn, item.media_type, item.kodi_media_id)
    if not art_rows:
        return 0

    artwork_dir: Path = cfg.artwork_dir
    max_px: int = cfg.artwork_thumbnail_max_px
    updated = 0

    for art in art_rows:
        if art.art_type not in _ALLOWED_ART_TYPES:
            continue

        existing = (
            db.query(Artwork)
            .filter_by(media_item_id=item.id, art_type=art.art_type)
            .first()
        )
        if (
            existing
            and existing.source_url == art.url
            and existing.local_path
            and (artwork_dir / existing.local_path).exists()
        ):
            continue

        is_show_art = (
            (
                (art.art_type.startswith("tvshow.") and item.media_type == "episode")
                or (
                    art.art_type == "season.poster"
                    and item.media_type == "episode"
                    and item.season is not None
                )
            )
            and bool(item.show_title)
        )

        if is_show_art:
            show_safe: Optional[str] = _show_title_safe(item.show_title)  # type: ignore[arg-type]
            if art.art_type == "season.poster":
                stem = f"season_{item.season}_poster"
            else:
                stem = _art_type_safe(art.art_type)

            canonical_dir = artwork_dir / "shows" / show_safe
            if canonical_dir.exists():
                matches = [
                    f for f in canonical_dir.glob(f"{stem}.*")
                    if "_thumb" not in f.name
                ]
                if matches:
                    canon_abs = matches[0]
                    thumb_name = f"{stem}_thumb.webp"
                    thumb_path = canonical_dir / thumb_name
                    _upsert_artwork(
                        db, item.id, art.art_type, art.url,
                        local_path=str(canon_abs.relative_to(artwork_dir)),
                        thumbnail_path=str(Path("shows") / show_safe / thumb_name) if thumb_path.exists() else None,
                        file_size=canon_abs.stat().st_size,
                        fetch_error=None,
                    )
                    updated += 1
                    continue
        else:
            show_safe = None
            stem = _art_type_safe(art.art_type)

        kind, url = classify_art_url(art.url)
        if kind == "skip":
            continue

        download_url: Optional[str] = None
        fetch_error: Optional[str] = None

        if kind == "http":
            download_url = url
        elif kind == "vfs":
            if rpc is None:
                fetch_error = "VFS art requires JSON-RPC but no client was provided"
            else:
                try:
                    download_url = rpc.prepare_download(url)
                except Exception as exc:
                    fetch_error = f"PrepareDownload failed: {exc}"

        if fetch_error or download_url is None:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None, fetch_error)
            updated += 1
            continue

        # Async download — caller's client has timeout configured
        try:
            resp = await client.get(download_url, follow_redirects=True)
            resp.raise_for_status()
            data = resp.content
        except Exception as exc:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None, str(exc))
            updated += 1
            continue

        if len(data) < _MIN_FILE_BYTES:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None,
                            f"Response too small ({len(data)} bytes)")
            updated += 1
            continue

        ct = resp.headers.get("content-type", "")
        ext = _guess_extension(download_url, ct)

        if is_show_art and show_safe is not None:
            rel_path = Path("shows") / show_safe / f"{stem}{ext}"
        else:
            rel_path = Path(item.media_type) / str(item.id) / f"{stem}{ext}"
        abs_path = artwork_dir / rel_path

        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(data)
        except OSError as exc:
            _upsert_artwork(db, item.id, art.art_type, art.url,
                            None, None, None, f"Write failed: {exc}")
            updated += 1
            continue

        thumb_rel: Optional[str] = None
        if max_px > 0:
            thumb_name = f"{stem}_thumb.webp"
            if is_show_art and show_safe is not None:
                thumb_abs = artwork_dir / "shows" / show_safe / thumb_name
                if _make_thumbnail(data, thumb_abs, max_px):
                    thumb_rel = str(Path("shows") / show_safe / thumb_name)
            else:
                thumb_abs = artwork_dir / item.media_type / str(item.id) / thumb_name
                if _make_thumbnail(data, thumb_abs, max_px):
                    thumb_rel = str(Path(item.media_type) / str(item.id) / thumb_name)

        _upsert_artwork(
            db, item.id, art.art_type, art.url,
            local_path=str(rel_path),
            thumbnail_path=thumb_rel,
            file_size=len(data),
            fetch_error=None,
        )
        updated += 1

    return updated


def fetch_all_artwork(
    kodi_conn,
    rpc: Optional[KodiJSONRPC],
    db: Session,
    cfg: Settings,
    item_id: Optional[int] = None,
) -> tuple[int, int]:
    """
    Fetch artwork for all items (or one item if item_id is given).

    Returns (items_processed, artwork_updated).
    """
    if item_id is not None:
        item = db.get(MediaItem, item_id)
        if item is None:
            return 0, 0
        items = [item]
    else:
        items = (
            db.query(MediaItem)
            .filter(MediaItem.kodi_media_id.isnot(None))
            .all()
        )

    total_updated = 0
    for item in items:
        try:
            count = fetch_artwork_for_item(item, kodi_conn, rpc, db, cfg)
            total_updated += count
        except Exception as exc:
            logger.error("Error fetching art for item %d (%s): %s", item.id, item.title, exc)

    db.flush()
    return len(items), total_updated
