# Changelog

All notable changes to LogSAGA are documented here.

---

## [Unreleased] — since 1.0.0

### Added
- **Home Assistant MQTT** (Phase 6) — 7 sensors + 1 binary sensor via MQTT discovery;
  no HA YAML required. See [docs/home-assistant.md](docs/home-assistant.md).
- **Global search** (Ctrl+K) — searches local library + TMDB, deduped by TMDB ID.
- **Manual watch history** (`POST /api/watched`) — add any movie or episode; TMDB-only
  items get a `MediaItem` stub created and artwork fetched automatically.
- **TMDB detail pages** — `/tmdb/movie/{id}`, `/tmdb/tv/{id}`, season detail;
  "Add to History" with 3 date modes (now / release date / custom).
- **Actor detail pages** — profile photo, bio, birthday; watched history and library grids.
  Links from cast strips on item/show/TMDB pages.
- **Cast enrichment** — `enrich-cast` and `enrich-episode-cast` CLI commands;
  periodic `cast_sync_loop` background task (every 24 h).
- **TMDB ID backfill** — `enrich-tmdb-ids` assigns TMDB IDs to unmatched library items.
- **Backup system** — create, verify, download (credentials stripped), and restore backups
  from the Configuration page or CLI. Pre-rebuild automatic backup.
- **Configuration UI** — live-editable Kodi DB, JSON-RPC, TMDB, MQTT, and App settings
  on the `/configuration` page; no container restart needed.
- **Recently Viewed** — TMDB browse history (last 50), accessible from sidebar.

### Changed
- Dashboard now shows summary stat cards + recently played + recently added strips.
- Item detail, show detail, and TMDB pages use hero backdrop + overlapping poster layout.
- Artwork fetch now falls back to TMDB CDN for items missing required types after Kodi pass.

---

## [1.0.0] — 2026-11-09 — Initial public release

### Features
- **Watch history tracking** — polls Kodi's shared MariaDB library every 30 s, records
  every play attempt (finished or not) in an append-only SQLite database
- **Session rules** — configurable watched threshold (90% position or within credits
  window), 6-hour merge window for stop/resume, ignored sessions for accidental starts
- **Backfill** — imports existing Kodi `playCount` / `lastPlayed` history on first run;
  `backfill-prior-plays` creates placeholder sessions for plays predating tracking
- **Full library browsing** — Movies and TV Shows grids with Watched / All / Unwatched
  filter tabs; Show Detail and Season Detail pages with per-episode watched state
- **Artwork cache** — downloads and caches poster, fanart, clearlogo, episode thumbs
  locally; serves WebP thumbnails; never fetches at browse time
- **Dashboard views** — Recently Played, Continue Watching, Started Not Finished,
  History (searchable/filterable table, CSV export), Recently Added, Stats, Item Detail,
  Settings/Status
- **Docker container** — multi-stage build (Node + Python), port 8790, `/config` volume,
  PUID/PGID support, automatic Alembic migrations on startup
- **Unraid template** — community app XML with every variable documented

### Stack
- Python 3.13, FastAPI, SQLAlchemy 2 / Alembic, SQLite (WAL mode)
- React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui (zinc dark)
- 199 tests

---

*Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).*
