"""
Read-only access to Kodi's MariaDB database.

PROTECTION LAYERS (non-negotiable — do not remove):
  Layer 2: verify grants on connect — refuse to run as kodi/root or with write grants
  Layer 3: read-only session set on every connection
  Layer 4: SQL guard on every execute — only SELECT / SHOW / SET SESSION allowed

The MariaDB-level enforcement (Layer 1) and test coverage (Layer 5) live outside
this file but are always in force. See CLAUDE.md "Kodi database protection".

All Kodi SQL is defined as module-level string constants. No string building, no
f-strings, no format() in query construction. No ORM models are mapped to Kodi tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pymysql
import pymysql.cursors

from logsaga.config import Settings


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class KodiDBError(Exception):
    """Base class for Kodi DB errors."""


class KodiDBSecurityError(KodiDBError):
    """Raised when a security check fails. App must abort, not catch and continue."""


# ---------------------------------------------------------------------------
# Layer 2: Grant verification
# ---------------------------------------------------------------------------
# Users that must never be used to connect (they have broad write access)
_DANGEROUS_USERS = frozenset({"kodi", "root", "admin"})

# Grant keywords that indicate write access
_DANGEROUS_GRANT_KEYWORDS = frozenset({
    "ALL PRIVILEGES",
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "INDEX",
    "REFERENCES",
    "TRIGGER",
    "GRANT OPTION",
    "SUPER",
    "FILE",
    "EXECUTE",
    "EVENT",
    "LOCK TABLES",
    "RELOAD",
    "SHUTDOWN",
    "PROCESS",
    "REPLICATION",
})

# A grant on *.* means global-level access — never acceptable (except USAGE).
_STAR_STAR_PATTERN = re.compile(r"ON\s+\*\.\*", re.IGNORECASE)

# GRANT USAGE ON *.* is the standard MariaDB "login" marker added to every user.
# It confers no real privileges. We allow it and skip the star-star check for it.
_USAGE_ON_GLOBAL = re.compile(r"GRANT\s+USAGE\s+ON\s+\*\.\*", re.IGNORECASE)


def _verify_grants(conn: pymysql.Connection) -> None:
    """
    Layer 2: Check that the connected account has only SELECT/USAGE grants.

    Raises KodiDBSecurityError if the account has any dangerous privileges,
    has grants on *.*, or is named kodi/root. No override flag exists.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT USER()")
        row = cur.fetchone()
        # row may be a dict (DictCursor) or a tuple (plain Cursor) — handle both
        current_user: str = (next(iter(row.values())) if isinstance(row, dict) else row[0]) if row else ""

    # Check username portion (before the @host)
    username = current_user.split("@")[0].lower().strip("'`\"")
    if username in _DANGEROUS_USERS:
        raise KodiDBSecurityError(
            f"Refusing to run as '{username}'. "
            f"Use the logsaga account (SELECT-only on MyVideos%). "
            f"See docs/setup.md for the CREATE USER / GRANT SQL."
        )

    # Fetch grants
    with conn.cursor() as cur:
        cur.execute("SHOW GRANTS FOR CURRENT_USER()")
        rows = cur.fetchall()

    grant_lines = [
        (next(iter(r.values())) if isinstance(r, dict) else r[0]) for r in rows
    ]

    for line in grant_lines:
        line_upper = line.upper()

        # GRANT USAGE ON *.* is the standard login marker — no real privileges.
        # Still check for dangerous additions (e.g. WITH GRANT OPTION).
        if _USAGE_ON_GLOBAL.search(line):
            for keyword in _DANGEROUS_GRANT_KEYWORDS:
                if keyword in line_upper:
                    raise KodiDBSecurityError(
                        f"Account '{current_user}' has dangerous privilege '{keyword}' "
                        f"on the global grant. Grant line: {line!r}"
                    )
            continue  # pure USAGE ON *.* is acceptable

        # Reject any other grant on *.* (too broad — even SELECT on *.* is not allowed)
        if _STAR_STAR_PATTERN.search(line):
            raise KodiDBSecurityError(
                f"Account '{current_user}' has a grant on *.* which is too broad. "
                f"The logsaga account must only have SELECT on MyVideos%.*. "
                f"Grant line: {line!r}"
            )

        # Reject dangerous keywords on any database
        for keyword in _DANGEROUS_GRANT_KEYWORDS:
            if keyword in line_upper:
                raise KodiDBSecurityError(
                    f"Account '{current_user}' has dangerous privilege '{keyword}'. "
                    f"Only SELECT is permitted. Grant line: {line!r}"
                )


