# Changelog

All notable changes to LogSAGA are documented here.

---

## [Unreleased]

### Planned
- Home Assistant MQTT discovery (sensors for last watched, in-progress, daily/weekly stats)
- Dashboard YAML for HA using built-in cards only

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
