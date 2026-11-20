# LogSAGA — Frontend

React 19 + TypeScript + Vite frontend for the LogSAGA watch history dashboard.

Built to static files and served by FastAPI — no separate Node server in production.
In development, Vite proxies `/api` and `/art` requests to the backend at `:8000`.

## Dev setup

```bash
npm install
npm run dev    # dev server at http://localhost:5173
```

The backend must be running at `:8000` for API calls to work.

## Build

```bash
npm run build  # outputs to ../backend/dist/
```

The FastAPI container serves the built files automatically.

## Type-check

```bash
npm run build  # tsc -b is part of the build step
```

## Regenerate API types

With the backend running at `:8000`:

```bash
npm run generate-types
```

Commits `src/api/types.ts` so the frontend can build without the backend running.

## Architecture

See [../docs/frontend.md](../docs/frontend.md) for full details: routes, components, hooks, and styling conventions.