# ---------------------------------------------------------------------------
# Layer 3: Read-only session setup
# ---------------------------------------------------------------------------
def _set_read_only(conn: pymysql.Connection) -> None:
    """Layer 3: Set the connection to READ COMMITTED, READ ONLY."""
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cur.execute("SET SESSION TRANSACTION READ ONLY")


# ---------------------------------------------------------------------------
# Layer 4: SQL guard
# ---------------------------------------------------------------------------
_ALLOWED_VERBS = frozenset({"SELECT", "SHOW", "SET"})


def _execute(cur: pymysql.cursors.DictCursor, sql: str, params=None) -> None:
    """
    Layer 4: Guard wrapper around cursor.execute().

    Only SELECT, SHOW, and SET SESSION statements are permitted.
    All Kodi SQL must be called through this function — never call
    cur.execute() directly in this module.
    """
    verb = sql.strip().split()[0].upper()
    if verb not in _ALLOWED_VERBS:
        raise KodiDBSecurityError(
            f"SQL guard refused statement starting with '{verb}'. "
            f"Only SELECT, SHOW, and SET SESSION are permitted in the Kodi DB layer."
        )
    # Extra check: SET must be SET SESSION (not SET GLOBAL or bare SET @@)
    if verb == "SET":
        second = sql.strip().split()[1].upper() if len(sql.strip().split()) > 1 else ""
        if second != "SESSION":
            raise KodiDBSecurityError(
                f"SQL guard refused SET without SESSION: {sql.strip()!r}. "
                f"Only SET SESSION is permitted."
            )

    if params is not None:
        cur.execute(sql, params)
    else:
        cur.execute(sql)


# ---------------------------------------------------------------------------
# SQL query constants (module-level, no f-strings or string building)
# ---------------------------------------------------------------------------

_SQL_SHOW_DATABASES = "SHOW DATABASES LIKE 'MyVideos%'"

_SQL_CHEAP_CHECK = """
SELECT MAX(f.lastPlayed)                             AS max_played,
       COUNT(CASE WHEN f.playCount > 0 THEN 1 END)  AS played_count,
       COUNT(b.idBookmark)                           AS bookmark_count,
       MAX(b.timeInSeconds)                          AS max_resume
FROM files f
LEFT JOIN bookmark b ON b.idFile = f.idFile AND b.type = 1
"""

_SQL_FETCH_FILE_SNAPSHOT = """
SELECT f.idFile,
       f.playCount,
       f.lastPlayed,
       b.timeInSeconds      AS resume_pos,
       b.totalTimeInSeconds AS resume_total
FROM files f
LEFT JOIN bookmark b ON b.idFile = f.idFile AND b.type = 1
WHERE f.lastPlayed IS NOT NULL
   OR f.lastPlayed != ''
   OR EXISTS (
       SELECT 1 FROM bookmark bx
       WHERE bx.idFile = f.idFile AND bx.type = 1
   )
"""

_SQL_IDENTIFY_MOVIE = """
SELECT 'movie'          AS media_type,
       m.idMovie        AS media_id,
       m.c00            AS title,
       NULL             AS show_title,
       NULL             AS season,
       NULL             AS episode,
       m.c11            AS runtime_s,
       m.premiered      AS release_date,
       m.c01            AS plot,
       m.c14            AS genres,
       m.c15            AS director
FROM movie m
WHERE m.idFile = %s
"""

