# -*- coding: utf-8 -*-
"""Use book T×w when portfolio_engine has published allocation.json."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _alloc_path() -> Path | None:
    raw = (os.getenv("PORTFOLIO_ALLOC_PATH") or "").strip()
    if raw:
        return Path(raw)
    sibling = Path(__file__).resolve().parent.parent / "portfolio_engine" / "allocation.json"
    return sibling if sibling.exists() else None


def book_equity(fallback: float, default_sleeve: str) -> float:
    path = _alloc_path()
    sleeve = (os.getenv("PORTFOLIO_SLEEVE") or default_sleeve).strip()
    if path is None:
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        val = float((data.get("allocated") or {}).get(sleeve) or 0)
        return val if val > 0 else fallback
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback
