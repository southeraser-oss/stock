from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backtest import run_backtest  # noqa: E402
from universe import load_symbols  # noqa: E402
from backend.db import cash_movements, init_db, insert_cash_movement, insert_json, latest_backtests  # noqa: E402


load_dotenv(ROOT / ".env")
init_db()

app = FastAPI(title="Stock Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BacktestRequest(BaseModel):
    start_date: str
    end_date: str
    style: Literal["momentum", "growth", "conservative", "high_volatility"] = "momentum"
    initial_capital: float = Field(gt=0)
    max_positions: int = Field(default=5, ge=1, le=20)
    allocation_pct: float = Field(default=0.2, gt=0, le=1)
    max_symbols: int = Field(default=35, ge=1, le=200)
    ai_enabled: bool = True


class CashMovementRequest(BaseModel):
    amount: float = Field(gt=0)
    note: str | None = None


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "scheduler": {
            "github_actions_cron": "*/30 * * * *",
            "workflow": ".github/workflows/analyze.yml",
            "interactive_api": "/api/backtests",
        },
    }


@app.post("/api/backtests")
def create_backtest(request: BacktestRequest) -> dict[str, object]:
    previous_ai = os.getenv("AI_BACKTEST_ENABLED")
    previous_max = os.getenv("MAX_SYMBOLS")
    os.environ["AI_BACKTEST_ENABLED"] = "true" if request.ai_enabled else "false"
    os.environ["MAX_SYMBOLS"] = str(request.max_symbols)
    try:
        symbols, universe = load_symbols()
        histories = _fetch_histories(symbols[: request.max_symbols])
        result = run_backtest(
            histories=histories,
            start_date=request.start_date,
            end_date=request.end_date,
            style=request.style,
            initial_capital=request.initial_capital,
            max_positions=request.max_positions,
            allocation_pct=request.allocation_pct,
            fee_bps=float(os.getenv("PAPER_FEE_BPS", "2")),
        )
    finally:
        _restore_env("AI_BACKTEST_ENABLED", previous_ai)
        _restore_env("MAX_SYMBOLS", previous_max)

    run_id = insert_json(
        "backtest_runs",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "start_date": request.start_date,
            "end_date": request.end_date,
            "style": request.style,
            "initial_capital": request.initial_capital,
        },
        "result_json",
        result,
    )
    return {"id": run_id, "universe": universe, "result": result}


@app.get("/api/backtests")
def list_backtests() -> dict[str, object]:
    return {"items": latest_backtests()}


@app.post("/api/cash/deposit")
def deposit(request: CashMovementRequest) -> dict[str, object]:
    row_id = insert_cash_movement(datetime.now(UTC).isoformat(), "DEPOSIT", request.amount, request.note)
    return {"id": row_id, "type": "DEPOSIT", "amount": request.amount}


@app.post("/api/cash/withdraw")
def withdraw(request: CashMovementRequest) -> dict[str, object]:
    row_id = insert_cash_movement(datetime.now(UTC).isoformat(), "WITHDRAWAL", request.amount, request.note)
    return {"id": row_id, "type": "WITHDRAWAL", "amount": request.amount}


@app.get("/api/cash")
def list_cash() -> dict[str, object]:
    return {"items": cash_movements()}


if (ROOT / "docs").exists():
    app.mount("/", StaticFiles(directory=ROOT / "docs", html=True), name="docs")


def _fetch_histories(symbols: list[str]) -> dict[str, object]:
    histories = {}
    for symbol in symbols:
        try:
            history = yf.Ticker(symbol).history(
                period=os.getenv("HISTORY_PERIOD", "2y"),
                interval="1d",
                auto_adjust=False,
            )
            if not history.empty:
                histories[symbol] = history
        except Exception:
            continue
    return histories


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous
