from __future__ import annotations

import os
from typing import Any

import yfinance as yf


FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA", "AMD", "AVGO", "NFLX",
    "PLTR", "SOFI", "HOOD", "COIN", "RDDT", "SNOW", "CRWD", "NET", "DDOG", "MDB",
    "SHOP", "MELI", "SE", "ROKU", "U", "PATH", "HIMS", "CELH", "CAVA", "DUOL",
    "RKLB", "IONQ", "ASTS", "RGTI", "SOUN", "AI", "SMCI", "APP", "AFRM", "UPST",
    "0700.HK", "9988.HK", "3690.HK", "9618.HK", "1810.HK", "1024.HK", "2015.HK", "9868.HK",
    "600519.SS", "601318.SS", "600036.SS", "600276.SS", "603259.SS", "688981.SS",
    "000858.SZ", "002594.SZ", "300750.SZ", "300760.SZ", "000333.SZ", "002415.SZ",
]

SCREENER_NAMES = [
    "small_cap_gainers",
    "aggressive_small_caps",
    "growth_technology_stocks",
    "undervalued_growth_stocks",
    "day_gainers",
]


def load_symbols() -> tuple[list[str], dict[str, Any]]:
    raw_symbols = os.getenv("STOCK_SYMBOLS", "auto").strip()
    max_symbols = _int_env("MAX_SYMBOLS", 35)

    if raw_symbols and raw_symbols.lower() not in {"auto", "screeners"}:
        symbols = _dedupe([symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()])
        return symbols[:max_symbols], {"mode": "manual", "requested": raw_symbols, "count": len(symbols[:max_symbols])}

    screener_symbols, errors = _symbols_from_screeners()
    symbols = _dedupe([*screener_symbols, *FALLBACK_SYMBOLS])[:max_symbols]
    return symbols, {
        "mode": "auto_screeners",
        "screeners": SCREENER_NAMES,
        "screener_count": len(screener_symbols),
        "fallback_count": len(FALLBACK_SYMBOLS),
        "count": len(symbols),
        "markets": ["US", "HK", "CN"],
        "errors": errors,
    }


def _symbols_from_screeners() -> tuple[list[str], list[str]]:
    if os.getenv("ENABLE_YFINANCE_SCREENERS", "true").lower() != "true":
        return [], ["Yahoo screeners disabled by ENABLE_YFINANCE_SCREENERS=false"]

    symbols: list[str] = []
    errors: list[str] = []
    per_screener = _int_env("SCREENER_COUNT", 12)

    for name in SCREENER_NAMES:
        try:
            data = yf.screen(name, count=per_screener)
            quotes = data.get("quotes", []) if isinstance(data, dict) else []
            symbols.extend(str(row.get("symbol", "")).upper() for row in quotes if row.get("symbol"))
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    return _dedupe(symbols), errors


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
