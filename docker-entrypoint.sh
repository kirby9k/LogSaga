#!/bin/sh
# docker-entrypoint.sh — PUID/PGID privilege-dropping, migration, and start

set -e

PUID=${PUID:-99}
PGID=${PGID:-100}

# Create group/user if they don't already exist with the requested IDs
if ! getent group "$PGID" > /dev/null 2>&1; then
    groupadd -g "$PGID" appuser
fi
if ! getent passwd "$PUID" > /dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -s /bin/sh -d /config -M appuser
fi

# Give the runtime user ownership of /config (db + artwork live there)
chown -R "$PUID:$PGID" /config

# Run Alembic migrations as the runtime user (safe to run on every start —
# Alembic is idempotent and only applies unapplied migrations)
cd /app
gosu "$PUID:$PGID" alembic upgrade head

# Hand off to uvicorn
# --workers 1: the app holds in-memory state and background tasks; multiple
#   workers would cause duplicate observations and split caches
exec gosu "$PUID:$PGID" uvicorn logsaga.main:app \
    --host 0.0.0.0 \
    --port 8790 \
    --workers 1 \
    --no-access-log
