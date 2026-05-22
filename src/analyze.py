from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import yfinance as yf
from dotenv import load_dotenv

from backtest import run_backtest, serialize_histories
from indicators import calculate_indicators, rule_based_signal
from ledger import append_ledger
from llm import analyze_with_llms
from report import ensure_dashboard_html, write_data_json, write_latest_json
from simulator import simulate_trading
from universe import load_symbols


def main() -> None:
    load_dotenv()
    symbols, universe = load_symbols()
    histories: dict[str, Any] = {}
    stocks = [_analyze_symbol(symbol, histories) for symbol in symbols]
    successful_stocks = [stock for stock in stocks if not stock.get("error")]
    portfolio = simulate_trading(
        stocks=successful_stocks,
        cash_delta=_float_env("PAPER_CASH_DELTA", 0),
        cash_delta_id=os.getenv("PAPER_CASH_DELTA_ID", "default"),
        max_positions=_int_env("PAPER_MAX_POSITIONS", 4),
        allocation_pct=_float_env("PAPER_ALLOCATION_PCT", 0.24),
        fee_bps=_float_env("PAPER_FEE_BPS", 2),
        trading_enabled=_bool_env("PAPER_TRADING_ENABLED", True),
        force_liquidate=_bool_env("PAPER_FORCE_LIQUIDATE", False),
    )
    backtest = run_backtest(
        histories=histories,
        start_date=os.getenv("BACKTEST_START_DATE", "2026-01-01"),
        end_date=os.getenv("BACKTEST_END_DATE") or datetime.now(UTC).date().isoformat(),
        style=os.getenv("BACKTEST_STYLE", "momentum"),
        initial_capital=_float_env("BACKTEST_INITIAL_CAPITAL", 100_000),
        max_positions=_int_env("BACKTEST_MAX_POSITIONS", 5),
        allocation_pct=_float_env("BACKTEST_ALLOCATION_PCT", 0.2),
        fee_bps=_float_env("PAPER_FEE_BPS", 2),
    )
    llm_analysis = analyze_with_llms(successful_stocks, portfolio, backtest) if successful_stocks else {}
    ledger = append_ledger(portfolio, backtest, llm_analysis)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbols": symbols,
        "universe": universe,
        "stocks": stocks,
        "portfolio": portfolio,
        "backtest": backtest,
        "llm_analysis": llm_analysis,
        "ledger": ledger,
        "disclaimer": "Analysis and alerts only. No automatic trading.",
    }

    latest_path = write_latest_json(payload)
    write_data_json("history.json", serialize_histories(histories))
    write_data_json("backtest_latest.json", backtest)
    write_data_json("ledger.json", ledger)
    html_path = ensure_dashboard_html()
    print(f"Wrote {latest_path}")
    print(f"Dashboard ready at {html_path}")


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
        }
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}


def _safe_company_name(ticker: yf.Ticker) -> str | None:
    try:
        info = ticker.get_info()
        return info.get("shortName") or info.get("longName")
    except Exception:
        return None


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
