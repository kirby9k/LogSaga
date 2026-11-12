# Backend Reference

Updated: 2026-11-12 · Covers all phases through Phase 6 (deployed).

---

## Project layout

```
backend/
  pyproject.toml          dependencies + console script entry points
  uv.lock                 locked dependency tree (committed — required for reproducible builds)
  alembic.ini             Alembic config (sqlalchemy.url unused — env.py reads env vars)
  logsaga/
    config.py             pydantic-settings — all env vars with defaults
    models.py             SQLAlchemy 2.x declarative models (7 tables); PRAGMA busy_timeout=30000
    main.py               FastAPI app, lifespan, poll loop, library sync loop, MQTT publisher
    cli.py                Typer CLI: doctor, migrate, backfill, backfill-prior-plays,
                          sync, sync-library, enrich-metadata, rebuild-sessions,
                          fetch-artwork, migrate-artwork-paths  (10 commands)
    kodi/
      db.py               Read-only MariaDB access — all 4 code-level protection layers
      jsonrpc.py          Kodi JSON-RPC client (artwork only, allowlisted methods)
    observe/
      poller.py           poll_once() — diffs Kodi state, appends kodi_observation rows;
                          auto-fetches artwork for new items; sets show_id on new episodes
    sessions/
      rules.py            Pure functions: observations → session state machine (no I/O)
      builder.py          DB layer: load observations, call rules, persist sessions
    artwork/
      cache.py            Fetch and cache artwork files; async/parallel; per-item commits;
                          canonical show paths (shows/<title_safe>/); filesystem dedup
    library/
      sync.py             Full library sync → kodi_show + media_item (watched + unwatched)
    api/
      routes.py           FastAPI route handlers (17 endpoints)
      schemas.py          Pydantic response models
      deps.py             FastAPI dependency injection (db session, settings)
    mqtt/
      publisher.py        Home Assistant MQTT discovery publisher (8 entities)
  migrations/
    env.py                Alembic env — reads LOGSAGA_DB_PATH or LOGSAGA_DB
    versions/             Migration scripts (0001–0004)
  tests/                  211+ tests across 7 modules
```

---

## Running locally (Windows)

All commands run from `backend/` with uv and the `.env` at the project root.

```powershell
# Check DB connection and grants
uv run --env-file ../.env logsaga doctor

# Run Alembic migrations (first time setup)
uv run --env-file ../.env logsaga migrate

# Import existing Kodi history (dry run first)
uv run --env-file ../.env logsaga backfill --dry-run
uv run --env-file ../.env logsaga backfill

# Create placeholder sessions for plays before tracking began
uv run --env-file ../.env logsaga backfill-prior-plays

# Sync full library (watched + unwatched items, show metadata)
uv run --env-file ../.env logsaga sync-library

# Populate plot, year, genres, codec data from Kodi
uv run --env-file ../.env logsaga enrich-metadata

# Download artwork (parallel; safe to restart)
uv run --env-file ../.env logsaga fetch-artwork --concurrency 10

# Run one poll cycle manually
uv run --env-file ../.env logsaga sync --once

# Rebuild all sessions from observations (after tuning rules)
uv run --env-file ../.env logsaga rebuild-sessions

# Migrate artwork paths (old-layout → canonical; use --dry-run first)
uv run --env-file ../.env logsaga migrate-artwork-paths

# Start the backend server
uv run --env-file ../.env uvicorn logsaga.main:app --host 0.0.0.0 --port 8000

# Run tests
uv run pytest
```

The `logsaga` console script is defined in `pyproject.toml` as `logsaga = "logsaga.cli:app"`.
Running `python -m logsaga.cli` does **not** work — it imports the module without calling `app()`.

**Important (Windows dev):** Always use forward slashes in `--env-file`:
`../.env` works; `..\.env` is mangled by the shell. Do not use `--reload` with uvicorn;
the background poll task (using `asyncio.to_thread`) prevents clean shutdown on file-change
restarts, leaving orphaned worker processes.

---

## Data models

SQLite in WAL mode. All timestamps UTC. File: `logsaga/models.py`.