_SQL_IDENTIFY_EPISODE = """
SELECT 'episode'            AS media_type,
       e.idEpisode          AS media_id,
       e.c00                AS title,
       s.c00                AS show_title,
       CAST(e.c12 AS SIGNED) AS season,
       CAST(e.c13 AS SIGNED) AS episode,
       NULL                 AS runtime_s,
       e.c05                AS release_date,
       e.c01                AS plot,
       s.c08                AS genres,
       NULL                 AS director
FROM episode e
JOIN tvshow s ON s.idShow = e.idShow
WHERE e.idFile = %s
"""

_SQL_GET_UNIQUE_IDS = """
SELECT type, value
FROM uniqueid
WHERE media_id = %s AND media_type = %s
"""

_SQL_GET_STREAM_DURATION = """
SELECT iVideoDuration
FROM streamdetails
WHERE idFile = %s AND iStreamType = 0
ORDER BY iVideoDuration DESC
LIMIT 1
"""

_SQL_GET_STREAMDETAILS_FULL = """
SELECT iStreamType, strVideoCodec, iVideoWidth, iVideoHeight,
       strHdrType, iVideoDuration, strAudioCodec, iAudioChannels
FROM streamdetails
WHERE idFile = %s AND iStreamType IN (0, 1)
ORDER BY iStreamType ASC
LIMIT 4
"""

_SQL_GET_ART = "SELECT type, url FROM art WHERE media_type = %s AND media_id = %s"

_SQL_GET_SHOW_ID_FOR_EPISODE = "SELECT idShow FROM episode WHERE idEpisode = %s"

# ---------------------------------------------------------------------------
# Library sync SQL — full library queries (not filtered to played items)
# ---------------------------------------------------------------------------

_SQL_LIBRARY_MOVIES = """
SELECT m.idMovie       AS media_id,
       m.idFile        AS file_id,
       m.c00           AS title,
       m.c11           AS runtime_s,
       m.premiered     AS release_date,
       m.c01           AS plot,
       m.c14           AS genres,
       m.c15           AS director,
       f.dateAdded     AS date_added,
       f.playCount     AS play_count,
       f.lastPlayed    AS last_played
FROM movie m
JOIN files f ON f.idFile = m.idFile
"""

_SQL_LIBRARY_EPISODES = """
SELECT e.idEpisode          AS media_id,
       e.idFile             AS file_id,
       e.idShow             AS kodi_idShow,
       e.c00                AS title,
       s.c00                AS show_title,
       CAST(e.c12 AS SIGNED) AS season,
       CAST(e.c13 AS SIGNED) AS episode,
       e.c05                AS release_date,
       e.c01                AS plot,
       s.c08                AS genres,
       f.dateAdded          AS date_added,
       f.playCount          AS play_count,
       f.lastPlayed         AS last_played
FROM episode e
JOIN tvshow s ON s.idShow = e.idShow
JOIN files f ON f.idFile = e.idFile
"""

_SQL_LIBRARY_SHOWS = """
SELECT s.idShow     AS kodi_idShow,
       s.c00        AS title,
       s.c01        AS plot,
       s.c05        AS premiered,
       s.c08        AS genres,
       s.c02        AS status,
       s.c14        AS studio
FROM tvshow s
"""
# All columns confirmed via L-05 live DB query (2026-11-06):
# c00=title  c01=plot  c02=status("Ending"/"Continuing")
# c05=premiered(YYYY-MM-DD)  c08=genres("Drama / Western")  c14=studio("Paramount+")

_SQL_GET_SHOW_UNIQUE_IDS = """
SELECT type, value
FROM uniqueid
WHERE media_id = %s AND media_type = 'tvshow'
"""

# Enrich queries — same columns as the IDENTIFY variants but keyed by media_id
_SQL_ENRICH_MOVIE = """
SELECT 'movie'          AS media_type,
       m.idMovie        AS media_id,
       m.c00            AS title,
       NULL             AS show_title,
       NULL             AS season,
       NULL             AS episode,
       m.c11            AS runtime_s,
       m.premiered      AS release_date,
       m.c01            AS plot,
       m.c14            AS genres,
       m.c15            AS director
FROM movie m
WHERE m.idMovie = %s
"""

