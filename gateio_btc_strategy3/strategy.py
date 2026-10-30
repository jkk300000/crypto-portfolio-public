"""
Squeeze Momentum + IBS entry, ATR stop/TP, SuperTrend trail exit.
Pine source: SQZMOM_IBS_ST_TRAIL.pine
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config


@dataclass
class EntryInfo:
    side: str
    size: str
    sl_price: float
    tp_price: float
    sl_pct: float
    leverage: float


def compute_position_size(equity: float, close: float, sl_dist: float) -> Tuple[int, float, float]:
    """Match Pine: effLev = min(maxLeverage, maxRiskPct / slPct), qty = equity * lev / close."""
    close_safe = max(float(close), 1e-12)
    sl_pct = (float(sl_dist) / close_safe) * 100.0 if sl_dist is not None else float("nan")
    if not np.isfinite(sl_pct) or sl_pct <= 0 or sl_pct > config.MAX_SL_PCT:
        return 0, sl_pct, 0.0
    sl_pct_safe = max(sl_pct, 0.01)
    eff_lev = min(config.MAX_LEVERAGE, config.MAX_RISK_PCT / sl_pct_safe)
    equity_now = max(float(equity) * float(getattr(config, "equity_usage_ratio", 1.0)), 1e-6)
    btc_qty = equity_now * eff_lev / close_safe
    mult = float(getattr(config, "CONTRACT_MULTIPLIER", 0.0001))
    contracts = int(round(btc_qty / mult))
    return max(0, contracts), sl_pct, eff_lev


def check_entry(
    candles_with_indicators: List[Dict[str, Any]],
    last_bar_index: int,
    equity: float,
) -> Tuple[bool, bool, Optional[EntryInfo], Optional[EntryInfo]]:
    if last_bar_index < 0 or last_bar_index >= len(candles_with_indicators):
        return False, False, None, None

    row = candles_with_indicators[last_bar_index]
    c = row.get("c")
    sl_dist = row.get("sl_dist")
    sqz_val = row.get("sqz_val")
    if c is None or sl_dist is None or sqz_val is None:
        return False, False, None, None

    close_safe = float(c) if float(c) > 0 else 1e-6
    size, sl_pct, eff_lev = compute_position_size(equity, close_safe, float(sl_dist))
    order_min = getattr(config, "ORDER_SIZE_MIN", 1)
    pos_ok = size >= order_min and np.isfinite(sl_pct) and sl_pct > 0 and sl_pct <= config.MAX_SL_PCT

    ibs = float(row.get("ibs") or 0.5)
    val = float(sqz_val)
    val_rising = bool(row.get("val_rising"))
    val_falling = bool(row.get("val_falling"))
    ibs_long_ok = ibs < config.IBS_LONG_MAX
    ibs_short_ok = ibs > config.IBS_SHORT_MIN

    long_ok = bool(config.ALLOW_LONG) and pos_ok and val > 0 and val_rising and ibs_long_ok
    short_ok = bool(config.ALLOW_SHORT) and pos_ok and val < 0 and val_falling and ibs_short_ok

    long_sl = close_safe - float(sl_dist)
    short_sl = close_safe + float(sl_dist)
    long_tp = close_safe + float(sl_dist) * config.RR_RATIO
    short_tp = close_safe - float(sl_dist) * config.RR_RATIO

    long_info = EntryInfo("long", str(size), long_sl, long_tp, sl_pct, eff_lev) if long_ok else None
    short_info = EntryInfo("short", str(size), short_sl, short_tp, sl_pct, eff_lev) if short_ok else None
    return long_ok, short_ok, long_info, short_info


def check_supertrend_exit(row: Dict[str, Any], position_side: str, after_entry: bool) -> bool:
    if not after_entry:
        return False
    if position_side == "long" and bool(row.get("sell_signal")):
        return True
    if position_side == "short" and bool(row.get("buy_signal")):
        return True
    return False


def get_last_completed_bar_row(candles: List[Dict], bar_seconds: int, now_ts: float):
    if not candles:
        return None, None
    for i in range(len(candles) - 1, -1, -1):
        row = candles[i]
        bar_t = row.get("t")
        if bar_t is None:
            continue
        if bar_t + bar_seconds <= now_ts:
            return row, i
    return None, None


def fallback_tp_sl(row: Dict[str, Any], side: str) -> Optional[Tuple[float, float]]:
    c = row.get("c")
    sl_dist = row.get("sl_dist")
    if c is None or sl_dist is None:
        return None
    close_safe = float(c)
    dist = float(sl_dist)
    if side == "long":
        return close_safe - dist, close_safe + dist * config.RR_RATIO
    return close_safe + dist, close_safe - dist * config.RR_RATIO
