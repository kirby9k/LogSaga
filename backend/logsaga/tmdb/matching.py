"""
TMDB title/year matching helpers.

Used by both the CLI ``enrich-tmdb-ids`` command and the background cast sync
to find the correct TMDB ID when searching by title + year.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_title(title: str) -> str:
    """Lowercase ASCII, replace punctuation with spaces, collapse whitespace."""
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    title = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", title).strip()


def pick_movie_match(
    results: list[dict],
    local_title: str,
    local_year: int | None,
) -> int | None:
    """Return the TMDB movie ID of the first result that matches title and year, or None."""
    local_norm = normalize_title(local_title)
    for r in results[:5]:
        tmdb_norm = normalize_title(r.get("title", ""))
        tmdb_orig_norm = normalize_title(r.get("original_title", ""))
        if local_norm not in (tmdb_norm, tmdb_orig_norm):
            continue
        if local_year:
            rd = r.get("release_date", "")
            tmdb_year = int(rd[:4]) if rd and len(rd) >= 4 else None
            if tmdb_year and abs(tmdb_year - local_year) > 1:
                continue
        return r.get("id")
    return None


def pick_tv_match(
    results: list[dict],
    local_title: str,
    local_year: int | None,
) -> int | None:
    """Return the TMDB TV ID of the first result that matches title and year, or None."""
    local_norm = normalize_title(local_title)
    for r in results[:5]:
        tmdb_norm = normalize_title(r.get("name", ""))
        tmdb_orig_norm = normalize_title(r.get("original_name", ""))
        if local_norm not in (tmdb_norm, tmdb_orig_norm):
            continue
        if local_year:
            fad = r.get("first_air_date", "")
            tmdb_year = int(fad[:4]) if fad and len(fad) >= 4 else None
            if tmdb_year and abs(tmdb_year - local_year) > 1:
                continue
        return r.get("id")
    return None