_SQL_ENRICH_EPISODE = """
SELECT 'episode'            AS media_type,
       e.idEpisode          AS media_id,
       e.c00                AS title,
       s.c00                AS show_title,
       CAST(e.c12 AS SIGNED) AS season,
       CAST(e.c13 AS SIGNED) AS episode,
       NULL                 AS runtime_s,
       e.c05                AS release_date,
       e.c01                AS plot,
       s.c08                AS genres,
       NULL                 AS director
FROM episode e
JOIN tvshow s ON s.idShow = e.idShow
WHERE e.idEpisode = %s
"""


# ---------------------------------------------------------------------------
# Data structures returned to callers
# ---------------------------------------------------------------------------
@dataclass
class CheapCheck:
    max_played: Optional[str]
    played_count: int
    bookmark_count: int
    max_resume: Optional[float]


@dataclass
class FileState:
    file_id: int
    play_count: Optional[int]
    last_played: Optional[str]
    resume_pos_s: Optional[float]
    resume_total_s: Optional[float]


@dataclass
class ArtRow:
    art_type: str   # e.g. 'poster', 'fanart', 'thumb', 'tvshow.poster'
    url: str        # raw URL as stored in Kodi's art table


@dataclass
class MediaIdentity:
    media_type: str           # 'movie' or 'episode'
    media_id: int             # Kodi's idMovie or idEpisode (unstable across rebuilds)
    title: str
    show_title: Optional[str]
    season: Optional[int]
    episode: Optional[int]
    runtime_s: Optional[int]
    release_date: Optional[str]
    imdb_id: Optional[str]
    tmdb_id: Optional[str]
    tvdb_id: Optional[str]
    plot: Optional[str] = None
    genres: Optional[str] = None
    director: Optional[str] = None
    video_codec: Optional[str] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    hdr_type: Optional[str] = None
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None


@dataclass
class LibraryMovie:
    media_id: int
    file_id: int
    title: str
    runtime_s: Optional[int]
    release_date: Optional[str]
    plot: Optional[str]
    genres: Optional[str]
    director: Optional[str]
    date_added: Optional[str]
    play_count: Optional[int]
    last_played: Optional[str]


@dataclass
class LibraryEpisode:
    media_id: int
    file_id: int
    kodi_idShow: int
    title: str
    show_title: str
    season: Optional[int]
    episode: Optional[int]
    release_date: Optional[str]
    plot: Optional[str]
    genres: Optional[str]
    date_added: Optional[str]
    play_count: Optional[int]
    last_played: Optional[str]


@dataclass
class LibraryShow:
    kodi_idShow: int
    title: str
    plot: Optional[str]
    premiered: Optional[str]
    genres: Optional[str]
    status: Optional[str]
    studio: Optional[str]
    tvdb_id: Optional[str]
    imdb_id: Optional[str]
    tmdb_id: Optional[str]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def detect_db_name(cfg: Settings) -> str:
    """
    Find the highest-numbered MyVideos% database (e.g. MyVideos131).

    If KODI_DB_NAME is set in config, use that directly.
    Otherwise connect without specifying a database and query SHOW DATABASES.
    """
    if cfg.kodi_db_name:
        return cfg.kodi_db_name

    conn = pymysql.connect(
        host=cfg.kodi_db_host,
        port=cfg.kodi_db_port,
        user=cfg.kodi_db_user,
        password=cfg.kodi_db_password,
        cursorclass=pymysql.cursors.Cursor,
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL_SHOW_DATABASES)
            dbs = sorted(r[0] for r in cur.fetchall())
        if not dbs:
            raise KodiDBError(
                "No MyVideos% database found. "
                "Check that the logsaga account has GRANT SELECT ON MyVideos%.*."
            )
        return dbs[-1]
    finally:
        conn.close()


