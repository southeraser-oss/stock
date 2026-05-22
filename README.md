# Automated Stock Analysis Dashboard

This repository builds a static paper-trading stock dashboard with a Python backend and GitHub Pages frontend.

The backend runs every 30 minutes in GitHub Actions, fetches market data from `yfinance`, calculates common technical indicators, runs a simulated trading strategy, optionally calls LLM APIs for screening, and publishes `/docs` as a GitHub Pages site.

## Features

- Fetches stock history from `yfinance`
- Uses Yahoo Finance screeners plus a broad fallback watchlist instead of only mega-cap tech names
- Calculates SMA20, SMA60, RSI14, daily return, and volume change
- Uses DeepSeek for low-cost stock screening when `DEEPSEEK_API_KEY` is available
- Calls OpenAI Responses API for premium portfolio review when `OPENAI_API_KEY` is present, unless disabled
- Simulates paper trades with cash balance, total return, holdings, equity curve, and trade history
- Runs historical backtests with selectable date range, style, and capital
- Keeps a daily review ledger with a dashboard calendar view
- Falls back to rule-based analysis when API keys are missing
- Writes structured output to `docs/data/latest.json`
- Serves a simple static dashboard from `docs/index.html`
- Runs every 30 minutes and supports manual workflow runs
- Analysis and alerts only. No automatic trading.

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/analyze.py
```

Open `docs/index.html` through a local web server so the browser can fetch `docs/data/latest.json`:

```bash
python -m http.server 8000 --directory docs
```

Then visit `http://localhost:8000`.

## Configuration

Use GitHub repository variables for non-secret values:

| Name | Default |
| --- | --- |
| `USE_OPENAI_PREMIUM` | `auto` |
| `STOCK_SYMBOLS` | `auto` |
| `MAX_SYMBOLS` | `35` |
| `ENABLE_YFINANCE_SCREENERS` | `true` |
| `SCREENER_COUNT` | `12` |
| `MODEL_DEEPSEEK` | `deepseek-chat` |
| `MODEL_OPENAI` | `gpt-4.1-mini` |
| `DEEPSEEK_MAX_TOKENS` | `900` |
| `OPENAI_MAX_OUTPUT_TOKENS` | `1200` |
| `PAPER_CASH_DELTA` | `0` |
| `PAPER_CASH_DELTA_ID` | `default` |
| `PAPER_MAX_POSITIONS` | `4` |
| `PAPER_ALLOCATION_PCT` | `0.24` |
| `PAPER_FEE_BPS` | `2` |
| `PAPER_TRADING_ENABLED` | `true` |
| `PAPER_FORCE_LIQUIDATE` | `false` |
| `ENABLE_TRADING_SKILLS` | `true` |
| `TRADING_SKILLS_MAX_CHARS` | `6000` |
| `HISTORY_PERIOD` | `2y` |
| `BACKTEST_START_DATE` | `2026-01-01` |
| `BACKTEST_END_DATE` | empty, defaults to today |
| `BACKTEST_STYLE` | `momentum` |
| `BACKTEST_INITIAL_CAPITAL` | `100000` |
| `BACKTEST_MAX_POSITIONS` | `5` |
| `BACKTEST_ALLOCATION_PCT` | `0.2` |
| `AI_BACKTEST_ENABLED` | `true` |
| `AI_DAILY_CANDIDATE_LIMIT` | `12` |
| `DEEPSEEK_DAILY_MAX_TOKENS` | `700` |
| `AI_BUY_MIN_CONFIDENCE` | `0.55` |
| `OPENAI_BACKTEST_MAX_OUTPUT_TOKENS` | `1200` |

Use GitHub repository secrets for API keys:

| Name | Purpose |
| --- | --- |
| `DEEPSEEK_API_KEY` | Optional DeepSeek screening |
| `OPENAI_API_KEY` | Optional premium OpenAI analysis |

If API keys are missing, the workflow still succeeds and the dashboard uses rule-based analysis.

## AI Work Split

DeepSeek and OpenAI are deliberately not used as interchangeable fallbacks.

DeepSeek handles broad, repetitive, lower-cost work: compact indicator review for every tracked symbol, broad screening, watchlist triage, first-pass risk flags, and daily backtest BUY/SELL/HOLD order decisions.

