# LogSAGA

A self-hosted watch history dashboard for [Kodi](https://kodi.tv). Kodi itself keeps no play history — it only stores the most recent state per file. LogSAGA fixes that by polling Kodi's shared MariaDB database, recording every play attempt in its own SQLite database, and serving a modern web dashboard.

**It never writes to your Kodi database.** Access is enforced at four independent layers (see [Security](#security)).

---

## Features

- **Records everything** — every play attempt is logged, finished or not. Survives library cleans, rescans, and "mark unwatched" in Kodi. History is never deleted.
- **Smart session rules** — configurable watched threshold (default 90% position, or within the credits window), 6-hour merge window for stop/resume/crash recovery, ignored sessions for accidental short starts.
- **Full library browsing** — Movies and TV Shows grids with Watched / All / Unwatched filter tabs, sort options, Show Detail and Season Detail pages with per-episode progress.
- **Artwork cache** — downloads posters, fanart, clearlogos, and episode thumbnails locally. The UI never fetches from Kodi or external sources at browse time.
- **History import** — backfills existing Kodi `playCount` / `lastPlayed` data on first run, including placeholder sessions for plays that predate the tracker.
- **Home Assistant** — MQTT discovery sensors for last watched, recently played, in-progress items, and daily/weekly watch time. *(Phase 6 — in development)*
- **Runs anywhere Docker runs** — single container, port 8790, persistent `/config` volume.

### Dashboard views

| View | Description |
|---|---|
| Recently Played | Poster timeline grouped by date, newest first, with status badges |
| Continue Watching | Items Kodi currently has a resume point for |
| Started, Not Finished | Partial sessions with % reached |
| Movies | Full library grid with filter tabs and sort options |
| TV Shows | Full library grid; links to per-show detail |
| Show Detail | Season list with progress bars and "next up" hint |
| Season Detail | Episode list with thumbnails, air dates, watched state |
| Recently Added | Items by `dateAdded` from Kodi's library |
| History | Searchable/filterable table with CSV export |
| Stats | Watch time per day/week/month, top shows/movies, completion rate |
| Item Detail | Metadata, artwork, every session with its raw observations |
| Settings/Status | Sync health, DB info, session rules, rebuild button |

---

## Requirements

- **Kodi** with a shared **MariaDB** database (the standard multi-client setup)
- Kodi version 21 (Omega) or later — auto-detects the `MyVideos%` database name
- **Docker** on your server (Unraid, Linux, macOS, Windows)
- A `SELECT`-only MariaDB account for LogSAGA to use (see [Setup](#setup))

> **Not required:** Kodi-Headless, a specific Kodi client configuration, or Home Assistant. Kodi-Headless is only needed if you have NFS-hosted artwork that requires JSON-RPC `Files.PrepareDownload` to fetch.

---

## Quick start

```bash
# 1. Pull the image
docker pull ghcr.io/kirby9k/logsaga:latest

# 2. Create a config directory
mkdir -p /opt/logsaga

# 3. Create your .env (see Configuration below)
cp .env.example /opt/logsaga/.env
nano /opt/logsaga/.env

# 4. Run
docker run -d \
  --name logsaga \
  --restart unless-stopped \
  -p 8790:8790 \
  -v /opt/logsaga:/config \
  --env-file /opt/logsaga/.env \
  ghcr.io/kirby9k/logsaga:latest

# 5. Import existing history (run once after first start)
docker exec logsaga logsaga backfill
docker exec logsaga logsaga backfill-prior-plays
docker exec logsaga logsaga sync-library
docker exec logsaga logsaga enrich-metadata
docker exec logsaga logsaga fetch-artwork --concurrency 10
```

Open `http://YOUR_SERVER_IP:8790` in a browser.

For a full walkthrough including Unraid-specific steps and docker compose, see **[docs/setup.md](docs/setup.md)**.

---

## Setup

### 1. Create the read-only MariaDB account

Connect to your MariaDB instance as root and run:

```sql
CREATE USER 'logsaga'@'%' IDENTIFIED BY 'choose-a-strong-password';
GRANT SELECT ON `MyVideos%`.* TO 'logsaga'@'%';
FLUSH PRIVILEGES;
SHOW GRANTS FOR 'logsaga'@'%';
```

The `MyVideos%` wildcard covers the current database and any new one Kodi creates on upgrade — no changes needed after a Kodi version update.

### 2. Configure

Copy `.env.example` to `.env` and fill in your values:

```dotenv
KODI_DB_HOST=YOUR_MARIADB_IP
KODI_DB_PORT=3306
KODI_DB_USER=logsaga
KODI_DB_PASSWORD=your-strong-password

KODI_JSONRPC_URL=http://YOUR_KODI_HEADLESS_IP:8086/jsonrpc

TZ=America/Los_Angeles   # must match your Kodi clients' timezone

PUBLIC_BASE_URL=http://YOUR_SERVER_IP:8790
```

See [Configuration](#configuration) for all available options.

### 3. First-run import

Run these once after the container first starts, in order:

```bash
# Import existing watched history from Kodi's playCount / lastPlayed
docker exec logsaga logsaga backfill

# Create placeholder sessions for plays before tracking began
docker exec logsaga logsaga backfill-prior-plays

# Sync full library (watched + unwatched items and show metadata)
docker exec logsaga logsaga sync-library

# Fill in plot, year, genres, codec data for all items
docker exec logsaga logsaga enrich-metadata

# Download artwork (resumable — safe to Ctrl-C and re-run)
docker exec logsaga logsaga fetch-artwork --concurrency 10
```

---

## Configuration

All settings are environment variables. Copy `.env.example` to `.env` and set the values you need.

### Required

| Variable | Description |
|---|---|
| `KODI_DB_HOST` | Hostname or IP of your MariaDB server |
| `KODI_DB_PASSWORD` | Password for the `logsaga` account |
| `KODI_JSONRPC_URL` | Kodi JSON-RPC endpoint (e.g. `http://192.168.1.x:8086/jsonrpc`) |
| `TZ` | Timezone matching your Kodi clients (e.g. `America/Los_Angeles`) |

### Optional — connection

| Variable | Default | Description |
|---|---|---|
| `KODI_DB_PORT` | `3306` | MariaDB port |
| `KODI_DB_USER` | `logsaga` | MariaDB username |
| `KODI_DB_NAME` | *(auto-detect)* | Pin to a specific `MyVideosNNN` database |
| `KODI_JSONRPC_USER` | *(blank)* | Kodi JSON-RPC username |
| `KODI_JSONRPC_PASSWORD` | *(blank)* | Kodi JSON-RPC password |

### Optional — behaviour

| Variable | Default | Description |
|---|---|---|
| `POLL_SECONDS` | `30` | How often to poll Kodi's database |
| `LIBRARY_SYNC_HOURS` | `2.0` | How often to run a full library sync |
| `PUBLIC_BASE_URL` | `http://localhost:8790` | Base URL used in MQTT artwork links |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `PUID` / `PGID` | `99` / `100` | User/group ID the process runs as |

### Optional — session rules

These control how LogSAGA decides what counts as a watch.

| Variable | Default | Description |
|---|---|---|
| `MIN_STARTED_SECONDS` | `120` | Minimum position before a partial session is kept (below this = ignored) |
| `SESSION_GAP_HOURS` | `6` | Activity gap that merges into the same session |
| `WATCHED_PERCENT` | `90` | % of runtime that counts as watched |
| `WATCHED_REMAINING_MINUTES_MOVIE` | `8` | Credits window for movies (minutes from end) |
| `WATCHED_REMAINING_MINUTES_EPISODE` | `3` | Credits window for episodes (minutes from end) |

### Optional — MQTT (Home Assistant)

| Variable | Default | Description |
|---|---|---|
| `MQTT_HOST` | *(blank = disabled)* | MQTT broker hostname/IP |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USER` / `MQTT_PASSWORD` | *(blank)* | MQTT credentials |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | HA discovery prefix |

---

## Docker Compose

```yaml
services:
  logsaga:
    image: ghcr.io/kirby9k/logsaga:latest
    container_name: logsaga
    restart: unless-stopped
    ports:
      - "8790:8790"
    volumes:
      - /opt/logsaga:/config
    env_file:
      - /opt/logsaga/.env
```

---

## Unraid

An Unraid community app template is included at `unraid/logsaga.xml`. See [docs/setup.md](docs/setup.md) for step-by-step Unraid instructions.

---

## CLI commands

```bash
docker exec logsaga logsaga <command>
```

| Command | Description |
|---|---|
| `doctor` | Check DB connection, verify grants, report config |
| `backfill` | Import existing history from Kodi's playCount/lastPlayed |
| `backfill-prior-plays` | Create placeholder sessions for pre-tracking plays |
| `sync-library` | Sync full library (movies, shows, episodes) |
| `enrich-metadata` | Backfill plot, genres, codec data from Kodi |
| `fetch-artwork [--concurrency N]` | Download and cache artwork |
| `rebuild-sessions` | Rebuild all sessions from raw observations |
| `migrate` | Apply pending database migrations |

---

## How it works

Kodi keeps no play history — it only stores the most recent state per file. LogSAGA works in two layers:

1. **Raw observations** (append-only, never edited) — every poll diffs Kodi's state against a local snapshot and records what changed: resume point set/moved/cleared, `lastPlayed` changed, `playCount` increased, file removed.

2. **Watch sessions** (derived from observations) — the session builder converts observations into sessions using the configurable rules. Sessions can be rebuilt at any time from the raw observations using `rebuild-sessions`.

Because observations are kept forever, tuning the session rules and rebuilding is always safe — no data is lost.

---

## Security

LogSAGA enforces read-only access to Kodi's database at four independent layers:

1. **MariaDB grants** — the `logsaga` account has only `SELECT` on `MyVideos%.*`
2. **Read-only session** — `SET SESSION TRANSACTION READ ONLY` on every connection
3. **Query allowlist** — all Kodi SQL is defined as fixed constants; a guard rejects anything that isn't `SELECT`, `SHOW`, or `SET SESSION`
4. **Startup check** — the app exits with an error if the connected account has more than `SELECT`/`USAGE` privileges

See [SECURITY.md](SECURITY.md) for the full policy.

The web UI has **no authentication**. It is intended for LAN use only. Do not expose port 8790 to the internet.

---

## Development

```bash
# Backend
cd backend
uv sync
cp ../.env.example ../.env   # fill in your values
uv run --env-file ../.env logsaga doctor
uv run --env-file ../.env uvicorn logsaga.main:app --host 0.0.0.0 --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev    # dev server at http://localhost:5173, proxies /api to :8000

# Tests
cd backend
uv run pytest
```

See [docs/backend.md](docs/backend.md) and [docs/frontend.md](docs/frontend.md) for architecture details.

---

## License

[MIT](LICENSE)