def connect(cfg: Settings, db_name: Optional[str] = None) -> pymysql.Connection:
    """
    Open a verified, read-only connection to Kodi's MariaDB.

    Applies Layer 2 (grant check) and Layer 3 (read-only session) on every
    new connection. Raises KodiDBSecurityError if either check fails.
    """
    if db_name is None:
        db_name = detect_db_name(cfg)

    conn = pymysql.connect(
        host=cfg.kodi_db_host,
        port=cfg.kodi_db_port,
        user=cfg.kodi_db_user,
        password=cfg.kodi_db_password,
        database=db_name,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=15,
        write_timeout=15,
        charset="utf8mb4",
    )

    # Layer 2: verify the account has only SELECT grants
    _verify_grants(conn)

    # Layer 3: enforce read-only session
    _set_read_only(conn)

    return conn


def cheap_check(conn: pymysql.Connection) -> CheapCheck:
    """
    Fast single-query check for any change in files/bookmark.

    If the returned values match the previous poll's values, nothing has
    changed and the full snapshot can be skipped.
    """
    with conn.cursor() as cur:
        _execute(cur, _SQL_CHEAP_CHECK)
        row = cur.fetchone()
    return CheapCheck(
        max_played=row["max_played"],
        played_count=int(row["played_count"] or 0),
        bookmark_count=int(row["bookmark_count"] or 0),
        max_resume=row["max_resume"],
    )


def fetch_file_snapshot(conn: pymysql.Connection) -> dict[int, FileState]:
    """
    Fetch all files that have any play/resume activity.

    Returns a dict keyed by idFile.
    """
    with conn.cursor() as cur:
        _execute(cur, _SQL_FETCH_FILE_SNAPSHOT)
        rows = cur.fetchall()

    result: dict[int, FileState] = {}
    for row in rows:
        fid = row["idFile"]
        result[fid] = FileState(
            file_id=fid,
            play_count=row["playCount"],
            last_played=row["lastPlayed"] or None,
            resume_pos_s=row["resume_pos"],
            resume_total_s=row["resume_total"],
        )
    return result


def identify_file(conn: pymysql.Connection, file_id: int) -> Optional[MediaIdentity]:
    """
    Identify what a file is (movie or episode) and return its metadata + stable IDs.

    Returns None if the file is not a movie or episode (extra, trailer, orphan, etc.).
    """
    with conn.cursor() as cur:
        # Try movie first
        _execute(cur, _SQL_IDENTIFY_MOVIE, (file_id,))
        row = cur.fetchone()

        if row is None:
            # Try episode
            _execute(cur, _SQL_IDENTIFY_EPISODE, (file_id,))
            row = cur.fetchone()

        if row is None:
            return None

        media_type = row["media_type"]
        media_id = row["media_id"]
        runtime_s = int(row["runtime_s"]) if row["runtime_s"] else None

        # Fetch video + audio streamdetails (also provides runtime fallback)
        _execute(cur, _SQL_GET_STREAMDETAILS_FULL, (file_id,))
        stream_rows = cur.fetchall()

        video_codec: Optional[str] = None
        video_width: Optional[int] = None
        video_height: Optional[int] = None
        hdr_type: Optional[str] = None
        audio_codec: Optional[str] = None
        audio_channels: Optional[int] = None

        for sr in stream_rows:
            if sr["iStreamType"] == 0:  # video stream
                video_codec = sr["strVideoCodec"] or None
                video_width = sr["iVideoWidth"] or None
                video_height = sr["iVideoHeight"] or None
                hdr_type = sr["strHdrType"] or None
                if runtime_s is None and sr["iVideoDuration"]:
                    runtime_s = int(sr["iVideoDuration"])
            elif sr["iStreamType"] == 1:  # audio stream
                audio_codec = sr["strAudioCodec"] or None
                audio_channels = sr["iAudioChannels"] or None

        # Fetch stable external IDs
        _execute(cur, _SQL_GET_UNIQUE_IDS, (media_id, media_type))
        id_rows = cur.fetchall()

    imdb_id = None
    tmdb_id = None
    tvdb_id = None
    for id_row in id_rows:
        id_type = (id_row["type"] or "").lower()
        value = id_row["value"]
        if id_type == "imdb":
            imdb_id = value
        elif id_type == "tmdb":
            tmdb_id = value
        elif id_type == "tvdb":
            tvdb_id = value

    return MediaIdentity(
        media_type=media_type,
        media_id=media_id,
        title=row["title"] or "",
        show_title=row["show_title"],
        season=row["season"],
        episode=row["episode"],
        runtime_s=runtime_s,
        release_date=row["release_date"],
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        plot=row["plot"] or None,
        genres=row["genres"] or None,
        director=row["director"] or None,
        video_codec=video_codec,
        video_width=video_width,
        video_height=video_height,
        hdr_type=hdr_type,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
    )


