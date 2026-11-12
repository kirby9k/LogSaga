# Setup Guide

## 1. MariaDB — create the read-only account

Run inside the MariaDB container on Unraid (or any MariaDB client connected as root):

```sql
-- Create the read-only account
CREATE USER 'logsaga'@'%' IDENTIFIED BY 'choose-a-strong-password';

-- Grant SELECT on current and future MyVideos databases
-- The % wildcard covers MyVideos131, MyVideos132, etc. — no changes needed on Kodi upgrade
GRANT SELECT ON `MyVideos%`.* TO 'logsaga'@'%';

FLUSH PRIVILEGES;

-- Verify — should show only USAGE on *.* and SELECT on MyVideos%.*
SHOW GRANTS FOR 'logsaga'@'%';
```

Inside the Unraid MariaDB container the CLI is `mariadb`, not `mysql`:
```bash
mariadb -u root -p
```

## 2. Backup Kodi's database before first connection

```bash
# Run inside the MariaDB container on Unraid
mariadb-dump -u root -p MyVideos131 > /mnt/user/backups/MyVideos131-$(date +%Y%m%d).sql
```

Replace `MyVideos131` with the current DB name if Kodi has upgraded.

## 3. Local development (Windows, no Docker)

Prerequisites:
- Python 3.13+ (uv manages the venv)
- Node.js 20+ (for frontend build)
- uv installed

```powershell
# From backend/
uv sync                                              # install Python deps

# Run migrations (first time)
uv run --env-file ../.env logsaga migrate

# Check MariaDB connection and grants
uv run --env-file ../.env logsaga doctor

# Import existing history
uv run --env-file ../.env logsaga backfill

# Download artwork (recently played items first; resumes safely if interrupted)
uv run --env-file ../.env logsaga fetch-artwork

# Start backend
uv run --env-file ../.env uvicorn logsaga.main:app --host 0.0.0.0 --port 8000
```

**Important:** Always use forward slashes in `--env-file` on Windows:
`../.env` works; `..\.env` is mangled by the shell and silently loads no env vars.

