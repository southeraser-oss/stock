from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"


def write_latest_json(payload: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / "latest.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def write_data_json(filename: str, payload: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / filename
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def ensure_dashboard_html() -> Path:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DOCS_DIR / "index.html"
    if output_path.exists():
        return output_path

    output_path.write_text(DEFAULT_HTML, encoding="utf-8")
    return output_path


DEFAULT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock Analysis Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d7dde5;
      --green: #137a4b;
      --red: #ba2f2f;
      --amber: #9a6500;
      --blue: #255a9b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 28px clamp(16px, 4vw, 48px) 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 { margin: 0 0 8px; font-size: clamp(26px, 4vw, 42px); letter-spacing: 0; }
    .subhead { color: var(--muted); margin: 0; max-width: 880px; line-height: 1.5; }
    main { padding: 24px clamp(16px, 4vw, 48px) 42px; display: grid; gap: 20px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
    .status { color: var(--muted); font-size: 14px; }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 8px;
      padding: 9px 13px;
      cursor: pointer;
      font: inherit;
    }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 160px;
    }
    .symbol { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .symbol strong { font-size: 22px; }
    .badge { border-radius: 999px; padding: 4px 9px; font-size: 12px; text-transform: uppercase; }
    .bullish { background: #e3f5eb; color: var(--green); }
    .bearish { background: #fae5e5; color: var(--red); }
    .neutral { background: #f6edd8; color: var(--amber); }
    .metric { display: flex; justify-content: space-between; gap: 16px; border-top: 1px solid var(--line); padding: 8px 0; }
    .metric span:first-child { color: var(--muted); }
    .metric span:last-child { font-variant-numeric: tabular-nums; text-align: right; }
    .analysis {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      display: grid;
      gap: 14px;
    }
    .analysis h2 { margin: 0; font-size: 18px; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      background: #f0f3f7;
      border-radius: 8px;
      padding: 14px;
      color: #243447;
    }
    .error { color: var(--red); }
    @media (max-width: 560px) {
      header { padding-top: 22px; }
      main { padding-top: 18px; }
      .toolbar { align-items: flex-start; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Stock Analysis Dashboard</h1>
    <p class="subhead">Automated technical screening every 30 minutes. This dashboard is for analysis and alerts only, not automatic trading.</p>
  </header>
  <main>
    <section class="toolbar">
      <div class="status" id="status">Loading latest analysis...</div>
      <button type="button" id="refresh">Refresh</button>
    </section>
    <section class="grid" id="cards"></section>
    <section class="analysis">
      <h2>LLM Screening</h2>
      <pre id="llm">No LLM analysis loaded.</pre>
    </section>
  </main>
  <script>
    const cards = document.querySelector("#cards");
    const statusEl = document.querySelector("#status");
    const llmEl = document.querySelector("#llm");
    const refresh = document.querySelector("#refresh");

    function fmt(value, suffix = "") {
      if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
      return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
    }

    function renderCard(row) {
      if (row.error) {
        return `
          <article class="card">
            <div class="symbol">
              <strong>${row.symbol}</strong>
              <span class="badge neutral">error</span>
            </div>
            <p class="status error">${row.error}</p>
          </article>
        `;
      }
      const signal = row.rule_based || {};
      const rating = signal.rating || "neutral";
      return `
        <article class="card">
          <div class="symbol">
            <strong>${row.symbol}</strong>
            <span class="badge ${rating}">${rating}</span>
          </div>
          <div class="metric"><span>Price</span><span>${fmt(row.indicators.price)}</span></div>
          <div class="metric"><span>SMA20</span><span>${fmt(row.indicators.sma20)}</span></div>
          <div class="metric"><span>SMA60</span><span>${fmt(row.indicators.sma60)}</span></div>
          <div class="metric"><span>RSI14</span><span>${fmt(row.indicators.rsi14)}</span></div>
          <div class="metric"><span>Daily return</span><span>${fmt(row.indicators.daily_return_pct, "%")}</span></div>
          <div class="metric"><span>Volume change</span><span>${fmt(row.indicators.volume_change_pct, "%")}</span></div>
          <p class="status">${signal.summary || ""}</p>
        </article>
      `;
    }

    async function loadDashboard() {
      try {
        statusEl.textContent = "Loading latest analysis...";
        const response = await fetch(`data/latest.json?ts=${Date.now()}`);
        if (!response.ok) throw new Error(`Could not load latest.json (${response.status})`);
        const data = await response.json();
        cards.innerHTML = (data.stocks || []).map(renderCard).join("");
        const updated = new Date(data.generated_at).toLocaleString();
        statusEl.textContent = `Last updated: ${updated} | Symbols: ${data.symbols.join(", ")}`;
        llmEl.textContent = JSON.stringify(data.llm_analysis, null, 2);
      } catch (error) {
        cards.innerHTML = "";
        statusEl.innerHTML = `<span class="error">${error.message}</span>`;
        llmEl.textContent = "Run the GitHub Action or python src/analyze.py to generate docs/data/latest.json.";
      }
    }

    refresh.addEventListener("click", loadDashboard);
    loadDashboard();
  </script>
</body>
</html>
"""