OpenAI handles smaller, higher-quality synthesis: portfolio review, backtest interpretation after the daily DeepSeek decisions are complete, risk diagnosis, final action priorities, and review language.

The output JSON includes `llm_analysis.division_of_labor` so the split is auditable.

## Stock Universe

Set `STOCK_SYMBOLS=auto` to use Yahoo Finance screeners such as `small_cap_gainers`, `aggressive_small_caps`, `growth_technology_stocks`, and `undervalued_growth_stocks`.

If the screener call fails, the app falls back to a broader watchlist that includes mega-cap, growth, small-cap, and high-beta names. Use `MAX_SYMBOLS` to control cost and runtime.

## Dashboard Controls

The static dashboard includes local controls for adding funds, pausing/resuming the displayed simulation, and selling all visible paper positions.

Because GitHub Pages is static, these buttons update the browser-local view with `localStorage`. Server-side scheduled runs are controlled by GitHub repository variables such as `PAPER_CASH_DELTA`, `PAPER_CASH_DELTA_ID`, `PAPER_TRADING_ENABLED`, and `PAPER_FORCE_LIQUIDATE`.

For server-side deposits, set `PAPER_CASH_DELTA` to a positive amount and change `PAPER_CASH_DELTA_ID` to a new unique value. For withdrawals, use a negative `PAPER_CASH_DELTA`. The ID prevents the 30-minute workflow from applying the same cash movement repeatedly.

## Trading Skill Pack

Put Markdown strategy notes in `skills/`. Every LLM analysis run loads those files and passes them as trading skill context.

Use this for your own screening rules, risk preferences, market-regime filters, and position-sizing playbooks.

## Paper Trading Logic

The simulator replays recent daily history and trades only inside the paper portfolio.

Buy logic favors:

- Price above SMA20
- SMA20 above SMA60
- RSI in a healthy momentum range
- Positive daily return
- Expanding volume

Sell logic includes:

- Stop loss
- Take profit
- Price breaking below SMA20
- Overbought reversal risk

This is a simulation for analysis and iteration. It does not place real trades and cannot guarantee returns.

## Backtesting

The dashboard has a Backtest page where you can choose start date, end date, trading style, and initial capital. The browser runs an immediate local backtest preview from `docs/data/history.json`.

The scheduled backend also runs a formal backtest using repository variables such as `BACKTEST_START_DATE`, `BACKTEST_END_DATE`, `BACKTEST_STYLE`, and `BACKTEST_INITIAL_CAPITAL`. With `AI_BACKTEST_ENABLED=true`, each trading day calls DeepSeek with only data available up to that day, applies the returned simulated orders, then calls OpenAI once at the end for a compressed high-quality review. That output is saved to `docs/data/backtest_latest.json` and included in `docs/data/latest.json`.

## Review Ledger

Each scheduled run writes a daily review entry to `docs/data/ledger.json`. The dashboard Review Ledger page renders those entries as a calendar-style view so you can revisit prior decisions and results.

## GitHub Pages

The workflow deploys the generated `/docs` folder to GitHub Pages through the official Pages artifact flow.

In the repository settings, enable GitHub Pages with GitHub Actions as the source.

## Output Shape

`docs/data/latest.json` contains:

```json
{
  "generated_at": "2026-05-22T00:00:00+00:00",
  "symbols": ["AAPL", "NVDA"],
  "stocks": [
    {
      "symbol": "AAPL",
      "company_name": "Apple Inc.",
      "indicators": {
        "price": 123.45,
        "sma20": 120.12,
        "sma60": 118.34,
        "rsi14": 54.2,
        "daily_return_pct": 1.25,
        "volume": 12345678,
        "volume_change_pct": 8.8
      },
      "rule_based": {
        "rating": "bullish",
        "confidence": "medium",
        "summary": "Price is above SMA20; SMA20 is above SMA60"
      }
    }
  ],
  "portfolio": {
    "mode": "paper_trading",
    "cash": 0,
    "balance": 0,
    "total_return_pct": 0,
    "holdings": [],
    "trade_history": [],
    "cash_movements": [],
    "equity_curve": []
  },
  "llm_analysis": {},
  "disclaimer": "Analysis and alerts only. No automatic trading."
}
```
