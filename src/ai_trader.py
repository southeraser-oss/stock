from __future__ import annotations

import json
import os
from typing import Any

import requests

from skillbook import load_trading_skills


def deepseek_daily_orders(
    date: str,
    style: str,
    cash: float,
    positions: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {"status": "skipped_missing_key", "orders": [], "review": "DeepSeek key missing."}

    payload = {
        "model": os.getenv("MODEL_DEEPSEEK", "deepseek-chat"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the daily decision engine for a paper-trading backtest. "
                    "DeepSeek's job is high-volume daily screening and order triage. "
                    "Use only data available up to the given date. Return JSON only with keys: orders, review. "
                    "Each order must contain symbol, action BUY/SELL/HOLD, confidence 0-1, allocation_pct 0-1, reason."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "date": date,
                        "style": style,
                        "cash": round(cash, 2),
                        "positions": list(positions.values()),
                        "candidates": candidates[: int(os.getenv("AI_DAILY_CANDIDATE_LIMIT", "12"))],
                        "trading_skills": load_trading_skills(),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.15,
        "max_tokens": int(os.getenv("DEEPSEEK_DAILY_MAX_TOKENS", "700")),
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        parsed["status"] = "ok"
        return parsed
    except Exception as exc:
        return {"status": "error", "error": str(exc), "orders": [], "review": "DeepSeek daily decision failed."}


def openai_backtest_review(backtest: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    compact = {
        "style": backtest.get("style"),
        "period": [backtest.get("start_date"), backtest.get("end_date")],
        "initial_capital": backtest.get("initial_capital"),
        "ending_balance": backtest.get("ending_balance"),
        "total_return_pct": backtest.get("total_return_pct"),
        "trade_count": len(backtest.get("trade_history", [])),
        "holdings": backtest.get("holdings", []),
        "daily_reviews": backtest.get("daily_reviews", [])[-20:],
        "recent_trades": backtest.get("trade_history", [])[-30:],
        "ai_decision_stats": backtest.get("ai", {}),
    }
    payload = {
        "model": os.getenv("MODEL_OPENAI", "gpt-4.1-mini"),
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are the premium review layer. OpenAI's job is not daily bulk screening; "
                            "it performs high-quality synthesis after DeepSeek's daily backtest decisions. "
                            "Return concise JSON with keys: verdict, strengths, weaknesses, next_experiments, risk_notes."
                        ),
                    }
                ],
            },
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(compact, ensure_ascii=False)}]},
        ],
        "text": {"format": _review_schema()},
        "temperature": 0.2,
        "max_output_tokens": int(os.getenv("OPENAI_BACKTEST_MAX_OUTPUT_TOKENS", "1200")),
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
        return {"status": "error", "error": str(exc)}


def _review_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "backtest_review",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "verdict": {"type": "string"},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "weaknesses": {"type": "array", "items": {"type": "string"}},
                "next_experiments": {"type": "array", "items": {"type": "string"}},
                "risk_notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "strengths", "weaknesses", "next_experiments", "risk_notes"],
        },
    }


def _extract_openai_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks)
