# Backend Reference

Generated: 2026-11-05 · Updated: 2026-11-05 session 2 · Covers Phase 1–4 implementation

---

## Project layout

```
backend/
  pyproject.toml          dependencies + console script entry points
  uv.lock                 locked dependency tree
  alembic.ini             Alembic config (sqlalchemy.url unused — env.py reads env vars)
  logsaga/
    config.py             pydantic-settings — all env vars with defaults
    models.py             SQLAlchemy 2.x declarative models (6 tables)
    main.py               FastAPI app, lifespan, poll loop, MQTT publisher
    cli.py                Typer CLI: doctor, migrate, backfill, sync, rebuild-sessions, fetch-artwork
    kodi/
      db.py               Read-only MariaDB access — all 4 protection layers
      jsonrpc.py          Kodi JSON-RPC client (artwork only, allowlisted methods)
    observe/
      poller.py           poll_once() — diffs Kodi state, appends kodi_observation rows
    sessions/
      rules.py            Pure functions: observations → session state machine (no I/O)
      builder.py          DB layer: load observations, call rules, persist sessions
    artwork/
      cache.py            Fetch and cache artwork files; per-item commits; tvshow dedup
    api/
      routes.py           8 FastAPI route handlers
      schemas.py          Pydantic response models
      deps.py             FastAPI dependency injection (db session, settings)
    mqtt/
      publisher.py        Home Assistant MQTT discovery publisher
  migrations/
    env.py                Alembic env — reads LOGSAGA_DB_PATH or LOGSAGA_DB
    versions/             Migration scripts
  tests/                  147 tests across 6 modules
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

# Download artwork for all items (per-item commits; safe to restart; resumes from where it left off)
uv run --env-file ../.env logsaga fetch-artwork

# Run one poll cycle manually
uv run --env-file ../.env logsaga sync --once

# Rebuild all sessions from observations (after tuning rules)
uv run --env-file ../.env logsaga rebuild-sessions

# Start the backend server
uv run --env-file ../.env uvicorn logsaga.main:app --host 0.0.0.0 --port 8000

# Run tests
uv run pytest
```

The `logsaga` console script is defined in `pyproject.toml` as
`logsaga = "logsaga.cli:app"`. Running `python -m logsaga.cli`
does NOT work — it imports the module without calling `app()`.

---

## Data models

SQLite in WAL mode. All timestamps UTC. File: `logsaga/models.py`.

### media_item
Stable item identity. One row per unique file observed by the poller.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| media_type | ENUM | `movie` or `episode` |
| title | TEXT | Episode title or movie title |
| show_title | TEXT | Episodes only |
| season / episode | INTEGER | Episodes only |
| year | INTEGER | Release year |
| plot | TEXT | Scraped plot description |
| runtime_s | INTEGER | Runtime in seconds |
| genres | TEXT | Slash-separated list |
| imdb_id / tmdb_id / tvdb_id | VARCHAR(20) | Stable external IDs from Kodi's uniqueid table |
| kodi_file_id | INTEGER | Kodi's idFile (unstable across rebuilds — not used for identity) |
| kodi_media_id | INTEGER | Kodi's idMovie or idEpisode |
| kodi_state | ENUM | `watched`, `unwatched`, `in_progress`, `removed` |
| metadata_hash | VARCHAR(64) | Hash of scraped fields for change detection |
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
| source | ENUM | `poll` (live) or `backfill` (imported) |
| device | VARCHAR(100) | Always null — not currently tracked |

### artwork
Cached artwork per media item.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| media_item_id | INTEGER FK | Unique with art_type (one row per type per item) |
| art_type | VARCHAR(50) | `poster`, `fanart`, `clearlogo`, `thumb`, `tvshow.poster`, `tvshow.fanart` |
| source_url | TEXT | Raw URL from Kodi's art table |
| local_path | TEXT | Relative to `ARTWORK_DIR`, e.g. `movie/42/poster.jpg` |
| thumbnail_path | TEXT | WebP thumbnail, e.g. `movie/42/poster_thumb.webp` |
| file_size | INTEGER | Bytes |
| fetched_at | DATETIME UTC | Last successful download |
| fetch_error | TEXT | Last error message (null if succeeded) |

**Art types fetched (allowlist in `artwork/cache.py`):**
- Movies: `poster` → `fanart` → `clearlogo` (preference order for display)
- Episodes: `thumb` → `tvshow.poster` → `tvshow.fanart`
- All others (`banner`, `clearart`, `discart`, etc.) are skipped

**tvshow art deduplication:** Before downloading `tvshow.*` art for an episode,
`fetch_artwork_for_item` queries for another episode of the same show that already has
it. If found, the existing `local_path` is reused — no download, no duplicate file.

**fetch-artwork locking:** Commits after every individual item so the SQLite write lock
is held for milliseconds only. Downloads happen between commits. This allows the backend
poll loop to write `sync_state` without contention.

### sync_state
Single row (id = 1). Health record updated after every poll cycle.

### settings (AppSetting)
Key/value store. Snapshotted at `rebuild-sessions` time so rebuilds are reproducible.

---

## Session rules

Configured via env vars. Confirmed values as of 2026-11-04:

| Rule | Env var | Value | Meaning |
|------|---------|-------|---------|
| Started threshold | `MIN_STARTED_SECONDS` | 120 s | Below this with no completion → `ignored` |
| Session gap | `SESSION_GAP_HOURS` | 6 h | Activity within this window merges into one session |
| Watched % | `WATCHED_PERCENT` | 90% | Position at or beyond this → `completed` |
| Credits window (movie) | `WATCHED_REMAINING_MINUTES_MOVIE` | 8 min | Within this of end → `completed` |
| Credits window (episode) | `WATCHED_REMAINING_MINUTES_EPISODE` | 3 min | Within this of end → `completed` |

**Key behaviours:**
- A `playcount_up` during an active session (with resume cleared) always completes it (`kodi_playcount`)
- A `playcount_up` with no active session and no resume = "Mark as watched" → creates a completed session (`kodi_marked`)
- `playcount_cleared` / `resume_cleared` are recorded as observations and update `kodi_state` but never change existing sessions
- Sessions are **fully rebuildable** from `kodi_observation`. Run `rebuild-sessions` after tuning any rule.

---

## API endpoints

Base path: `/api`. OpenAPI docs at `/api/docs`. All responses are JSON.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Sync status, DB name, counters, active rule settings |
| GET | `/api/recent` | Recent sessions, newest first. Params: `limit`, `offset`, `type`, `status` |
| GET | `/api/sessions` | Full history. Same params as recent plus `search`, `date_from`, `date_to` |
| GET | `/api/in-progress` | Items with active Kodi resume points |
| GET | `/api/shows` | TV shows grouped by title with episode/watched counts, poster, next unwatched. Param: `search` |
| GET | `/api/items/{id}` | Full item detail: metadata, all sessions, all observations, all artwork |
| GET | `/api/stats` | Aggregated stats. Param: `days` (1–365, default 30) |
| POST | `/api/admin/rebuild-sessions` | Rebuild all sessions from observations. Runs in thread pool. |
| GET | `/api/admin/artwork-status` | Artwork download progress: total_items, items_with_artwork, total_artwork_rows, pending |

Artwork is served at `/art/<local_path>` (FastAPI `StaticFiles` mounted on `ARTWORK_DIR`).

---

## Kodi DB protection layers

All 4 layers must stay in place. See `kodi/db.py`.

1. **MariaDB grants** — `logsaga` account has only `SELECT ON MyVideos%.*`
2. **Grant verification** (`_verify_grants`)  — checks `SHOW GRANTS FOR CURRENT_USER()` on every connect. Rejects: dangerous users (`kodi`, `root`), any grant on `*.*`, any write keyword.
3. **Read-only session** (`_set_read_only`) — `SET SESSION TRANSACTION READ ONLY` + `READ COMMITTED` on every connection
4. **SQL guard** (`_execute`) — wrapper around every `cursor.execute()`. Only allows `SELECT`, `SHOW`, `SET SESSION`. All Kodi SQL is defined as module-level string constants — no f-strings, no string building.

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
| `TZ` | `America/Los_Angeles` | Must match Kodi client timezone (lastPlayed is local time) |
| `LOGSAGA_DB_PATH` | `logsaga.db` | SQLite file path |
| `ARTWORK_DIR` | `/config/artwork` | Where artwork files are stored |
| `ARTWORK_THUMBNAIL_MAX_PX` | `400` | Max dimension for WebP thumbnails (0 = disabled) |
| `PUBLIC_BASE_URL` | `http://localhost:8790` | Used in MQTT artwork URLs |
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

---

## Tests

147 tests in `backend/tests/`. Run with `uv run pytest`.

| Module | Coverage |
|--------|----------|
| `test_kodi_db_protection.py` | All 4 protection layers; grant check, read-only session, SQL guard |
| `test_session_rules.py` | All session rule scenarios: crash/resume, stop at 50%, watch to end, mark watched, rewatch, etc. |
| `test_sessions_builder.py` | DB-level builder with scripted observation timelines |
| `test_poller.py` | Poller diff logic; all 7 observation kinds |
| `test_artwork_cache.py` | Artwork fetch, skip-if-exists, thumbnail creation, art type allowlist |
| `test_api.py` | All 8 API endpoints, health status logic, rebuild endpoint |