def fetch_art_for_item(
    conn: pymysql.Connection,
    media_type: str,
    kodi_media_id: int,
) -> list[ArtRow]:
    """
    Fetch all art URLs for a media item from Kodi's art table.

    For episodes, also fetches the parent tvshow's art (prefixed 'tvshow.').
    Returns an empty list if the item has no art.

    media_type: 'movie' or 'episode' (matches Kodi's art.media_type values).
    kodi_media_id: Kodi's idMovie or idEpisode (from MediaItem.kodi_media_id).
    """
    result: list[ArtRow] = []

    with conn.cursor() as cur:
        _execute(cur, _SQL_GET_ART, (media_type, kodi_media_id))
        for row in cur.fetchall():
            url = row["url"] or ""
            if url:
                result.append(ArtRow(art_type=row["type"], url=url))

        if media_type == "episode":
            # Also fetch art for the parent tvshow
            _execute(cur, _SQL_GET_SHOW_ID_FOR_EPISODE, (kodi_media_id,))
            show_row = cur.fetchone()
            if show_row:
                show_id = show_row["idShow"]
                _execute(cur, _SQL_GET_ART, ("tvshow", show_id))
                for row in cur.fetchall():
                    url = row["url"] or ""
                    if url:
                        # Prefix tvshow art types so they don't collide with
                        # episode-level art in our artwork table
                        result.append(
                            ArtRow(art_type=f"tvshow.{row['type']}", url=url)
                        )

    return result


def enrich_item(
    conn: pymysql.Connection,
    media_type: str,
    kodi_media_id: int,
    kodi_file_id: Optional[int],
) -> Optional[MediaIdentity]:
    """
    Re-fetch all metadata for an existing MediaItem by its Kodi media_id.

    Used by `logsaga enrich-metadata` to backfill fields that were absent
    when the item was first seen. Returns None if the item no longer exists in
    Kodi (e.g. library clean removed it) — the caller must skip gracefully.

    kodi_file_id is optional; if None, streamdetails are not fetched.
    """
    with conn.cursor() as cur:
        if media_type == "movie":
            _execute(cur, _SQL_ENRICH_MOVIE, (kodi_media_id,))
        else:
            _execute(cur, _SQL_ENRICH_EPISODE, (kodi_media_id,))
        row = cur.fetchone()

        if row is None:
            return None

        runtime_s = int(row["runtime_s"]) if row["runtime_s"] else None

        video_codec: Optional[str] = None
        video_width: Optional[int] = None
        video_height: Optional[int] = None
        hdr_type: Optional[str] = None
        audio_codec: Optional[str] = None
        audio_channels: Optional[int] = None

        if kodi_file_id is not None:
            _execute(cur, _SQL_GET_STREAMDETAILS_FULL, (kodi_file_id,))
            for sr in cur.fetchall():
                if sr["iStreamType"] == 0:
                    video_codec = sr["strVideoCodec"] or None
                    video_width = sr["iVideoWidth"] or None
                    video_height = sr["iVideoHeight"] or None
                    hdr_type = sr["strHdrType"] or None
                    if runtime_s is None and sr["iVideoDuration"]:
                        runtime_s = int(sr["iVideoDuration"])
                elif sr["iStreamType"] == 1:
                    audio_codec = sr["strAudioCodec"] or None
                    audio_channels = sr["iAudioChannels"] or None

        _execute(cur, _SQL_GET_UNIQUE_IDS, (kodi_media_id, media_type))
        id_rows = cur.fetchall()

    imdb_id = None
    tmdb_id = None
    tvdb_id = None
    for id_row in id_rows:
        id_type = (id_row["type"] or "").lower()
        value = id_row["value"]
        if id_type == "imdb":
            imdb_id = value
        elif id_type == "tmdb":
            tmdb_id = value
        elif id_type == "tvdb":
            tvdb_id = value

    return MediaIdentity(
        media_type=media_type,
        media_id=kodi_media_id,
        title=row["title"] or "",
        show_title=row["show_title"],
        season=row["season"],
        episode=row["episode"],
        runtime_s=runtime_s,
        release_date=row["release_date"],
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        plot=row["plot"] or None,
        genres=row["genres"] or None,
        director=row["director"] or None,
        video_codec=video_codec,
        video_width=video_width,
        video_height=video_height,
        hdr_type=hdr_type,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
    )


