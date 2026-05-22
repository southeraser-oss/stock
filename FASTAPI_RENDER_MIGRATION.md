# Static Site to FastAPI Render App

This note describes the current app architecture so another static dashboard can be converted the same way.

## Target Shape

Use FastAPI as the single production server.

- FastAPI serves the frontend HTML at `/`.
- The browser calls same-origin relative APIs only, such as `/api/health` and `/api/backtest`.
- API keys stay on the backend as environment variables.
- GitHub Pages is not the live deployment path.
- GitHub Actions can remain as an optional manual job, but not as the website host.
- Render runs the app with `uvicorn backend.app:app --host 0.0.0.0 --port $PORT`.

## File Layout

Recommended structure:

```text
backend/
  app.py              # FastAPI routes and frontend serving
  db.py               # SQLite helpers or database access
docs/
  index.html          # Frontend UI served by FastAPI
  data/               # Optional generated JSON snapshots
src/
  analyze.py          # Optional manual analysis job
  backtest.py         # Core domain logic
  ai_trader.py        # LLM/API integration
requirements.txt
render.yaml
.env.example
```

## Backend Responsibilities

`backend/app.py` should own all dynamic behavior:

- `GET /` returns `docs/index.html`.
- `GET /api/health` reports backend and secret configuration status without exposing secrets.
- `GET /api/state` or `/api/dashboard` returns the current app state.
- `POST` routes mutate backend state, run analysis, or start backtests.
- Secrets are read only from environment variables.
- State is saved in SQLite, generated JSON, or a hosted database.

For this stock app, the main routes are:

```text
GET  /api/health
GET  /api/dashboard
GET  /api/state
POST /api/backtest
POST /api/paper/add-funds
POST /api/paper/withdraw
POST /api/paper/pause
POST /api/paper/resume
POST /api/paper/sell-all
```

## Frontend Rules

The frontend should be environment-agnostic.

Use:

```js
fetch("/api/dashboard")
fetch("/api/backtest", { method: "POST", ... })
```

Do not use:

```js
fetch("http://127.0.0.1:8000/api/...")
fetch("http://localhost:8000/api/...")
fetch("https://username.github.io/repo/data/latest.json")
```

The same HTML should work locally and on Render because the browser talks to the same origin that served the page.

## Render Setup

Use `render.yaml` or configure manually:

```yaml
services:
  - type: web
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn backend.app:app --host 0.0.0.0 --port $PORT
```

Set secrets in Render Environment Variables:

```text
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
```

Set normal config there too:

```text
STOCK_SYMBOLS=auto
MAX_SYMBOLS=35
AI_BACKTEST_ENABLED=true
USE_OPENAI_PREMIUM=auto
```

Do not hardcode secrets in `docs/index.html`, checked-in JSON, or GitHub workflow files.

## GitHub Actions Role

After conversion, GitHub Actions should be optional background work only.

Good uses:

- Run a manual analysis workflow when you explicitly want it.
- Refresh generated JSON snapshots.
- Commit non-secret generated state.

Avoid:

- Deploying GitHub Pages as the production app.
- Requiring repo variables for interactive frontend choices.
- Treating workflow dispatch inputs as the main user interface.

Interactive choices like date range, strategy, and initial capital should come from the frontend request body and be handled by FastAPI.

## Local Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## State Warning

Render free tier can run the app, but local filesystem state is not a durable database. For long-lived state, use a Render persistent disk or move SQLite/state to a hosted database.

## Migration Checklist

1. Move static HTML under `docs/index.html`.
2. Create `backend/app.py`.
3. Add a `/` route that serves the HTML.
4. Replace static JSON fetches with `/api/...` calls.
5. Move all secret/API work into backend routes.
6. Add backend state storage.
7. Remove GitHub Pages deployment from the production path.
8. Add `requirements.txt` and `render.yaml`.
9. Verify locally with Uvicorn.
10. Configure Render environment variables.
