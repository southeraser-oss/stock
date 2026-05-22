from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"


def load_trading_skills() -> str:
    if os.getenv("ENABLE_TRADING_SKILLS", "true").lower() != "true":
        return ""

    max_chars = _int_env("TRADING_SKILLS_MAX_CHARS", 6000)
    chunks: list[str] = []

    for path in sorted(SKILLS_DIR.glob("**/*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        chunks.append(f"## {path.relative_to(SKILLS_DIR)}\n{text}")
        if len("\n\n".join(chunks)) >= max_chars:
            break

    return "\n\n".join(chunks)[:max_chars]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
