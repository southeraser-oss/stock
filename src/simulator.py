from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "docs" / "data" / "paper_state.json"


def simulate_trading(
    stocks: list[dict[str, Any]],
    cash_delta: float,
    cash_delta_id: str,
    max_positions: int,
    allocation_pct: float,
    fee_bps: float,
    trading_enabled: bool,
    force_liquidate: bool,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    state = _load_state()
    applied_delta_ids = set(state.get("applied_cash_delta_ids", []))
    should_apply_cash_delta = cash_delta != 0 and cash_delta_id not in applied_delta_ids
    state["cash"] = round(max(0, float(state.get("cash", 0)) + (cash_delta if should_apply_cash_delta else 0)), 2)
    state["updated_at"] = now
    state["trading_enabled"] = trading_enabled

    if should_apply_cash_delta:
        state.setdefault("cash_movements", []).append(
            {
                "date": now,
                "type": "DEPOSIT" if cash_delta > 0 else "WITHDRAWAL",
                "amount": round(abs(cash_delta), 2),
                "id": cash_delta_id,
                "note": "Configured paper-cash delta",
            }
        )
        state["applied_cash_delta_ids"] = [*state.get("applied_cash_delta_ids", []), cash_delta_id][-240:]

    market = _market_by_symbol(stocks)
    positions = {row["symbol"]: _position_from_holding(row) for row in state.get("holdings", [])}
    trades = state.setdefault("trade_history", [])

    for symbol, position in list(positions.items()):
        if symbol not in market:
            continue
        price = market[symbol]["price"]
        indicators = market[symbol]["indicators"]
        reason = "Force liquidate setting" if force_liquidate else _sell_decision(position, price, indicators)
        if reason:
            _sell_position(state, positions, trades, symbol, price, fee_bps, reason, now)

    if trading_enabled and not force_liquidate:
        candidates = sorted(
            (
                (symbol, _buy_score(row["indicators"]), row)
                for symbol, row in market.items()
                if symbol not in positions
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        for symbol, score, row in candidates:
            if score < 3 or len(positions) >= max_positions:
                continue
            balance = _portfolio_value(state["cash"], positions, market)
            spend = min(state["cash"] * 0.95, balance * allocation_pct)
            shares = int(spend // row["price"])
            if shares <= 0:
                continue
            cost = shares * row["price"]
            fee = _fee(cost, fee_bps)
            if cost + fee > state["cash"]:
                continue
            state["cash"] = round(state["cash"] - cost - fee, 2)
            positions[symbol] = {
                "symbol": symbol,
                "shares": shares,
                "avg_price": round(row["price"], 2),
                "opened_at": now,
            }
            trades.append(_trade_row(now, symbol, "BUY", shares, row["price"], fee, None, _buy_reason(row["indicators"])))

    holdings = [_holding_row(position, market[position["symbol"]]) for position in positions.values() if position["symbol"] in market]
    balance = _portfolio_value(state["cash"], positions, market)
    net_deposits = _net_deposits(state.get("cash_movements", []))

    state["holdings"] = holdings
    state["balance"] = round(balance, 2)
    state["cash"] = round(state["cash"], 2)
    state["total_return_pct"] = round(((balance / net_deposits) - 1) * 100, 2) if net_deposits > 0 else 0
    state["equity_curve"] = [*state.get("equity_curve", []), {"date": now, "balance": state["balance"], "cash": state["cash"]}][-240:]
    state["trade_history"] = trades[-240:]
    state["cash_movements"] = state.get("cash_movements", [])[-240:]
    state["strategy"] = {
        "max_positions": max_positions,
        "allocation_pct": allocation_pct,
        "fee_bps": fee_bps,
        "trading_enabled": trading_enabled,
        "force_liquidate": force_liquidate,
        "buy_logic": "Buy strongest current candidates above trend confirmation score.",
        "sell_logic": "Sell on trend break, overbought reversal, stop loss, take profit, or force liquidation.",
    }

    _save_state(state)
    return state


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "mode": "paper_trading",
        "cash": 0,
        "balance": 0,
        "total_return_pct": 0,
        "holdings": [],
        "trade_history": [],
        "cash_movements": [],
        "applied_cash_delta_ids": [],
        "equity_curve": [],
    }


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _position_from_holding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row["symbol"],
        "shares": row["shares"],
        "avg_price": row["avg_price"],
        "opened_at": row["opened_at"],
    }


def _market_by_symbol(stocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = {}
    for stock in stocks:
        indicators = stock.get("indicators") or {}
        price = indicators.get("price")
        if stock.get("symbol") and isinstance(price, (int, float)):
            rows[stock["symbol"]] = {"price": float(price), "indicators": indicators}
    return rows


def _sell_position(
    state: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    symbol: str,
    price: float,
    fee_bps: float,
    reason: str,
    now: str,
) -> None:
    position = positions[symbol]
    proceeds = position["shares"] * price
    fee = _fee(proceeds, fee_bps)
    state["cash"] = round(state["cash"] + proceeds - fee, 2)
    pnl = (price - position["avg_price"]) * position["shares"] - fee
    trades.append(_trade_row(now, symbol, "SELL", position["shares"], price, fee, pnl, reason))
    del positions[symbol]


def _buy_score(indicators: dict[str, Any]) -> int:
    score = 0
    if _gt(indicators.get("price"), indicators.get("sma20")):
        score += 1
    if _gt(indicators.get("sma20"), indicators.get("sma60")):
        score += 1
    if _between(indicators.get("rsi14"), 38, 70):
        score += 1
    if _gt(indicators.get("daily_return_pct"), 0):
        score += 1
    if _gt(indicators.get("volume_change_pct"), 10):
        score += 1
    if _gt(indicators.get("rsi14"), 78):
        score -= 2
    return score


def _sell_decision(position: dict[str, Any], price: float, indicators: dict[str, Any]) -> str | None:
    pnl_pct = ((price / position["avg_price"]) - 1) * 100
    if pnl_pct <= -7:
        return "Stop loss triggered"
    if pnl_pct >= 18:
        return "Take profit after strong run"
    if _gt(indicators.get("sma20"), price):
        return "Price broke below SMA20"
    if _gt(indicators.get("rsi14"), 82) and _gt(-1, indicators.get("daily_return_pct")):
        return "Overbought reversal risk"
    return None


def _buy_reason(indicators: dict[str, Any]) -> str:
    reasons = []
    if _gt(indicators.get("price"), indicators.get("sma20")):
        reasons.append("price above SMA20")
    if _gt(indicators.get("sma20"), indicators.get("sma60")):
        reasons.append("SMA20 above SMA60")
    if _between(indicators.get("rsi14"), 38, 70):
        reasons.append("RSI in momentum range")
    if _gt(indicators.get("volume_change_pct"), 10):
        reasons.append("volume expanding")
    return ", ".join(reasons) or "positive composite score"


def _holding_row(position: dict[str, Any], market_row: dict[str, Any]) -> dict[str, Any]:
    current_price = market_row["price"]
    market_value = position["shares"] * current_price
    unrealized = (current_price - position["avg_price"]) * position["shares"]
    return {
        "symbol": position["symbol"],
        "shares": position["shares"],
        "avg_price": round(position["avg_price"], 2),
        "current_price": round(current_price, 2),
        "market_value": round(market_value, 2),
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_return_pct": round(((current_price / position["avg_price"]) - 1) * 100, 2),
        "opened_at": position["opened_at"],
    }


def _trade_row(
    date: str,
    symbol: str,
    side: str,
    shares: int,
    price: float,
    fee: float,
    pnl: float | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "date": date,
        "symbol": symbol,
        "side": side,
        "shares": shares,
        "price": round(price, 2),
        "fee": round(fee, 2),
        "pnl": None if pnl is None else round(pnl, 2),
        "reason": reason,
    }


def _portfolio_value(cash: float, positions: dict[str, dict[str, Any]], market: dict[str, dict[str, Any]]) -> float:
    return cash + sum(
        position["shares"] * market[position["symbol"]]["price"]
        for position in positions.values()
        if position["symbol"] in market
    )


def _net_deposits(movements: list[dict[str, Any]]) -> float:
    deposits = sum(row["amount"] for row in movements if row["type"] == "DEPOSIT")
    withdrawals = sum(row["amount"] for row in movements if row["type"] == "WITHDRAWAL")
    return deposits - withdrawals


def _fee(value: float, fee_bps: float) -> float:
    return value * fee_bps / 10_000


def _gt(left: Any, right: Any) -> bool:
    return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left > right


def _between(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and low <= value <= high