**If `uv` is not in PATH** (e.g. in Claude Code's bash environment), use the venv directly.
The venv is at `backend/.venv/Scripts/` and contains `logsaga.exe` and `uvicorn.exe`.
Load `.env` via Python's `dotenv` before spawning processes:
```python
# Example: start backend via venv + dotenv
"backend/.venv/Scripts/python.exe" -c "
from dotenv import load_dotenv; load_dotenv('.env')
import os, subprocess, sys
os.chdir('backend')
subprocess.Popen([sys.executable, '-m', 'uvicorn', 'logsaga.main:app',
                  '--host', '0.0.0.0', '--port', '8000'])
"
```

**Verify backend has the .env loaded:** after a few poll cycles, `GET /api/health` should
show a non-null `kodi_db_name`. If it stays `null`, the backend started without credentials.

```powershell
# From frontend/ — dev server with hot reload (proxies /api to :8000)
npm install
npm run dev        # http://localhost:5173

# Production build (outputs to backend/dist/)
npm run build
```

The backend serves the built frontend from `backend/dist/` automatically.
After `npm run build`, `http://localhost:8000` serves the full app.

## 4. Environment variables (.env)

Copy `.env.example` to `.env` and fill in your values.
The `.env` file is gitignored — never commit it.

Key values to set:
- `KODI_DB_PASSWORD` — password for the `logsaga` MariaDB account
- `LOGSAGA_DB_PATH` — full path to your SQLite file
- `ARTWORK_DIR` — full path to your artwork directory
- `TZ` — your Kodi clients' local timezone (lastPlayed is written in this timezone)
- `PUBLIC_BASE_URL` — base URL for artwork links in MQTT messages

## 5. Docker deploy on Unraid

### Prerequisites

- Git available on the Unraid host (install the **NerdTools** plugin → `git` package, or
  use the **Community Applications** `Git` plugin).
- The `logsaga` MariaDB account already created (section 1 above).
- Kodi's database backed up (section 2 above).
- Port **8790** free on the Unraid host.

---

### Step 1 — Clone the repo onto Unraid

Open the **Unraid terminal** (or SSH in) and run:

```bash
mkdir -p /mnt/user/appdata/logsaga-src
cd /mnt/user/appdata/logsaga-src
git clone https://github.com/kirby9k/logsaga.git .
```

> The source tree only lives on the Unraid host temporarily — the Docker image bakes
> everything in. You can keep the clone for future rebuilds and `git pull` upgrades.

---

### Step 2 — Get the Docker image

**Option A — Pull from GitHub Container Registry (recommended)**

```bash
docker pull ghcr.io/kirby9k/logsaga:latest
```

This downloads the pre-built image. Skip to Step 3.

**Option B — Build from source**

If you prefer to build locally (or need to run a modified version):

```bash
cd /mnt/user/appdata/logsaga-src
docker build -t logsaga .
```

The build has two stages (Node frontend + Python backend). When using a locally built
image, use `logsaga` as the repository name instead of the GHCR path in Step 4.

Verify the image exists:
```bash
docker images | grep logsaga
```

---

### Step 3 — Create the appdata directory and `.env`

```bash
mkdir -p /mnt/user/appdata/logsaga

cp /mnt/user/appdata/logsaga-src/.env.example \
   /mnt/user/appdata/logsaga-src/.env
```

Edit the `.env` with your values (use `nano` or the Unraid file manager):

```bash
nano /mnt/user/appdata/logsaga-src/.env
```

Minimum required values to fill in:

```dotenv
KODI_DB_HOST=<IP of your MariaDB container host>
KODI_DB_PASSWORD=<password you set in step 1>
KODI_JSONRPC_URL=http://<kodi-headless IP>:8086/jsonrpc
TZ=America/Los_Angeles          # match your Kodi clients' timezone
PUBLIC_BASE_URL=http://<unraid IP>:8790
```

Leave the storage paths commented out — Docker sets them to `/config/logsaga.db`
and `/config/artwork` by default, which map to your appdata volume.

---

### Step 4A — Start with docker compose (recommended for first deploy)

> **⚠️ Important for new installs:** Start the server **after** running Step 5 (backfill),
> not before. If the server polls Kodi before backfill has run, it creates all history
> observations with **today's** timestamp instead of the correct historical `lastPlayed`
> dates. The recommended sequence is: start the container → immediately run the import
> commands in Step 5 → then let the server serve normal traffic.

```bash
cd /mnt/user/appdata/logsaga-src

# Point compose at the .env file and start
docker compose --env-file .env up -d
```

Check it started:
```bash
docker logs logsaga --tail 40
```

You should see Alembic running migrations, then uvicorn starting on port 8790.
The healthcheck goes green after ~60 s (Alembic + first poll cycle).

```bash
docker ps --filter name=logsaga
```

---

### Step 4B — Add via Unraid Community Applications (alternative)

1. In the Unraid UI go to **Docker → Add Container**.
2. Click **Template repositories** and add your repo URL, or manually paste the
   contents of `unraid/logsaga.xml` via **Import**.
3. Fill in every red-starred field (passwords, IPs).
4. Click **Apply**.

The template sets the same defaults as `docker-compose.yml`; both approaches are
equivalent.

---

### Step 5 — First-run data import

**Run these immediately after Step 4**, before the server has had time to do more than
a few poll cycles. The import commands use Kodi's historical `lastPlayed` dates — if
the poll loop has already run extensively, some observations will show today's date.

The app starts polling immediately, but your existing history needs to be imported
from Kodi's `playCount` / `lastPlayed` data. Run these once, in order:

```bash
# 1. Import existing watched history from Kodi's playCount/lastPlayed
docker exec logsaga logsaga backfill

# 2. Create placeholder sessions for plays older than the backfill
#    (items watched more than once; creates "Date unknown" sessions)
docker exec logsaga logsaga backfill-prior-plays

# 3. Sync the full library (watched + unwatched items, show metadata)
docker exec logsaga logsaga sync-library

# 4. Fill in plot, year, genres, codec data for all items
docker exec logsaga logsaga enrich-metadata

# 5. Download artwork (runs until complete; safe to Ctrl-C and re-run)
docker exec logsaga logsaga fetch-artwork --concurrency 10
```

`fetch-artwork` can take a while for a large library (~10 k items). It is fully
resumable — re-run any time to pick up new items.

---

### Step 6 — Verify

```bash
# Health endpoint
curl http://localhost:8790/api/health

# Should show kodi_db_name, last_poll, item counts, etc.
```

Open `http://<unraid IP>:8790` in a browser. You should see the Recently Played
page populated with your history.

---

### Upgrading

```bash
cd /mnt/user/appdata/logsaga-src
git pull
docker build -t logsaga .
docker compose --env-file .env up -d   # recreates the container with the new image
```

Alembic runs automatically on startup and applies any new migrations.

---

### Uninstalling / data wipe

```bash
docker stop logsaga && docker rm logsaga
docker rmi logsaga
# Optionally remove data (irreversible):
rm -rf /mnt/user/appdata/logsaga
```

## 6. Alembic migrations

Migrations live in `backend/migrations/versions/`. The `env.py` reads
`LOGSAGA_DB_PATH` (bare path) or `LOGSAGA_DB` (full SQLAlchemy URL).

```powershell
# Apply all pending migrations
uv run --env-file ../.env logsaga migrate

# Or run Alembic directly
uv run --env-file ../.env alembic upgrade head

# Check current revision
uv run --env-file ../.env alembic current
```