### media_item
Stable item identity. One row per unique item observed by the poller or library sync.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| media_type | ENUM | `movie` or `episode` |
| title | TEXT | Episode title or movie title |
| show_title | TEXT | Episodes only |
| season / episode | INTEGER | Episodes only |
| year | INTEGER | Release year |
| release_date | TEXT | Full date string from Kodi |
| plot | TEXT | Scraped plot description |
| runtime_s | INTEGER | Runtime in seconds |
| genres | TEXT | Slash-separated list |
| director | TEXT | Movies only |
| video_codec / audio_codec | TEXT | From streamdetails |
| video_width / video_height | INTEGER | From streamdetails |
| hdr_type | TEXT | From streamdetails |
| audio_channels | INTEGER | From streamdetails |
| imdb_id / tmdb_id / tvdb_id | VARCHAR(20) | Stable external IDs from Kodi's uniqueid table |
| kodi_file_id | INTEGER | Kodi's idFile (unstable across rebuilds — not used for identity) |
| kodi_media_id | INTEGER | Kodi's idMovie or idEpisode |
| kodi_state | ENUM | `watched`, `unwatched`, `in_progress`, `removed` |
| date_added | TEXT | From Kodi; sentinel `1951-09-25` when NULL |
| show_id | INTEGER FK | → kodi_show.id; set on new episodes immediately by poller |
| metadata_hash | VARCHAR(64) | Hash of scraped fields for change detection |
| created_at / updated_at | DATETIME UTC | |

### kodi_show
TV show record. Populated by the library sync background task.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | = Kodi's `idShow` |
| title | TEXT | Show title |
| plot | TEXT | |
| year / premiered | TEXT | |
| studio | TEXT | |
| status | TEXT | Continuing / Ended |
| genres | TEXT | Slash-separated |
| tvdb_id / imdb_id / tmdb_id | VARCHAR(20) | |
| kodi_state | ENUM | `watched`, `unwatched`, `in_progress`, `removed` |
| created_at / updated_at | DATETIME UTC | |

### kodi_file_state
Snapshot of last-seen Kodi values per file. Used to diff on each poll.

| Column | Type | Notes |
|--------|------|-------|
| kodi_file_id | INTEGER PK | Kodi's idFile |
| play_count | INTEGER | Last seen playCount |
| last_played | VARCHAR(32) | `YYYY-MM-DD HH:MM:SS` as Kodi wrote it (client local time) |
| resume_pos_s / resume_total_s | FLOAT | Last seen bookmark values |
| updated_at | DATETIME UTC | |

### kodi_observation
Append-only raw change events. Never edited. Sessions are derived from these.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| media_item_id | INTEGER FK | → media_item.id |
| kodi_file_id | INTEGER | For reference |
| kind | ENUM | See observation kinds below |
| observed_at | DATETIME UTC | When the poller saw this change |
| kodi_last_played | VARCHAR(32) | Kodi value at observation time |
| kodi_play_count | INTEGER | |
| resume_pos_s / resume_total_s | FLOAT | |

**Observation kinds:**
- `resume_set` — a resume bookmark appeared for the first time
- `resume_moved` — an existing resume bookmark changed position
- `resume_cleared` — resume bookmark was deleted (item finished or marked unwatched)
- `lastplayed_changed` — `lastPlayed` timestamp changed
- `playcount_up` — `playCount` increased (Kodi considers item watched)
- `playcount_cleared` — `playCount` dropped to 0 (marked unwatched in Kodi)
- `file_removed` — Kodi's file row disappeared (library clean)

### watch_session
Derived from observations. Can be fully rebuilt with `rebuild-sessions`.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| media_item_id | INTEGER FK | |
| started_at / last_activity_at | DATETIME UTC | First and most recent observation |
| ended_at | DATETIME UTC | When session closed (null if open) |
| completed_at | DATETIME UTC | When completion criteria were met |
| max_position_s | FLOAT | Furthest known resume position |
| runtime_s | INTEGER | Best known runtime at session time |
| percent | FLOAT | max_position_s / runtime_s × 100 |
| status | ENUM | `open`, `completed`, `partial`, `ignored` |
| completion_reason | ENUM | `position`, `kodi_playcount`, `kodi_marked` |
| source | ENUM | `poll` (live), `backfill` (imported), `backfill_prior` (pre-history placeholder) |
| device | VARCHAR(100) | Always null — not currently tracked |

