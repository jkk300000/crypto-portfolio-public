# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _path() -> Path:
    raw = (os.getenv("PORTFOLIO_EVENTS_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / "portfolio_engine" / "events.jsonl"


def emit(sleeve: str, kind: str, text: str, data: dict | None = None) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sleeve": sleeve,
            "kind": kind,
            "text": text,
            "data": data or {},
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return
