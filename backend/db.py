from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "stock_dashboard.sqlite3"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                style TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                result_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cash_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                movement_type TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS review_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )


def insert_json(table: str, columns: dict[str, Any], json_column: str, payload: dict[str, Any]) -> int:
    with connect() as conn:
        data = {**columns, json_column: json.dumps(payload, ensure_ascii=False)}
        names = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cursor = conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            list(data.values()),
        )
        return int(cursor.lastrowid)


def latest_backtests(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_with_json(row, "result_json") for row in rows]


def insert_cash_movement(created_at: str, movement_type: str, amount: float, note: str | None = None) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO cash_movements (created_at, movement_type, amount, note) VALUES (?, ?, ?, ?)",
            (created_at, movement_type, amount, note),
        )
        return int(cursor.lastrowid)


def cash_movements(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cash_movements ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _row_with_json(row: sqlite3.Row, json_column: str) -> dict[str, Any]:
    data = dict(row)
    data[json_column] = json.loads(data[json_column])
    return data
