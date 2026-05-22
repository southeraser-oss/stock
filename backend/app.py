from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import requests
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
ANALYSIS_LOG_PATH = DATA_DIR / "analysis_log.json"
REVIEW_LOG_PATH = DATA_DIR / "review_log.json"

sys.path.insert(0, str(ROOT / "src"))

from backtest import run_backtest  # noqa: E402
from indicators import calculate_indicators, rule_based_signal  # noqa: E402
from llm import analyze_with_llms  # noqa: E402
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


class AnalysisRequest(BaseModel):
    max_symbols: int = Field(default=35, ge=1, le=200)
    note: str | None = None
    cash_intent: Literal["none", "add", "withdraw"] = "none"
    cash_amount: float = Field(default=0, ge=0)
    cash_currency: str = "SGD"


class HoldingInput(BaseModel):
    symbol: str
    company_name: str | None = None
    shares: float = Field(gt=0)
    market_value: float = Field(ge=0)


class HoldingsUploadRequest(BaseModel):
    holdings: list[HoldingInput]
    note: str | None = None


class HoldingUpdateRequest(BaseModel):
    symbol: str
    shares: float = Field(gt=0)


class ReviewRequest(BaseModel):
    start_date: str
    end_date: str
    focus: str | None = None


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
            "github_actions_cron": None,
            "workflow": ".github/workflows/analyze.yml",
            "role": "manual analysis by default; workflow_dispatch remains optional",
            "interactive_api": "/api/analysis/run",
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


@app.post("/api/analysis/run")
def run_manual_analysis(request: AnalysisRequest) -> dict[str, Any]:
    symbols, universe = load_symbols()
    histories: dict[str, Any] = {}
    stocks = [_analyze_symbol(symbol, histories) for symbol in symbols[: request.max_symbols]]
    successful_stocks = [stock for stock in stocks if not stock.get("error")]
    portfolio = _paper_state()
    backtest = _read_json(BACKTEST_PATH, {})
    analysis_request = {
        "note": request.note or "",
        "cash_intent": request.cash_intent,
        "cash_amount": request.cash_amount,
        "cash_currency": request.cash_currency,
    }
    recent_log = _read_json(ANALYSIS_LOG_PATH, {"entries": []}).get("entries", [])
    llm_analysis = (
        analyze_with_llms(successful_stocks, portfolio, backtest, analysis_request, recent_log)
        if successful_stocks
        else {}
    )
    entry = {
        "id": _log_id(),
        "created_at": _now(),
        "note": request.note or "",
        "cash_intent": request.cash_intent,
        "cash_amount": request.cash_amount,
        "cash_currency": request.cash_currency,
        "symbols": [row.get("symbol") for row in successful_stocks],
        "universe": universe,
        "portfolio_snapshot": portfolio,
        "llm_analysis": llm_analysis,
    }
    log = _append_log(ANALYSIS_LOG_PATH, entry)
    latest = _dashboard_payload()
    latest["symbols"] = symbols[: request.max_symbols]
    latest["universe"] = universe
    latest["stocks"] = stocks
    latest["llm_analysis"] = llm_analysis
    latest["analysis_log"] = log
    latest["generated_at"] = entry["created_at"]
    _write_json(LATEST_PATH, {k: v for k, v in latest.items() if k not in {"backend", "history"}})
    return {"entry": entry, "analysis_log": log, "dashboard": _dashboard_payload()}


@app.get("/api/analysis/log")
def analysis_log() -> dict[str, Any]:
    return _read_json(ANALYSIS_LOG_PATH, {"entries": []})


@app.post("/api/portfolio/upload-holdings")
def upload_holdings(request: HoldingsUploadRequest) -> dict[str, Any]:
    uploaded_at = _now()
    state_data = _paper_state()
    state_data["holdings"] = [_uploaded_holding(row, uploaded_at) for row in request.holdings]
    state_data["uploaded_holdings_note"] = request.note or ""
    state_data["updated_at"] = uploaded_at
    _save_paper_state(state_data)
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/portfolio/update-holding")
def update_holding(request: HoldingUpdateRequest) -> dict[str, Any]:
    state_data = _paper_state()
    symbol = request.symbol.strip().upper()
    updated = False
    for holding in state_data.get("holdings", []):
        if str(holding.get("symbol", "")).upper() != symbol:
            continue
        price = float(holding.get("current_price") or 0)
        avg_price = float(holding.get("avg_price") or price or 0)
        holding["shares"] = request.shares
        holding["market_value"] = round(request.shares * price, 2)
        holding["unrealized_pnl"] = round((price - avg_price) * request.shares, 2)
        holding["unrealized_return_pct"] = round(((price / avg_price) - 1) * 100, 2) if avg_price else 0
        holding["updated_at"] = _now()
        updated = True
        break
    if not updated:
        raise HTTPException(status_code=404, detail=f"Holding not found: {symbol}")
    state_data["updated_at"] = _now()
    _save_paper_state(state_data)
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/portfolio/refresh-prices")
def refresh_holding_prices() -> dict[str, Any]:
    state_data = _paper_state()
    refreshed_at = _now()
    errors: list[str] = []
    for holding in state_data.get("holdings", []):
        try:
            price = _latest_market_price(str(holding.get("symbol", "")))
            if price <= 0:
                raise ValueError("No latest price")
            shares = float(holding.get("shares") or 0)
            avg_price = float(holding.get("avg_price") or price)
            holding["current_price"] = round(price, 4)
            holding["market_value"] = round(shares * price, 2)
            holding["unrealized_pnl"] = round((price - avg_price) * shares, 2)
            holding["unrealized_return_pct"] = round(((price / avg_price) - 1) * 100, 2) if avg_price else 0
            holding["price_refreshed_at"] = refreshed_at
        except Exception as exc:
            errors.append(f"{holding.get('symbol')}: {exc}")
    state_data["updated_at"] = refreshed_at
    _save_paper_state(state_data)
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload(), "errors": errors}


