# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import NAMES, ROOT
from .kakao import send_kakao

EVENTS_PATH = ROOT / "events.jsonl"
CURSOR_PATH = ROOT / "events.cursor"

_KIND_KR = {
    "entry": "진입",
    "be_add": "추가진입",
    "close": "청산",
    "supertrend_close": "청산",
    "transfer": "이체",
}


def append_event(sleeve: str, kind: str, text: str, data: dict | None = None) -> None:
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sleeve": sleeve,
        "kind": kind,
        "text": text,
        "data": data or {},
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_event(ev: dict) -> str:
    kind = _KIND_KR.get(ev.get("kind") or "", ev.get("kind") or "")
    body = (ev.get("text") or "").strip()
    if ev.get("kind") == "transfer":
        return f"[이체] {body}"[:200]
    name = NAMES.get(ev.get("sleeve") or "", ev.get("sleeve") or "")
    return f"[{kind}] {name}\n{body}"[:200]


def drain_events() -> int:
    if not EVENTS_PATH.exists():
        return 0
    start = 0
    if CURSOR_PATH.exists():
        try:
            start = int(CURSOR_PATH.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            start = 0
    raw = EVENTS_PATH.read_bytes()
    if start > len(raw):
        start = 0
    chunk = raw[start:]
    if not chunk:
        return 0
    sent = 0
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if ev.get("kind") not in _KIND_KR:
            continue
        if send_kakao(format_event(ev)):
            sent += 1
    CURSOR_PATH.write_text(str(len(raw)), encoding="utf-8")
    return sent
