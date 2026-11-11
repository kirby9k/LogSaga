"""
REST API route handlers for LogSAGA.

All routes are mounted under the /api prefix by main.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from logsaga.api.deps import get_cfg, get_db
from logsaga.api.schemas import (
    AdminSettingsIn,
    AdminSettingsOut,
    ArtworkOut,
    ArtworkStatusOut,
    DailyStatOut,
    EpisodeSummaryOut,
    HealthOut,
    InProgressOut,
    JobStateOut,
    MediaItemDetailOut,
    MediaItemSummaryOut,
    MovieListOut,
    MovieSummaryOut,
    NextUnwatchedOut,
    ObservationOut,
    RecentItemOut,
    RecentlyAddedItemOut,
    RecentlyAddedListOut,
    RebuildSessionsOut,
    SeasonSummaryOut,
    SessionListOut,
    SessionOut,
    ShowDetailOut,
    ShowStatOut,
    ShowSummaryOut,
    StatsOut,
)
from logsaga.config import Settings
from logsaga.models import (
    AppSetting,
    Artwork,
    KodiObservation,
    KodiShow,
    KodiState,
    MediaItem,
    MediaType,
    SessionStatus,
    SyncState,
    WatchSession,
)

router = APIRouter(tags=["api"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
_MOVIE_ART_PREF = ["poster", "fanart", "clearlogo"]
_EP_ART_PREF = ["season.poster", "tvshow.poster", "tvshow.fanart"]


def _poster_url(item: MediaItem, thumbnail: bool = True) -> Optional[str]:
    """
    Return the best artwork URL for an item.

    For movies: prefer poster > fanart > clearlogo.
    For episodes: prefer season.poster > tvshow.poster > tvshow.fanart.
    Returns the thumbnail path (/art/...) if available and thumbnail=True,
    otherwise falls back to the full-size local_path.
    Returns None if no artwork has been downloaded yet.
    """
    prefer = _MOVIE_ART_PREF if item.media_type == "movie" else _EP_ART_PREF
    for art_type in prefer:
        for art in item.artwork:
            if art.art_type == art_type:
                path = (art.thumbnail_path if thumbnail else None) or art.local_path
                if path:
                    return f"/art/{path}"
    return None


def _item_summary(item: MediaItem) -> MediaItemSummaryOut:
    """Build a MediaItemSummaryOut from an ORM MediaItem (artwork must be loaded)."""
    return MediaItemSummaryOut(
        id=item.id,
        media_type=item.media_type,
        title=item.title,
        show_title=item.show_title,
        show_id=item.show_id,
        season=item.season,
        episode=item.episode,
        year=item.year,
        runtime_s=item.runtime_s,
        kodi_state=item.kodi_state,
        poster_url=_poster_url(item, thumbnail=False),
        thumbnail_url=_poster_url(item, thumbnail=True),
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthOut)
def get_health(
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
) -> HealthOut:
    """Return sync health, counters, and active session-rule settings."""
    sync = db.get(SyncState, 1)
    settings_rows = db.query(AppSetting).all()
    settings_dict = {r.key: r.value for r in settings_rows}

    # Also include active config values (some may not be in settings table yet)
    settings_dict.update({
        "min_started_seconds": str(cfg.min_started_seconds),
        "session_gap_hours": str(cfg.session_gap_hours),
        "watched_percent": str(cfg.watched_percent),
        "watched_remaining_minutes_movie": str(cfg.watched_remaining_minutes_movie),
        "watched_remaining_minutes_episode": str(cfg.watched_remaining_minutes_episode),
        "poll_seconds": str(cfg.poll_seconds),
        "tz": cfg.tz,
    })

    if sync is None:
        return HealthOut(status="error", settings=settings_dict)

    # Determine health status
    stale_threshold = max(cfg.poll_seconds * 2, 300)  # at least 5 minutes
    if sync.last_success_at is None:
        status = "error" if sync.error_count > 0 else "degraded"
    else:
        age_s = (_now_utc() - sync.last_success_at).total_seconds()
        status = "ok" if age_s <= stale_threshold else "degraded"

    return HealthOut(
        status=status,
        kodi_db_name=sync.kodi_db_name,
        last_poll_at=sync.last_poll_at,
        last_success_at=sync.last_success_at,
        last_error=sync.last_error,
        last_error_at=sync.last_error_at,
        poll_count=sync.poll_count,
        observation_count=sync.observation_count,
        error_count=sync.error_count,
        settings=settings_dict,
    )


# ---------------------------------------------------------------------------
# GET /recent  &  GET /sessions
# ---------------------------------------------------------------------------
def _session_query(
    db: Session,
    media_type: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    """Build a filtered query of (WatchSession, MediaItem) pairs."""
    q = (
        db.query(WatchSession, MediaItem)
        .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
        .options(selectinload(WatchSession.media_item).selectinload(MediaItem.artwork))
        .filter(WatchSession.status != SessionStatus.ignored)
    )
    if media_type:
        q = q.filter(MediaItem.media_type == media_type)
    if status:
        q = q.filter(WatchSession.status == status)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (MediaItem.title.ilike(like)) | (MediaItem.show_title.ilike(like))
        )
    if date_from:
        q = q.filter(WatchSession.started_at >= date_from)
    if date_to:
        q = q.filter(WatchSession.started_at <= date_to)
    return q


@router.get("/recent", response_model=SessionListOut)
def get_recent(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    type: Optional[str] = Query(None, description="movie or episode"),
    status: Optional[str] = Query(None, description="completed, partial, or open"),
    db: Session = Depends(get_db),
) -> SessionListOut:
    """Return recent watch sessions, newest first (excludes ignored)."""
    q = _session_query(db, media_type=type, status=status)
    total = q.count()
    rows = q.order_by(WatchSession.last_activity_at.desc()).offset(offset).limit(limit).all()
    items = [
        RecentItemOut(session=SessionOut.model_validate(ws), item=_item_summary(mi))
        for ws, mi in rows
    ]
    return SessionListOut(total=total, items=items)


@router.get("/sessions", response_model=SessionListOut)
def get_sessions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    type: Optional[str] = Query(None, description="movie or episode"),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
) -> SessionListOut:
    """
    Full history table — searchable and filterable.

    Supports all filters from /recent plus text search and date range.
    """
    q = _session_query(
        db,
        media_type=type,
        status=status,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    total = q.count()
    rows = q.order_by(WatchSession.last_activity_at.desc()).offset(offset).limit(limit).all()
    items = [
        RecentItemOut(session=SessionOut.model_validate(ws), item=_item_summary(mi))
        for ws, mi in rows
    ]
    return SessionListOut(total=total, items=items)


# ---------------------------------------------------------------------------
# GET /in-progress
# ---------------------------------------------------------------------------
@router.get("/in-progress", response_model=list[InProgressOut])
def get_in_progress(
    db: Session = Depends(get_db),
) -> list[InProgressOut]:
    """Items that Kodi currently has an active resume point for."""
    items = (
        db.query(MediaItem)
        .options(selectinload(MediaItem.artwork))
        .filter(MediaItem.kodi_state == KodiState.in_progress)
        .order_by(MediaItem.updated_at.desc())
        .all()
    )

    result = []
    for item in items:
        # Find the most recent non-ignored session for position info
        session = (
            db.query(WatchSession)
            .filter(
                WatchSession.media_item_id == item.id,
                WatchSession.status != SessionStatus.ignored,
            )
            .order_by(WatchSession.last_activity_at.desc())
            .first()
        )
        resume_pos = session.max_position_s if session else None
        runtime = session.runtime_s if session else item.runtime_s
        pct = (resume_pos / runtime * 100.0) if resume_pos and runtime and runtime > 0 else None
        result.append(
            InProgressOut(
                item=_item_summary(item),
                resume_pos_s=resume_pos,
                resume_total_s=float(runtime) if runtime else None,
                percent=pct,
            )
        )
    return result


# ---------------------------------------------------------------------------
# GET /shows  (L-07)
# ---------------------------------------------------------------------------
def _show_poster(show_id: int, db: Session) -> tuple[Optional[str], Optional[str]]:
    """Return (poster_url, thumbnail_url) for a show using its kodi_show.id."""
    art_row = (
        db.query(Artwork)
        .join(MediaItem, Artwork.media_item_id == MediaItem.id)
        .filter(
            MediaItem.show_id == show_id,
            Artwork.art_type == "tvshow.poster",
            Artwork.local_path.isnot(None),
        )
        .first()
    )
    if art_row:
        return (
            f"/art/{art_row.local_path}",
            f"/art/{art_row.thumbnail_path}" if art_row.thumbnail_path else None,
        )
    return None, None


@router.get("/shows", response_model=list[ShowSummaryOut])
def get_shows(
    search: Optional[str] = Query(None),
    filter: str = Query("history", description="history | all | unwatched"),
    sort: str = Query("activity", description="activity | title | added"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[ShowSummaryOut]:
    """
    TV shows from kodi_show, keyed by integer show_id.

    filter=history (default): shows with at least one watch session
    filter=all: entire Kodi show library
    filter=unwatched: shows with no completed session
    """
    has_session_sq = (
        db.query(MediaItem.show_id)
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            WatchSession.status != SessionStatus.ignored,
            MediaItem.show_id.isnot(None),
        )
        .distinct()
    )

    shows_q = db.query(KodiShow).filter(KodiShow.kodi_state != KodiState.removed)

    if filter == "history":
        shows_q = shows_q.filter(KodiShow.id.in_(has_session_sq))
    elif filter == "unwatched":
        shows_q = shows_q.filter(
            KodiShow.id.notin_(has_session_sq),
            KodiShow.kodi_state == KodiState.unwatched,
        )
    # filter == "all": no extra filter beyond excluding removed

    if search:
        shows_q = shows_q.filter(KodiShow.title.ilike(f"%{search}%"))

    total_shows = shows_q.count()

    # For activity sorting, build last-activity subquery
    last_activity_sq = (
        db.query(
            MediaItem.show_id,
            func.max(WatchSession.last_activity_at).label("last_at"),
        )
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            WatchSession.status != SessionStatus.ignored,
            MediaItem.show_id.isnot(None),
        )
        .group_by(MediaItem.show_id)
        .subquery()
    )

    if sort == "title":
        shows = (
            shows_q.order_by(KodiShow.title.asc())
            .offset(offset).limit(limit).all()
        )
    elif sort == "added":
        shows = (
            shows_q.order_by(KodiShow.created_at.desc())
            .offset(offset).limit(limit).all()
        )
    else:  # sort == "activity" (default)
        shows = (
            shows_q
            .outerjoin(last_activity_sq, last_activity_sq.c.show_id == KodiShow.id)
            .order_by(last_activity_sq.c.last_at.desc().nullslast())
            .offset(offset).limit(limit).all()
        )

    # Bulk: total episode counts per show
    show_ids = [s.id for s in shows]
    total_ep_map: dict[int, int] = {}
    watched_ep_map: dict[int, int] = {}
    last_act_map: dict[int, Any] = {}

    if show_ids:
        for row in (
            db.query(MediaItem.show_id, func.count(MediaItem.id).label("cnt"))
            .filter(
                MediaItem.show_id.in_(show_ids),
                MediaItem.media_type == MediaType.episode,
            )
            .group_by(MediaItem.show_id)
            .all()
        ):
            total_ep_map[row.show_id] = row.cnt

        # Watched episodes: completed session OR kodi_state=watched
        # Build per-show sets to union both signals without double-counting.
        _watched_sets: dict[int, set[int]] = {}
        for row in (
            db.query(MediaItem.show_id, WatchSession.media_item_id)
            .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id.in_(show_ids),
                WatchSession.status == SessionStatus.completed,
            )
            .all()
        ):
            _watched_sets.setdefault(row.show_id, set()).add(row.media_item_id)
        for row in (
            db.query(MediaItem.show_id, MediaItem.id)
            .filter(
                MediaItem.show_id.in_(show_ids),
                MediaItem.kodi_state == KodiState.watched,
            )
            .all()
        ):
            _watched_sets.setdefault(row.show_id, set()).add(row.id)
        for _sid, _ids in _watched_sets.items():
            watched_ep_map[_sid] = len(_ids)

        # Last activity per show
        for row in (
            db.query(
                MediaItem.show_id,
                func.max(WatchSession.last_activity_at).label("last_at"),
            )
            .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id.in_(show_ids),
                WatchSession.status != SessionStatus.ignored,
            )
            .group_by(MediaItem.show_id)
            .all()
        ):
            last_act_map[row.show_id] = row.last_at

    # Bulk: artwork per show (one poster per show_id; deduplicate in Python)
    poster_map: dict[int, tuple[Optional[str], Optional[str]]] = {}
    if show_ids:
        art_rows = (
            db.query(
                MediaItem.show_id,
                Artwork.local_path,
                Artwork.thumbnail_path,
            )
            .join(Artwork, Artwork.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id.in_(show_ids),
                Artwork.art_type == "tvshow.poster",
                Artwork.local_path.isnot(None),
            )
            .all()
        )
        for row in art_rows:
            if row.show_id not in poster_map:  # keep first match
                poster_map[row.show_id] = (
                    f"/art/{row.local_path}",
                    f"/art/{row.thumbnail_path}" if row.thumbnail_path else None,
                )

    result = []
    for show in shows:
        total_eps = total_ep_map.get(show.id, 0)
        watched_eps = watched_ep_map.get(show.id, 0)
        poster_url, thumb_url = poster_map.get(show.id, (None, None))
        last_at = last_act_map.get(show.id)

        # Find next unwatched episode (only for shows with some history)
        next_unwatched = None
        if watched_eps > 0 and watched_eps < total_eps:
            watched_ids = set(
                r[0] for r in (
                    db.query(WatchSession.media_item_id)
                    .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
                    .filter(
                        MediaItem.show_id == show.id,
                        WatchSession.status == SessionStatus.completed,
                    )
                    .all()
                )
            ) | set(
                r[0] for r in (
                    db.query(MediaItem.id)
                    .filter(
                        MediaItem.show_id == show.id,
                        MediaItem.kodi_state == KodiState.watched,
                    )
                    .all()
                )
            )
            next_ep = (
                db.query(MediaItem)
                .filter(
                    MediaItem.show_id == show.id,
                    MediaItem.media_type == MediaType.episode,
                    MediaItem.season.isnot(None),
                    MediaItem.episode.isnot(None),
                    MediaItem.id.notin_(watched_ids),
                )
                .order_by(MediaItem.season, MediaItem.episode)
                .first()
            )
            if next_ep:
                next_unwatched = NextUnwatchedOut(
                    item_id=next_ep.id,
                    season=next_ep.season,
                    episode=next_ep.episode,
                    title=next_ep.title,
                )

        result.append(ShowSummaryOut(
            show_id=show.id,
            show_title=show.title,
            total_episodes=total_eps,
            watched_episodes=watched_eps,
            kodi_state=show.kodi_state.value if hasattr(show.kodi_state, "value") else show.kodi_state,
            last_activity_at=last_at,
            poster_url=poster_url,
            thumbnail_url=thumb_url,
            next_unwatched=next_unwatched,
        ))
    return result


# ---------------------------------------------------------------------------
# GET /shows/{show_id}  (L-08)
# ---------------------------------------------------------------------------
@router.get("/shows/{show_id}", response_model=ShowDetailOut)
def get_show(
    show_id: int,
    db: Session = Depends(get_db),
) -> ShowDetailOut:
    """Full show detail: metadata, all seasons, and all episodes (watched + unwatched)."""
    show = db.get(KodiShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail=f"Show {show_id} not found")

    # All episodes for this show, ordered by season/episode
    episodes = (
        db.query(MediaItem)
        .options(selectinload(MediaItem.artwork))
        .filter(
            MediaItem.show_id == show_id,
            MediaItem.media_type == MediaType.episode,
        )
        .order_by(MediaItem.season, MediaItem.episode)
        .all()
    )

    # Watched: completed session OR kodi marks as fully watched
    watched_ids = set(
        r[0] for r in (
            db.query(WatchSession.media_item_id)
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id,
                WatchSession.status == SessionStatus.completed,
            )
            .all()
        )
    ) | set(
        r[0] for r in (
            db.query(MediaItem.id)
            .filter(
                MediaItem.show_id == show_id,
                MediaItem.kodi_state == KodiState.watched,
            )
            .all()
        )
    )

    # Group episodes by season
    seasons_dict: dict[int, list[MediaItem]] = {}
    for ep in episodes:
        s = ep.season or 0
        seasons_dict.setdefault(s, []).append(ep)

    seasons_out = []
    for season_num in sorted(seasons_dict.keys()):
        eps = seasons_dict[season_num]
        ep_out = []
        for ep in eps:
            # Episode thumb
            thumb_url = None
            for art in ep.artwork:
                if art.art_type in ("thumb", "season.poster", "tvshow.poster"):
                    path = art.thumbnail_path or art.local_path
                    if path:
                        thumb_url = f"/art/{path}"
                        break

            ep_out.append(EpisodeSummaryOut(
                id=ep.id,
                episode=ep.episode or 0,
                title=ep.title,
                release_date=ep.release_date,
                runtime_s=ep.runtime_s,
                kodi_state=ep.kodi_state.value if hasattr(ep.kodi_state, "value") else ep.kodi_state,
                watched=ep.id in watched_ids,
                plot=ep.plot,
                thumb_url=thumb_url,
            ))

        watched_in_season = sum(1 for e in eps if e.id in watched_ids)
        seasons_out.append(SeasonSummaryOut(
            season=season_num,
            total_episodes=len(eps),
            watched_episodes=watched_in_season,
            episodes=ep_out,
        ))

    # Artwork: look for tvshow.* art on any episode of this show
    poster_url = None
    fanart_url = None
    logo_url = None
    for art_type_want, attr_name in [
        ("tvshow.poster", "poster_url"),
        ("tvshow.fanart", "fanart_url"),
        ("tvshow.clearlogo", "logo_url"),
    ]:
        art_row = (
            db.query(Artwork)
            .join(MediaItem, Artwork.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id,
                Artwork.art_type == art_type_want,
                Artwork.local_path.isnot(None),
            )
            .first()
        )
        if art_row:
            path = art_row.local_path
            val = f"/art/{path}" if path else None
            if attr_name == "poster_url":
                poster_url = val
            elif attr_name == "fanart_url":
                fanart_url = val
            else:
                logo_url = val

    state_val = show.kodi_state.value if hasattr(show.kodi_state, "value") else show.kodi_state

    return ShowDetailOut(
        show_id=show.id,
        show_title=show.title,
        plot=show.plot,
        year=show.year,
        premiered=show.premiered,
        studio=show.studio,
        status=show.status,
        genres=show.genres,
        tvdb_id=show.tvdb_id,
        imdb_id=show.imdb_id,
        tmdb_id=show.tmdb_id,
        kodi_state=state_val,
        total_episodes=len(episodes),
        watched_episodes=len(watched_ids),
        poster_url=poster_url,
        fanart_url=fanart_url,
        logo_url=logo_url,
        seasons=seasons_out,
    )


# ---------------------------------------------------------------------------
# GET /shows/{show_id}/seasons/{season}  (L-09)
# ---------------------------------------------------------------------------
@router.get("/shows/{show_id}/seasons/{season}", response_model=SeasonSummaryOut)
def get_season(
    show_id: int,
    season: int,
    db: Session = Depends(get_db),
) -> SeasonSummaryOut:
    """All episodes for one season of a show (watched + unwatched)."""
    show = db.get(KodiShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail=f"Show {show_id} not found")

    episodes = (
        db.query(MediaItem)
        .options(selectinload(MediaItem.artwork))
        .filter(
            MediaItem.show_id == show_id,
            MediaItem.media_type == MediaType.episode,
            MediaItem.season == season,
        )
        .order_by(MediaItem.episode)
        .all()
    )

    if not episodes:
        raise HTTPException(status_code=404, detail=f"Season {season} not found for show {show_id}")

    watched_ids = set(
        r[0] for r in (
            db.query(WatchSession.media_item_id)
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id,
                MediaItem.season == season,
                WatchSession.status == SessionStatus.completed,
            )
            .all()
        )
    ) | set(
        r[0] for r in (
            db.query(MediaItem.id)
            .filter(
                MediaItem.show_id == show_id,
                MediaItem.season == season,
                MediaItem.kodi_state == KodiState.watched,
            )
            .all()
        )
    )

    ep_out = []
    for ep in episodes:
        thumb_url = None
        for art in ep.artwork:
            if art.art_type in ("thumb", "season.poster", "tvshow.poster"):
                path = art.thumbnail_path or art.local_path
                if path:
                    thumb_url = f"/art/{path}"
                    break

        ep_out.append(EpisodeSummaryOut(
            id=ep.id,
            episode=ep.episode or 0,
            title=ep.title,
            release_date=ep.release_date,
            runtime_s=ep.runtime_s,
            kodi_state=ep.kodi_state.value if hasattr(ep.kodi_state, "value") else ep.kodi_state,
            watched=ep.id in watched_ids,
            plot=ep.plot,
            thumb_url=thumb_url,
        ))

    return SeasonSummaryOut(
        season=season,
        total_episodes=len(episodes),
        watched_episodes=len(watched_ids),
        episodes=ep_out,
    )


# ---------------------------------------------------------------------------
# GET /recently-added  (L-10)
# ---------------------------------------------------------------------------
@router.get("/recently-added", response_model=RecentlyAddedListOut)
def get_recently_added(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    type: Optional[str] = Query(None, description="movie | episode"),
    db: Session = Depends(get_db),
) -> RecentlyAddedListOut:
    """
    Items sorted by date_added DESC (most recently added to Kodi's library first).

    Items with the sentinel date (1951-09-25) appear last.
    """
    from logsaga.library.sync import DATE_ADDED_SENTINEL

    base = (
        db.query(MediaItem)
        .options(selectinload(MediaItem.artwork))
        .filter(MediaItem.kodi_state != KodiState.removed)
    )

    if type:
        base = base.filter(MediaItem.media_type == type)

    total = base.count()

    # Sort: sentinel date goes last; NULL goes last too
    items = (
        base
        .order_by(
            # Sentinel dates sort after real dates
            MediaItem.date_added == DATE_ADDED_SENTINEL,
            MediaItem.date_added.desc().nullslast(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for item in items:
        result.append(RecentlyAddedItemOut(
            id=item.id,
            media_type=item.media_type,
            title=item.title,
            show_title=item.show_title,
            show_id=item.show_id,
            season=item.season,
            episode=item.episode,
            year=item.year,
            date_added=item.date_added,
            kodi_state=item.kodi_state.value if hasattr(item.kodi_state, "value") else item.kodi_state,
            poster_url=_poster_url(item, thumbnail=False),
            thumbnail_url=_poster_url(item, thumbnail=True),
        ))

    return RecentlyAddedListOut(total=total, items=result)


# ---------------------------------------------------------------------------
# GET /items/{id}
# ---------------------------------------------------------------------------
@router.get("/items/{item_id}", response_model=MediaItemDetailOut)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
) -> MediaItemDetailOut:
    """Full item detail: metadata, artwork, all sessions, all observations."""
    item = (
        db.query(MediaItem)
        .options(
            selectinload(MediaItem.artwork),
            selectinload(MediaItem.sessions),
            selectinload(MediaItem.observations),
        )
        .filter(MediaItem.id == item_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    sessions_sorted = sorted(item.sessions, key=lambda s: s.started_at, reverse=True)
    obs_sorted = sorted(item.observations, key=lambda o: o.observed_at, reverse=True)

    return MediaItemDetailOut(
        id=item.id,
        media_type=item.media_type,
        title=item.title,
        show_title=item.show_title,
        show_id=item.show_id,
        season=item.season,
        episode=item.episode,
        year=item.year,
        plot=item.plot,
        runtime_s=item.runtime_s,
        genres=item.genres,
        imdb_id=item.imdb_id,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        release_date=item.release_date,
        director=item.director,
        video_codec=item.video_codec,
        video_width=item.video_width,
        video_height=item.video_height,
        hdr_type=item.hdr_type,
        audio_codec=item.audio_codec,
        audio_channels=item.audio_channels,
        kodi_state=item.kodi_state,
        created_at=item.created_at,
        updated_at=item.updated_at,
        artwork=[ArtworkOut.model_validate(a) for a in item.artwork],
        sessions=[SessionOut.model_validate(s) for s in sessions_sorted],
        observations=[ObservationOut.model_validate(o) for o in obs_sorted],
    )


# ---------------------------------------------------------------------------
# DELETE /sessions/{session_id}  — permanently delete a watch session
# ---------------------------------------------------------------------------
from fastapi import Response as _Response  # noqa: E402


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
) -> _Response:
    """
    Permanently delete a watch session.

    The row is removed from the database.
    Returns 204 on success, 404 if the session does not exist.
    """
    session = db.get(WatchSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    db.delete(session)
    db.commit()
    return _Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /stats
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=StatsOut)
def get_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> StatsOut:
    """Aggregated watch statistics."""
    # --- Overall session counts ---
    total = db.query(WatchSession).count()
    completed = (
        db.query(WatchSession)
        .filter(WatchSession.status == SessionStatus.completed)
        .count()
    )
    partial = (
        db.query(WatchSession)
        .filter(WatchSession.status == SessionStatus.partial)
        .count()
    )

    # --- Total minutes (completed sessions with known runtime) ---
    total_seconds_row = (
        db.query(func.sum(WatchSession.runtime_s))
        .filter(
            WatchSession.status == SessionStatus.completed,
            WatchSession.runtime_s.isnot(None),
        )
        .scalar()
    )
    total_minutes = int((total_seconds_row or 0) / 60)

    # --- Completed counts by media type ---
    type_counts = (
        db.query(MediaItem.media_type, func.count(WatchSession.id).label("cnt"))
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(WatchSession.status == SessionStatus.completed)
        .group_by(MediaItem.media_type)
        .all()
    )
    type_map = {r.media_type: r.cnt for r in type_counts}
    movies_watched = type_map.get("movie", 0)
    episodes_watched = type_map.get("episode", 0)

    completion_rate = (completed / total) if total > 0 else 0.0

    # --- Daily stats for last N days ---
    since = _now_utc() - timedelta(days=days)
    daily_rows = (
        db.query(
            func.strftime("%Y-%m-%d", WatchSession.completed_at).label("day"),
            func.sum(WatchSession.runtime_s).label("total_s"),
            func.count(WatchSession.id).label("cnt"),
        )
        .filter(
            WatchSession.status == SessionStatus.completed,
            WatchSession.completed_at.isnot(None),
            WatchSession.completed_at >= since,
        )
        .group_by(func.strftime("%Y-%m-%d", WatchSession.completed_at))
        .order_by(func.strftime("%Y-%m-%d", WatchSession.completed_at))
        .all()
    )
    daily = [
        DailyStatOut(
            date=r.day,
            minutes_watched=int((r.total_s or 0) / 60),
            sessions_count=r.cnt,
        )
        for r in daily_rows
    ]

    # --- Top 10 shows (use max show_id per title for navigation) ---
    top_rows = (
        db.query(
            MediaItem.show_title,
            func.max(MediaItem.show_id).label("show_id"),
            func.count(WatchSession.id).label("cnt"),
            func.sum(WatchSession.runtime_s).label("total_s"),
        )
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            WatchSession.status == SessionStatus.completed,
            MediaItem.media_type == "episode",
            MediaItem.show_title.isnot(None),
        )
        .group_by(MediaItem.show_title)
        .order_by(func.count(WatchSession.id).desc())
        .limit(10)
        .all()
    )
    top_shows = [
        ShowStatOut(
            show_title=r.show_title,
            watched_episodes=r.cnt,
            total_minutes=int((r.total_s or 0) / 60),
            show_id=r.show_id,
        )
        for r in top_rows
    ]

    # --- Additional stats ---

    # Rewatches: items with >= 2 completed sessions
    total_rewatches = (
        db.query(func.count())
        .select_from(
            db.query(WatchSession.media_item_id)
            .filter(WatchSession.status == SessionStatus.completed)
            .group_by(WatchSession.media_item_id)
            .having(func.count(WatchSession.id) >= 2)
            .subquery()
        )
        .scalar()
    ) or 0

    # Watch time split by media type
    type_minutes_rows = (
        db.query(
            MediaItem.media_type,
            func.sum(WatchSession.runtime_s).label("total_s"),
        )
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            WatchSession.status == SessionStatus.completed,
            WatchSession.runtime_s.isnot(None),
        )
        .group_by(MediaItem.media_type)
        .all()
    )
    type_min_map = {r.media_type: int((r.total_s or 0) / 60) for r in type_minutes_rows}
    movie_minutes = type_min_map.get("movie", 0)
    episode_minutes = type_min_map.get("episode", 0)

    # Longest single session (by runtime_s)
    longest_s = (
        db.query(func.max(WatchSession.runtime_s))
        .filter(
            WatchSession.status == SessionStatus.completed,
            WatchSession.runtime_s.isnot(None),
        )
        .scalar()
    ) or 0
    longest_session_minutes = int(longest_s / 60)

    # Streak calculation — fetch distinct completed session dates
    date_rows = (
        db.query(
            func.strftime("%Y-%m-%d", WatchSession.completed_at).label("day"),
        )
        .filter(
            WatchSession.status == SessionStatus.completed,
            WatchSession.completed_at.isnot(None),
        )
        .distinct()
        .order_by(func.strftime("%Y-%m-%d", WatchSession.completed_at))
        .all()
    )
    streak_dates: list[str] = [r.day for r in date_rows if r.day]

    current_streak_days = 0
    longest_streak_days = 0

    if streak_dates:
        from datetime import date as _date

        def _parse(ds: str) -> _date:
            return _date.fromisoformat(ds)

        # Compute longest streak
        run = 1
        longest_streak_days = 1
        for i in range(1, len(streak_dates)):
            prev = _parse(streak_dates[i - 1])
            curr = _parse(streak_dates[i])
            if (curr - prev).days == 1:
                run += 1
                if run > longest_streak_days:
                    longest_streak_days = run
            else:
                run = 1

        # Compute current streak (must end today or yesterday)
        today = _now_utc().date()
        last_day = _parse(streak_dates[-1])
        if (today - last_day).days <= 1:
            run = 1
            for i in range(len(streak_dates) - 1, 0, -1):
                prev = _parse(streak_dates[i - 1])
                curr = _parse(streak_dates[i])
                if (curr - prev).days == 1:
                    run += 1
                else:
                    break
            current_streak_days = run

    return StatsOut(
        total_sessions=total,
        completed_sessions=completed,
        partial_sessions=partial,
        total_minutes_watched=total_minutes,
        movies_watched=movies_watched,
        episodes_watched=episodes_watched,
        completion_rate=round(completion_rate, 4),
        daily=daily,
        top_shows=top_shows,
        total_rewatches=total_rewatches,
        movie_minutes=movie_minutes,
        episode_minutes=episode_minutes,
        longest_session_minutes=longest_session_minutes,
        current_streak_days=current_streak_days,
        longest_streak_days=longest_streak_days,
    )


# ---------------------------------------------------------------------------
# GET /movies
# ---------------------------------------------------------------------------
@router.get("/movies", response_model=MovieListOut)
def get_movies(
    search: Optional[str] = Query(None),
    sort: str = Query("title"),
    filter: str = Query("history", description="history | all | unwatched"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> MovieListOut:
    """
    Movies list.

    filter=history (default): movies with at least one non-ignored session
    filter=all: entire Kodi movie library (watched + unwatched)
    filter=unwatched: movies with no completed session and kodi_state=unwatched
    """
    has_session_sq = (
        db.query(WatchSession.media_item_id)
        .filter(WatchSession.status != SessionStatus.ignored)
        .distinct()
    )

    base = (
        db.query(MediaItem)
        .options(selectinload(MediaItem.artwork))
        .filter(MediaItem.media_type == "movie")
    )

    if filter == "history":
        base = base.filter(MediaItem.id.in_(has_session_sq))
    elif filter == "unwatched":
        base = base.filter(
            MediaItem.kodi_state == KodiState.unwatched,
            MediaItem.id.notin_(has_session_sq),
        )
    # filter == "all": no extra filter

    if search:
        base = base.filter(MediaItem.title.ilike(f"%{search}%"))

    total = base.count()

    if sort == "recent":
        last_activity_sq = (
            db.query(
                WatchSession.media_item_id,
                func.max(WatchSession.last_activity_at).label("last_at"),
            )
            .filter(WatchSession.status != SessionStatus.ignored)
            .group_by(WatchSession.media_item_id)
            .subquery()
        )
        items = (
            base
            .outerjoin(last_activity_sq, last_activity_sq.c.media_item_id == MediaItem.id)
            .order_by(last_activity_sq.c.last_at.desc().nullslast())
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif sort == "added":
        items = base.order_by(
            MediaItem.date_added.desc().nullslast()
        ).offset(offset).limit(limit).all()
    else:
        items = base.order_by(MediaItem.title.asc()).offset(offset).limit(limit).all()

    # Bulk-load session stats for the returned page
    item_ids = [item.id for item in items]
    stats_by_id: dict[int, Any] = {}
    if item_ids:
        for row in (
            db.query(
                WatchSession.media_item_id,
                func.count(WatchSession.id).label("total"),
                func.max(WatchSession.last_activity_at).label("last_at"),
            )
            .filter(
                WatchSession.media_item_id.in_(item_ids),
                WatchSession.status != SessionStatus.ignored,
            )
            .group_by(WatchSession.media_item_id)
            .all()
        ):
            stats_by_id[row.media_item_id] = row

    result = []
    for item in items:
        s = stats_by_id.get(item.id)
        result.append(MovieSummaryOut(
            id=item.id,
            title=item.title,
            year=item.year,
            runtime_s=item.runtime_s,
            genres=item.genres,
            kodi_state=item.kodi_state,
            poster_url=_poster_url(item, thumbnail=False),
            thumbnail_url=_poster_url(item, thumbnail=True),
            total_sessions=s.total if s else 0,
            last_watched_at=s.last_at if s else None,
        ))

    return MovieListOut(total=total, items=result)


# ---------------------------------------------------------------------------
# POST /admin/rebuild-sessions
# ---------------------------------------------------------------------------
@router.post("/admin/rebuild-sessions", response_model=RebuildSessionsOut)
async def rebuild_sessions(
    request: Request,
    cfg: Settings = Depends(get_cfg),
) -> RebuildSessionsOut:
    """
    Rebuild all watch sessions from scratch by re-applying session rules
    against all stored observations.

    This is a maintenance operation — it may take several seconds on a large
    library. Observations are never modified; only watch_session rows are rebuilt.

    The app is LAN-only with no authentication (per project design), so this
    endpoint is intentionally unrestricted.

    Runs rebuild_all() in a thread to avoid blocking the event loop.
    A fresh SQLAlchemy session is created inside the thread to avoid
    passing a session across thread boundaries.
    """
    from logsaga.sessions.builder import rebuild_all

    engine = request.app.state.engine

    def _run() -> tuple[int, int]:
        from sqlalchemy.orm import sessionmaker
        with sessionmaker(bind=engine)() as db:
            items_processed, sessions_created = rebuild_all(db, cfg)
            db.commit()
            return items_processed, sessions_created

    _job_state["rebuild_sessions"]["running"] = True
    _job_state["rebuild_sessions"]["last_result"] = None
    try:
        items_processed, sessions_created = await asyncio.to_thread(_run)
        msg = f"Rebuilt {sessions_created} sessions across {items_processed} items."
        _job_state["rebuild_sessions"]["last_result"] = msg
        return RebuildSessionsOut(
            items_processed=items_processed,
            sessions_created=sessions_created,
            message=msg,
        )
    except Exception as exc:
        _job_state["rebuild_sessions"]["last_result"] = f"Error: {exc}"
        raise
    finally:
        _job_state["rebuild_sessions"]["running"] = False
        _job_state["rebuild_sessions"]["last_run"] = _now_utc()


# ---------------------------------------------------------------------------
# GET /admin/artwork-status
# ---------------------------------------------------------------------------
@router.get("/admin/artwork-status", response_model=ArtworkStatusOut)
def get_artwork_status(
    db: Session = Depends(get_db),
) -> ArtworkStatusOut:
    """
    Artwork download progress summary.

    Returns counts of items with/without downloaded artwork so the Settings
    page can show a progress indicator during the initial fetch-artwork run.
    Only counts media items that have a kodi_media_id (i.e. still in Kodi's
    library and eligible for artwork).
    """
    total_items = (
        db.query(func.count(MediaItem.id))
        .filter(MediaItem.kodi_media_id.isnot(None))
        .scalar()
    ) or 0

    items_with_artwork = (
        db.query(func.count(func.distinct(Artwork.media_item_id)))
        .filter(Artwork.local_path.isnot(None))
        .scalar()
    ) or 0

    total_artwork_rows = (
        db.query(func.count(Artwork.id))
        .scalar()
    ) or 0

    return ArtworkStatusOut(
        total_items=total_items,
        items_with_artwork=items_with_artwork,
        total_artwork_rows=total_artwork_rows,
        pending=max(0, total_items - items_with_artwork),
    )


# ---------------------------------------------------------------------------
# GET /admin/settings — read editable session-rule settings
# PUT /admin/settings — update one or more settings
# ---------------------------------------------------------------------------

# Settings that may be edited via the UI with their allowed ranges.
_EDITABLE_SETTINGS: dict[str, tuple[type, float, float]] = {
    "min_started_seconds":              (int,   0,    600),
    "session_gap_hours":                (float, 0.5,  48),
    "watched_percent":                  (int,   50,   100),
    "watched_remaining_minutes_movie":  (int,   0,    30),
    "watched_remaining_minutes_episode":(int,   0,    15),
    "poll_seconds":                     (int,   10,   300),
    "library_sync_hours":               (float, 0.5,  24),
}


def _read_settings(db: Session, cfg: Settings) -> AdminSettingsOut:
    """Return current values, preferring DB overrides over config defaults."""
    rows = {r.key: r.value for r in db.query(AppSetting).all()}

    def _get(key: str, default: Any, cast: type) -> Any:
        raw = rows.get(key)
        if raw is not None:
            try:
                return cast(raw)
            except (ValueError, TypeError):
                pass
        return default

    return AdminSettingsOut(
        min_started_seconds=_get("min_started_seconds", cfg.min_started_seconds, int),
        session_gap_hours=_get("session_gap_hours", cfg.session_gap_hours, float),
        watched_percent=_get("watched_percent", cfg.watched_percent, int),
        watched_remaining_minutes_movie=_get(
            "watched_remaining_minutes_movie", cfg.watched_remaining_minutes_movie, int),
        watched_remaining_minutes_episode=_get(
            "watched_remaining_minutes_episode", cfg.watched_remaining_minutes_episode, int),
        poll_seconds=_get("poll_seconds", cfg.poll_seconds, int),
        library_sync_hours=_get("library_sync_hours", cfg.library_sync_hours, float),
    )


@router.get("/admin/settings", response_model=AdminSettingsOut)
def get_admin_settings(
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
) -> AdminSettingsOut:
    """Return the current editable session-rule and operational settings."""
    return _read_settings(db, cfg)


@router.put("/admin/settings", response_model=AdminSettingsOut)
def put_admin_settings(
    body: AdminSettingsIn,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
) -> AdminSettingsOut:
    """
    Update one or more editable settings. Only provided fields are changed.
    Values are validated against their allowed ranges and persisted to the
    settings table so they survive restarts.
    """
    updates = body.model_dump(exclude_none=True)

    errors: dict[str, str] = {}
    for key, value in updates.items():
        if key not in _EDITABLE_SETTINGS:
            errors[key] = "unknown setting"
            continue
        cast, lo, hi = _EDITABLE_SETTINGS[key]
        try:
            v = cast(value)
        except (ValueError, TypeError):
            errors[key] = f"must be a {cast.__name__}"
            continue
        if not (lo <= v <= hi):
            errors[key] = f"must be between {lo} and {hi}"

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    for key, value in updates.items():
        cast, _, _ = _EDITABLE_SETTINGS[key]
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=str(cast(value)))
            db.add(row)
        else:
            row.value = str(cast(value))
    db.commit()

    return _read_settings(db, cfg)


# ---------------------------------------------------------------------------
# Job state (module-level, in-memory, not persisted across restarts)
# ---------------------------------------------------------------------------
_job_state: dict[str, dict] = {
    "poll":             {"running": False, "last_run": None, "last_result": None},
    "sync_library":     {"running": False, "last_run": None, "last_result": None},
    "fetch_artwork":    {"running": False, "last_run": None, "last_result": None},
    "enrich_metadata":  {"running": False, "last_run": None, "last_result": None},
    "rebuild_sessions": {"running": False, "last_run": None, "last_result": None},
}


async def _run_job(name: str, coro) -> None:
    """Wrap a job coroutine with job-state tracking (fire-and-forget)."""
    _job_state[name]["running"] = True
    _job_state[name]["last_result"] = None
    try:
        result = await coro
        _job_state[name]["last_result"] = str(result)
    except Exception as exc:
        _job_state[name]["last_result"] = f"Error: {exc}"
    finally:
        _job_state[name]["running"] = False
        _job_state[name]["last_run"] = _now_utc()


async def _do_poll(engine, cfg: Settings) -> str:
    """Run a single poll cycle with its own ephemeral Kodi connection."""
    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.observe.poller import poll_once
    from logsaga.sessions.builder import build_incremental
    from sqlalchemy.orm import sessionmaker as _sm

    def _sync() -> str:
        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        try:
            with _sm(bind=engine)() as db:
                obs = poll_once(conn, db, cfg) or []
                by_item: dict[int, list] = {}
                for o in obs:
                    by_item.setdefault(o.media_item_id, []).append(o)
                for item_id, item_obs in by_item.items():
                    try:
                        build_incremental(item_id, item_obs, db, cfg)
                    except Exception:
                        pass
                db.commit()
            return f"{len(obs)} observation(s)"
        finally:
            conn.close()

    return await asyncio.to_thread(_sync)


async def _do_sync_library(engine, cfg: Settings) -> str:
    """Run a full library sync with its own ephemeral Kodi connection."""
    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.library.sync import sync_library
    from sqlalchemy.orm import sessionmaker as _sm

    def _sync() -> str:
        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        try:
            with _sm(bind=engine, autoflush=False)() as db:
                result = sync_library(conn, db, cfg)
            return (
                f"new={result.total_new}, "
                f"updated={result.total_updated}, "
                f"removed={result.total_removed}"
            )
        finally:
            conn.close()

    return await asyncio.to_thread(_sync)


async def _do_fetch_artwork(engine, cfg: Settings) -> str:
    """Fetch artwork for all items with a Kodi connection."""
    from logsaga.artwork.cache import fetch_all_artwork
    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.kodi.jsonrpc import KodiJSONRPC
    from sqlalchemy.orm import sessionmaker as _sm

    def _sync() -> str:
        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        rpc = KodiJSONRPC(cfg) if cfg.kodi_jsonrpc_url else None
        try:
            SessionLocal = _sm(bind=engine, autoflush=False)
            with SessionLocal() as db:
                item_ids = [
                    r[0] for r in
                    db.query(MediaItem.id)
                    .filter(MediaItem.kodi_media_id.isnot(None))
                    .all()
                ]
            total_updated = 0
            for iid in item_ids:
                with SessionLocal() as db:
                    _, updated = fetch_all_artwork(conn, rpc, db, cfg, item_id=iid)
                    db.commit()
                    total_updated += updated
            return f"{len(item_ids)} items, {total_updated} artwork rows updated"
        finally:
            conn.close()
            if rpc:
                rpc.close()

    return await asyncio.to_thread(_sync)


async def _do_enrich_metadata(engine, cfg: Settings) -> str:
    """Enrich metadata for items missing plot or year."""
    from logsaga.kodi.db import connect, detect_db_name, enrich_item as _enrich_item
    from sqlalchemy import or_
    from sqlalchemy.orm import sessionmaker as _sm

    def _sync() -> str:
        db_name = detect_db_name(cfg)
        conn = connect(cfg, db_name=db_name)
        try:
            with _sm(bind=engine)() as db:
                items = (
                    db.query(MediaItem)
                    .filter(
                        MediaItem.kodi_media_id.isnot(None),
                        or_(MediaItem.plot.is_(None), MediaItem.year.is_(None)),
                    )
                    .order_by(MediaItem.id)
                    .all()
                )
                enriched = 0
                for item in items:
                    identity = _enrich_item(
                        conn, item.media_type, item.kodi_media_id, item.kodi_file_id
                    )
                    if identity is None:
                        continue
                    item.plot = identity.plot
                    item.genres = identity.genres
                    item.release_date = identity.release_date
                    item.director = identity.director
                    item.video_codec = identity.video_codec
                    item.video_width = identity.video_width
                    item.video_height = identity.video_height
                    item.hdr_type = identity.hdr_type
                    item.audio_codec = identity.audio_codec
                    item.audio_channels = identity.audio_channels
                    if identity.release_date:
                        try:
                            item.year = int(identity.release_date[:4])
                        except (ValueError, IndexError):
                            pass
                    enriched += 1
                    if enriched % 100 == 0:
                        db.commit()
                db.commit()
            return f"{enriched} items enriched"
        finally:
            conn.close()

    return await asyncio.to_thread(_sync)


# ---------------------------------------------------------------------------
# GET /admin/jobs
# POST /admin/poll, /sync-library, /fetch-artwork, /enrich-metadata
# ---------------------------------------------------------------------------

@router.get("/admin/jobs", response_model=list[JobStateOut])
def get_jobs() -> list[JobStateOut]:
    """Current state of all background maintenance jobs."""
    return [
        JobStateOut(
            name=name,
            running=state["running"],
            last_run=state["last_run"],
            last_result=state["last_result"],
        )
        for name, state in _job_state.items()
    ]


@router.post("/admin/poll")
async def trigger_poll(request: Request, cfg: Settings = Depends(get_cfg)):
    """Trigger a single poll cycle immediately (fire and forget)."""
    if _job_state["poll"]["running"]:
        raise HTTPException(409, "Job already running")
    asyncio.create_task(_run_job("poll", _do_poll(request.app.state.engine, cfg)))
    return {"message": "Poll cycle triggered"}


@router.post("/admin/sync-library")
async def trigger_sync_library(request: Request, cfg: Settings = Depends(get_cfg)):
    """Trigger a full library sync immediately (fire and forget)."""
    if _job_state["sync_library"]["running"]:
        raise HTTPException(409, "Job already running")
    asyncio.create_task(_run_job("sync_library", _do_sync_library(request.app.state.engine, cfg)))
    return {"message": "Library sync triggered"}


@router.post("/admin/fetch-artwork")
async def trigger_fetch_artwork(request: Request, cfg: Settings = Depends(get_cfg)):
    """Trigger artwork fetch for all items (fire and forget)."""
    if _job_state["fetch_artwork"]["running"]:
        raise HTTPException(409, "Job already running")
    asyncio.create_task(_run_job("fetch_artwork", _do_fetch_artwork(request.app.state.engine, cfg)))
    return {"message": "Artwork fetch triggered"}


@router.post("/admin/enrich-metadata")
async def trigger_enrich_metadata(request: Request, cfg: Settings = Depends(get_cfg)):
    """Trigger metadata enrichment for all items missing plot/year (fire and forget)."""
    if _job_state["enrich_metadata"]["running"]:
        raise HTTPException(409, "Job already running")
    asyncio.create_task(_run_job("enrich_metadata", _do_enrich_metadata(request.app.state.engine, cfg)))
    return {"message": "Metadata enrichment triggered"}
