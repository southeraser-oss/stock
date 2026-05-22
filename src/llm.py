from __future__ import annotations

import json
import os
from typing import Any

import requests

from skillbook import load_trading_skills


def analyze_with_llms(
    stock_rows: list[dict[str, Any]],
    portfolio: dict[str, Any],
    backtest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return optional LLM analysis, falling back cleanly when keys are absent."""
    skills = load_trading_skills()
    deepseek = _call_deepseek(stock_rows, skills)
    premium = None

    if _should_call_openai():
        premium = _call_openai(_premium_context(stock_rows, portfolio, backtest or {}, deepseek, skills))

    return {
        "division_of_labor": {
            "deepseek": "Batch screening: compact rows for every tracked symbol, broad repetitive scoring, watchlist triage.",
            "openai": "Premium synthesis: portfolio review, risk diagnosis, backtest interpretation, and final action priorities from compressed context.",
        },
        "deepseek": deepseek,
        "openai_premium": premium,
        "provider_status": {
            "deepseek": _provider_status(deepseek, os.getenv("DEEPSEEK_API_KEY")),
            "openai": _provider_status(premium, os.getenv("OPENAI_API_KEY")),
        },
    }


def _call_deepseek(stock_rows: list[dict[str, Any]], skills: str) -> dict[str, Any] | None:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.getenv("MODEL_DEEPSEEK", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(stock_rows, skills)},
        ],
        "temperature": 0.2,
        "max_tokens": _int_env("DEEPSEEK_MAX_TOKENS", 900),
        "response_format": {"type": "json_object"},
    }

    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_json(content)
    except Exception as exc:
        return {"error": str(exc)}


def _call_openai(context: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.getenv("MODEL_OPENAI", "gpt-4.1-mini"),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": _premium_system_prompt()}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False)}]},
        ],
        "text": {"format": _openai_response_schema()},
        "temperature": 0.2,
        "max_output_tokens": _int_env("OPENAI_MAX_OUTPUT_TOKENS", 1200),
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("output_text") or _extract_openai_text(data)
        return _parse_json(content)
    except Exception as exc:
        return {"error": str(exc)}


def _system_prompt() -> str:
    return (
        "You are a cautious paper-trading stock screening assistant. Analyze technical indicators and provided trading skills. "
        "Do not recommend automatic trading. Return concise JSON with keys: "
        "market_view, top_watchlist, risks, alerts."
    )


def _premium_system_prompt() -> str:
    return (
        "You are a senior paper-trading portfolio analyst. Improve the simulated portfolio process, "
        "but do not suggest real-money trading or guaranteed returns. Focus on position quality, "
        "risk controls, cash use, and next paper-trading alerts. Return strict JSON only."
    )


def _user_prompt(stock_rows: list[dict[str, Any]], skills: str) -> str:
    compact = [_compact_stock_row(row) for row in stock_rows]
    return (
        "Trading skills:\n"
        + (skills or "No custom trading skills provided.")
        + "\n\nScreen these compact stock rows for a paper-trading dashboard:\n"
        + json.dumps(compact, ensure_ascii=False)
    )


def _premium_context(
    stock_rows: list[dict[str, Any]],
    portfolio: dict[str, Any],
    backtest: dict[str, Any],
    deepseek: dict[str, Any] | None,
    skills: str,
) -> dict[str, Any]:
    ranked = sorted(
        [_compact_stock_row(row) for row in stock_rows],
        key=lambda row: row.get("composite_score", 0),
        reverse=True,
    )
    return {
        "portfolio": {
            "balance": portfolio.get("balance"),
            "cash": portfolio.get("cash"),
            "total_return_pct": portfolio.get("total_return_pct"),
            "holdings": portfolio.get("holdings", []),
            "recent_trades": portfolio.get("trade_history", [])[-20:],
            "strategy": portfolio.get("strategy", {}),
        },
        "backtest": {
            "style": backtest.get("style"),
            "start_date": backtest.get("start_date"),
            "end_date": backtest.get("end_date"),
            "initial_capital": backtest.get("initial_capital"),
            "ending_balance": backtest.get("ending_balance"),
            "total_return_pct": backtest.get("total_return_pct"),
            "recent_trades": backtest.get("trade_history", [])[-20:],
            "daily_reviews": backtest.get("daily_reviews", [])[-10:],
        },
        "top_candidates": ranked[:8],
        "deepseek_screen": deepseek,
        "trading_skills": skills or "No custom trading skills provided.",
        "task": "Give premium analysis for the paper-trading simulation and the next monitoring priorities.",
    }


def _compact_stock_row(row: dict[str, Any]) -> dict[str, Any]:
    indicators = row.get("indicators", {})
    rule_based = row.get("rule_based", {})
    return {
        "symbol": row.get("symbol"),
        "company_name": row.get("company_name"),
        "price": indicators.get("price"),
        "sma20": indicators.get("sma20"),
        "sma60": indicators.get("sma60"),
        "rsi14": indicators.get("rsi14"),
        "daily_return_pct": indicators.get("daily_return_pct"),
        "volume_change_pct": indicators.get("volume_change_pct"),
        "rule_rating": rule_based.get("rating"),
        "rule_summary": rule_based.get("summary"),
        "composite_score": _composite_score(indicators),
    }


def _composite_score(indicators: dict[str, Any]) -> int:
    score = 0
    if _gt(indicators.get("price"), indicators.get("sma20")):
        score += 1
    if _gt(indicators.get("sma20"), indicators.get("sma60")):
        score += 1
    if _between(indicators.get("rsi14"), 40, 68):
        score += 1
    if _gt(indicators.get("daily_return_pct"), 0):
        score += 1
    if _gt(indicators.get("volume_change_pct"), 10):
        score += 1
    return score


def _openai_response_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "stock_screening",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "market_view": {"type": "string"},
                "top_watchlist": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "symbol": {"type": "string"},
                            "reason": {"type": "string"},
                            "rating": {"type": "string"},
                        },
                        "required": ["symbol", "reason", "rating"],
                    },
                },
                "risks": {"type": "array", "items": {"type": "string"}},
                "alerts": {"type": "array", "items": {"type": "string"}},
                "portfolio_actions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["market_view", "top_watchlist", "risks", "alerts", "portfolio_actions"],
        },
    }


def _parse_json(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        return {"text": content}


def _extract_openai_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _should_call_openai() -> bool:
    if not os.getenv("OPENAI_API_KEY"):
        return False
    setting = os.getenv("USE_OPENAI_PREMIUM", "auto").lower()
    return setting in {"1", "true", "yes", "on", "auto"}


def _provider_status(result: dict[str, Any] | None, api_key: str | None) -> str:
    if not api_key:
        return "skipped_missing_key"
    if not result:
        return "skipped"
    if result.get("error"):
        return "error"
    return "ok"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _gt(left: Any, right: Any) -> bool:
    return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left > right


def _between(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and low <= value <= high
