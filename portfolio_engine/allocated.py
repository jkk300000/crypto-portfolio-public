# -*- coding: utf-8 -*-
"""Bots import this (or copy the reader) to size on book allocation, not local cash."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def alloc_path() -> Optional[Path]:
    raw = (os.getenv("PORTFOLIO_ALLOC_PATH") or "").strip()
    if raw:
        return Path(raw)
    here = Path(__file__).resolve().parent / "allocation.json"
    return here if here.exists() else None


def read_allocated(sleeve: str, fallback: float) -> float:
    path = alloc_path()
    if path is None or not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        val = (data.get("allocated") or {}).get(sleeve)
        if val is None:
            return fallback
        out = float(val)
        return out if out > 0 else fallback
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback
