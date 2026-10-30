# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .book import BookSnapshot, allocate
from .config import ALLOC_PATH, NAMES, WEIGHTS


def publish(
    cash: Mapping[str, float],
    path: Path | None = None,
    weights: Mapping[str, float] | None = None,
) -> BookSnapshot:
    snap = allocate(cash, weights or WEIGHTS)
    dest = path or ALLOC_PATH
    payload = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "names": dict(NAMES),
        **snap.to_dict(),
    }
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return snap
