from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any

import pandas as pd

from ai_trader import deepseek_daily_orders, openai_backtest_review
from indicators import calculate_indicators


STYLE_CONFIGS = {
    "momentum": {"min_score": 4, "rsi_low": 42, "rsi_high": 72, "stop": -7, "take": 18},
    "growth": {"min_score": 3, "rsi_low": 38, "rsi_high": 78, "stop": -10, "take": 24},
    "conservative": {"min_score": 4, "rsi_low": 40, "rsi_high": 65, "stop": -5, "take": 12},
    "high_volatility": {"min_score": 3, "rsi_low": 35, "rsi_high": 82, "stop": -12, "take": 30},
}


def run_backtest(
    histories: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
    style: str,
    initial_capital: float,
    max_positions: int,
    allocation_pct: float,
    fee_bps: float,
) -> dict[str, Any]:
    config = STYLE_CONFIGS.get(style, STYLE_CONFIGS["momentum"])
    dates = [date for date in _all_dates(histories) if start_date <= date.strftime("%Y-%m-%d") <= end_date]
    ai_enabled = os.getenv("AI_BACKTEST_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    ai_decisions: list[dict[str, Any]] = []
    cash = float(initial_capital)
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []

    for date in dates:
        date_key = date.strftime("%Y-%m-%d")
        market = _market_snapshot(histories, date)
        if not market:
            continue

        ranked = sorted(
            (
                (symbol, _score(row["indicators"], config), row)
                for symbol, row in market.items()
                if symbol not in positions
            ),
            key=lambda item: item[1],
            reverse=True,
        )

        if ai_enabled:
            candidates = [_candidate_row(symbol, score, row) for symbol, score, row in ranked]
            decision = deepseek_daily_orders(date_key, style, cash, positions, candidates)
            ai_decisions.append(
                {
                    "date": date_key,
                    "status": decision.get("status"),
                    "order_count": len(decision.get("orders", [])),
                    "review": decision.get("review"),
                    "error": decision.get("error"),
                }
            )
            cash = _apply_ai_orders(
                date_key,
                cash,
                positions,
                trades,
                market,
                decision.get("orders", []),
                max_positions,
                allocation_pct,
                fee_bps,
            )
        else:
            for symbol, position in list(positions.items()):
                if symbol not in market:
                    continue
                reason = _sell_reason(position, market[symbol], config)
                if not reason:
                    continue
                price = market[symbol]["price"]
                proceeds = position["shares"] * price
                fee = _fee(proceeds, fee_bps)
                pnl = (price - position["avg_price"]) * position["shares"] - fee
                cash += proceeds - fee
                trades.append(_trade(date_key, symbol, "SELL", position["shares"], price, fee, pnl, reason))
                del positions[symbol]

            for symbol, score, row in ranked:
                if len(positions) >= max_positions or score < config["min_score"]:
                    continue
                balance = _portfolio_value(cash, positions, market)
                spend = min(cash * 0.95, balance * allocation_pct)
                shares = int(spend // row["price"])
                if shares <= 0:
                    continue
                cost = shares * row["price"]
                fee = _fee(cost, fee_bps)
                if cost + fee > cash:
                    continue
                cash -= cost + fee
                positions[symbol] = {"symbol": symbol, "shares": shares, "avg_price": row["price"], "opened_at": date_key}
                trades.append(_trade(date_key, symbol, "BUY", shares, row["price"], fee, None, _buy_reason(row["indicators"], style)))

        balance = _portfolio_value(cash, positions, market)
        curve.append({"date": date_key, "balance": round(balance, 2), "cash": round(cash, 2)})
        if len(curve) == 1 or date.day in {1, 15} or date == dates[-1]:
            reviews.append(_daily_review(date_key, balance, cash, positions, ranked[:5], ai_decisions[-1] if ai_decisions else None))

    final_market = _market_snapshot(histories, dates[-1]) if dates else {}
    balance = _portfolio_value(cash, positions, final_market) if final_market else cash
    backtest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "style": style,
        "engine": "deepseek_daily_ai" if ai_enabled else "rule_based_fallback",
        "initial_capital": round(initial_capital, 2),
        "ending_balance": round(balance, 2),
        "total_return_pct": round(((balance / initial_capital) - 1) * 100, 2) if initial_capital > 0 else 0,
        "cash": round(cash, 2),
        "holdings": [_holding(row, final_market.get(symbol)) for symbol, row in positions.items() if symbol in final_market],
        "trade_history": trades,
        "equity_curve": curve,
        "daily_reviews": reviews[-80:],
        "summary": _summary(initial_capital, balance, trades, style, ai_enabled),
        "ai": {
            "enabled": ai_enabled,
            "deepseek_daily_decisions": ai_decisions[-120:],
            "deepseek_ok_days": sum(1 for row in ai_decisions if row.get("status") == "ok"),
            "deepseek_error_days": sum(1 for row in ai_decisions if row.get("status") == "error"),
            "deepseek_skipped_days": sum(1 for row in ai_decisions if str(row.get("status", "")).startswith("skipped")),
            "openai_review": None,
        },
    }
    backtest["ai"]["openai_review"] = openai_backtest_review(backtest) if ai_enabled else None
    return backtest


def _apply_ai_orders(
    date: str,
    cash: float,
    positions: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    market: dict[str, dict[str, Any]],
    orders: list[dict[str, Any]],
    max_positions: int,
    allocation_pct: float,
    fee_bps: float,
) -> float:
    for order in orders:
        symbol = str(order.get("symbol", "")).upper()
        action = str(order.get("action", "HOLD")).upper()
        if symbol not in market:
            continue
        price = market[symbol]["price"]
        reason = str(order.get("reason", "AI daily order"))
        if action == "SELL" and symbol in positions:
            position = positions[symbol]
            proceeds = position["shares"] * price
            fee = _fee(proceeds, fee_bps)
            pnl = (price - position["avg_price"]) * position["shares"] - fee
            cash += proceeds - fee
            trades.append(_trade(date, symbol, "SELL", position["shares"], price, fee, pnl, reason))
            del positions[symbol]

    for order in orders:
        symbol = str(order.get("symbol", "")).upper()
        action = str(order.get("action", "HOLD")).upper()
        confidence = float(order.get("confidence", 0) or 0)
        if action != "BUY" or symbol not in market or symbol in positions or len(positions) >= max_positions:
            continue
        if confidence < float(os.getenv("AI_BUY_MIN_CONFIDENCE", "0.55")):
            continue
        requested_allocation = float(order.get("allocation_pct", allocation_pct) or allocation_pct)
        clipped_allocation = min(max(requested_allocation, 0.02), allocation_pct)
        balance = _portfolio_value(cash, positions, market)
        spend = min(cash * 0.95, balance * clipped_allocation)
        shares = int(spend // market[symbol]["price"])
        if shares <= 0:
            continue
        cost = shares * market[symbol]["price"]
        fee = _fee(cost, fee_bps)
        if cost + fee > cash:
            continue
        cash -= cost + fee
        positions[symbol] = {"symbol": symbol, "shares": shares, "avg_price": market[symbol]["price"], "opened_at": date}
        trades.append(_trade(date, symbol, "BUY", shares, market[symbol]["price"], fee, None, str(order.get("reason", "AI daily buy"))))
    return cash


def _candidate_row(symbol: str, score: int, row: dict[str, Any]) -> dict[str, Any]:
    indicators = row["indicators"]
    return {
        "symbol": symbol,
        "score": score,
        "price": indicators.get("price"),
        "sma20": indicators.get("sma20"),
        "sma60": indicators.get("sma60"),
        "rsi14": indicators.get("rsi14"),
        "daily_return_pct": indicators.get("daily_return_pct"),
        "volume_change_pct": indicators.get("volume_change_pct"),
    }

def serialize_histories(histories: dict[str, pd.DataFrame]) -> dict[str, Any]:
    payload: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "symbols": sorted(histories)}
    rows: dict[str, list[dict[str, Any]]] = {}
    for symbol, history in histories.items():
        frame = history.tail(520).copy()
        frame.index = pd.to_datetime(frame.index).normalize()
        rows[symbol] = [
            {
                "date": index.strftime("%Y-%m-%d"),
                "close": _clean(row.get("Close")),
                "volume": int(row.get("Volume", 0)) if pd.notna(row.get("Volume")) else 0,
            }
            for index, row in frame.iterrows()
            if pd.notna(row.get("Close"))
        ]
    payload["history"] = rows
    return payload


def _all_dates(histories: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    dates: set[pd.Timestamp] = set()
    for history in histories.values():
        dates.update(set(pd.to_datetime(history.index).normalize()))
    return sorted(dates)


def _market_snapshot(histories: dict[str, pd.DataFrame], date: pd.Timestamp) -> dict[str, dict[str, Any]]:
    rows = {}
    for symbol, history in histories.items():
        frame = history.copy()
        frame.index = pd.to_datetime(frame.index).normalize()
        window = frame.loc[frame.index <= date]
        if len(window) < 60:
            continue
        indicators = calculate_indicators(window)
        price = indicators.get("price")
        if isinstance(price, (int, float)):
            rows[symbol] = {"price": float(price), "indicators": indicators}
    return rows


def _score(indicators: dict[str, Any], config: dict[str, Any]) -> int:
    score = 0
    if _gt(indicators.get("price"), indicators.get("sma20")):
        score += 1
    if _gt(indicators.get("sma20"), indicators.get("sma60")):
        score += 1
    if _between(indicators.get("rsi14"), config["rsi_low"], config["rsi_high"]):
        score += 1
    if _gt(indicators.get("daily_return_pct"), 0):
        score += 1
    if _gt(indicators.get("volume_change_pct"), 8):
        score += 1
    return score


def _sell_reason(position: dict[str, Any], row: dict[str, Any], config: dict[str, Any]) -> str | None:
    price = row["price"]
    indicators = row["indicators"]
    pnl_pct = ((price / position["avg_price"]) - 1) * 100
    if pnl_pct <= config["stop"]:
        return "Backtest stop loss"
    if pnl_pct >= config["take"]:
        return "Backtest take profit"
    if _gt(indicators.get("sma20"), price):
        return "Backtest trend break"
    return None


def _daily_review(
    date: str,
    balance: float,
    cash: float,
    positions: dict[str, Any],
    ranked: list[Any],
    ai_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    leaders = [item[0] for item in ranked[:3]]
    return {
        "date": date,
        "balance": round(balance, 2),
        "cash": round(cash, 2),
        "positions": sorted(positions),
        "watchlist": leaders,
        "ai_status": ai_decision.get("status") if ai_decision else "rule_based",
        "note": (ai_decision or {}).get("review") or f"Reviewed {len(ranked)} candidates; strongest watchlist: {', '.join(leaders) or 'none'}.",
    }


def _summary(initial: float, balance: float, trades: list[dict[str, Any]], style: str, ai_enabled: bool) -> str:
    engine = "DeepSeek daily AI" if ai_enabled else "rule-based fallback"
    return f"{style} backtest used {engine} and ended at {round(balance, 2)} from {round(initial, 2)} with {len(trades)} simulated trades."


def _buy_reason(indicators: dict[str, Any], style: str) -> str:
    return f"{style} score passed: RSI {indicators.get('rsi14')}, daily return {indicators.get('daily_return_pct')}%"


def _holding(position: dict[str, Any], market_row: dict[str, Any] | None) -> dict[str, Any]:
    price = market_row["price"] if market_row else position["avg_price"]
    return {
        "symbol": position["symbol"],
        "shares": position["shares"],
        "avg_price": round(position["avg_price"], 2),
        "current_price": round(price, 2),
        "market_value": round(position["shares"] * price, 2),
        "opened_at": position["opened_at"],
    }


def _trade(date: str, symbol: str, side: str, shares: int, price: float, fee: float, pnl: float | None, reason: str) -> dict[str, Any]:
    return {"date": date, "symbol": symbol, "side": side, "shares": shares, "price": round(price, 2), "fee": round(fee, 2), "pnl": None if pnl is None else round(pnl, 2), "reason": reason}


def _portfolio_value(cash: float, positions: dict[str, dict[str, Any]], market: dict[str, dict[str, Any]]) -> float:
    return cash + sum(row["shares"] * market[symbol]["price"] for symbol, row in positions.items() if symbol in market)


def _fee(value: float, fee_bps: float) -> float:
    return value * fee_bps / 10_000


def _clean(value: Any) -> float | None:
    return round(float(value), 4) if pd.notna(value) else None


def _gt(left: Any, right: Any) -> bool:
    return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left > right


def _between(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and low <= value <= high
