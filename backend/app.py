from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
INDEX_PATH = DOCS_DIR / "index.html"
LATEST_PATH = DATA_DIR / "latest.json"
PAPER_STATE_PATH = DATA_DIR / "paper_state.json"
BACKTEST_PATH = DATA_DIR / "backtest_latest.json"
LEDGER_PATH = DATA_DIR / "ledger.json"
HISTORY_PATH = DATA_DIR / "history.json"

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


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI is missing.")
    return FileResponse(INDEX_PATH)


@app.head("/", include_in_schema=False)
def index_head() -> FileResponse:
    return index()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": _now(),
        "deployment": "fastapi_dynamic",
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "database": str(ROOT / "data" / "stock_dashboard.sqlite3"),
        "scheduler": {
            "github_actions_cron": "*/30 * * * *",
            "workflow": ".github/workflows/analyze.yml",
            "role": "optional scheduled analysis only",
            "interactive_api": "/api/backtest",
        },
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return _dashboard_payload()


@app.get("/api/state")
def state() -> dict[str, Any]:
    return _dashboard_payload()


@app.post("/api/backtest")
def create_backtest(request: BacktestRequest) -> dict[str, Any]:
    return _run_interactive_backtest(request)


@app.post("/api/backtests")
def create_backtest_alias(request: BacktestRequest) -> dict[str, Any]:
    return _run_interactive_backtest(request)


@app.get("/api/backtests")
def list_backtests() -> dict[str, Any]:
    return {"items": latest_backtests()}


@app.post("/api/paper/add-funds")
def add_funds(request: CashMovementRequest) -> dict[str, Any]:
    state_data = _paper_state()
    state_data["cash"] = float(state_data.get("cash", 0)) + request.amount
    _append_cash_movement(state_data, "DEPOSIT", request.amount, request.note)
    _save_paper_state(_normalize_paper_state(state_data))
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/withdraw")
def withdraw_funds(request: CashMovementRequest) -> dict[str, Any]:
    state_data = _paper_state()
    available = float(state_data.get("cash", 0))
    if request.amount > available:
        raise HTTPException(status_code=400, detail=f"Withdrawal exceeds available cash: {available:.2f}")
    state_data["cash"] = available - request.amount
    _append_cash_movement(state_data, "WITHDRAWAL", request.amount, request.note)
    _save_paper_state(_normalize_paper_state(state_data))
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/pause")
def pause_paper() -> dict[str, Any]:
    state_data = _paper_state()
    state_data["trading_enabled"] = False
    state_data.setdefault("strategy", {})["trading_enabled"] = False
    state_data["updated_at"] = _now()
    _save_paper_state(_normalize_paper_state(state_data))
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/resume")
def resume_paper() -> dict[str, Any]:
    state_data = _paper_state()
    state_data["trading_enabled"] = True
    state_data.setdefault("strategy", {})["trading_enabled"] = True
    state_data["updated_at"] = _now()
    _save_paper_state(_normalize_paper_state(state_data))
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/sell-all")
def sell_all() -> dict[str, Any]:
    state_data = _paper_state()
    sold_at = _now()
    holdings = list(state_data.get("holdings") or [])
    trade_history = list(state_data.get("trade_history") or [])
    proceeds = 0.0
    for holding in holdings:
        market_value = float(holding.get("market_value") or 0)
        proceeds += market_value
        trade_history.append(
            {
                "date": sold_at,
                "symbol": holding.get("symbol"),
                "side": "SELL",
                "shares": holding.get("shares", 0),
                "price": holding.get("current_price") or holding.get("avg_price") or 0,
                "fee": 0,
                "pnl": holding.get("unrealized_pnl"),
                "reason": "Manual sell-all from FastAPI dashboard",
            }
        )
    state_data["cash"] = float(state_data.get("cash", 0)) + proceeds
    state_data["holdings"] = []
    state_data["trade_history"] = trade_history
    state_data["updated_at"] = sold_at
    _save_paper_state(_normalize_paper_state(state_data))
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/cash/deposit")
def deposit_alias(request: CashMovementRequest) -> dict[str, Any]:
    return add_funds(request)


@app.post("/api/cash/withdraw")
def withdraw_alias(request: CashMovementRequest) -> dict[str, Any]:
    return withdraw_funds(request)


@app.get("/api/cash")
def list_cash() -> dict[str, Any]:
    return {"items": cash_movements()}


if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")


