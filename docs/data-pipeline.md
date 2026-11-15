# LogSAGA Data Pipeline

How data flows from Kodi into LogSAGA — covering every status, every field, every
trigger, and every manual command available.

---

## Background tasks (always running)

Four background tasks start when the container starts and run until it stops.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Container startup                                                   │
│                                                                      │
│  ① poll_loop          every 30 s      (POLL_SECONDS)                │
│  ② library_sync_loop  every 2 h       (LIBRARY_SYNC_HOURS)         │
│  ③ cast_sync_loop     every 24 h      (CAST_SYNC_HOURS)            │
│  ④ mqtt_publisher     continuous      (if MQTT_HOST set)            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ① Poll loop — every 30 s

**What it reads:** Kodi's MariaDB (`files`, `movie`, `episode`, `bookmark` tables).
**What it writes:** `kodi_observation`, `kodi_file_state`, `watch_session`, `media_item` (new only).

### Cheap check first

Before doing anything expensive, one query runs:
`MAX(lastPlayed)`, `COUNT(playCount > 0)`, bookmark row count, `MAX(resume position)`.

If all four values match the previous poll, the loop exits immediately — no diff, no
writes. This means zero load on MariaDB when nothing is happening.

### Full snapshot + diff

If any value changed, the loop fetches every `(file_id, play_count, last_played,
resume_pos_s, resume_total_s)` row and diffs it against `kodi_file_state` (the
last-seen snapshot in SQLite).

**Observation kinds emitted per file:**

| Kind | When |
|---|---|
| `resume_set` | Resume point appeared on a file that had none |
| `resume_moved` | Resume position changed by > 0.5 s |
| `resume_cleared` | Resume point removed (file finished or stopped) |
| `lastplayed_changed` | `lastPlayed` field changed (always at stop/completion) |
| `playcount_up` | `playCount` incremented (Kodi marks item watched) |
| `playcount_cleared` | `playCount` went to 0 / NULL ("mark as unwatched" in Kodi) |
| `file_removed` | File row disappeared (Kodi library clean) |

### New item detection

If the file has never been seen, `identify_file()` looks up the item in Kodi's DB
(`movie`/`episode` + `uniqueid` tables) and creates a `MediaItem` row in SQLite.

Dedup order for TMDB-only items already added manually:
1. Match by `kodi_file_id` (fast path, normal case)
2. Match by `tmdb_id` (links manually-added stub to Kodi file)
3. Match by `imdb_id` / `tvdb_id` (fallback)
4. Create new `MediaItem`

**When new items are created the poll loop fires `_enrich_new_items_bg`** (see below).

### Session builder (incremental)

After observations are written, `build_incremental()` runs for each affected item:

1. Loads existing `watch_session` rows for the item.
2. Applies new observations via the **rules engine** (`apply_observations()`).
3. Closes any open sessions idle longer than `SESSION_GAP_HOURS` (6 h).
4. Writes back only dirty (new/modified) rows.

---

## ② Library sync loop — every 2 h

**What it reads:** All movies, episodes, and shows from Kodi's MariaDB.
**What it writes:** `media_item`, `kodi_show` — metadata and `date_added` only.
**Never touches:** `kodi_observation`, `watch_session`.

### What it updates