@app.post("/api/review")
def review_period(request: ReviewRequest) -> dict[str, Any]:
    entry = _period_review(request, _dashboard_payload())
    log = _append_log(REVIEW_LOG_PATH, entry)
    return {"entry": entry, "review_log": log}


@app.get("/api/review/log")
def review_log() -> dict[str, Any]:
    return _read_json(REVIEW_LOG_PATH, {"entries": []})


@app.post("/api/paper/add-funds")
def add_funds(request: CashMovementRequest) -> dict[str, Any]:
    state_data = _paper_state()
    state_data["cash"] = float(state_data.get("cash", 0)) + request.amount
    _append_cash_movement(state_data, "DEPOSIT", request.amount, request.note)
    _save_paper_state(state_data)
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/withdraw")
def withdraw_funds(request: CashMovementRequest) -> dict[str, Any]:
    state_data = _paper_state()
    available = float(state_data.get("cash", 0))
    if request.amount > available:
        raise HTTPException(status_code=400, detail=f"Withdrawal exceeds available cash: {available:.2f}")
    state_data["cash"] = available - request.amount
    _append_cash_movement(state_data, "WITHDRAWAL", request.amount, request.note)
    _save_paper_state(state_data)
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/pause")
def pause_paper() -> dict[str, Any]:
    state_data = _paper_state()
    state_data["trading_enabled"] = False
    state_data.setdefault("strategy", {})["trading_enabled"] = False
    state_data["updated_at"] = _now()
    _save_paper_state(state_data, record_equity=False)
    return {"portfolio": _paper_state(), "dashboard": _dashboard_payload()}


@app.post("/api/paper/resume")
def resume_paper() -> dict[str, Any]:
    state_data = _paper_state()
    state_data["trading_enabled"] = True
    state_data.setdefault("strategy", {})["trading_enabled"] = True
    state_data["updated_at"] = _now()
    _save_paper_state(state_data, record_equity=False)
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
    _save_paper_state(state_data)
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
    payload["analysis_log"] = _read_json(ANALYSIS_LOG_PATH, {"entries": []})
    payload["review_log"] = _read_json(REVIEW_LOG_PATH, {"entries": []})
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


def _append_equity_point(state_data: dict[str, Any]) -> dict[str, Any]:
    curve = list(state_data.get("equity_curve") or [])
    point = {
        "date": _now(),
        "balance": round(float(state_data.get("balance") or 0), 2),
        "cash": round(float(state_data.get("cash") or 0), 2),
    }
    if not curve or float(curve[-1].get("balance", -1)) != point["balance"] or float(curve[-1].get("cash", -1)) != point["cash"]:
        curve.append(point)
    state_data["equity_curve"] = curve[-250:]
    return state_data


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
            "cron": "manual analysis by button",
            "interval_minutes": None,
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


def _analyze_symbol(symbol: str, histories: dict[str, Any]) -> dict[str, Any]:
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period=os.getenv("HISTORY_PERIOD", "2y"), interval="1d", auto_adjust=False)
        if history.empty:
            return {"symbol": symbol, "error": "No historical price data returned"}
        histories[symbol] = history
        indicators = calculate_indicators(history)
        return {
            "symbol": symbol,
            "company_name": _safe_company_name(ticker),
            "indicators": indicators,
            "rule_based": rule_based_signal(indicators),
            "analyst_summary": _safe_analyst_summary(ticker),
            "authority_notes": "Analyst recommendation data is fetched from Yahoo Finance/yfinance when available and used as third-party context, not as a guarantee.",
        }
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}


def _safe_company_name(ticker: yf.Ticker) -> str | None:
    try:
        info = ticker.get_info()
        return info.get("shortName") or info.get("longName")
    except Exception:
        return None


def _safe_analyst_summary(ticker: yf.Ticker) -> dict[str, Any] | None:
    summary: dict[str, Any] = {}
    try:
        recommendations_summary = ticker.get_recommendations_summary()
        if recommendations_summary is not None and not recommendations_summary.empty:
            summary["recommendations_summary"] = recommendations_summary.tail(4).reset_index().to_dict(orient="records")
    except Exception:
        pass
    try:
        upgrades = ticker.get_upgrades_downgrades()
        if upgrades is not None and not upgrades.empty:
            summary["recent_upgrades_downgrades"] = upgrades.tail(5).reset_index().to_dict(orient="records")
    except Exception:
        pass
    try:
        info = ticker.get_info()
        for key in ("recommendationMean", "recommendationKey", "targetMeanPrice", "targetMedianPrice", "numberOfAnalystOpinions"):
            if key in info:
                summary[key] = info.get(key)
    except Exception:
        pass
    return json.loads(json.dumps(summary, default=str)) if summary else None


def _latest_market_price(symbol: str) -> float:
    history = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
    if history.empty:
        return 0
    close = history["Close"].dropna()
    return float(close.iloc[-1]) if not close.empty else 0


def _uploaded_holding(row: HoldingInput, uploaded_at: str) -> dict[str, Any]:
    market_value = float(row.market_value)
    shares = float(row.shares)
    current_price = market_value / shares if shares else 0
    return {
        "symbol": row.symbol.strip().upper(),
        "company_name": row.company_name,
        "shares": shares,
        "avg_price": current_price,
        "current_price": current_price,
        "market_value": market_value,
        "unrealized_pnl": 0,
        "unrealized_return_pct": 0,
        "opened_at": uploaded_at,
        "source": "uploaded_current_holding",
    }


def _period_review(request: ReviewRequest, dashboard_data: dict[str, Any]) -> dict[str, Any]:
    portfolio = dashboard_data.get("portfolio", {})
    trades = [
        row
        for row in portfolio.get("trade_history", [])
        if request.start_date <= str(row.get("date", ""))[:10] <= request.end_date
    ]
    analyses = [
        row
        for row in dashboard_data.get("analysis_log", {}).get("entries", [])
        if request.start_date <= str(row.get("created_at", ""))[:10] <= request.end_date
    ]
    context = {
        "period": [request.start_date, request.end_date],
        "focus": request.focus or "",
        "current_portfolio": portfolio,
        "period_trades": trades,
        "period_analysis_count": len(analyses),
        "recent_analysis": analyses[-5:],
    }
    review = _call_openai_review(context) or _fallback_review(context)
    return {
        "id": _log_id(),
        "created_at": _now(),
        "start_date": request.start_date,
        "end_date": request.end_date,
        "focus": request.focus or "",
        "trade_count": len(trades),
        "analysis_count": len(analyses),
        "review": review,
    }


def _call_openai_review(context: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    payload = {
        "model": os.getenv("MODEL_OPENAI", "gpt-4.1-mini"),
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are a paper-trading review analyst. Review the selected period, "
                            "explain what worked, what failed, and what to adjust. Return JSON only "
                            "with keys: summary, lessons, mistakes, next_adjustments, risk_notes."
                        ),
                    }
                ],
            },
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False)}]},
        ],
        "text": {"format": _period_review_schema()},
        "temperature": 0.2,
        "max_output_tokens": int(os.getenv("OPENAI_REVIEW_MAX_OUTPUT_TOKENS", "1000")),
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("output_text") or _extract_openai_text(data)
        parsed = json.loads(content)
        parsed["status"] = "ok"
        return parsed
    except Exception as exc:
        return {
            "status": "error",
            "summary": "OpenAI review failed.",
            "error": str(exc),
            "lessons": [],
            "mistakes": [],
            "next_adjustments": [],
            "risk_notes": [],
        }


def _period_review_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "period_review",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "lessons": {"type": "array", "items": {"type": "string"}},
                "mistakes": {"type": "array", "items": {"type": "string"}},
                "next_adjustments": {"type": "array", "items": {"type": "string"}},
                "risk_notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "lessons", "mistakes", "next_adjustments", "risk_notes"],
        },
    }


def _fallback_review(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "rule_based_fallback",
        "summary": f"Reviewed {len(context['period_trades'])} trades and {context['period_analysis_count']} saved analyses in the selected period.",
        "lessons": ["Add API keys to enable AI-generated period review."],
        "mistakes": [],
        "next_adjustments": ["Run manual AI analysis after uploading current holdings."],
        "risk_notes": ["This is paper-trading review only, not real-money advice."],
    }


def _append_log(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    log = _read_json(path, {"entries": []})
    entries = list(log.get("entries") or [])
    entries.append(entry)
    if path == ANALYSIS_LOG_PATH:
        cutoff = datetime.now(UTC) - timedelta(days=10)
        entries = [row for row in entries if _parse_time(row.get("created_at")) >= cutoff]
    log["entries"] = entries[-250:]
    _write_json(path, log)
    return log


def _log_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")


def _extract_openai_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _parse_time(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


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


def _save_paper_state(state_data: dict[str, Any], record_equity: bool = True) -> None:
    normalized = _normalize_paper_state(state_data)
    if record_equity:
        normalized = _append_equity_point(normalized)
    _write_json(PAPER_STATE_PATH, normalized)


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _now() -> str:
    return datetime.now(UTC).isoformat()
