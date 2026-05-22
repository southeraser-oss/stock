# Automated Stock Paper Trading Dashboard

This project is now a dynamic FastAPI app, not a GitHub Pages static site.

FastAPI serves the dashboard UI at `/`, exposes same-origin APIs under `/api/*`, keeps paper-trading state on the backend, and runs interactive AI backtests from the dates, style, and capital selected in the frontend.

The app is for analysis and paper trading only. It does not place real-money orders.

## What Runs Where

- `backend/app.py` serves the dashboard and all interactive APIs.
- `docs/index.html` is the UI, but it is served by FastAPI.
- `docs/data/*.json` keeps generated analysis, paper state, ledger, and latest backtest output.
- `data/stock_dashboard.sqlite3` stores backend backtest runs and cash movements.
- `.github/workflows/analyze.yml` is optional scheduled analysis only. It no longer deploys the site.
- `render.yaml` contains the Render web-service start command.

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add keys to `.env` if you want real AI calls:

```bash
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
```

Run the app:

```bash
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Render Deployment

Create a new Render Web Service from this repository.

Use:

```bash
uvicorn backend.app:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` already sets this start command.

Render free tier can run this app, but its local filesystem is not a durable database. For serious long-running paper-trading history, upgrade to a persistent disk or move the SQLite state to a hosted database.

In Render, add these environment variables:

| Name | Purpose |
| --- | --- |
| `DEEPSEEK_API_KEY` | Broad daily AI decisions and repetitive screening |
| `OPENAI_API_KEY` | Higher-quality synthesis and final reviews |
| `STOCK_SYMBOLS` | Use `auto` for broad US/HK/CN universe |
| `MAX_SYMBOLS` | Controls scan/backtest cost |
| `USE_OPENAI_PREMIUM` | `auto`, `true`, or `false` |
| `AI_BACKTEST_ENABLED` | `true` to use AI in interactive backtests |
| `ENABLE_TRADING_SKILLS` | `true` to load Markdown skills from `skills/` |

Do not put API keys in frontend files. The browser only calls relative paths like `/api/health`, `/api/dashboard`, `/api/backtest`, and `/api/paper/add-funds`.

## API Routes

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/state`
- `POST /api/backtest`
- `POST /api/paper/add-funds`
- `POST /api/paper/withdraw`
- `POST /api/paper/pause`
- `POST /api/paper/resume`
- `POST /api/paper/sell-all`

## AI Work Split

DeepSeek and OpenAI are deliberately assigned different jobs.

DeepSeek handles broad, repetitive, lower-cost work: daily BUY/SELL/HOLD decisions, symbol triage, compact indicator review, and first-pass risk flags.

OpenAI handles smaller, higher-quality work: final backtest review, portfolio diagnosis, risk synthesis, and decision-quality review.

The backtest output reports DeepSeek ok/skipped/error days and OpenAI review status so you can see whether the APIs actually ran.

## Interactive Backtests

The frontend sends your selected start date, end date, style, and capital to `/api/backtest`.

The backend then fetches market data, runs the simulation day by day, calls DeepSeek for AI orders when enabled, applies simulated buy/sell actions, and calls OpenAI once at the end for review.

You do not need to edit GitHub Variables for every backtest.

## Paper Trading Controls

The dashboard buttons are backend actions:

- Add funds writes cash into backend paper state.
- Withdraw deducts available cash.
- Pause/Start updates backend trading status.
- Sell All liquidates simulated holdings into cash and records SELL trades.

The initial balance is zero until you add funds.

## Optional GitHub Actions

GitHub Actions still runs every 30 minutes if enabled. It is now only a scheduled analysis job that can update generated JSON files.

It is not the production site deployment path. Render serves the live app.

## Trading Skill Pack

Put Markdown strategy notes in `skills/`. AI analysis loads these files and uses them as trading-skill context.

Use this for your own screening rules, risk limits, position sizing, market-regime filters, and review checklists.
