"""
Artwork service utilities — required-type definitions and DB query helpers.

This module has no I/O of its own (no HTTP, no file access). It operates
only on the SQLite database via the SQLAlchemy session passed in.
"""

from __future__ import annotations

from typing import Optional

import structlog
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from logsaga.models import Artwork, MediaItem

logger = structlog.get_logger(__name__)

# Art types that are considered mandatory for each media_type.
# An item is "missing artwork" when any of its required types has no
# Artwork row with a non-NULL local_path.
REQUIRED_ART_TYPES: dict[str, set[str]] = {
    "movie":   {"poster", "fanart"},
    "episode": {"thumb", "tvshow.poster"},
}


def get_missing_art_types(item: MediaItem, db: Session) -> set[str]:
    """Return the required art types that have no downloaded file for *item*.

    A type is considered missing when there is no Artwork row for it with a
    non-NULL local_path.  Types that are not in REQUIRED_ART_TYPES for the
    item's media_type are ignored.
    """
    required = REQUIRED_ART_TYPES.get(item.media_type, set())
    if not required:
        return set()

    have = {
        row.art_type
        for row in db.query(Artwork.art_type)
        .filter(
            Artwork.media_item_id == item.id,
            Artwork.art_type.in_(required),
            Artwork.local_path.isnot(None),
        )
        .all()
    }
    return required - have


def find_items_with_missing_art(db: Session) -> list[int]:
    """Return MediaItem IDs (all media types) missing at least one required art type.

    Includes:
    - Kodi-library items (kodi_media_id IS NOT NULL)
    - TMDB-only items (kodi_media_id IS NULL but tmdb_id IS NOT NULL)

    Items with neither kodi_media_id nor tmdb_id are skipped — there's no
    source to fetch artwork from.
    """
    result_ids: set[int] = set()

    for media_type, required_types in REQUIRED_ART_TYPES.items():
        for art_type in required_types:
            # Subquery: item IDs that already have this art type downloaded
            has_art_sq = (
                db.query(Artwork.media_item_id)
                .filter(
                    Artwork.art_type == art_type,
                    Artwork.local_path.isnot(None),
                )
                .subquery()
            )

            missing = [
                r[0]
                for r in db.query(MediaItem.id)
                .filter(
                    MediaItem.media_type == media_type,
                    ~MediaItem.id.in_(has_art_sq),
                    or_(
                        MediaItem.kodi_media_id.isnot(None),
                        MediaItem.tmdb_id.isnot(None),
                    ),
                )
                .all()
            ]
            result_ids.update(missing)

    return list(result_ids)


def reset_stuck_retry_budgets(
    db: Session,
    item_ids: Optional[list[int]] = None,
) -> int:
    """Reset Artwork rows whose retry budget is exhausted (consecutive_failures >= 3,
    retry_after IS NULL).

    After this call the next fill attempt will retry those items rather than
    skipping them permanently.

    item_ids=None resets all stuck rows across the entire library.
    Returns the number of rows reset.
    """
    q = (
        db.query(Artwork)
        .filter(
            Artwork.consecutive_failures >= 3,
            Artwork.retry_after.is_(None),
            Artwork.local_path.is_(None),  # only reset rows still missing a file
        )
    )
    if item_ids is not None:
        q = q.filter(Artwork.media_item_id.in_(item_ids))

    rows = q.all()
    for row in rows:
        row.consecutive_failures = 0
        # retry_after already NULL; leave fetch_error as-is (audit trail)

    if rows:
        logger.info("artwork_retry_budget_reset", count=len(rows))

    return len(rows)
