"""
REST API route handlers for LogSAGA.

All routes are mounted under the /api prefix by main.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from logsaga.api.deps import get_cfg, get_db, get_tmdb_client, get_tmdb_client_optional
from logsaga.api.schemas import (
    ActorAppearanceOut,
    ActorDetailOut,
    ActorFilmographyOut,
    ActorRecentSessionOut,
    AdminSettingsIn,
    FilmographyItemOut,
    AdminSettingsOut,
    ArtworkMissingOut,
    ArtworkOut,
    ArtworkRecoverOut,
    ArtworkStatusOut,
    CastStatusOut,
    ConfigAllOut,
    ConfigAppIn,
    ConfigAppOut,
    ConfigJSONRPCIn,
    ConfigJSONRPCOut,
    ConfigKodiIn,
    ConfigKodiOut,
    ConfigMQTTIn,
    ConfigMQTTOut,
    ConfigTMDBIn,
    ConfigTMDBOut,
    ConnectionTestOut,
    DailyStatOut,
    DashboardStatsOut,
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
    PostWatchedIn,
    PostWatchedOut,
    RecentItemOut,
    RecentlyAddedItemOut,
    RecentlyAddedListOut,
    RecentlyViewedOut,
    RebuildSessionsOut,
    SearchOut,
    SearchResultOut,
    SeasonSummaryOut,
    SessionListOut,
    SessionOut,
    ShowDetailOut,
    ShowStatOut,
    ShowSummaryOut,
    StatsOut,
    TMDBDiscoveryItem,
    TMDBEpisodeOut,
    TMDBMovieOut,
    TMDBPersonOut,
    ActorRecommendationsOut,
    TMDBSeasonOut,
    TMDBSeasonSummaryOut,
    TMDBTVOut,
    BackupInfoOut,
    BackupListOut,
    RestoreResultOut,
    VerifyResultOut,
)
from logsaga.config import Settings
from logsaga.models import (
    AppSetting,
    Artwork,
    ItemCast,
    KodiShow,
    KodiState,
    MediaItem,
    MediaType,
    Person,
    RecentlyViewed,
    SessionStatus,
    SyncState,
    WatchSession,
)

router = APIRouter(tags=["api"])

# How stale cast must be before triggering a background refresh
_CAST_REFRESH_DAYS = 30
# Minimum gap between consecutive refresh attempts for the same item (cooldown)
_CAST_REFRESH_COOLDOWN_HOURS = 12


# ---------------------------------------------------------------------------
# Page-based cast / bio background refresh helpers
# ---------------------------------------------------------------------------

async def _refresh_cast_for_show_bg(app, show_id: int) -> None:
    """Background: refresh TMDB cast for a show. Stamps cast_fetched_at on success."""
    from datetime import datetime as _dt

    from sqlalchemy.orm import sessionmaker as _sm

    from logsaga.api.cast import populate_cast_for_show
    from logsaga.models import KodiShow as _KodiShow
    from logsaga.tmdb.client import TMDBClient

    cfg = app.state.settings
    if not cfg.tmdb_api_key:
        return
    engine = app.state.engine
    SessionLocal = _sm(bind=engine, autoflush=False)
    now = _dt.utcnow()
    try:
        with SessionLocal() as db:
            show = db.get(_KodiShow, show_id)
            if show is None or not show.tmdb_id:
                return
            tmdb = TMDBClient(cfg.tmdb_api_key, db)
            await tmdb.tv_detail(int(show.tmdb_id))
            populate_cast_for_show(show, db)
            show.cast_fetched_at = now
            db.commit()
    except Exception as exc:
        log.warning("page_cast_refresh_show_failed", extra={"show_id": show_id, "error": str(exc)})


async def _refresh_cast_for_movie_bg(app, item_id: int) -> None:
    """Background: refresh TMDB cast for a movie. Stamps cast_fetched_at on success."""
    from datetime import datetime as _dt

    from sqlalchemy.orm import sessionmaker as _sm

    from logsaga.api.cast import populate_cast_for_item
    from logsaga.tmdb.client import TMDBClient

    cfg = app.state.settings
    if not cfg.tmdb_api_key:
        return
    engine = app.state.engine
    SessionLocal = _sm(bind=engine, autoflush=False)
    now = _dt.utcnow()
    try:
        with SessionLocal() as db:
            item = db.get(MediaItem, item_id)
            if item is None or not item.tmdb_id:
                return
            tmdb = TMDBClient(cfg.tmdb_api_key, db)
            await tmdb.movie_detail(int(item.tmdb_id))
            populate_cast_for_item(item, db)
            item.cast_fetched_at = now
            db.commit()
    except Exception as exc:
        log.warning("page_cast_refresh_movie_failed", extra={"item_id": item_id, "error": str(exc)})


async def _refresh_actor_bio_bg(app, tmdb_person_id: int) -> None:
    """Background: refresh actor biography from TMDB. Stamps fetched_at on success."""
    from datetime import datetime as _dt

    from sqlalchemy.orm import sessionmaker as _sm

    from logsaga.tmdb.client import TMDBClient

    cfg = app.state.settings
    if not cfg.tmdb_api_key:
        return
    engine = app.state.engine
    SessionLocal = _sm(bind=engine, autoflush=False)
    now = _dt.utcnow()
    try:
        with SessionLocal() as db:
            person = db.get(Person, tmdb_person_id)
            if person is None:
                return
            tmdb = TMDBClient(cfg.tmdb_api_key, db)
            bio_data = await tmdb.person_detail(tmdb_person_id)
            person.biography = bio_data.get("biography") or None
            person.birthday = bio_data.get("birthday") or None
            person.birthplace = bio_data.get("place_of_birth") or None
            person.imdb_id = bio_data.get("imdb_id") or None
            ext = bio_data.get("external_ids") or {}
            person.instagram_id = ext.get("instagram_id") or None
            person.wikidata_id = ext.get("wikidata_id") or None
            person.fetched_at = now
            db.commit()
    except Exception as exc:
        log.warning(
            "page_bio_refresh_failed",
            extra={"tmdb_person_id": tmdb_person_id, "error": str(exc)},
        )


def _should_refresh_cast(
    fetched_at: Optional[datetime],
    attempted_at: Optional[datetime],
    now: datetime,
) -> bool:
    """Return True when a background cast refresh should be triggered.

    Conditions:
      - cast_fetched_at is NULL (never populated) or older than 30 days (stale)
      - cast_refresh_attempted_at is NULL or older than 12 hours (not in cooldown)
    """
    stale = (
        fetched_at is None
        or (now - fetched_at).total_seconds() > _CAST_REFRESH_DAYS * 86400
    )
    if not stale:
        return False
    on_cooldown = (
        attempted_at is not None
        and (now - attempted_at).total_seconds() < _CAST_REFRESH_COOLDOWN_HOURS * 3600
    )
    return not on_cooldown


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
        return HealthOut(status="error", version=cfg.build_version, settings=settings_dict)

    # Determine health status
    stale_threshold = max(cfg.poll_seconds * 2, 300)  # at least 5 minutes
    if sync.last_success_at is None:
        status = "error" if sync.error_count > 0 else "degraded"
    else:
        age_s = (_now_utc() - sync.last_success_at).total_seconds()
        status = "ok" if age_s <= stale_threshold else "degraded"

    return HealthOut(
        status=status,
        version=cfg.build_version,
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
    filter=former: removed from Kodi or manual show stubs (kodi_idShow IS NULL)
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

    if filter == "former":
        shows_q = db.query(KodiShow).filter(
            or_(
                KodiShow.kodi_state == KodiState.removed,
                KodiShow.kodi_idShow.is_(None),
            )
        )
    else:
        shows_q = db.query(KodiShow).filter(
            KodiShow.kodi_state != KodiState.removed,
            KodiShow.kodi_idShow.isnot(None),
        )
        if filter == "history":
            shows_q = shows_q.filter(KodiShow.id.in_(has_session_sq))
        elif filter == "unwatched":
            shows_q = shows_q.filter(
                KodiShow.id.notin_(has_session_sq),
                KodiShow.kodi_state == KodiState.unwatched,
            )
        # filter == "all": no extra filter beyond excluding removed/manual

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
            rating_imdb=show.rating_imdb,
            votes_imdb=show.votes_imdb,
            rating_tmdb=show.rating_tmdb,
            votes_tmdb=show.votes_tmdb,
            rating_trakt=show.rating_trakt,
            votes_trakt=show.votes_trakt,
            userrating=show.userrating,
        ))
    return result


# ---------------------------------------------------------------------------
# GET /shows/{show_id}  (L-08)
# ---------------------------------------------------------------------------
@router.get("/shows/{show_id}", response_model=ShowDetailOut)
async def get_show(
    show_id: int,
    request: Request,
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

    # Build a map of episode_id → most recent completed session time
    all_ep_ids = [ep.id for ep in episodes]
    last_session_at_by_ep: dict[int, Any] = {}
    if all_ep_ids:
        rows = (
            db.query(WatchSession.media_item_id, func.max(WatchSession.completed_at))
            .filter(
                WatchSession.status == SessionStatus.completed,
                WatchSession.media_item_id.in_(all_ep_ids),
            )
            .group_by(WatchSession.media_item_id)
            .all()
        )
        last_session_at_by_ep = {r[0]: r[1] for r in rows}

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
                last_session_at=last_session_at_by_ep.get(ep.id),
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

    from logsaga.api.cast import populate_cast_for_show
    cast = populate_cast_for_show(show, db)

    # Trigger a background cast refresh if data is stale and not in cooldown
    now = datetime.utcnow()
    if _should_refresh_cast(show.cast_fetched_at, show.cast_refresh_attempted_at, now):
        show.cast_refresh_attempted_at = now
        db.commit()
        asyncio.create_task(_refresh_cast_for_show_bg(request.app, show_id))

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
        rating_imdb=show.rating_imdb,
        votes_imdb=show.votes_imdb,
        rating_tmdb=show.rating_tmdb,
        votes_tmdb=show.votes_tmdb,
        rating_trakt=show.rating_trakt,
        votes_trakt=show.votes_trakt,
        userrating=show.userrating,
        seasons=seasons_out,
        cast=cast,
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
        show_tmdb_id=show.tmdb_id,
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
async def get_item(
    item_id: int,
    request: Request,
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

    from logsaga.api.cast import populate_cast_for_item
    cast = populate_cast_for_item(item, db)

    # Trigger background cast refresh for movies if data is stale and not on cooldown.
    # Episodes use show-level cast; refresh is triggered from the show detail endpoint.
    now = datetime.utcnow()
    if item.media_type == "movie" and _should_refresh_cast(
        item.cast_fetched_at, item.cast_refresh_attempted_at, now
    ):
        item.cast_refresh_attempted_at = now
        db.commit()
        asyncio.create_task(_refresh_cast_for_movie_bg(request.app, item_id))

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
        rating_imdb=item.rating_imdb,
        votes_imdb=item.votes_imdb,
        rating_tmdb=item.rating_tmdb,
        votes_tmdb=item.votes_tmdb,
        rating_trakt=item.rating_trakt,
        votes_trakt=item.votes_trakt,
        userrating=item.userrating,
        created_at=item.created_at,
        updated_at=item.updated_at,
        artwork=[ArtworkOut.model_validate(a) for a in item.artwork],
        sessions=[SessionOut.model_validate(s) for s in sessions_sorted],
        observations=[ObservationOut.model_validate(o) for o in obs_sorted],
        cast=cast,
    )


# ---------------------------------------------------------------------------
# POST /items/{item_id}/recover-artwork
# ---------------------------------------------------------------------------
@router.post("/items/{item_id}/recover-artwork", response_model=list[ArtworkOut])
async def recover_item_artwork(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
) -> list[ArtworkOut]:
    """
    Targeted artwork recovery for a single item.

    Tries Kodi first (if the item is still in the Kodi library), then falls
    back to TMDB for any types still missing.  Rate-limited to once per
    10 minutes per item.

    Returns the updated artwork list for the item.
    """
    from datetime import timedelta

    item = db.get(MediaItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    # Rate-limit: once per 10 minutes per item
    now = _now_utc()
    last = _recover_artwork_cooldown.get(item_id)
    if last and (now - last).total_seconds() < 600:
        pass  # Silently return current artwork — don't reject with error
    else:
        _recover_artwork_cooldown[item_id] = now

        # Kodi pass (sync, in thread)
        if item.kodi_media_id is not None:
            def _kodi_fetch() -> None:
                from logsaga.artwork.cache import fetch_artwork_for_item
                from logsaga.kodi.db import connect, detect_db_name
                from logsaga.kodi.jsonrpc import KodiJSONRPC
                from sqlalchemy.orm import sessionmaker as _sm
                SessionLocal = _sm(bind=request.app.state.engine, autoflush=False)
                db_name = detect_db_name(cfg)
                conn = connect(cfg, db_name=db_name)
                rpc = KodiJSONRPC(cfg) if cfg.kodi_jsonrpc_url else None
                try:
                    with SessionLocal() as _db:
                        _item = _db.get(MediaItem, item_id)
                        if _item:
                            fetch_artwork_for_item(_item, conn, rpc, _db, cfg, force=True)
                            _db.commit()
                finally:
                    conn.close()
                    if rpc:
                        rpc.close()
            try:
                await asyncio.to_thread(_kodi_fetch)
            except Exception as exc:
                logger.warning("recover_artwork_kodi_failed",
                               item_id=item_id, error=str(exc))

        # TMDB fallback for still-missing types
        if cfg.tmdb_api_key:
            from logsaga.artwork.cache import fetch_artwork_tmdb_fallback_async
            from logsaga.artwork.service import get_missing_art_types
            from logsaga.tmdb.client import TMDBClient
            # Re-query item with fresh session to reflect Kodi fetch results
            from sqlalchemy.orm import sessionmaker as _sm2
            with _sm2(bind=request.app.state.engine, autoflush=False)() as fdb:
                fresh = fdb.get(MediaItem, item_id)
                if fresh:
                    missing = get_missing_art_types(fresh, fdb)
                    if missing:
                        try:
                            tmdb = TMDBClient(cfg.tmdb_api_key, fdb)
                            await fetch_artwork_tmdb_fallback_async(
                                fresh, missing, tmdb, fdb, cfg
                            )
                        except Exception as exc:
                            logger.warning("recover_artwork_tmdb_failed",
                                           item_id=item_id, error=str(exc))

    # Return fresh artwork list
    db.expire(item)
    db.refresh(item)
    return [ArtworkOut.model_validate(a) for a in item.artwork]


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
    filter=former: removed from Kodi or manual imports (kodi_media_id IS NULL)
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

    if filter == "former":
        base = base.filter(
            or_(
                MediaItem.kodi_state == KodiState.removed,
                MediaItem.kodi_media_id.is_(None),
            )
        )
    else:
        # Exclude removed items and manual imports from all non-former tabs
        base = base.filter(
            MediaItem.kodi_state != KodiState.removed,
            MediaItem.kodi_media_id.isnot(None),
        )
        if filter == "history":
            base = base.filter(MediaItem.id.in_(has_session_sq))
        elif filter == "unwatched":
            base = base.filter(
                MediaItem.kodi_state == KodiState.unwatched,
                MediaItem.id.notin_(has_session_sq),
            )
        # filter == "all": no extra filter beyond excluding removed/manual

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
            rating_imdb=item.rating_imdb,
            votes_imdb=item.votes_imdb,
            rating_tmdb=item.rating_tmdb,
            votes_tmdb=item.votes_tmdb,
            rating_trakt=item.rating_trakt,
            votes_trakt=item.votes_trakt,
            userrating=item.userrating,
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
# GET /admin/artwork/missing
# ---------------------------------------------------------------------------
@router.get("/admin/artwork/missing", response_model=ArtworkMissingOut)
def get_artwork_missing(
    db: Session = Depends(get_db),
) -> ArtworkMissingOut:
    """
    Count items missing at least one required art type.

    Required types: movies need poster + fanart; episodes need thumb + tvshow.poster.
    Includes both Kodi-library items and TMDB-only stubs.
    """
    from logsaga.artwork.service import REQUIRED_ART_TYPES, find_items_with_missing_art

    missing_ids = set(find_items_with_missing_art(db))
    total_missing = len(missing_ids)

    # Count per art type
    by_type: dict[str, int] = {}
    for media_type, art_types in REQUIRED_ART_TYPES.items():
        for art_type in art_types:
            has_art_sq = (
                db.query(Artwork.media_item_id)
                .filter(
                    Artwork.art_type == art_type,
                    Artwork.local_path.isnot(None),
                )
                .subquery()
            )
            count = (
                db.query(func.count(MediaItem.id))
                .filter(
                    MediaItem.media_type == media_type,
                    ~MediaItem.id.in_(has_art_sq),
                )
                .scalar()
            ) or 0
            if count:
                by_type[art_type] = count

    return ArtworkMissingOut(total_missing=total_missing, by_type=by_type)


# ---------------------------------------------------------------------------
# POST /admin/artwork/recover-missing
# ---------------------------------------------------------------------------
@router.post("/admin/artwork/recover-missing")
async def recover_missing_artwork(
    request: Request,
    cfg: Settings = Depends(get_cfg),
) -> dict:
    """
    Reset stuck retry budgets and re-run artwork fill for all items missing
    required art types.

    Unlike /admin/fetch-artwork (which force-fetches everything), this endpoint:
    - Only targets items actually missing required art types
    - Resets artwork rows whose retry budget is exhausted (3+ failures)
    - Falls back to TMDB when Kodi's art table has no URL for a type

    Returns 409 if the fetch_artwork job is already running.
    """
    if _job_state["fetch_artwork"]["running"]:
        raise HTTPException(status_code=409, detail="fetch_artwork job is already running")

    async def _do_recover() -> str:
        from logsaga.artwork.service import reset_stuck_retry_budgets
        engine = request.app.state.engine
        SessionLocal = sessionmaker(bind=engine, autoflush=False)
        with SessionLocal() as db:
            n = reset_stuck_retry_budgets(db)
            if n:
                db.commit()
        await _fill_missing_artwork_bg(request.app)
        return f"Recovery complete: reset {n} stuck rows"

    asyncio.create_task(_run_job("fetch_artwork", _do_recover()))
    return {"message": "Artwork recovery started"}


# ---------------------------------------------------------------------------
# GET /admin/cast-status
# ---------------------------------------------------------------------------
@router.get("/admin/cast-status", response_model=CastStatusOut)
def get_cast_status(
    db: Session = Depends(get_db),
) -> CastStatusOut:
    """Cast population progress summary (movies + TV shows)."""
    total_items = (
        db.query(func.count(MediaItem.id))
        .filter(
            MediaItem.tmdb_id.isnot(None),
            MediaItem.media_type.in_([MediaType.movie, MediaType.episode]),
        )
        .scalar()
    ) or 0

    items_with_cast = (
        db.query(func.count(func.distinct(ItemCast.media_item_id)))
        .filter(ItemCast.media_item_id.isnot(None))
        .scalar()
    ) or 0

    total_shows = (
        db.query(func.count(KodiShow.id))
        .filter(KodiShow.tmdb_id.isnot(None))
        .scalar()
    ) or 0

    shows_with_cast = (
        db.query(func.count(func.distinct(ItemCast.show_id)))
        .filter(ItemCast.show_id.isnot(None))
        .scalar()
    ) or 0

    return CastStatusOut(
        total_items=total_items,
        items_with_cast=items_with_cast,
        total_shows=total_shows,
        shows_with_cast=shows_with_cast,
        pending_items=max(0, total_items - items_with_cast),
        pending_shows=max(0, total_shows - shows_with_cast),
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
    "enrich_cast":      {"running": False, "last_run": None, "last_result": None},
    "enrich_tmdb_ids":  {"running": False, "last_run": None, "last_result": None},
    "rebuild_sessions": {"running": False, "last_run": None, "last_result": None},
}

# Per-item cooldown for POST /items/{id}/recover-artwork (10 minutes)
_recover_artwork_cooldown: dict[int, datetime] = {}


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
                result = poll_once(conn, db, cfg)
                obs = result.observations
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
    """Fetch artwork for all items: Kodi-library items via Kodi art table, TMDB-only items via TMDB URLs."""
    import httpx
    from logsaga.artwork.cache import fetch_artwork_for_item_async, fetch_tmdb_artwork_async
    from logsaga.kodi.db import connect, detect_db_name
    from logsaga.kodi.jsonrpc import KodiJSONRPC
    from logsaga.models import Artwork as _Artwork
    from sqlalchemy.orm import sessionmaker as _sm

    _CONCURRENCY = 5
    db_name = detect_db_name(cfg)
    kodi_conn = connect(cfg, db_name=db_name)
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
            # TMDB-only items (kodi_media_id is None) that have pending artwork stubs
            tmdb_only_ids = [
                r[0] for r in
                db.query(MediaItem.id)
                .filter(MediaItem.kodi_media_id.is_(None))
                .join(_Artwork, _Artwork.media_item_id == MediaItem.id)
                .filter(_Artwork.local_path.is_(None))
                .distinct()
                .all()
            ]

        sem = asyncio.Semaphore(_CONCURRENCY)
        total_updated = 0

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            async def fetch_one(iid: int) -> int:
                async with sem:
                    with SessionLocal() as db:
                        item = db.get(MediaItem, iid)
                        if item is None:
                            return 0
                        try:
                            count = await fetch_artwork_for_item_async(
                                item, kodi_conn, rpc, db, cfg, client, force=True
                            )
                            db.commit()
                            return count
                        except Exception as exc:
                            db.rollback()
                            log.warning("fetch_artwork item=%d: %s", iid, exc)
                            return 0

            for coro in asyncio.as_completed([fetch_one(iid) for iid in item_ids]):
                total_updated += await coro

        # Process TMDB-only items sequentially (they download from TMDB CDN directly)
        tmdb_updated = 0
        for iid in tmdb_only_ids:
            with SessionLocal() as db:
                item = db.get(MediaItem, iid)
                if item is None:
                    continue
                try:
                    tmdb_updated += await fetch_tmdb_artwork_async(item, db, cfg)
                except Exception as exc:
                    log.warning("fetch_artwork tmdb_only item=%d: %s", iid, exc)
        total_updated += tmdb_updated

        kodi_count = len(item_ids)
        tmdb_count = len(tmdb_only_ids)
        return (
            f"{kodi_count} Kodi items + {tmdb_count} TMDB-only items scanned, "
            f"{total_updated} artwork rows updated"
        )
    finally:
        kodi_conn.close()
        if rpc:
            rpc.close()


async def _do_enrich_cast(engine, cfg: Settings) -> str:
    """Populate cast rows for shows/movies that have a tmdb_id but no cast yet."""
    if not cfg.tmdb_api_key:
        return "TMDB_API_KEY not configured"

    from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show
    from logsaga.models import ItemCast, KodiShow, MediaItem
    from logsaga.tmdb.client import TMDBClient
    from sqlalchemy.orm import sessionmaker as _sm

    _CONCURRENCY = 5
    SessionLocal = _sm(bind=engine, autoflush=False)
    sem = asyncio.Semaphore(_CONCURRENCY)

    with SessionLocal() as db:
        shows_with_cast = {
            row[0]
            for row in db.query(ItemCast.show_id)
            .filter(ItemCast.show_id.isnot(None))
            .distinct()
            .all()
        }
        show_ids = [
            row[0]
            for row in db.query(KodiShow.id)
            .filter(KodiShow.tmdb_id.isnot(None))
            .all()
            if row[0] not in shows_with_cast
        ]
        movies_with_cast = {
            row[0]
            for row in db.query(ItemCast.media_item_id)
            .filter(ItemCast.media_item_id.isnot(None))
            .distinct()
            .all()
        }
        movie_item_ids = [
            row[0]
            for row in db.query(MediaItem.id)
            .filter(
                MediaItem.media_type == "movie",
                MediaItem.tmdb_id.isnot(None),
            )
            .all()
            if row[0] not in movies_with_cast
        ]

    total = len(show_ids) + len(movie_item_ids)
    if total == 0:
        return "No items needing cast"

    shows_done = 0
    movies_done = 0

    async def enrich_show(show_id: int) -> tuple[str, bool]:
        async with sem:
            with SessionLocal() as db:
                show = db.get(KodiShow, show_id)
                if show is None or not show.tmdb_id:
                    return ("show", False)
                try:
                    tmdb = TMDBClient(cfg.tmdb_api_key, db)
                    await tmdb.tv_detail(show.tmdb_id)
                    populate_cast_for_show(show, db)
                    return ("show", True)
                except Exception as exc:
                    db.rollback()
                    log.warning("enrich_cast show=%d: %s", show_id, exc)
                    return ("show", False)

    async def enrich_movie(item_id: int) -> tuple[str, bool]:
        async with sem:
            with SessionLocal() as db:
                item = db.get(MediaItem, item_id)
                if item is None or not item.tmdb_id:
                    return ("movie", False)
                try:
                    tmdb = TMDBClient(cfg.tmdb_api_key, db)
                    await tmdb.movie_detail(item.tmdb_id)
                    populate_cast_for_item(item, db)
                    return ("movie", True)
                except Exception as exc:
                    db.rollback()
                    log.warning("enrich_cast movie=%d: %s", item_id, exc)
                    return ("movie", False)

    all_coros = (
        [enrich_show(sid) for sid in show_ids]
        + [enrich_movie(iid) for iid in movie_item_ids]
    )

    for coro in asyncio.as_completed(all_coros):
        kind, success = await coro
        if success:
            if kind == "show":
                shows_done += 1
            else:
                movies_done += 1

    return f"{shows_done}/{len(show_ids)} shows, {movies_done}/{len(movie_item_ids)} movies enriched"


async def _do_enrich_tmdb_ids(engine, cfg: Settings) -> str:
    """Search TMDB to assign IDs to movies/shows that are missing them."""
    if not cfg.tmdb_api_key:
        return "TMDB_API_KEY not configured"

    import re
    import unicodedata
    from logsaga.models import KodiShow, MediaItem
    from logsaga.tmdb.client import TMDBClient
    from sqlalchemy.orm import sessionmaker as _sm

    def _norm(title: str) -> str:
        t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
        t = re.sub(r"[^\w\s]", " ", t.lower())
        return re.sub(r"\s+", " ", t).strip()

    def _pick_movie(results: list[dict], local_title: str, local_year: int | None) -> int | None:
        ln = _norm(local_title)
        for r in results[:5]:
            if ln not in (_norm(r.get("title", "")), _norm(r.get("original_title", ""))):
                continue
            if local_year:
                rd = r.get("release_date", "")
                ty = int(rd[:4]) if rd and len(rd) >= 4 else None
                if ty and abs(ty - local_year) > 1:
                    continue
            return r.get("id")
        return None

    def _pick_tv(results: list[dict], local_title: str, local_year: int | None) -> int | None:
        ln = _norm(local_title)
        for r in results[:5]:
            if ln not in (_norm(r.get("name", "")), _norm(r.get("original_name", ""))):
                continue
            if local_year:
                fad = r.get("first_air_date", "")
                ty = int(fad[:4]) if fad and len(fad) >= 4 else None
                if ty and abs(ty - local_year) > 1:
                    continue
            return r.get("id")
        return None

    _CONCURRENCY = 5
    SessionLocal = _sm(bind=engine, autoflush=False)
    sem = asyncio.Semaphore(_CONCURRENCY)

    with SessionLocal() as db:
        movie_item_ids = [
            row[0]
            for row in db.query(MediaItem.id)
            .filter(
                MediaItem.media_type == "movie",
                MediaItem.tmdb_id.is_(None),
            )
            .all()
        ]
        show_ids = [
            row[0]
            for row in db.query(KodiShow.id)
            .filter(KodiShow.tmdb_id.is_(None))
            .all()
        ]

    total = len(movie_item_ids) + len(show_ids)
    if total == 0:
        return "No items needing TMDB IDs"

    movies_matched = 0
    shows_matched = 0

    async def resolve_movie(item_id: int) -> tuple[str, bool]:
        async with sem:
            with SessionLocal() as db:
                item = db.get(MediaItem, item_id)
                if item is None or item.tmdb_id:
                    return ("movie", False)
                try:
                    tmdb = TMDBClient(cfg.tmdb_api_key, db)
                    data = await tmdb.search_movie(item.title, item.year)
                    matched_id = _pick_movie(data.get("results", []), item.title, item.year)
                    if matched_id:
                        item.tmdb_id = str(matched_id)
                        db.commit()
                        return ("movie", True)
                    return ("movie", False)
                except Exception as exc:
                    db.rollback()
                    log.warning("enrich_tmdb_ids movie=%d: %s", item_id, exc)
                    return ("movie", False)

    async def resolve_show(show_id: int) -> tuple[str, bool]:
        async with sem:
            with SessionLocal() as db:
                show = db.get(KodiShow, show_id)
                if show is None or show.tmdb_id:
                    return ("show", False)
                try:
                    tmdb = TMDBClient(cfg.tmdb_api_key, db)
                    data = await tmdb.search_tv(show.title, show.year)
                    matched_id = _pick_tv(data.get("results", []), show.title, show.year)
                    if matched_id:
                        show.tmdb_id = str(matched_id)
                        db.commit()
                        return ("show", True)
                    return ("show", False)
                except Exception as exc:
                    db.rollback()
                    log.warning("enrich_tmdb_ids show=%d: %s", show_id, exc)
                    return ("show", False)

    all_coros = (
        [resolve_movie(iid) for iid in movie_item_ids]
        + [resolve_show(sid) for sid in show_ids]
    )

    for coro in asyncio.as_completed(all_coros):
        kind, matched = await coro
        if matched:
            if kind == "movie":
                movies_matched += 1
            else:
                shows_matched += 1

    return (
        f"{movies_matched}/{len(movie_item_ids)} movies matched, "
        f"{shows_matched}/{len(show_ids)} shows matched"
    )


# ---------------------------------------------------------------------------
# GET /admin/jobs
# POST /admin/poll, /sync-library, /fetch-artwork, /enrich-cast, /enrich-tmdb-ids
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


@router.post("/admin/enrich-cast")
async def trigger_enrich_cast(request: Request, cfg: Settings = Depends(get_cfg)):
    """Trigger cast enrichment for all shows/movies missing cast rows (fire and forget)."""
    if _job_state["enrich_cast"]["running"]:
        raise HTTPException(409, "Job already running")
    asyncio.create_task(_run_job("enrich_cast", _do_enrich_cast(request.app.state.engine, cfg)))
    return {"message": "Cast enrichment triggered"}


@router.post("/admin/enrich-tmdb-ids")
async def trigger_enrich_tmdb_ids(request: Request, cfg: Settings = Depends(get_cfg)):
    """Search TMDB to assign IDs to items missing them (fire and forget)."""
    if _job_state["enrich_tmdb_ids"]["running"]:
        raise HTTPException(409, "Job already running")
    asyncio.create_task(_run_job("enrich_tmdb_ids", _do_enrich_tmdb_ids(request.app.state.engine, cfg)))
    return {"message": "TMDB ID enrichment triggered"}


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------
@router.get("/search", response_model=SearchOut)
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    type: str = Query("all", description="all | movie | tv"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
) -> SearchOut:
    """
    Combined local library + TMDB search.

    Local results are always returned. TMDB results are included when
    TMDB_API_KEY is configured; items already in the local library are
    de-duplicated (local version wins).
    """
    from logsaga.tmdb.client import TMDBClient

    results: list[SearchResultOut] = []

    # --- Local search ---
    local_tmdb_ids: set[str] = set()

    if type in ("all", "movie"):
        movie_q = (
            db.query(MediaItem)
            .options(selectinload(MediaItem.artwork))
            .filter(
                MediaItem.media_type == MediaType.movie,
                MediaItem.title.ilike(f"%{q}%"),
            )
            .limit(limit)
            .all()
        )
        for item in movie_q:
            has_hist = (
                db.query(WatchSession.id)
                .filter_by(media_item_id=item.id)
                .first()
            ) is not None
            results.append(SearchResultOut(
                source="local",
                media_type="movie",
                tmdb_id=item.tmdb_id,
                local_id=item.id,
                show_id=None,
                title=item.title,
                year=item.year,
                poster_url=_poster_url(item, thumbnail=True),
                has_history=has_hist,
            ))
            if item.tmdb_id:
                local_tmdb_ids.add(item.tmdb_id)

    if type in ("all", "tv"):
        # Search shows by title
        show_q = (
            db.query(KodiShow)
            .filter(KodiShow.title.ilike(f"%{q}%"))
            .limit(limit)
            .all()
        )
        for show in show_q:
            # Check if any episode has history
            has_hist = (
                db.query(WatchSession.id)
                .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
                .filter(MediaItem.show_id == show.id)
                .first()
            ) is not None
            # Get poster from an episode
            poster: Optional[str] = None
            ep = (
                db.query(MediaItem)
                .options(selectinload(MediaItem.artwork))
                .filter(MediaItem.show_id == show.id)
                .first()
            )
            if ep:
                poster = _poster_url(ep, thumbnail=True)
            results.append(SearchResultOut(
                source="local",
                media_type="tv",
                tmdb_id=show.tmdb_id,
                local_id=None,
                show_id=show.id,
                title=show.title,
                year=show.year,
                poster_url=poster,
                has_history=has_hist,
            ))
            if show.tmdb_id:
                local_tmdb_ids.add(show.tmdb_id)

    # --- TMDB search (if key available) ---
    if cfg.tmdb_enabled:
        try:
            tmdb = TMDBClient(api_key=cfg.tmdb_api_key, db=db)
            tmdb_type = {"movie": "movie", "tv": "tv", "all": "multi"}.get(type, "multi")
            raw = await tmdb.search(q, media_type=tmdb_type)
            tmdb_results = raw.get("results", [])

            for r in tmdb_results[:limit]:
                r_type = r.get("media_type") or type  # multi returns media_type
                if r_type not in ("movie", "tv"):
                    continue
                r_tmdb_id = str(r.get("id", ""))
                if r_tmdb_id in local_tmdb_ids:
                    continue  # local version already in results

                title = r.get("title") or r.get("name") or ""
                year: Optional[int] = None
                date_str = r.get("release_date") or r.get("first_air_date")
                if date_str:
                    try:
                        year = int(date_str[:4])
                    except (ValueError, IndexError):
                        pass
                poster_path = r.get("poster_path")
                poster_url = TMDBClient.poster_url(poster_path) if poster_path else None

                results.append(SearchResultOut(
                    source="tmdb",
                    media_type=r_type,
                    tmdb_id=r_tmdb_id,
                    local_id=None,
                    show_id=None,
                    title=title,
                    year=year,
                    poster_url=poster_url,
                    has_history=False,
                ))
        except Exception as exc:
            log.warning("TMDB search failed (results degraded to local only): %s", exc)

    return SearchOut(results=results[:limit], tmdb_enabled=cfg.tmdb_enabled)


# ---------------------------------------------------------------------------
# GET /tmdb/movie/{tmdb_id}
# ---------------------------------------------------------------------------
@router.get("/tmdb/movie/{tmdb_id}", response_model=TMDBMovieOut)
async def get_tmdb_movie(
    tmdb_id: int,
    db: Session = Depends(get_db),
    tmdb: "TMDBClient" = Depends(get_tmdb_client),  # type: ignore[name-defined]
) -> TMDBMovieOut:
    """Cached TMDB movie detail. Auto-tracks in recently_viewed."""
    from logsaga.api.manual import _upsert_recently_viewed

    data = await tmdb.movie_detail(tmdb_id)

    # Find local match
    local_item = db.query(MediaItem).filter_by(
        media_type=MediaType.movie, tmdb_id=str(tmdb_id)
    ).first()
    local_id = local_item.id if local_item else None

    genres = [g.get("name", "") for g in data.get("genres", [])]

    director: Optional[str] = None
    for person in data.get("credits", {}).get("crew", []):
        if person.get("job") == "Director":
            director = person.get("name")
            break

    cast = [
        TMDBPersonOut(
            name=p["name"],
            character=p.get("character"),
            tmdb_person_id=p.get("id"),
            profile_url=(
                f"https://image.tmdb.org/t/p/w185{p['profile_path']}"
                if p.get("profile_path")
                else None
            ),
        )
        for p in data.get("credits", {}).get("cast", [])[:20]
    ]

    poster_url = tmdb.poster_url(data.get("poster_path"))
    backdrop_url = tmdb.backdrop_url(data.get("backdrop_path"))

    # Track in recently_viewed (only when not already in local library)
    if local_id is None:
        _upsert_recently_viewed(
            db,
            media_type="movie",
            tmdb_id=tmdb_id,
            title=data.get("title", ""),
            poster_url=poster_url,
        )
        db.commit()

    return TMDBMovieOut(
        tmdb_id=tmdb_id,
        title=data.get("title", ""),
        overview=data.get("overview") or None,
        release_date=data.get("release_date") or None,
        runtime=data.get("runtime"),
        genres=genres,
        poster_url=poster_url,
        backdrop_url=backdrop_url,
        vote_average=data.get("vote_average"),
        director=director,
        cast=cast,
        local_id=local_id,
    )


# ---------------------------------------------------------------------------
# GET /tmdb/tv/{tmdb_id}
# ---------------------------------------------------------------------------
@router.get("/tmdb/tv/{tmdb_id}", response_model=TMDBTVOut)
async def get_tmdb_tv(
    tmdb_id: int,
    db: Session = Depends(get_db),
    tmdb: "TMDBClient" = Depends(get_tmdb_client),  # type: ignore[name-defined]
) -> TMDBTVOut:
    """Cached TMDB TV show detail. Auto-tracks in recently_viewed."""
    from logsaga.api.manual import _upsert_recently_viewed

    data = await tmdb.tv_detail(tmdb_id)

    local_show = db.query(KodiShow).filter_by(tmdb_id=str(tmdb_id)).first()
    local_show_id = local_show.id if local_show else None

    genres = [g.get("name", "") for g in data.get("genres", [])]

    seasons = [
        TMDBSeasonSummaryOut(
            season=s.get("season_number", 0),
            title=s.get("name", f"Season {s.get('season_number', 0)}"),
            episode_count=s.get("episode_count", 0),
            air_date=s.get("air_date") or None,
            poster_url=tmdb.poster_url(s.get("poster_path")),
        )
        for s in data.get("seasons", [])
    ]

    cast = [
        TMDBPersonOut(
            name=p["name"],
            character=p.get("character"),
            tmdb_person_id=p.get("id"),
            profile_url=(
                f"https://image.tmdb.org/t/p/w185{p['profile_path']}"
                if p.get("profile_path")
                else None
            ),
        )
        for p in data.get("credits", {}).get("cast", [])[:20]
    ]

    poster_url = tmdb.poster_url(data.get("poster_path"))
    backdrop_url = tmdb.backdrop_url(data.get("backdrop_path"))

    if local_show_id is None:
        _upsert_recently_viewed(
            db,
            media_type="tv",
            tmdb_id=tmdb_id,
            title=data.get("name", ""),
            poster_url=poster_url,
        )
        db.commit()

    return TMDBTVOut(
        tmdb_id=tmdb_id,
        title=data.get("name", ""),
        overview=data.get("overview") or None,
        first_air_date=data.get("first_air_date") or None,
        status=data.get("status") or None,
        genres=genres,
        poster_url=poster_url,
        backdrop_url=backdrop_url,
        vote_average=data.get("vote_average"),
        seasons=seasons,
        cast=cast,
        local_show_id=local_show_id,
    )


# ---------------------------------------------------------------------------
# GET /tmdb/tv/{tmdb_id}/season/{season_number}
# ---------------------------------------------------------------------------
@router.get("/tmdb/tv/{tmdb_id}/season/{season_number}", response_model=TMDBSeasonOut)
async def get_tmdb_season(
    tmdb_id: int,
    season_number: int,
    db: Session = Depends(get_db),
    tmdb: "TMDBClient" = Depends(get_tmdb_client),  # type: ignore[name-defined]
) -> TMDBSeasonOut:
    """Cached TMDB season detail with episode list."""
    data = await tmdb.season_detail(tmdb_id, season_number)

    local_show = db.query(KodiShow).filter_by(tmdb_id=str(tmdb_id)).first()
    local_show_id = local_show.id if local_show else None

    episodes = [
        TMDBEpisodeOut(
            season=ep.get("season_number", season_number),
            episode=ep.get("episode_number", 0),
            title=ep.get("name", ""),
            overview=ep.get("overview") or None,
            air_date=ep.get("air_date") or None,
            runtime=ep.get("runtime"),
            still_url=tmdb.poster_url(ep.get("still_path"), size="w300") if ep.get("still_path") else None,
        )
        for ep in data.get("episodes", [])
    ]

    return TMDBSeasonOut(
        tmdb_id=tmdb_id,
        season=season_number,
        title=data.get("name", f"Season {season_number}"),
        overview=data.get("overview") or None,
        air_date=data.get("air_date") or None,
        poster_url=tmdb.poster_url(data.get("poster_path")),
        episodes=episodes,
        local_show_id=local_show_id,
    )


# ---------------------------------------------------------------------------
# GET /recently-viewed
# ---------------------------------------------------------------------------
@router.get("/recently-viewed", response_model=list[RecentlyViewedOut])
def get_recently_viewed(
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[RecentlyViewedOut]:
    """Recently browsed TMDB pages (items not yet added to local history)."""
    rows = (
        db.query(RecentlyViewed)
        .order_by(RecentlyViewed.viewed_at.desc())
        .limit(limit)
        .all()
    )
    return [RecentlyViewedOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /watched — manual watch history
# ---------------------------------------------------------------------------
@router.post("/watched", response_model=PostWatchedOut)
async def post_watched(
    body: PostWatchedIn,
    request: Request,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
) -> PostWatchedOut:
    """
    Manually add one or more items to watch history.

    Items are created as completed WatchSession rows with source=manual.
    New items (not yet in local DB) are fetched from TMDB and artwork is
    downloaded asynchronously.
    """
    from logsaga.api.manual import create_manual_sessions
    from logsaga.tmdb.client import TMDBClient

    tmdb_client = (
        TMDBClient(api_key=cfg.tmdb_api_key, db=db)
        if cfg.tmdb_enabled
        else None
    )

    # Fire-and-forget artwork + cast for new TMDB-only items
    engine = request.app.state.engine
    def _schedule_enrich(item_id: int) -> None:
        from datetime import datetime as _dt

        from logsaga.api.cast import populate_cast_for_item, populate_cast_for_show
        from logsaga.artwork.cache import fetch_tmdb_artwork_async
        from logsaga.models import KodiShow as _KodiShow
        from sqlalchemy.orm import sessionmaker as _sm

        async def _run():
            now = _dt.utcnow()
            with _sm(bind=engine)() as enrich_db:
                art_item = enrich_db.get(MediaItem, item_id)
                if art_item is None:
                    return
                # Artwork — TMDB-only items only (Kodi-linked items get art via poll loop)
                if art_item.kodi_media_id is None:
                    await fetch_tmdb_artwork_async(art_item, enrich_db, cfg)
                # Cast — TMDB detail is already cached by create_manual_sessions
                art_item.cast_refresh_attempted_at = now
                if art_item.media_type == "movie":
                    populate_cast_for_item(art_item, enrich_db)
                    art_item.cast_fetched_at = now
                elif art_item.media_type == "episode" and art_item.show_id:
                    show = enrich_db.get(_KodiShow, art_item.show_id)
                    if show:
                        populate_cast_for_show(show, enrich_db)
                        show.cast_fetched_at = now
                        show.cast_refresh_attempted_at = now
                enrich_db.commit()

        asyncio.create_task(_run())

    return await create_manual_sessions(
        db=db,
        items=body.items,
        tmdb_client=tmdb_client,
        schedule_artwork=_schedule_enrich,
    )


# ---------------------------------------------------------------------------
# GET /actors/{tmdb_person_id}
# ---------------------------------------------------------------------------
@router.get("/actors/{tmdb_person_id}", response_model=ActorDetailOut)
async def get_actor(
    tmdb_person_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tmdb: Optional["TMDBClient"] = Depends(get_tmdb_client_optional),  # type: ignore[name-defined]
) -> ActorDetailOut:
    """Actor profile: biography + all watched/library appearances.

    The person must already be in the local DB (populated lazily when any
    item detail page is visited).  Returns 404 if the actor is unknown.

    Bio refresh rules:
    - First visit (fetched_at IS NULL): fetch bio now (blocking, one-time cost).
    - Subsequent visits: re-fetch in background if bio is > 30 days old and the
      last attempt was > 12 hours ago.  Stale bio is returned immediately.
    """
    person = db.get(Person, tmdb_person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Actor not found in local database")

    now = datetime.utcnow()

    if person.fetched_at is None and tmdb is not None:
        # First visit: fetch bio synchronously so the page has content right away
        try:
            bio_data = await tmdb.person_detail(tmdb_person_id)
            person.biography = bio_data.get("biography") or None
            person.birthday = bio_data.get("birthday") or None
            person.birthplace = bio_data.get("place_of_birth") or None
            person.imdb_id = bio_data.get("imdb_id") or None
            ext = bio_data.get("external_ids") or {}
            person.instagram_id = ext.get("instagram_id") or None
            person.wikidata_id = ext.get("wikidata_id") or None
            person.fetched_at = now
            person.bio_refresh_attempted_at = now
            db.commit()
        except Exception as exc:
            log.warning("Failed to fetch biography for person %d: %s", tmdb_person_id, exc)
    else:
        # Subsequent visits: check whether a background refresh is due
        bio_stale = (
            person.fetched_at is not None
            and (now - person.fetched_at).total_seconds() > _CAST_REFRESH_DAYS * 86400
        )
        bio_on_cooldown = (
            person.bio_refresh_attempted_at is not None
            and (now - person.bio_refresh_attempted_at).total_seconds()
            < _CAST_REFRESH_COOLDOWN_HOURS * 3600
        )
        if bio_stale and not bio_on_cooldown and tmdb is not None:
            person.bio_refresh_attempted_at = now
            db.commit()
            asyncio.create_task(_refresh_actor_bio_bg(request.app, tmdb_person_id))

    watched_items = _build_actor_appearances(tmdb_person_id, db, watched_only=True)
    library_items = _build_actor_appearances(
        tmdb_person_id,
        db,
        watched_only=False,
        exclude_show_ids={a.show_id for a in watched_items if a.show_id},
        exclude_item_ids={a.item_id for a in watched_items if a.item_id},
    )

    profile_url = (
        f"https://image.tmdb.org/t/p/w342{person.profile_path}"
        if person.profile_path
        else None
    )

    return ActorDetailOut(
        tmdb_person_id=tmdb_person_id,
        name=person.name,
        biography=person.biography,
        birthday=person.birthday,
        birthplace=person.birthplace,
        profile_url=profile_url,
        imdb_id=person.imdb_id,
        instagram_id=person.instagram_id,
        wikidata_id=person.wikidata_id,
        watched_items=watched_items,
        library_items=library_items,
    )


def _build_actor_appearances(
    tmdb_person_id: int,
    db: Session,
    watched_only: bool,
    exclude_show_ids: Optional[set] = None,
    exclude_item_ids: Optional[set] = None,
) -> list[ActorAppearanceOut]:
    """Build the list of TV show and movie appearances for an actor.

    TV shows are grouped at the show level (not per-episode).
    Movies are individual media_item rows.
    """
    result: list[ActorAppearanceOut] = []

    # --- TV shows ---
    # Path 1: show-level cast rows (item_cast.show_id populated by enrich-cast)
    show_rows_direct = (
        db.query(KodiShow.id, KodiShow.title, ItemCast.character)
        .join(ItemCast, ItemCast.show_id == KodiShow.id)
        .filter(ItemCast.person_id == tmdb_person_id)
        .all()
    )
    # Path 2: episode-level cast rows (item_cast.media_item_id populated by enrich-episode-cast)
    show_rows_episode = (
        db.query(KodiShow.id, KodiShow.title, ItemCast.character)
        .join(MediaItem, MediaItem.id == ItemCast.media_item_id)
        .join(KodiShow, KodiShow.id == MediaItem.show_id)
        .filter(
            ItemCast.person_id == tmdb_person_id,
            MediaItem.media_type == MediaType.episode,
        )
        .distinct()
        .all()
    )
    # Merge both paths: prefer show-level character name but don't duplicate shows
    seen_show_ids: dict[int, tuple[str, Optional[str]]] = {}
    for sid, title, character in show_rows_direct:
        seen_show_ids[sid] = (title, character)
    for sid, title, character in show_rows_episode:
        if sid not in seen_show_ids:
            seen_show_ids[sid] = (title, character)
    show_rows = [(sid, title, char) for sid, (title, char) in seen_show_ids.items()]

    for show_id_val, show_title, character in show_rows:
        if exclude_show_ids and show_id_val in exclude_show_ids:
            continue

        # Count total episodes in library for this show
        total_eps = (
            db.query(func.count(MediaItem.id))
            .filter(
                MediaItem.show_id == show_id_val,
                MediaItem.media_type == MediaType.episode,
            )
            .scalar()
            or 0
        )

        # Count watched episodes
        watched_eps = (
            db.query(func.count(func.distinct(WatchSession.media_item_id)))
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id_val,
                WatchSession.status == SessionStatus.completed,
            )
            .scalar()
            or 0
        )

        last_watched_at = (
            db.query(func.max(WatchSession.completed_at))
            .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id_val,
                WatchSession.status == SessionStatus.completed,
            )
            .scalar()
        )

        if watched_only and last_watched_at is None:
            continue

        # Poster: look for tvshow.poster on any episode of this show
        poster_art = (
            db.query(Artwork)
            .join(MediaItem, Artwork.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id_val,
                Artwork.art_type == "tvshow.poster",
                Artwork.local_path.isnot(None),
            )
            .first()
        )
        poster_url = f"/art/{poster_art.local_path}" if poster_art else None

        result.append(
            ActorAppearanceOut(
                media_type="tv",
                title=show_title,
                show_id=show_id_val,
                character=character,
                total_episodes=total_eps,
                watched_episodes=watched_eps,
                watched=watched_eps > 0,
                last_watched_at=last_watched_at,
                poster_url=poster_url,
            )
        )

    # --- Movies ---
    movie_rows = (
        db.query(
            MediaItem.id,
            MediaItem.title,
            ItemCast.character,
        )
        .join(ItemCast, ItemCast.media_item_id == MediaItem.id)
        .filter(
            ItemCast.person_id == tmdb_person_id,
            MediaItem.media_type == MediaType.movie,
        )
        .all()
    )

    for item_id_val, movie_title, character in movie_rows:
        if exclude_item_ids and item_id_val in exclude_item_ids:
            continue

        last_watched_at = (
            db.query(func.max(WatchSession.completed_at))
            .filter(
                WatchSession.media_item_id == item_id_val,
                WatchSession.status == SessionStatus.completed,
            )
            .scalar()
        )

        if watched_only and last_watched_at is None:
            continue

        # Poster
        poster_art = (
            db.query(Artwork)
            .filter(
                Artwork.media_item_id == item_id_val,
                Artwork.art_type == "poster",
                Artwork.local_path.isnot(None),
            )
            .first()
        )
        poster_url = f"/art/{poster_art.local_path}" if poster_art else None

        result.append(
            ActorAppearanceOut(
                media_type="movie",
                title=movie_title,
                item_id=item_id_val,
                character=character,
                watched=last_watched_at is not None,
                last_watched_at=last_watched_at,
                poster_url=poster_url,
            )
        )

    # Sort: most recently watched first
    result.sort(key=lambda x: x.last_watched_at or datetime.min, reverse=True)
    return result


# ---------------------------------------------------------------------------
# GET /api/actors/{tmdb_person_id}/recommendations
# ---------------------------------------------------------------------------
@router.get(
    "/actors/{tmdb_person_id}/recommendations",
    response_model=ActorRecommendationsOut,
)
async def get_actor_recommendations(
    tmdb_person_id: int,
    db: Session = Depends(get_db),
    tmdb: Optional["TMDBClient"] = Depends(get_tmdb_client_optional),  # type: ignore[name-defined]
) -> ActorRecommendationsOut:
    """Return popular and recent TMDB titles featuring an actor.

    Includes items already in the local library (marked with in_library/watched).
    Both sections contain up to 12 items, with no duplicates between them.
    Results are cached in tmdb_cache for 30 days.
    """
    from logsaga.tmdb.client import TMDBClient

    if tmdb is None:
        return ActorRecommendationsOut()

    try:
        credits_data = await tmdb.person_credits(tmdb_person_id)
    except Exception as exc:
        log.warning("Failed to fetch person credits for %d: %s", tmdb_person_id, exc)
        return ActorRecommendationsOut()

    # Build sets of TMDB IDs in local library
    local_movie_tmdb_ids: set[int] = {
        int(r[0]) for r in db.query(MediaItem.tmdb_id).filter(
            MediaItem.media_type == MediaType.movie,
            MediaItem.tmdb_id.isnot(None),
        ).all()
    }
    local_show_tmdb_ids: set[int] = {
        int(r[0]) for r in db.query(KodiShow.tmdb_id).filter(
            KodiShow.tmdb_id.isnot(None)
        ).all()
    }
    # Build sets of watched TMDB IDs
    watched_movie_tmdb_ids: set[int] = {
        int(r[0]) for r in db.query(MediaItem.tmdb_id)
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            MediaItem.media_type == MediaType.movie,
            MediaItem.tmdb_id.isnot(None),
            WatchSession.status == SessionStatus.completed,
        ).all()
    }
    watched_show_tmdb_ids: set[int] = {
        int(r[0]) for r in db.query(KodiShow.tmdb_id)
        .join(MediaItem, MediaItem.show_id == KodiShow.id)
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            KodiShow.tmdb_id.isnot(None),
            WatchSession.status == SessionStatus.completed,
        ).all()
    }

    _DISCOVERY_LIMIT = 12
    _EXCLUDED_GENRES = {10767, 10763, 10764}

    def _to_discovery(entry: dict) -> TMDBDiscoveryItem | None:
        media_type = entry.get("media_type")
        if media_type not in ("movie", "tv"):
            return None
        tmdb_id = entry.get("id")
        if not tmdb_id:
            return None
        # Skip talk shows, news, and reality TV (TMDB genre IDs 10767, 10763, 10764)
        genre_ids = set(entry.get("genre_ids") or [])
        if genre_ids & _EXCLUDED_GENRES:
            return None
        title = entry.get("title") or entry.get("name") or ""
        date_str = entry.get("release_date") or entry.get("first_air_date") or ""
        year: int | None = None
        try:
            year = int(date_str[:4]) if date_str else None
        except (ValueError, TypeError):
            pass
        poster_path = entry.get("poster_path")
        in_lib = (
            (media_type == "movie" and tmdb_id in local_movie_tmdb_ids)
            or (media_type == "tv" and tmdb_id in local_show_tmdb_ids)
        )
        is_watched = (
            (media_type == "movie" and tmdb_id in watched_movie_tmdb_ids)
            or (media_type == "tv" and tmdb_id in watched_show_tmdb_ids)
        )
        return TMDBDiscoveryItem(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            poster_url=TMDBClient.poster_url(poster_path, "w342") if poster_path else None,
            year=year,
            popularity=entry.get("popularity"),
            in_library=in_lib,
            watched=is_watched,
        )

    cast_entries = credits_data.get("cast", [])
    items = [i for e in cast_entries if (i := _to_discovery(e)) is not None]

    # Most popular: sort by popularity desc
    popular_items = sorted(items, key=lambda x: x.popularity or 0.0, reverse=True)[:_DISCOVERY_LIMIT]
    popular_ids = {i.tmdb_id for i in popular_items}

    # Most recent: sort by year desc, skip items already in popular
    recent_items = sorted(
        [i for i in items if i.tmdb_id not in popular_ids],
        key=lambda x: x.year or 0,
        reverse=True,
    )[:_DISCOVERY_LIMIT]

    return ActorRecommendationsOut(most_popular=popular_items, most_recent=recent_items)


# ---------------------------------------------------------------------------
# GET /api/actors/{tmdb_person_id}/filmography
# ---------------------------------------------------------------------------
@router.get("/actors/{tmdb_person_id}/filmography", response_model=ActorFilmographyOut)
async def get_actor_filmography(
    tmdb_person_id: int,
    db: Session = Depends(get_db),
    tmdb: Optional["TMDBClient"] = Depends(get_tmdb_client_optional),  # type: ignore[name-defined]
) -> ActorFilmographyOut:
    """Full TMDB filmography for an actor, split into movies and TV shows.

    Each item has a credit_type ("acting", "self", "production", "unknown") derived
    from the TMDB combined_credits cast/crew arrays.  Library membership and watched
    status are checked against the local DB.

    Results are cached in tmdb_cache for 30 days (same cache as recommendations).
    """
    from logsaga.tmdb.client import TMDBClient

    if tmdb is None:
        return ActorFilmographyOut()

    try:
        credits_data = await tmdb.person_credits(tmdb_person_id)
    except Exception as exc:
        log.warning("Failed to fetch person credits for filmography %d: %s", tmdb_person_id, exc)
        return ActorFilmographyOut()

    _EXCLUDED_GENRES = {10767, 10763, 10764}

    # --- Library/watched lookup tables ---
    # Movies: tmdb_id → MediaItem.id
    movie_tmdb_to_item_id: dict[int, int] = {
        int(r[0]): r[1]
        for r in db.query(MediaItem.tmdb_id, MediaItem.id).filter(
            MediaItem.media_type == MediaType.movie,
            MediaItem.tmdb_id.isnot(None),
        ).all()
    }
    # Watched movie tmdb_ids
    watched_movie_tmdb_ids: set[int] = {
        int(r[0]) for r in db.query(MediaItem.tmdb_id)
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(
            MediaItem.media_type == MediaType.movie,
            MediaItem.tmdb_id.isnot(None),
            WatchSession.status == SessionStatus.completed,
        ).all()
    }
    # TV shows: tmdb_id → (show_id, watched_eps, total_eps)
    show_rows = db.query(KodiShow.tmdb_id, KodiShow.id).filter(
        KodiShow.tmdb_id.isnot(None)
    ).all()
    show_tmdb_to_id: dict[int, int] = {int(r[0]): r[1] for r in show_rows}

    # Episode counts per show — two aggregated queries instead of 2×N per-show queries
    show_ids = list(show_tmdb_to_id.values())
    if show_ids:
        total_rows = (
            db.query(MediaItem.show_id, func.count(MediaItem.id))
            .filter(
                MediaItem.show_id.in_(show_ids),
                MediaItem.media_type == MediaType.episode,
            )
            .group_by(MediaItem.show_id)
            .all()
        )
        total_eps_by_show: dict[int, int] = {r[0]: r[1] for r in total_rows}

        watched_rows = (
            db.query(MediaItem.show_id, func.count(func.distinct(WatchSession.media_item_id)))
            .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id.in_(show_ids),
                WatchSession.status == SessionStatus.completed,
            )
            .group_by(MediaItem.show_id)
            .all()
        )
        watched_eps_by_show: dict[int, int] = {r[0]: r[1] for r in watched_rows}
    else:
        total_eps_by_show = {}
        watched_eps_by_show = {}

    def _year_from_entry(entry: dict) -> int | None:
        date_str = entry.get("release_date") or entry.get("first_air_date") or ""
        try:
            return int(date_str[:4]) if date_str else None
        except (ValueError, TypeError):
            return None

    def _credit_type_from_cast(character: str | None) -> str:
        if not character:
            return "unknown"
        if character.lower().startswith("self"):
            return "self"
        return "acting"

    seen: set[tuple[int, str, str]] = set()  # (tmdb_id, media_type, credit_type)
    movies: list[FilmographyItemOut] = []
    tv_shows: list[FilmographyItemOut] = []

    # Process cast entries
    for entry in credits_data.get("cast", []):
        media_type = entry.get("media_type")
        if media_type not in ("movie", "tv"):
            continue
        tmdb_id = entry.get("id")
        if not tmdb_id:
            continue
        genre_ids = set(entry.get("genre_ids") or [])
        if genre_ids & _EXCLUDED_GENRES:
            continue
        character = entry.get("character") or None
        credit_type = _credit_type_from_cast(character)
        key = (tmdb_id, media_type, credit_type)
        if key in seen:
            continue
        seen.add(key)

        title = entry.get("title") or entry.get("name") or ""
        year = _year_from_entry(entry)
        poster_path = entry.get("poster_path")
        poster_url = TMDBClient.poster_url(poster_path, "w342") if poster_path else None

        if media_type == "movie":
            item_id = movie_tmdb_to_item_id.get(tmdb_id)
            in_lib = item_id is not None
            is_watched = tmdb_id in watched_movie_tmdb_ids
            movies.append(FilmographyItemOut(
                tmdb_id=tmdb_id, media_type="movie", title=title, year=year,
                poster_url=poster_url, character=character, credit_type=credit_type,
                item_id=item_id, in_library=in_lib, watched=is_watched,
            ))
        else:
            show_id = show_tmdb_to_id.get(tmdb_id)
            in_lib = show_id is not None
            w_eps = watched_eps_by_show.get(show_id, 0) if show_id else None
            t_eps = total_eps_by_show.get(show_id, 0) if show_id else None
            is_watched = (w_eps or 0) > 0
            tv_shows.append(FilmographyItemOut(
                tmdb_id=tmdb_id, media_type="tv", title=title, year=year,
                poster_url=poster_url, character=character, credit_type=credit_type,
                show_id=show_id, in_library=in_lib, watched=is_watched,
                watched_episodes=w_eps, total_episodes=t_eps,
            ))

    # Process crew entries (credit_type = "production")
    for entry in credits_data.get("crew", []):
        media_type = entry.get("media_type")
        if media_type not in ("movie", "tv"):
            continue
        tmdb_id = entry.get("id")
        if not tmdb_id:
            continue
        genre_ids = set(entry.get("genre_ids") or [])
        if genre_ids & _EXCLUDED_GENRES:
            continue
        job = entry.get("job") or None
        credit_type = "production"
        key = (tmdb_id, media_type, credit_type)
        if key in seen:
            continue
        seen.add(key)

        title = entry.get("title") or entry.get("name") or ""
        year = _year_from_entry(entry)
        poster_path = entry.get("poster_path")
        poster_url = TMDBClient.poster_url(poster_path, "w342") if poster_path else None

        if media_type == "movie":
            item_id = movie_tmdb_to_item_id.get(tmdb_id)
            in_lib = item_id is not None
            is_watched = tmdb_id in watched_movie_tmdb_ids
            movies.append(FilmographyItemOut(
                tmdb_id=tmdb_id, media_type="movie", title=title, year=year,
                poster_url=poster_url, job=job, credit_type=credit_type,
                item_id=item_id, in_library=in_lib, watched=is_watched,
            ))
        else:
            show_id = show_tmdb_to_id.get(tmdb_id)
            in_lib = show_id is not None
            w_eps = watched_eps_by_show.get(show_id, 0) if show_id else None
            t_eps = total_eps_by_show.get(show_id, 0) if show_id else None
            is_watched = (w_eps or 0) > 0
            tv_shows.append(FilmographyItemOut(
                tmdb_id=tmdb_id, media_type="tv", title=title, year=year,
                poster_url=poster_url, job=job, credit_type=credit_type,
                show_id=show_id, in_library=in_lib, watched=is_watched,
                watched_episodes=w_eps, total_episodes=t_eps,
            ))

    # Sort each list by year desc (None years sort last)
    movies.sort(key=lambda x: x.year or 0, reverse=True)
    tv_shows.sort(key=lambda x: x.year or 0, reverse=True)

    return ActorFilmographyOut(movies=movies, tv_shows=tv_shows)


# ---------------------------------------------------------------------------
# GET /api/actors/{tmdb_person_id}/recently-played
# ---------------------------------------------------------------------------
@router.get(
    "/actors/{tmdb_person_id}/recently-played",
    response_model=list[ActorRecentSessionOut],
)
def get_actor_recent_sessions(
    tmdb_person_id: int,
    db: Session = Depends(get_db),
) -> list[ActorRecentSessionOut]:
    """Watch sessions for movies and episodes featuring the actor.

    Movies: actor appears via item_cast.media_item_id.
    Episodes: actor appears via item_cast.show_id (show-level cast lookup).
    Sessions are ordered by completed_at DESC, limited to 20, deduplicated by session_id.
    """
    results: dict[int, ActorRecentSessionOut] = {}

    # --- Movie sessions ---
    movie_rows = (
        db.query(WatchSession, MediaItem)
        .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
        .join(ItemCast, ItemCast.media_item_id == MediaItem.id)
        .filter(
            ItemCast.person_id == tmdb_person_id,
            MediaItem.media_type == MediaType.movie,
            WatchSession.status == SessionStatus.completed,
        )
        .order_by(WatchSession.completed_at.desc())
        .all()
    )
    for session, item in movie_rows:
        if session.id in results:
            continue
        # Poster
        poster_art = (
            db.query(Artwork)
            .filter(
                Artwork.media_item_id == item.id,
                Artwork.art_type == "poster",
                Artwork.local_path.isnot(None),
            )
            .first()
        )
        results[session.id] = ActorRecentSessionOut(
            session_id=session.id,
            media_type="movie",
            title=item.title,
            completed_at=session.completed_at,
            item_id=item.id,
            poster_url=f"/art/{poster_art.local_path}" if poster_art else None,
        )

    # --- Episode sessions (via show-level item_cast) ---
    episode_rows = (
        db.query(WatchSession, MediaItem, KodiShow)
        .join(MediaItem, WatchSession.media_item_id == MediaItem.id)
        .join(KodiShow, KodiShow.id == MediaItem.show_id)
        .join(ItemCast, ItemCast.show_id == KodiShow.id)
        .filter(
            ItemCast.person_id == tmdb_person_id,
            MediaItem.media_type == MediaType.episode,
            WatchSession.status == SessionStatus.completed,
        )
        .order_by(WatchSession.completed_at.desc())
        .all()
    )
    # Build a poster lookup for shows (avoid per-row queries)
    show_poster_cache: dict[int, str | None] = {}

    def _show_poster(show_id: int) -> str | None:
        if show_id in show_poster_cache:
            return show_poster_cache[show_id]
        art = (
            db.query(Artwork)
            .join(MediaItem, Artwork.media_item_id == MediaItem.id)
            .filter(
                MediaItem.show_id == show_id,
                Artwork.art_type == "tvshow.poster",
                Artwork.local_path.isnot(None),
            )
            .first()
        )
        url = f"/art/{art.local_path}" if art else None
        show_poster_cache[show_id] = url
        return url

    for session, item, show in episode_rows:
        if session.id in results:
            continue
        results[session.id] = ActorRecentSessionOut(
            session_id=session.id,
            media_type="tv",
            title=item.title,
            show_title=show.title,
            season_number=item.season,
            episode_number=item.episode,
            completed_at=session.completed_at,
            item_id=item.id,
            show_id=show.id,
            poster_url=_show_poster(show.id),
        )

    # Sort all by completed_at DESC, limit 20
    sorted_sessions = sorted(
        results.values(),
        key=lambda s: s.completed_at or datetime.min,
        reverse=True,
    )
    return sorted_sessions[:20]


# ---------------------------------------------------------------------------
# GET /api/dashboard/stats
# ---------------------------------------------------------------------------
@router.get("/dashboard/stats", response_model=DashboardStatsOut)
def get_dashboard_stats(db: Session = Depends(get_db)) -> DashboardStatsOut:
    """Lightweight counts for the dashboard page."""
    total_movies = (
        db.query(func.count(MediaItem.id))
        .filter(MediaItem.media_type == MediaType.movie, MediaItem.kodi_state != KodiState.removed)
        .scalar() or 0
    )
    total_episodes = (
        db.query(func.count(MediaItem.id))
        .filter(MediaItem.media_type == MediaType.episode, MediaItem.kodi_state != KodiState.removed)
        .scalar() or 0
    )
    total_shows = (
        db.query(func.count(KodiShow.id)).scalar() or 0
    )

    type_counts = (
        db.query(MediaItem.media_type, func.count(WatchSession.id).label("cnt"))
        .join(WatchSession, WatchSession.media_item_id == MediaItem.id)
        .filter(WatchSession.status == SessionStatus.completed)
        .group_by(MediaItem.media_type)
        .all()
    )
    type_map = {r.media_type: r.cnt for r in type_counts}

    return DashboardStatsOut(
        total_movies=total_movies,
        total_shows=total_shows,
        total_episodes=total_episodes,
        movie_plays=type_map.get("movie", 0),
        episode_plays=type_map.get("episode", 0),
    )


# ---------------------------------------------------------------------------
# Configuration API helpers
# ---------------------------------------------------------------------------

def _upsert_config_keys(db: Session, updates: dict[str, Any]) -> None:
    """Write a dict of config key→value to AppSetting (insert or update)."""
    from datetime import datetime as _dt
    for key, value in updates.items():
        setting = db.get(AppSetting, key)
        if setting:
            setting.value = str(value)
            setting.updated_at = _dt.utcnow()
        else:
            db.add(AppSetting(key=key, value=str(value)))
    db.commit()


async def _reload_effective_settings(request: Request) -> None:
    """Reload effective settings (env + DB) and store on app.state."""
    from logsaga.config import get_settings as _get_settings
    from logsaga.main import _load_effective_settings
    base = _get_settings()
    new_cfg = _load_effective_settings(base, request.app.state.engine)
    request.app.state.settings = new_cfg


# ---------------------------------------------------------------------------
# GET /api/admin/config
# ---------------------------------------------------------------------------
@router.get("/admin/config", response_model=ConfigAllOut)
def get_admin_config(cfg: Settings = Depends(get_cfg)) -> ConfigAllOut:
    """Return all configuration sections. Credentials are never returned — only is_set booleans."""
    return ConfigAllOut(
        kodi=ConfigKodiOut(
            host=cfg.kodi_db_host,
            port=cfg.kodi_db_port,
            user=cfg.kodi_db_user,
            password_is_set=bool(cfg.kodi_db_password),
            db_name=cfg.kodi_db_name,
        ),
        jsonrpc=ConfigJSONRPCOut(
            url=cfg.kodi_jsonrpc_url,
            user=cfg.kodi_jsonrpc_user,
            password_is_set=bool(cfg.kodi_jsonrpc_password),
        ),
        tmdb=ConfigTMDBOut(api_key_is_set=bool(cfg.tmdb_api_key)),
        mqtt=ConfigMQTTOut(
            host=cfg.mqtt_host,
            port=cfg.mqtt_port,
            user=cfg.mqtt_user,
            password_is_set=bool(cfg.mqtt_password),
            discovery_prefix=cfg.mqtt_discovery_prefix,
        ),
        app=ConfigAppOut(
            public_base_url=cfg.public_base_url,
            log_level=cfg.log_level,
            tz=cfg.tz,
        ),
    )


# ---------------------------------------------------------------------------
# PUT /api/admin/config/kodi
# ---------------------------------------------------------------------------
@router.put("/admin/config/kodi", response_model=ConnectionTestOut)
async def update_config_kodi(
    body: ConfigKodiIn,
    request: Request,
    db: Session = Depends(get_db),
) -> ConnectionTestOut:
    """Save Kodi DB settings, reset the connection, and run a connection test."""
    updates: dict[str, Any] = {
        "kodi_db_host": body.host,
        "kodi_db_port": body.port,
        "kodi_db_user": body.user,
        "kodi_db_name": body.db_name,
    }
    if body.password:  # Empty / None = keep existing password
        updates["kodi_db_password"] = body.password
    _upsert_config_keys(db, updates)
    await _reload_effective_settings(request)

    # Reset connections so they reconnect with new credentials on next poll
    for state in (request.app.state.poll_kodi_state, request.app.state.library_kodi_state):
        state.conn = None
        state.db_name = None

    cfg = request.app.state.settings
    if not cfg.kodi_db_host:
        return ConnectionTestOut(status="not_configured", message="Host not configured.")

    from logsaga.kodi.db import KodiDBError, KodiDBSecurityError, connect
    try:
        conn = await asyncio.to_thread(connect, cfg)
        conn.close()
        return ConnectionTestOut(status="ok", message=f"Connected to {cfg.kodi_db_host}.")
    except (KodiDBError, KodiDBSecurityError) as exc:
        return ConnectionTestOut(status="error", message=str(exc))
    except Exception as exc:
        return ConnectionTestOut(status="error", message=f"Connection failed: {exc}")


# ---------------------------------------------------------------------------
# PUT /api/admin/config/jsonrpc
# ---------------------------------------------------------------------------
@router.put("/admin/config/jsonrpc", response_model=ConnectionTestOut)
async def update_config_jsonrpc(
    body: ConfigJSONRPCIn,
    request: Request,
    db: Session = Depends(get_db),
) -> ConnectionTestOut:
    """Save Kodi JSON-RPC settings and test reachability."""
    updates: dict[str, Any] = {
        "kodi_jsonrpc_url": body.url,
        "kodi_jsonrpc_user": body.user,
    }
    if body.password:
        updates["kodi_jsonrpc_password"] = body.password
    _upsert_config_keys(db, updates)
    await _reload_effective_settings(request)

    cfg = request.app.state.settings
    if not cfg.kodi_jsonrpc_url:
        return ConnectionTestOut(status="not_configured", message="URL not configured.")

    from logsaga.kodi.jsonrpc import KodiJSONRPC, KodiJSONRPCError
    try:
        def _ping() -> None:
            rpc = KodiJSONRPC(cfg)
            rpc.call("JSONRPC.Ping")
        await asyncio.to_thread(_ping)
        return ConnectionTestOut(status="ok", message=f"Reachable at {cfg.kodi_jsonrpc_url}.")
    except KodiJSONRPCError as exc:
        return ConnectionTestOut(status="error", message=str(exc))
    except Exception as exc:
        return ConnectionTestOut(status="error", message=f"Connection failed: {exc}")


# ---------------------------------------------------------------------------
# PUT /api/admin/config/tmdb
# ---------------------------------------------------------------------------
@router.put("/admin/config/tmdb", response_model=ConnectionTestOut)
async def update_config_tmdb(
    body: ConfigTMDBIn,
    request: Request,
    db: Session = Depends(get_db),
) -> ConnectionTestOut:
    """Save TMDB API key and validate it with a minimal API call."""
    if body.api_key:  # Non-empty = replace
        _upsert_config_keys(db, {"tmdb_api_key": body.api_key})
    elif body.api_key == "":  # Explicit empty string = clear the key
        _upsert_config_keys(db, {"tmdb_api_key": ""})
    # None = leave unchanged
    await _reload_effective_settings(request)

    cfg = request.app.state.settings
    if not cfg.tmdb_api_key:
        return ConnectionTestOut(status="not_configured", message="API key not configured.")

    # Validate the key with a minimal TMDB authentication call
    import httpx as _httpx
    api_key_str = cfg.tmdb_api_key.get_secret_value()
    try:
        if api_key_str.startswith("eyJ"):
            headers = {"Authorization": f"Bearer {api_key_str}", "Accept": "application/json"}
            params: dict[str, str] = {}
        else:
            headers = {"Accept": "application/json"}
            params = {"api_key": api_key_str}
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.themoviedb.org/3/authentication",
                headers=headers,
                params=params,
            )
        if resp.status_code == 200:
            return ConnectionTestOut(status="ok", message="API key is valid.")
        elif resp.status_code == 401:
            return ConnectionTestOut(status="error", message="Invalid API key.")
        else:
            return ConnectionTestOut(status="error", message=f"TMDB returned HTTP {resp.status_code}.")
    except Exception as exc:
        return ConnectionTestOut(status="error", message=f"Request failed: {exc}")


# ---------------------------------------------------------------------------
# PUT /api/admin/config/mqtt
# ---------------------------------------------------------------------------
@router.put("/admin/config/mqtt", response_model=ConnectionTestOut)
async def update_config_mqtt(
    body: ConfigMQTTIn,
    request: Request,
    db: Session = Depends(get_db),
) -> ConnectionTestOut:
    """Save MQTT settings, restart the MQTT publisher task, and test connectivity."""
    updates: dict[str, Any] = {
        "mqtt_host": body.host,
        "mqtt_port": body.port,
        "mqtt_user": body.user,
        "mqtt_discovery_prefix": body.discovery_prefix,
    }
    if body.password:
        updates["mqtt_password"] = body.password
    _upsert_config_keys(db, updates)
    await _reload_effective_settings(request)

    cfg = request.app.state.settings

    # Cancel the existing MQTT task (if any) and start a fresh one with the new config
    existing_task = request.app.state.mqtt_task
    if existing_task and not existing_task.done():
        existing_task.cancel()
        from contextlib import suppress as _suppress
        with _suppress(asyncio.CancelledError):
            await existing_task
    request.app.state.mqtt_task = None

    if cfg.mqtt_enabled:
        from logsaga.mqtt.publisher import run_mqtt_publisher
        new_task = asyncio.create_task(run_mqtt_publisher(cfg, request.app.state.engine))
        request.app.state.mqtt_task = new_task

    if not cfg.mqtt_host:
        return ConnectionTestOut(status="not_configured", message="Host not configured.")

    # Test connectivity — brief connect + immediate disconnect
    import aiomqtt
    pw_str = cfg.mqtt_password.get_secret_value() or None
    async def _do_mqtt_test() -> None:
        async with aiomqtt.Client(
            hostname=cfg.mqtt_host,
            port=cfg.mqtt_port,
            username=cfg.mqtt_user or None,
            password=pw_str,
        ):
            pass

    try:
        await asyncio.wait_for(_do_mqtt_test(), timeout=5.0)
        return ConnectionTestOut(status="ok", message=f"Connected to {cfg.mqtt_host}.")
    except asyncio.TimeoutError:
        return ConnectionTestOut(status="error", message="Connection timed out.")
    except Exception as exc:
        return ConnectionTestOut(status="error", message=f"Connection failed: {exc}")


# ---------------------------------------------------------------------------
# PUT /api/admin/config/app
# ---------------------------------------------------------------------------
@router.put("/admin/config/app", response_model=ConnectionTestOut)
async def update_config_app(
    body: ConfigAppIn,
    request: Request,
    db: Session = Depends(get_db),
) -> ConnectionTestOut:
    """Save application settings (public URL, log level, timezone)."""
    _upsert_config_keys(db, {
        "public_base_url": body.public_base_url,
        "log_level": body.log_level.upper(),
        "tz": body.tz,
    })
    await _reload_effective_settings(request)

    cfg = request.app.state.settings

    # Apply log level immediately without restarting
    import structlog as _structlog
    _structlog.configure(
        wrapper_class=_structlog.make_filtering_bound_logger(
            logging.getLevelName(cfg.log_level)
        ),
    )

    return ConnectionTestOut(status="ok", message="Settings saved.")


# ---------------------------------------------------------------------------
# Backup / Restore / Verify
# ---------------------------------------------------------------------------
from fastapi import UploadFile, File as _File, BackgroundTasks  # noqa: E402
from fastapi.responses import FileResponse as _FileResponse      # noqa: E402
import os as _os                                                  # noqa: E402
import tempfile as _tempfile                                      # noqa: E402
from pathlib import Path as _Path                                 # noqa: E402
from sqlalchemy import create_engine as _create_engine           # noqa: E402


def _resolve_backup_zip(
    filename: Optional[str],
    upload: Optional[UploadFile],
    cfg: Settings,
) -> _Path:
    """
    Return a Path to the backup ZIP to operate on.

    If `upload` is provided, writes it to a temp file and returns that path.
    If `filename` is provided, resolves it against the backup directory.
    Raises HTTPException 400 if neither is provided or the file is not found.
    """
    from logsaga.backup.manager import backup_dir as _backup_dir

    if upload is not None:
        # Write the uploaded file to a temp location
        import shutil as _shutil
        fd, tmp_path = _tempfile.mkstemp(suffix=".zip", prefix="logsaga-upload-")
        try:
            with _os.fdopen(fd, "wb") as f:
                _shutil.copyfileobj(upload.file, f)
        except Exception:
            _os.unlink(tmp_path)
            raise
        return _Path(tmp_path)

    if filename:
        safe_name = _Path(filename).name  # strip any path components
        zip_path = _Path(_backup_dir(cfg.logsaga_db_path)) / safe_name
        if not zip_path.exists():
            raise HTTPException(status_code=404, detail=f"Backup not found: {safe_name}")
        return zip_path

    raise HTTPException(status_code=400, detail="Provide either 'file' upload or 'filename' query param")


@router.get("/admin/backup", response_model=BackupListOut)
def list_backups_route(
    cfg: Settings = Depends(get_cfg),
) -> BackupListOut:
    """List all backup ZIPs stored on the server, newest first."""
    from logsaga.backup.manager import list_backups as _list_backups, backup_dir as _backup_dir

    backups = _list_backups(cfg.logsaga_db_path)
    bdir = str(_backup_dir(cfg.logsaga_db_path))
    return BackupListOut(
        backup_dir=bdir,
        backups=[
            BackupInfoOut(
                filename=b.filename,
                size_bytes=b.size_bytes,
                created_at=b.created_at,
                sessions=b.sessions,
                media_items=b.media_items,
                shows=b.shows,
                schema_version=b.schema_version,
            )
            for b in backups
        ],
    )


@router.post("/admin/backup", response_model=BackupInfoOut)
def create_backup_route(
    cfg: Settings = Depends(get_cfg),
) -> BackupInfoOut:
    """Create a new backup of the current database and store it on the server."""
    from logsaga.backup.manager import create_backup as _create_backup

    try:
        info = _create_backup(cfg.logsaga_db_path, app_version=cfg.build_version)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")

    return BackupInfoOut(
        filename=info.filename,
        size_bytes=info.size_bytes,
        created_at=info.created_at,
        sessions=info.sessions,
        media_items=info.media_items,
        shows=info.shows,
        schema_version=info.schema_version,
    )


@router.get("/admin/backup/{filename}/download")
def download_backup_route(
    filename: str,
    background_tasks: BackgroundTasks,
    cfg: Settings = Depends(get_cfg),
) -> _FileResponse:
    """
    Download a backup ZIP with credential keys stripped from the settings table.
    The stripped copy is a temporary file deleted after the response is sent.
    """
    from logsaga.backup.manager import backup_dir as _backup_dir, make_stripped_copy

    safe_name = _Path(filename).name
    zip_path = _Path(_backup_dir(cfg.logsaga_db_path)) / safe_name
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup not found: {safe_name}")

    try:
        tmp_path = make_stripped_copy(zip_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to prepare download: {exc}")

    # Clean up temp file after the response is fully sent
    background_tasks.add_task(_os.unlink, str(tmp_path))

    return _FileResponse(
        path=str(tmp_path),
        filename=safe_name,
        media_type="application/zip",
    )


@router.post("/admin/backup/verify", response_model=VerifyResultOut)
async def verify_backup_route(
    file: Optional[UploadFile] = _File(None),
    filename: Optional[str] = Query(None),
    cfg: Settings = Depends(get_cfg),
) -> VerifyResultOut:
    """
    Verify a backup ZIP without modifying anything.

    Accepts either a multipart file upload or a ?filename= param pointing to
    a server-side backup.
    """
    from logsaga.backup.manager import verify_backup as _verify_backup

    is_upload = file is not None
    zip_path = _resolve_backup_zip(filename, file, cfg)

    try:
        # Get current schema version from live DB
        import sqlite3 as _sqlite3
        try:
            with _sqlite3.connect(cfg.logsaga_db_path) as conn:
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                current_schema = row[0] if row else ""
        except Exception:
            current_schema = ""

        result = _verify_backup(zip_path, current_schema)
    finally:
        if is_upload:
            zip_path.unlink(missing_ok=True)

    return VerifyResultOut(
        ok=result.ok,
        error=result.error,
        schema_version=result.schema_version,
        app_compatible=result.app_compatible,
        sessions=result.sessions,
        media_items=result.media_items,
        shows=result.shows,
        created_at=result.created_at,
        credentials_stripped=result.credentials_stripped,
    )


@router.post("/admin/backup/restore", response_model=RestoreResultOut)
async def restore_backup_route(
    request: Request,
    file: Optional[UploadFile] = _File(None),
    filename: Optional[str] = Query(None),
    cfg: Settings = Depends(get_cfg),
) -> RestoreResultOut:
    """
    Restore the database from a backup ZIP.

    Full overwrite — all current data is replaced. The SQLAlchemy engine is
    swapped atomically so in-flight requests complete cleanly before the new
    DB takes effect.

    Accepts either a multipart file upload or a ?filename= param.
    """
    from logsaga.backup.manager import verify_backup as _verify_backup, restore_from_zip

    is_upload = file is not None
    zip_path = _resolve_backup_zip(filename, file, cfg)

    try:
        # Validate before we touch anything
        import sqlite3 as _sqlite3
        try:
            with _sqlite3.connect(cfg.logsaga_db_path) as conn:
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                current_schema = row[0] if row else ""
        except Exception:
            current_schema = ""

        verify = _verify_backup(zip_path, current_schema)
        if not verify.ok:
            return RestoreResultOut(success=False, message=f"Backup failed verification: {verify.error}")
        if not verify.app_compatible:
            return RestoreResultOut(
                success=False,
                message=(
                    f"Schema mismatch: backup has schema {verify.schema_version!r}, "
                    f"app is on {current_schema!r}. Cannot restore."
                ),
            )

        # Pause background loops
        request.app.state.restore_in_progress = True
        try:
            # Give in-flight loop iterations a moment to yield
            await asyncio.sleep(0.5)

            # Dispose engine (closes idle connections; active ones finish naturally)
            old_engine = request.app.state.engine
            old_engine.dispose()

            # Atomic file swap
            restore_from_zip(zip_path, cfg.logsaga_db_path)

            # Recreate engine on the new file
            from sqlalchemy import create_engine as _ce
            new_engine = _ce(
                f"sqlite:///{cfg.logsaga_db_path}",
                connect_args={"check_same_thread": False},
                pool_pre_ping=True,
            )
            request.app.state.engine = new_engine

            # Reload settings from restored DB (may have different config)
            from logsaga.main import _load_effective_settings
            new_cfg = _load_effective_settings(cfg, new_engine)
            request.app.state.settings = new_cfg

        finally:
            request.app.state.restore_in_progress = False

    except HTTPException:
        raise
    except Exception as exc:
        return RestoreResultOut(success=False, message=f"Restore failed: {exc}")
    finally:
        if is_upload:
            zip_path.unlink(missing_ok=True)

    return RestoreResultOut(
        success=True,
        message="Restore complete. The database has been replaced with the backup.",
    )


@router.delete("/admin/backup/{filename}", status_code=204)
def delete_backup_route(
    filename: str,
    cfg: Settings = Depends(get_cfg),
) -> None:
    """Delete a backup ZIP from the server."""
    from logsaga.backup.manager import backup_dir as _backup_dir

    safe_name = _Path(filename).name
    zip_path = _Path(_backup_dir(cfg.logsaga_db_path)) / safe_name
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup not found: {safe_name}")
    try:
        zip_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete backup: {exc}")