- `title`, `plot`, `genres`, `director`, `runtime_s`, `release_date`, `year`
- `date_added` (from Kodi's `files.dateAdded` — sentinel `1951-09-25` when NULL)
- `kodi_state` (derived from `playCount`)
- `tmdb_id`, `imdb_id`, `tvdb_id` (from Kodi's `uniqueid` table, movies only via LEFT JOIN)
- Marks items no longer in Kodi as `kodi_state = removed`

### After sync: fire-and-forget tasks

If any new `MediaItem` rows were created:
- **`_fill_missing_artwork_bg`** — finds items with no downloaded artwork, fetches from Kodi.
- **`_fill_missing_cast_bg`** — finds items with `tmdb_id` but no `item_cast` rows, populates
  from local `tmdb_cache` (no network calls; fast).

---

## ③ Cast sync loop — every 24 h

**What it reads:** `media_item`, `kodi_show`, `item_cast` (to find gaps).
**What it calls:** TMDB API (live, rate-limited to 3 concurrent).
**Initial delay:** 10 minutes (lets library sync run first).
**Guard:** Does nothing if `TMDB_API_KEY` is not set.

### What it does

1. Finds all `KodiShow` rows with `tmdb_id` but no show-level `item_cast` rows.
2. Finds all movie `MediaItem` rows with `tmdb_id` but no item-level `item_cast` rows.
3. For each: calls `tmdb.tv_detail()` / `tmdb.movie_detail()` → writes to `tmdb_cache`
   (30-day TTL) → calls `populate_cast_for_show()` / `populate_cast_for_item()` →
   inserts `Person` + `ItemCast` rows.

This is the "catch-all" for items that the poll loop or library sync created but whose
cast wasn't populated at first detection.

---

## ④ New item enrichment — `_enrich_new_items_bg`

Fired whenever the poll loop creates a new `MediaItem`. Runs in background.

Steps per item:
1. **Artwork** — delegates to `_fetch_artwork_for_items_bg` (Kodi art table via MariaDB +
   JSON-RPC for `image://` paths).
2. **TMDB detail** — if `tmdb_api_key` set and `item.tmdb_id` present: calls
   `movie_detail()` or `tv_detail()` to warm `tmdb_cache`.
3. **Cast** — calls `populate_cast_for_item()` or `populate_cast_for_show()` using
   the just-cached data.

---

## How each status/field is populated

### `kodi_state` (recently added, in-progress, watched)

| State | How it gets set |
|---|---|
| `unwatched` | Default on item creation |
| `in_progress` | Poll loop: file has `playCount > 0` and a resume bookmark |
| `watched` | Poll loop: `playCount > 0`, no active resume; or manual add |
| `removed` | Library sync: file row gone from Kodi |

`kodi_state` is display-only context — it reflects what Kodi thinks. Watch history
(sessions) is the authoritative source.

### Watch sessions

Derived from `kodi_observation` by the rules engine. Rules are locked in:

| Rule | Value | Config var |
|---|---|---|
| Ignore threshold | 120 s | `MIN_STARTED_SECONDS` |
| Session merge gap | 6 h | `SESSION_GAP_HOURS` |
| Watched % | 90% | `WATCHED_PERCENT` |
| Movie credits window | 8 min remaining | `WATCHED_REMAINING_MINUTES_MOVIE` |
| Episode credits window | 3 min remaining | `WATCHED_REMAINING_MINUTES_EPISODE` |

**Session statuses:**

| Status | Meaning |
|---|---|
| `open` | Activity seen; gap timer running |
| `completed` | Position ≥ 90%, within credits window, or `playcount_up` observed |
| `partial` | Stopped before 90%; above 120 s threshold |
| `ignored` | Stopped below 120 s threshold |

**Completion reasons:**

| Reason | Trigger |
|---|---|
| `position` | Resume position reached threshold |
| `kodi_playcount` | Kodi incremented `playCount` (Device 2 natural end or "Mark as watched") |
| `manual` | Added via POST /api/watched |

Sessions are **rebuild-safe**: `kodi_observation` is append-only and sessions can be
fully rebuilt from observations at any time (`logsaga rebuild-sessions`).

### `date_added` (recently added sort order)

Set by **library sync** from Kodi's `files.dateAdded`. Items not yet synced have NULL.
Sentinel `1951-09-25 00:00:00` used for Kodi items whose `dateAdded` is NULL.

For **manually added TMDB-only items**: `date_added` is set to the current UTC time
when the stub is created (since the item came into LogSAGA now, not from Kodi).

### Metadata (title, plot, genres, director, year, runtime)

| Source | When |
|---|---|
| Kodi's MariaDB (`movie`/`episode` tables) | Poll loop (new item) or library sync |
| TMDB detail API | Manual add (new TMDB-only stub); also cached by cast sync and enrichment |
| `enrich-metadata` CLI | One-time backfill for items created before migration 0003 |

### Artwork (poster, fanart, thumb, clearlogo)

| Source | When |
|---|---|
| Kodi's art table (MariaDB) | Poll loop via `_enrich_new_items_bg`; library sync via `_fill_missing_artwork_bg` |
| TMDB CDN URLs | Manual TMDB adds via `fetch_tmdb_artwork_async` in `_schedule_enrich` |
| JSON-RPC (`Files.PrepareDownload`) | Used to resolve `image://` encoded Kodi paths to real URLs |

Art types cached: `poster`, `fanart`, `clearlogo`, `thumb`, `tvshow.poster`,
`tvshow.fanart`, `season.poster`.

Show-level art (`tvshow.*`, `season.*`) is stored once per show (filesystem dedup)
and reused across all episodes — not re-downloaded for every episode.

### Cast (actors)

| Source | When |
|---|---|
| `tmdb_cache` (local SQLite) | On first item/show detail page visit; after any TMDB detail fetch |
| TMDB credits API (live) | `_enrich_new_items_bg` (new poll-detected items); `_run_cast_sync_bg` (periodic); `enrich-cast` CLI |
| Manual add `_schedule_enrich` | Immediately after manual add (data already cached) |

Cast population is **idempotent** — `INSERT OR IGNORE` on `person` and `item_cast`.

---

## Manual triggers

### Settings UI (`/settings`)

Buttons that fire API calls immediately:

| Button | API call | What it does |
|---|---|---|
| **Trigger Poll** | `POST /api/admin/poll` | Runs one poll cycle (like the 30 s loop, but now) |
| **Trigger Library Sync** | `POST /api/admin/sync-library` | Runs one full library sync |
| **Trigger Artwork Fetch** | `POST /api/admin/fetch-artwork` | Fetches missing artwork for all items |
| **Trigger Metadata Enrich** | `POST /api/admin/enrich-metadata` | Backfills metadata fields |
| **Rebuild Sessions** | `POST /api/admin/rebuild-sessions` | Deletes all sessions, rebuilds from observations |

Settings you can change here: `POLL_SECONDS`, `LIBRARY_SYNC_HOURS`, session rules
(`MIN_STARTED_SECONDS`, `SESSION_GAP_HOURS`, `WATCHED_PERCENT`, credits windows).
Changes take effect immediately without restart.

### CLI commands (`docker exec logsaga logsaga <command>`)

| Command | What it does |
|---|---|
| `doctor` | Check DB connection, verify grants, show active settings |
| `migrate` | Run Alembic migrations (upgrade to head) |
| `sync --once` | Run one poll cycle and print observations |
| `sync-library` | Full library sync (all movies/episodes/shows) |
| `rebuild-sessions` | Delete all sessions, rebuild from observations |
| `fetch-artwork` | Download artwork for all items (or one with `--item-id`) |
| `enrich-cast` | Fetch TMDB show/movie credits for all items missing cast |
| `enrich-episode-cast` | Fetch per-episode credits (guest stars); separate from show-level cast |
| `enrich-tmdb-ids` | Search TMDB to find `tmdb_id` for items missing one |
| `enrich-metadata` | Backfill plot/genres/director/tech fields from Kodi |
| `backfill` | Import existing `playCount > 0` entries as completed sessions |
| `backfill-prior-plays` | Create `1970-01-01` placeholder sessions for plays before backfill |

**Useful flags:** `--dry-run` (preview without committing), `--concurrency N` (parallel TMDB
fetches, default 5), `--force` (re-process already-done items).

---

## Full flow: new item first seen by poll loop

```
Kodi plays something new (file never in kodi_file_state)
  │
  ├─ identify_file() reads Kodi DB → creates MediaItem
  │
  ├─ Observation written (resume_set or lastplayed_changed)
  │
  ├─ build_incremental() → WatchSession created (status=open)
  │
  └─ _enrich_new_items_bg() fired (background, non-blocking)
       ├─ _fetch_artwork_for_items_bg() → artwork downloaded
       ├─ tmdb.movie_detail() / tv_detail() → tmdb_cache populated
       └─ populate_cast_for_item/show() → ItemCast rows inserted
```

## Full flow: item added manually from TMDB

```
User clicks "Add to History" on a TMDB page
  │
  ├─ POST /api/watched
  │
  ├─ tmdb_client.movie_detail() (or tv_detail + season_detail) fetches data
  │
  ├─ _find_or_create_movie/episode() → MediaItem created with:
  │    date_added = now()   tmdb_id = set   kodi_media_id = NULL
  │
  ├─ WatchSession created (status=completed, source=manual)
  │
  └─ _schedule_enrich() fired (background, non-blocking)
       ├─ fetch_tmdb_artwork_async() → artwork downloaded (TMDB CDN)
       └─ populate_cast_for_item/show() → ItemCast rows inserted
              (tmdb_cache already warm from above)
```

## Full flow: library sync finds new item

```
New file added to Kodi library (not yet seen by poll loop)
  │
  ├─ library_sync_loop fires (every 2 h, or POST /api/admin/sync-library)
  │
  ├─ MediaItem created with metadata + date_added from Kodi
  │
  └─ (if new items found):
       ├─ _fill_missing_artwork_bg() → artwork for items with none
       └─ _fill_missing_cast_bg() → cast from local tmdb_cache (no network)
            └─ cast_sync_loop will fill any remaining gaps within 24 h
```

---

## What updates when you run `sync-library`

- All `media_item` rows: `title`, `plot`, `genres`, `director`, `runtime_s`,
  `release_date`, `year`, `date_added`, `kodi_state`, `tmdb_id` (now via LEFT JOIN)
- All `kodi_show` rows: same metadata fields
- Items no longer in Kodi: `kodi_state = removed` (history kept)
- New items get artwork + cast queued (see above)
- **Does not change**: `kodi_observation`, `watch_session`, `item_cast`, `artwork`

---

## What does NOT flow automatically

| Thing | Why | Fix |
|---|---|---|
| Cast for library-sync-new items | `_fill_missing_cast_bg` is cache-only; no TMDB call | Cast sync loop catches within 24 h, or run `enrich-cast` |
| `tmdb_id` for items Kodi never scraped a TMDB ID for | Kodi only writes `uniqueid` rows when it scrapes | Run `enrich-tmdb-ids` |
| Per-episode guest star cast | Separate from show-level cast | Run `enrich-episode-cast` |
| Metadata for items created before migration 0003 | Fields weren't stored then | Run `enrich-metadata` |
