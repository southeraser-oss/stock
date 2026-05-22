from __future__ import annotations

import math
from typing import Any

import pandas as pd


def calculate_indicators(history: pd.DataFrame) -> dict[str, Any]:
    """Calculate dashboard indicators from daily OHLCV history."""
    if history.empty:
        return {}

    frame = history.copy()
    close = _series(frame, "Close")
    volume = _series(frame, "Volume")

    daily_return = close.pct_change().iloc[-1] * 100 if len(close) >= 2 else None
    volume_change = volume.pct_change().iloc[-1] * 100 if len(volume) >= 2 else None

    return {
        "price": _clean_number(close.iloc[-1]),
        "sma20": _clean_number(close.rolling(20).mean().iloc[-1]),
        "sma60": _clean_number(close.rolling(60).mean().iloc[-1]),
        "rsi14": _clean_number(_rsi(close, 14).iloc[-1]),
        "daily_return_pct": _clean_number(daily_return),
        "volume": int(volume.iloc[-1]) if pd.notna(volume.iloc[-1]) else None,
        "volume_change_pct": _clean_number(volume_change),
    }


def rule_based_signal(indicators: dict[str, Any]) -> dict[str, str]:
    price = indicators.get("price")
    sma20 = indicators.get("sma20")
    sma60 = indicators.get("sma60")
    rsi14 = indicators.get("rsi14")
    daily_return = indicators.get("daily_return_pct")
    volume_change = indicators.get("volume_change_pct")

    score = 0
    reasons: list[str] = []

    if _has_numbers(price, sma20) and price > sma20:
        score += 1
        reasons.append("Price is above SMA20")
    elif _has_numbers(price, sma20):
        score -= 1
        reasons.append("Price is below SMA20")

    if _has_numbers(sma20, sma60) and sma20 > sma60:
        score += 1
        reasons.append("SMA20 is above SMA60")
    elif _has_numbers(sma20, sma60):
        score -= 1
        reasons.append("SMA20 is below SMA60")

    if _has_numbers(rsi14) and rsi14 < 30:
        score += 1
        reasons.append("RSI14 suggests oversold conditions")
    elif _has_numbers(rsi14) and rsi14 > 70:
        score -= 1
        reasons.append("RSI14 suggests overbought conditions")

    if _has_numbers(daily_return) and daily_return > 2:
        score += 1
        reasons.append("Strong positive daily return")
    elif _has_numbers(daily_return) and daily_return < -2:
        score -= 1
        reasons.append("Weak negative daily return")

    if _has_numbers(volume_change) and volume_change > 30:
        reasons.append("Volume is meaningfully above the prior day")

    if score >= 2:
        rating = "bullish"
    elif score <= -2:
        rating = "bearish"
    else:
        rating = "neutral"

    return {
        "rating": rating,
        "confidence": "medium" if abs(score) >= 2 else "low",
        "summary": "; ".join(reasons[:4]) or "Not enough signal strength for a clear view",
    }


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    value = frame[name]
    if isinstance(value, pd.DataFrame):
        return value.iloc[:, 0]
    return value


def _clean_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if math.isinf(number) or math.isnan(number):
        return None
    return round(number, 4)


def _has_numbers(*values: Any) -> bool:
    return all(isinstance(value, (int, float)) and not math.isnan(float(value)) for value in values)
