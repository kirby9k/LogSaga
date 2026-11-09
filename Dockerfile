# ── Stage 1: build frontend ───────────────────────────────────────────────
FROM node:22-slim AS node-build

WORKDIR /build

# Install dependencies first (cached unless package-lock.json changes)
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend

# Copy source and build
COPY frontend/ ./frontend/
# vite.config.ts writes to outDir: '../backend/dist', which resolves to /build/backend/dist/
RUN mkdir -p backend && npm run build --prefix frontend


# ── Stage 2: runtime ──────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Install system tools
# gosu: clean PUID/PGID privilege-dropping in the entrypoint
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official image (pinned to 0.5.x for reproducible builds)
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app

# ── Dependency layer (cached until pyproject.toml or uv.lock changes) ────
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# ── Application source ────────────────────────────────────────────────────
COPY backend/logsaga/ ./logsaga/
COPY backend/migrations/ ./migrations/
COPY backend/alembic.ini ./

# Install the project itself (registers the logsaga CLI script)
RUN uv sync --no-dev --frozen

# ── Frontend build output from Stage 1 ───────────────────────────────────
# main.py resolves dist as Path(__file__).parent.parent / "dist" = /app/dist/
COPY --from=node-build /build/backend/dist/ ./dist/

# ── Runtime environment defaults ─────────────────────────────────────────
ENV PATH="/app/.venv/bin:$PATH" \
    LOGSAGA_DB_PATH="/config/logsaga.db" \
    ARTWORK_DIR="/config/artwork" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create /config directory (bind-mounted at runtime; pre-created so the
# volume declaration below is recognised even before the mount is applied)
RUN mkdir -p /config/artwork

# ── Entrypoint ────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8790
VOLUME /config

# Use Python's built-in urllib so curl is not needed in the image.
# start-period covers Alembic migration + first connection attempt.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8790/api/health')" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
