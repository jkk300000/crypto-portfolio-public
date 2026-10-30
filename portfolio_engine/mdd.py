# -*- coding: utf-8 -*-
"""Persistent book-level peak / drawdown.

Mark = sleeve futures (total + UPL) + main-spot USDT + sub-account spot USDT.
Transfers between those pockets should not create a fake drawdown.

기록 MDD is the worst peak-to-trough of that mark since observation
started: trough / episode_peak - 1. It is not (current equity) /
(current 고점) - 1. Current 고점대비 uses the live ATH; 기록 MDD
keeps the ATH that existed at the historical trough.

Incomplete marks are ignored. A jump of >15% versus the last trusted
mark is treated as a missing-sleeve / transfer hole — not a new peak
and not a new 기록 MDD — even if the previous sample was a minute ago
(controller poll). A stored episode worse than -18% is discarded when
live equity is back near the peak; that pattern is a hole, not a crash.

Dashboard (2s) and controller (60s) share this file under an exclusive
lock so they cannot race a stale peak.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Mapping

from .config import ROOT

MDD_STATE = ROOT / "mdd_state.json"
_LOCK_PATH = ROOT / "mdd_state.lock"
_GLITCH_ABS = 0.15
_HOLE_MDD = -0.18
_MIN_SLEEVE = 1.0


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_mdd() -> dict[str, Any]:
    if not MDD_STATE.exists():
        return {}
    try:
        data = json.loads(MDD_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict[str, Any]) -> None:
    tmp = MDD_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MDD_STATE)


def _lock_fd():
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(_LOCK_PATH, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass
    return fh


def _unlock_fd(fh) -> None:
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    fh.close()


def book_mark_complete(
    sleeve_equities: Mapping[str, float | None],
    main_spot: float | None,
    sub_spot: float | None,
    sub_spots: Mapping[str, float] | None = None,
) -> bool:
    """True only when every pocket is present and no sleeve vanished mid-hop."""
    if main_spot is None or sub_spot is None:
        return False
    spots = sub_spots or {}
    if not sleeve_equities:
        return False
    for sleeve, equity in sleeve_equities.items():
        if equity is None or not math.isfinite(float(equity)):
            return False
        if float(equity) > _MIN_SLEEVE:
            continue
        if float(spots.get(sleeve) or 0) > _MIN_SLEEVE:
            continue
        return False
    return True


def _is_glitch(prev_equity: float | None, equity: float) -> bool:
    if not prev_equity or prev_equity <= 0:
        return False
    return abs(equity / prev_equity - 1.0) > _GLITCH_ABS


def _episode_mdd(state: dict[str, Any]) -> float:
    peak = float(state.get("mdd_peak") or 0.0)
    trough = float(state.get("mdd_trough") or 0.0)
    if peak > 0 and trough > 0:
        return min(0.0, trough / peak - 1.0)
    return min(0.0, float(state.get("mdd") or 0.0))


def _heal_hole(state: dict[str, Any], equity: float, peak: float, now: str) -> None:
    """Drop a stored trough that is a missing-sleeve hole, not a real crash."""
    trough = float(state.get("mdd_trough") or 0.0)
    mdd = _episode_mdd(state)
    if mdd > _HOLE_MDD or trough <= 0 or peak <= 0:
        return
    if equity < trough * 1.15 or equity < peak * 0.82:
        return
    dd = equity / peak - 1.0 if peak > 0 else 0.0
    state["mdd_peak"] = peak
    state["mdd_peak_ts"] = state.get("peak_ts")
    state["mdd_trough"] = equity
    state["mdd_trough_ts"] = now
    state["mdd"] = min(0.0, dd)
    state["healed_hole"] = True


def update_book_mdd(equity: float | None, ts: str | None = None) -> dict[str, Any]:
    """Update peak / current DD / max DD from one complete book mark-to-market sample."""
    if equity is None or not math.isfinite(float(equity)) or float(equity) <= 0:
        return load_mdd()
    equity = float(equity)
    now = ts or _utc()
    fh = _lock_fd()
    try:
        state = load_mdd()
        if not state.get("peak"):
            state = {
                "since": now,
                "peak": equity,
                "peak_ts": now,
                "equity": equity,
                "equity_ts": now,
                "current_dd": 0.0,
                "mdd": 0.0,
                "mdd_peak": equity,
                "mdd_peak_ts": now,
                "mdd_trough": equity,
                "mdd_trough_ts": now,
                "samples": 0,
            }
            _save(state)
            return state

        if _is_glitch(state.get("equity"), equity):
            return state

        peak = float(state["peak"])
        if equity > peak:
            peak = equity
            state["peak"] = peak
            state["peak_ts"] = now
        dd = equity / peak - 1.0 if peak > 0 else 0.0
        state["equity"] = equity
        state["equity_ts"] = now
        state["current_dd"] = dd
        state["samples"] = int(state.get("samples") or 0) + 1
        _heal_hole(state, equity, peak, now)
        if dd < float(state.get("mdd") or 0.0):
            state["mdd_peak"] = peak
            state["mdd_peak_ts"] = state.get("peak_ts")
            state["mdd_trough"] = equity
            state["mdd_trough_ts"] = now
        state["mdd"] = _episode_mdd(state)
        _save(state)
        return state
    finally:
        _unlock_fd(fh)
