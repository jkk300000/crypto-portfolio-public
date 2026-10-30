"""Persist first-entry price/TP so BE-add survives restart (Gate avg entry changes)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Optional

logger = logging.getLogger(__name__)


def load_state(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        logger.warning("SR state load failed: %s", e)
        return {}


def save_state(path: str, data: Optional[dict]) -> None:
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    payload: Any = data or {}
    fd, tmp = tempfile.mkstemp(prefix="sr_state_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