def fetch_library_movies(conn: pymysql.Connection) -> list[LibraryMovie]:
    """
    Fetch all movies from Kodi's library (watched and unwatched).

    Returns one LibraryMovie per movie file. Used by the library sync.
    """
    with conn.cursor() as cur:
        _execute(cur, _SQL_LIBRARY_MOVIES)
        rows = cur.fetchall()

    result = []
    for row in rows:
        runtime_raw = row["runtime_s"]
        runtime_s = None
        if runtime_raw:
            try:
                runtime_s = int(runtime_raw)
            except (ValueError, TypeError):
                pass
        result.append(LibraryMovie(
            media_id=row["media_id"],
            file_id=row["file_id"],
            title=row["title"] or "",
            runtime_s=runtime_s,
            release_date=row["release_date"] or None,
            plot=row["plot"] or None,
            genres=row["genres"] or None,
            director=row["director"] or None,
            date_added=row["date_added"] or None,
            play_count=row["play_count"],
            last_played=row["last_played"] or None,
        ))
    return result


def fetch_library_episodes(conn: pymysql.Connection) -> list[LibraryEpisode]:
    """
    Fetch all episodes from Kodi's library (watched and unwatched).

    Returns one LibraryEpisode per episode file. Used by the library sync.
    """
    with conn.cursor() as cur:
        _execute(cur, _SQL_LIBRARY_EPISODES)
        rows = cur.fetchall()

    result = []
    for row in rows:
        result.append(LibraryEpisode(
            media_id=row["media_id"],
            file_id=row["file_id"],
            kodi_idShow=row["kodi_idShow"],
            title=row["title"] or "",
            show_title=row["show_title"] or "",
            season=row["season"],
            episode=row["episode"],
            release_date=row["release_date"] or None,
            plot=row["plot"] or None,
            genres=row["genres"] or None,
            date_added=row["date_added"] or None,
            play_count=row["play_count"],
            last_played=row["last_played"] or None,
        ))
    return result


def fetch_library_shows(conn: pymysql.Connection) -> list[LibraryShow]:
    """
    Fetch all TV shows from Kodi's library.

    Also fetches unique IDs (tvdb/imdb/tmdb) for each show.
    Returns one LibraryShow per tvshow row. Used by the library sync.
    """
    with conn.cursor() as cur:
        _execute(cur, _SQL_LIBRARY_SHOWS)
        show_rows = cur.fetchall()

        result = []
        for row in show_rows:
            kodi_id = row["kodi_idShow"]

            # Fetch stable external IDs for this show
            _execute(cur, _SQL_GET_SHOW_UNIQUE_IDS, (kodi_id,))
            id_rows = cur.fetchall()

            tvdb_id = None
            imdb_id = None
            tmdb_id = None
            for id_row in id_rows:
                id_type = (id_row["type"] or "").lower()
                value = id_row["value"]
                if id_type == "tvdb":
                    tvdb_id = value
                elif id_type == "imdb":
                    imdb_id = value
                elif id_type == "tmdb":
                    tmdb_id = value

            result.append(LibraryShow(
                kodi_idShow=kodi_id,
                title=row["title"] or "",
                plot=row["plot"] or None,
                premiered=row["premiered"] or None,
                genres=row["genres"] or None,
                status=row["status"] or None,
                studio=row["studio"] or None,
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
            ))

    return result