`backfill_prior` sessions have `started_at = 1970-01-01` and represent plays that predate
the backfill (Kodi's `playCount > 1` at backfill time). UI displays "Date unknown".

### artwork
Cached artwork per media item.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| media_item_id | INTEGER FK | Unique with art_type |
| art_type | VARCHAR(50) | `poster`, `fanart`, `clearlogo`, `thumb`, `tvshow.poster`, `tvshow.fanart`, `season.poster` |
| source_url | TEXT | Raw URL from Kodi's art table |
| local_path | TEXT | Relative to `ARTWORK_DIR` |
| thumbnail_path | TEXT | WebP thumbnail |
| file_size | INTEGER | Bytes |
| fetched_at | DATETIME UTC | Last successful download |
| fetch_error | TEXT | Last error message (null if succeeded) |

**Art storage layout:**
- Movies: `movie/<item_id>/<type>.<ext>`
- Episode thumbs: `episode/<item_id>/thumb.<ext>`
- Show-level art: `shows/<show_title_safe>/<type>.<ext>` (shared across all episodes of the show)
  - `tvshow.poster` → `shows/<title>/tvshow_poster.<ext>`
  - `tvshow.fanart` → `shows/<title>/tvshow_fanart.<ext>`
  - `season.poster` → `shows/<title>/season_<N>_poster.<ext>`

**Deduplication:** Filesystem-based glob check before downloading show-level art.
If the canonical file exists, the DB row is updated to point there — no re-download.

**Art types fetched (allowlist):** `poster`, `fanart`, `clearlogo` (movies);
`thumb`, `tvshow.poster`, `tvshow.fanart`, `season.poster` (episodes).

**fetch-artwork locking:** Commits after every individual item so the SQLite write lock
is held for milliseconds only. `asyncio.Semaphore` controls concurrency.

### sync_state
Single row (id = 1). Health record updated after every poll cycle.

### settings (AppSetting)
Key/value store. Snapshotted at `rebuild-sessions` time so rebuilds are reproducible.

---

## Session rules

Configured via env vars. Confirmed values as of 2026-11-04 — locked in.

| Rule | Env var | Default | Meaning |
|------|---------|---------|---------|
| Started threshold | `MIN_STARTED_SECONDS` | 120 s | Below this with no completion → `ignored` |
| Session gap | `SESSION_GAP_HOURS` | 6 h | Activity within this window merges into one session |
| Watched % | `WATCHED_PERCENT` | 90% | Position at or beyond this → `completed` |
| Credits window (movie) | `WATCHED_REMAINING_MINUTES_MOVIE` | 8 min | Within this of end → `completed` |
| Credits window (episode) | `WATCHED_REMAINING_MINUTES_EPISODE` | 3 min | Within this of end → `completed` |

**Key behaviours:**
- A session opens when any activity appears (`resume_set/moved`, `lastplayed_changed`, `playcount_up`).
- Activity for the same item within `SESSION_GAP_HOURS` of the last activity merges into the same session. This absorbs crash/resume, stop-then-resume, and Kodi's end-of-file `playcount_up`.
- A session completes when `max_position_s / runtime_s ≥ WATCHED_PERCENT`, OR within the credits window, OR on `playcount_up` during an active session. Completion happens at most once.
- `playcount_up` with no active session and no resume = "played on Device 2" or "mark as watched" — both create a completed session (`completion_reason = kodi_playcount`). `kodi_marked` is reserved for if future tests distinguish Kodi menu marking.
- `playcount_cleared` / `resume_cleared` → recorded as observations, update `kodi_state`, but **never change, reopen, or delete existing sessions**.
- `file_removed` → recorded as observation, sets `kodi_state = removed`. Sessions are kept.
- Sessions are **fully rebuildable** from `kodi_observation`. Run `rebuild-sessions` after tuning any rule.

**Unit test scenarios (required coverage):** crash mid-episode then resume (1 session), stop at 50% (partial), watch to end (completed once), finish then Kodi marks watched (still 1 session), mark watched from menu (completed), mark unwatched (history unchanged), rewatch next day (2 sessions), 20-s resume tool writes (all fold into one session), library clean (history kept), DB name change after Kodi upgrade.

---

## API endpoints

Base path: `/api`. OpenAPI docs at `/api/docs`. All responses are JSON.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Sync status, DB name, counters, active rule settings |
| GET | `/api/recent` | Recent sessions, newest first. Params: `limit`, `offset`, `type`, `status` |
| GET | `/api/sessions` | Full history. Same params plus `search`, `date_from`, `date_to` |
| DELETE | `/api/sessions/{id}` | Hard-delete a session (204) |
| GET | `/api/in-progress` | Items with active Kodi resume points |
| GET | `/api/movies` | Movie library grid. Params: `filter` (history/all/unwatched), `sort`, `search`, `limit`, `offset` |
| GET | `/api/shows` | TV show grid. Params: `filter`, `sort`, `search` |
| GET | `/api/shows/{show_id}` | Show detail: metadata, all seasons, all episodes, watched flags |
| GET | `/api/shows/{show_id}/seasons/{season}` | Season detail: episode list with watched flags |
| GET | `/api/recently-added` | Items by `date_added` DESC. Params: `type`, `limit`, `offset` |
| GET | `/api/items/{id}` | Full item detail: metadata, all sessions, all observations, all artwork |
| GET | `/api/stats` | Aggregated stats. Param: `days` (1–365, default 30) |
| GET | `/api/admin/artwork-status` | Artwork progress: total_items, items_with_artwork, total_artwork_rows, pending |
| POST | `/api/admin/fetch-artwork` | Trigger artwork fetch (fire-and-forget) |
| GET | `/api/admin/settings` | Current session rule values from AppSetting |
| PUT | `/api/admin/settings` | Update session rule values |
| POST | `/api/admin/rebuild-sessions` | Rebuild all sessions from observations. Runs in thread pool. |

Artwork is served at `/art/<local_path>` (FastAPI `StaticFiles` mounted on `ARTWORK_DIR`).
The SPA is served by a catch-all route returning `index.html` for all non-API, non-asset paths.

---

## Kodi DB protection layers

All layers must stay in place. See `kodi/db.py`. Adding or removing any layer requires explicit approval.

1. **MariaDB grants** — `logsaga` account has only `SELECT ON MyVideos%.*`
2. **Grant verification** (`_verify_grants`) — checks `SHOW GRANTS FOR CURRENT_USER()` on every connect. Rejects: dangerous users (`kodi`, `root`), any grant on `*.*`, any write keyword. No override flag.
3. **Read-only session** (`_set_read_only`) — `SET SESSION TRANSACTION READ ONLY` + `READ COMMITTED` on every connection
4. **SQL guard** (`_execute`) — wrapper around every `cursor.execute()`. Only allows `SELECT`, `SHOW`, `SET SESSION`. All Kodi SQL is defined as module-level string constants — no f-strings, no string building.

**JSON-RPC allowlist** (`kodi/jsonrpc.py`): `JSONRPC.Ping`, `Files.PrepareDownload`, `*.Get*` only. Everything else raises before any request is sent.

---

## Configuration (env vars)

All vars with defaults are optional. See `logsaga/config.py`.

| Var | Default | Notes |
|-----|---------|-------|
| `KODI_DB_HOST` | *(required)* | MariaDB host |
| `KODI_DB_PORT` | `3306` | |
| `KODI_DB_USER` | `logsaga` | Must be the read-only account |
| `KODI_DB_PASSWORD` | *(required)* | |
| `KODI_DB_NAME` | *(auto-detect)* | Leave blank to auto-detect highest `MyVideos%` |
| `KODI_JSONRPC_URL` | *(required)* | Kodi JSON-RPC endpoint; port 8086 = Kodi-Headless default |
| `KODI_JSONRPC_USER` / `_PASSWORD` | *(blank)* | |
| `POLL_SECONDS` | `30` | Poll interval |
| `LIBRARY_SYNC_HOURS` | `2.0` | Full library sync interval |
| `TZ` | `America/Los_Angeles` | Must match Kodi client timezone (lastPlayed is local time) |
| `LOGSAGA_DB_PATH` | `logsaga.db` | SQLite file path |
| `ARTWORK_DIR` | `/config/artwork` | Where artwork files are stored |
| `ARTWORK_THUMBNAIL_MAX_PX` | `400` | Max dimension for WebP thumbnails (0 = disabled) |
| `PUBLIC_BASE_URL` | `http://localhost:8790` | Used in MQTT artwork URLs — set to your Unraid URL |
| `LOG_LEVEL` | `INFO` | |
| `MQTT_HOST` | *(blank = disabled)* | Leave blank to disable MQTT |
| `MQTT_PORT` | `1883` | |
| `MQTT_USER` / `_PASSWORD` | *(blank)* | |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | |
| `MIN_STARTED_SECONDS` | `120` | Session rule |
| `SESSION_GAP_HOURS` | `6` | Session rule |
| `WATCHED_PERCENT` | `90` | Session rule |
| `WATCHED_REMAINING_MINUTES_MOVIE` | `8` | Session rule |
| `WATCHED_REMAINING_MINUTES_EPISODE` | `3` | Session rule |

**Important:** `main.py` calls `load_dotenv()` at startup so `.env` values are available to
module-level `os.environ.get()` calls (artwork static mount, DB path). Docker containers
require env vars passed via `-e` flags in `docker run` — the `.env` file in `/config` is
not automatically read when the container starts.

---

## Tests

211+ tests in `backend/tests/`. Run with `uv run pytest`.

| Module | Coverage |
|--------|----------|
| `test_kodi_db_protection.py` | All 4 code-level protection layers; grant check, read-only session, SQL guard, static analysis scan |
| `test_session_rules.py` | All session rule scenarios: crash/resume, stop at 50%, watch to end, mark watched, rewatch, etc. |
| `test_sessions_builder.py` | DB-level builder with scripted observation timelines |
| `test_poller.py` | Poller diff logic; all 7 observation kinds; history preservation regression tests |
| `test_artwork_cache.py` | Artwork fetch, skip-if-exists, thumbnail creation, art type allowlist, show path dedup |
| `test_api.py` | All API endpoints, health status logic, rebuild endpoint, filter params |
| `test_library_sync.py` | Library sync, kodi_show upsert, media_item dedup, show state aggregation |
