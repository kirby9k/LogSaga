# Backend Reference

Updated: 2026-11-20 · Covers all phases through Phase 8+ (deployed).

---

## Project layout

```
backend/
  pyproject.toml          dependencies + console script entry points
  uv.lock                 locked dependency tree (committed — required for reproducible builds)
  alembic.ini             Alembic config (sqlalchemy.url unused — env.py reads env vars)
  logsaga/
    config.py             pydantic-settings — all env vars with defaults
    models.py             SQLAlchemy 2.x declarative models (12 tables); PRAGMA busy_timeout=30000
    main.py               FastAPI app, lifespan, poll loop, library sync loop, MQTT publisher
    cli.py                Typer CLI: doctor, migrate, backfill, backfill-prior-plays,
                          sync, sync-library, enrich-metadata, rebuild-sessions,
                          fetch-artwork, migrate-artwork-paths, enrich-cast,
                          enrich-episode-cast, enrich-tmdb-ids  (13 commands)
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
      routes.py           FastAPI route handlers (40+ endpoints)
      schemas.py          Pydantic response models
      deps.py             FastAPI dependency injection (db session, settings)
    mqtt/
      publisher.py        Home Assistant MQTT discovery publisher (8 entities)
  migrations/
    env.py                Alembic env — reads LOGSAGA_DB_PATH or LOGSAGA_DB
    versions/             Migration scripts (0001–0009)
  tests/                  305 tests across 9 modules
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
| completion_reason | ENUM | `position`, `kodi_playcount`, `kodi_marked`, `manual` |
| source | ENUM | `poll` (live), `backfill` (imported), `backfill_prior` (pre-history placeholder), `manual` (added via POST /api/watched) |
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

**Automatic artwork triggers:**
1. **Poll creates a new item** → `_fetch_artwork_for_items_bg` fires immediately for that item.
2. **Library sync completes** → `_fill_missing_artwork_bg` fires unconditionally (not just when
   new items were created). It queries for all `MediaItem` rows that have `kodi_media_id` but
   no `Artwork` row with a `local_path`, then fetches art for each. If every item already has
   artwork the function exits after one SQLite query without opening a Kodi connection.
3. **Item played** (Stage E) → `_enrich_played_items_bg` checks for missing artwork/cast on the
   played item and re-runs `_enrich_new_items_bg` if gaps exist.
4. **Manual** → `logsaga fetch-artwork` or `POST /api/admin/fetch-artwork` (force=True, bypasses
   retry backoff).

**Why `_fill_missing_artwork_bg` runs unconditionally:** The fast-sync path (triggered immediately
when Kodi's file count changes) fires within ~30 seconds of a new item being added — before Kodi
has finished writing art table rows for the new episode or show. The first run finds no art and
creates no rows. Without unconditional re-checking, those items would sit in a permanent dead zone:
they already exist in `media_item` so no future sync sets `has_new = True`, and they haven't been
played so Stage E never fires.

### sync_state
Single row (id = 1). Health record updated after every poll cycle.

### settings (AppSetting)
Key/value store. Snapshotted at `rebuild-sessions` time so rebuilds are reproducible.

### tmdb_cache
30-day response cache for TMDB API calls. Avoids redundant lookups during search, manual
history, and cast population. Added in migration 0005.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| cache_key | TEXT UNIQUE | `{endpoint}:{id}` or `search:{type}:{year}:{title}` |
| response_json | TEXT | Full TMDB API response body |
| fetched_at | DATETIME UTC | Used to compute TTL (30 days) |

### recently_viewed
Capped at 50 rows. Records TMDB pages visited from the global search / TMDB browse. Added
in migration 0005.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| tmdb_id | INTEGER | |
| media_type | TEXT | `movie` or `tv` |
| title | TEXT | |
| poster_path | TEXT | TMDB relative path |
| viewed_at | DATETIME UTC | Used for ordering; newest kept on overflow |

### person
TMDB person records. Populated lazily on first item/show detail view or by `enrich-cast`.
Added in migration 0006.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | = TMDB person ID |
| name | TEXT | |
| profile_path | TEXT | TMDB relative path |
| biography | TEXT | Fetched lazily on actor detail page |
| birthday / deathday | TEXT | |
| place_of_birth | TEXT | |
| imdb_id / tvdb_id | TEXT | Added in migration 0008 |
| bio_fetched_at | DATETIME UTC | NULL until biography is fetched |
| cast_refreshed_at | DATETIME UTC | Last time cast was refreshed from TMDB |
| cast_refresh_attempted_at | DATETIME UTC | For retry backoff (migration 0007) |

### item_cast
Junction table linking media items to cast members. Added in migration 0006.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| media_item_id | INTEGER FK | → media_item.id |
| show_id | INTEGER FK | → kodi_show.id (for show-level cast) |
| person_id | INTEGER FK | → person.id |
| character | TEXT | Character name |
| order | INTEGER | Billing order |

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
Full interactive spec available at `/api/docs` (Swagger UI) and `/api/redoc`.

**Core / health**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Sync status, DB name, counters, active rule settings |
| GET | `/api/stats` | Aggregated stats. Param: `days` (1–365, default 30) |
| GET | `/api/stats/dashboard` | Dashboard summary stats (total items, sessions, hours) |
| GET | `/api/jobs` | Background job state: running flag, last run, last result per job |

**History**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/recent` | Recent sessions, newest first. Params: `limit`, `offset`, `type`, `status` |
| GET | `/api/sessions` | Full history. Same params plus `search`, `date_from`, `date_to` |
| DELETE | `/api/sessions/{id}` | Hard-delete a session (204) |
| GET | `/api/in-progress` | Items with active Kodi resume points |
| POST | `/api/watched` | Manually add a watch session. Body: `PostWatchedIn`. Creates `MediaItem` stub for TMDB-only items. |