def _run_interactive_backtest(request: BacktestRequest) -> dict[str, Any]:
    previous_ai = os.getenv("AI_BACKTEST_ENABLED")
    previous_max = os.getenv("MAX_SYMBOLS")
    os.environ["AI_BACKTEST_ENABLED"] = "true" if request.ai_enabled else "false"
    os.environ["MAX_SYMBOLS"] = str(request.max_symbols)
    try:
        symbols, universe = load_symbols()
        histories = _fetch_histories(symbols[: request.max_symbols])
        if not histories:
            raise HTTPException(status_code=502, detail="No market history was returned by the data provider.")
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
            "created_at": _now(),
            "start_date": request.start_date,
            "end_date": request.end_date,
            "style": request.style,
            "initial_capital": request.initial_capital,
        },
        "result_json",
        result,
    )
    _write_json(BACKTEST_PATH, result)
    return {"id": run_id, "universe": universe, "result": result}


def _dashboard_payload() -> dict[str, Any]:
    latest = _read_json(LATEST_PATH, _empty_dashboard())
    payload = deepcopy(latest)
    payload["portfolio"] = _paper_state()
    payload["backtest"] = _read_json(BACKTEST_PATH, payload.get("backtest") or {})
    payload["ledger"] = _read_json(LEDGER_PATH, payload.get("ledger") or {"entries": []})
    payload["history"] = _read_json(HISTORY_PATH, payload.get("history") or {})
    payload["backend"] = health()
    payload.setdefault("generated_at", _now())
    return payload


def _paper_state() -> dict[str, Any]:
    return _normalize_paper_state(_read_json(PAPER_STATE_PATH, _default_paper_state()))


def _default_paper_state() -> dict[str, Any]:
    return {
        "mode": "paper_trading",
        "cash": 0,
        "balance": 0,
        "total_return_pct": 0,
        "holdings": [],
        "trade_history": [],
        "cash_movements": [],
        "equity_curve": [],
        "updated_at": _now(),
        "trading_enabled": True,
        "strategy": {
            "max_positions": int(os.getenv("PAPER_MAX_POSITIONS", "4")),
            "allocation_pct": float(os.getenv("PAPER_ALLOCATION_PCT", "0.24")),
            "fee_bps": float(os.getenv("PAPER_FEE_BPS", "2")),
            "trading_enabled": True,
            "force_liquidate": False,
            "buy_logic": "Buy strongest current candidates above trend confirmation score.",
            "sell_logic": "Sell on trend break, overbought reversal, stop loss, take profit, or force liquidation.",
        },
    }


def _normalize_paper_state(state_data: dict[str, Any]) -> dict[str, Any]:
    normalized = {**_default_paper_state(), **state_data}
    normalized["holdings"] = list(normalized.get("holdings") or [])
    normalized["trade_history"] = list(normalized.get("trade_history") or [])
    normalized["cash_movements"] = list(normalized.get("cash_movements") or [])
    normalized["equity_curve"] = list(normalized.get("equity_curve") or [])
    holdings_value = sum(float(row.get("market_value") or 0) for row in normalized["holdings"])
    normalized["balance"] = float(normalized.get("cash") or 0) + holdings_value
    deposits = sum(float(row.get("amount") or 0) for row in normalized["cash_movements"] if row.get("type") == "DEPOSIT")
    withdrawals = sum(float(row.get("amount") or 0) for row in normalized["cash_movements"] if row.get("type") == "WITHDRAWAL")
    net_funds = deposits - withdrawals
    normalized["total_return_pct"] = ((normalized["balance"] - net_funds) / net_funds * 100) if net_funds > 0 else 0
    normalized["updated_at"] = normalized.get("updated_at") or _now()
    normalized.setdefault("strategy", {})["trading_enabled"] = bool(normalized.get("trading_enabled", True))
    return normalized


def _append_cash_movement(state_data: dict[str, Any], movement_type: str, amount: float, note: str | None) -> None:
    created_at = _now()
    state_data.setdefault("cash_movements", []).append(
        {"date": created_at, "type": movement_type, "amount": amount, "note": note or ""}
    )
    state_data["updated_at"] = created_at
    insert_cash_movement(created_at, movement_type, amount, note)


def _empty_dashboard() -> dict[str, Any]:
    return {
        "generated_at": _now(),
        "symbols": [],
        "stocks": [],
        "llm_analysis": {},
        "automation": {
            "scheduler": "FastAPI dynamic app",
            "cron": "optional GitHub Actions: */30 * * * *",
            "interval_minutes": 30,
            "event_name": "interactive",
        },
        "portfolio": _default_paper_state(),
        "backtest": {},
        "ledger": {"entries": []},
    }


def _fetch_histories(symbols: list[str]) -> dict[str, Any]:
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


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_paper_state(state_data: dict[str, Any]) -> None:
    _write_json(PAPER_STATE_PATH, state_data)


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _now() -> str:
    return datetime.now(UTC).isoformat()
