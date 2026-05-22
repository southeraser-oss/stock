from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "docs" / "data" / "ledger.json"


def append_ledger(portfolio: dict[str, Any], backtest: dict[str, Any], llm_analysis: dict[str, Any]) -> dict[str, Any]:
    ledger = _load()
    today = datetime.now(UTC).date().isoformat()
    entries = [entry for entry in ledger.get("entries", []) if not (entry.get("date") == today and entry.get("type") == "daily")]
    review = _review_text(portfolio, backtest, llm_analysis)
    entries.append(
        {
            "date": today,
            "type": "daily",
            "title": "Daily Review",
            "portfolio_balance": portfolio.get("balance", 0),
            "portfolio_return_pct": portfolio.get("total_return_pct", 0),
            "backtest_return_pct": backtest.get("total_return_pct", 0),
            "holdings": [row.get("symbol") for row in portfolio.get("holdings", [])],
            "review": review,
        }
    )
    ledger = {"generated_at": datetime.now(UTC).isoformat(), "entries": sorted(entries, key=lambda row: row["date"])[-365:]}
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    return ledger


def _load() -> dict[str, Any]:
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    return {"entries": []}


def _review_text(portfolio: dict[str, Any], backtest: dict[str, Any], llm_analysis: dict[str, Any]) -> str:
    openai = llm_analysis.get("openai_premium") or {}
    if isinstance(openai, dict) and openai.get("market_view"):
        return openai["market_view"]
    return (
        f"Portfolio return {portfolio.get('total_return_pct', 0)}%; "
        f"latest backtest return {backtest.get('total_return_pct', 0)}%; "
        f"holdings {len(portfolio.get('holdings', []))}."
    )