**Library**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/movies` | Movie library grid. Params: `filter` (history/all/unwatched), `sort`, `search`, `limit`, `offset` |
| GET | `/api/shows` | TV show grid. Params: `filter`, `sort`, `search` |
| GET | `/api/shows/{show_id}` | Show detail: metadata, all seasons, all episodes, watched flags |
| GET | `/api/shows/{show_id}/seasons/{season}` | Season detail: episode list with watched flags |
| GET | `/api/recently-added` | Items by `date_added` DESC. Params: `type`, `limit`, `offset` |
| GET | `/api/items/{id}` | Full item detail: metadata, all sessions, all observations, all artwork |
| GET | `/api/items/{id}/artwork` | Artwork rows for one item |
| POST | `/api/items/{id}/recover-artwork` | Force re-fetch artwork for one item (rate-limited 10 min) |
| GET | `/api/search` | Search local library + TMDB. Params: `q`, `type`, `limit` |
| GET | `/api/recently-viewed` | TMDB browse history (capped at 50). Param: `limit` |

**Actors / cast**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/actors/{tmdb_person_id}` | Actor detail: bio, watched history, library grid |
| GET | `/api/actors/{tmdb_person_id}/filmography` | Full filmography with watch status |
| GET | `/api/actors/{tmdb_person_id}/recent-sessions` | 5 most-recent sessions for items featuring this actor |
| GET | `/api/actors/{tmdb_person_id}/recommendations` | Actor-based recommendations |

**TMDB proxies** (cached; use `tmdb_cache`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tmdb/movie/{tmdb_id}` | TMDB movie detail + local watch status |
| GET | `/api/tmdb/tv/{tmdb_id}` | TMDB TV show detail + local watch status |
| GET | `/api/tmdb/tv/{tmdb_id}/season/{season}` | TMDB season detail + local watch status per episode |

**Admin / maintenance**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/artwork-status` | Artwork progress: total_items, items_with_artwork, pending |
| POST | `/api/admin/fetch-artwork` | Trigger artwork fetch (fire-and-forget) |
| POST | `/api/admin/recover-missing-artwork` | Re-fetch artwork for all items missing local files |
| GET | `/api/admin/cast-status` | Cast coverage: items with/without cast entries |
| GET | `/api/admin/settings` | Current session rule values from AppSetting |
| PUT | `/api/admin/settings` | Update session rule values |
| POST | `/api/admin/rebuild-sessions` | Rebuild all sessions from observations. Runs in thread pool. |

**Configuration** (live-editable; persisted to env/`.env`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/config` | All config sections |
| GET/PUT | `/api/config/kodi` | Kodi DB connection settings |
| GET/PUT | `/api/config/jsonrpc` | Kodi JSON-RPC settings |
| GET/PUT | `/api/config/tmdb` | TMDB API key |
| GET/PUT | `/api/config/mqtt` | MQTT broker settings |
| GET/PUT | `/api/config/app` | App settings (poll interval, artwork dir, etc.) |
| POST | `/api/config/test-connection` | Test Kodi DB + JSON-RPC connectivity |

**Backups**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/backups` | List backups |
| POST | `/api/admin/backups` | Create a backup |
| POST | `/api/admin/backups/{name}/verify` | Verify backup integrity |
| POST | `/api/admin/backups/{name}/restore` | Restore from backup |
| DELETE | `/api/admin/backups/{name}` | Delete a backup |

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
| `CAST_SYNC_HOURS` | `24.0` | Periodic cast sync interval |
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

305 tests in `backend/tests/`. Run with `uv run pytest`.
Integration tests (require a live MariaDB container via testcontainers) are in `tests/integration/`.

| Module | Coverage |
|--------|----------|
| `test_protection.py` | All 4 code-level Kodi DB protection layers; grant check, read-only session, SQL guard, static analysis scan |
| `test_rules.py` | All session rule scenarios: crash/resume, stop at 50%, watch to end, mark watched, rewatch, percent fix, etc. |
| `test_backfill.py` | DB-level builder: rebuild_for_item, rebuild_all, build_incremental; non-poll session preservation |
| `test_poller.py` | Poller diff logic; all 7 observation kinds; history preservation regression tests |
| `test_artwork.py` | Artwork fetch, skip-if-exists, thumbnail creation, art type allowlist, show path dedup |
| `test_api.py` | Core API endpoints, health status logic, rebuild endpoint, filter params, session delete |
| `test_manual.py` | POST /api/watched: MediaItem creation, TMDB-only stubs, dedup, artwork trigger |
| `test_cast.py` | Cast population, INSERT OR IGNORE dedup, show detail last_session_at, actor detail |
| `test_actor_detail.py` | GET /api/actors/{id}: filmography grouping, watched/library grids |
