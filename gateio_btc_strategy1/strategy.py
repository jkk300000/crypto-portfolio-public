"""
EMA + RSI(7/14/21) + IBS entry, EMA stop, EMA trail, fixed RR take-profit.
Pine: ema200 +rsi trail.pine
  - 롱: close > EMA + RSI 전부 > 기준 + IBS 과매도
  - 숏: close < EMA + RSI 전부 < 기준 + IBS 과매수
  - 스탑: 진입 시 EMA (최소폭 옵션). 이후 유리한 방향으로만 EMA 트레일. TP는 최초 손절폭 기준 고정.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config


def round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return float(price)
    rounded = round(float(price) / tick) * tick
    nd = 0
    if tick < 1:
        s = f"{tick:.10f}".rstrip("0")
        nd = len(s.split(".")[-1]) if "." in s else 0
        return float(f"{rounded:.{nd}f}")
    return float(int(round(rounded)))


@dataclass
class EntryInfo:
    side: str
    size: str
    sl_price: float
    tp_price: float
    sl_pct: float
    leverage: float
    sl_dist: float
    entry_price: float


def _sl_plan(close: float, ema: float) -> Tuple[bool, float, float]:
    close_safe = max(float(close), 1e-12)
    raw_sl_dist = abs(close_safe - float(ema))
    sl_pct_raw = raw_sl_dist / close_safe * 100.0
    below_min = bool(config.USE_MIN_SL) and sl_pct_raw < float(config.MIN_SL_PCT)
    widen = below_min and config.MIN_SL_MODE == "widen"
    if below_min and not widen:
        return False, raw_sl_dist, sl_pct_raw
    sl_dist = close_safe * float(config.MIN_SL_PCT) / 100.0 if widen else raw_sl_dist
    sl_pct = sl_dist / close_safe * 100.0
    sl_allowed = sl_dist > 0 and sl_pct <= float(config.MAX_SL_PCT)
    return sl_allowed, sl_dist, sl_pct


def rr_ratio_for_side(side: str) -> float:
    if side == "short":
        return float(getattr(config, "RR_RATIO_SHORT", config.RR_RATIO))
    return float(config.RR_RATIO)


def compute_position_size(equity: float, close: float, sl_dist: float) -> Tuple[int, float, float]:
    close_safe = max(float(close), 1e-12)
    sl_pct = (float(sl_dist) / close_safe) * 100.0 if sl_dist is not None else float("nan")
    if not np.isfinite(sl_pct) or sl_pct <= 0 or sl_pct > config.MAX_SL_PCT:
        return 0, sl_pct, 0.0
    sl_pct_safe = max(sl_pct, 0.01)
    fee_cost = (2.0 * float(config.FEE_PCT_SIDE)) if config.USE_FEE_SIZING else 0.0
    eff_lev = min(float(config.MAX_LEVERAGE), float(config.MAX_RISK_PCT) / (sl_pct_safe + fee_cost))
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
    ema = row.get("ema")
    rsi1, rsi2, rsi3 = row.get("rsi1"), row.get("rsi2"), row.get("rsi3")
    if c is None or ema is None or rsi1 is None or rsi2 is None or rsi3 is None:
        return False, False, None, None

    close_safe = float(c) if float(c) > 0 else 1e-6
    sl_ok, sl_dist, sl_pct = _sl_plan(close_safe, float(ema))
    size, sl_pct_sz, eff_lev = compute_position_size(equity, close_safe, sl_dist) if sl_ok else (0, sl_pct, 0.0)
    order_min = getattr(config, "ORDER_SIZE_MIN", 1)
    pos_ok = sl_ok and size >= order_min and np.isfinite(sl_pct_sz) and sl_pct_sz > 0

    ibs = float(row.get("ibs") or 0.5)
    level = float(config.RSI_LEVEL)
    long_cond = (
        close_safe > float(ema)
        and float(rsi1) > level
        and float(rsi2) > level
        and float(rsi3) > level
        and ibs < float(config.IBS_LONG_MAX)
    )
    short_cond = (
        close_safe < float(ema)
        and float(rsi1) < level
        and float(rsi2) < level
        and float(rsi3) < level
        and ibs > float(config.IBS_SHORT_MIN)
    )
    long_ok = bool(config.ALLOW_LONG) and pos_ok and long_cond
    short_ok = bool(config.ALLOW_SHORT) and pos_ok and short_cond

    tick = float(getattr(config, "PRICE_TICK", 0.1))
    long_sl = round_to_tick(close_safe - sl_dist, tick)
    short_sl = round_to_tick(close_safe + sl_dist, tick)
    long_tp = round_to_tick(close_safe + (close_safe - long_sl) * rr_ratio_for_side("long"), tick)
    short_tp = round_to_tick(close_safe - (short_sl - close_safe) * rr_ratio_for_side("short"), tick)

    long_info = (
        EntryInfo("long", str(size), long_sl, long_tp, sl_pct_sz, eff_lev, sl_dist, close_safe) if long_ok else None
    )
    short_info = (
        EntryInfo("short", str(size), short_sl, short_tp, sl_pct_sz, eff_lev, sl_dist, close_safe) if short_ok else None
    )
    return long_ok, short_ok, long_info, short_info


def next_trail_stop(side: str, current_stop: float, ema: float, tick: float) -> Optional[float]:
    new_stop = round_to_tick(float(ema), tick)
    if side == "long" and new_stop > float(current_stop):
        return new_stop
    if side == "short" and new_stop < float(current_stop):
        return new_stop
    return None


def fallback_tp_sl(row: Dict[str, Any], side: str, entry_price: Optional[float] = None) -> Optional[Tuple[float, float]]:
    ema = row.get("ema")
    c = entry_price if entry_price is not None else row.get("c")
    if ema is None or c is None:
        return None
    tick = float(getattr(config, "PRICE_TICK", 0.1))
    close_safe = float(c)
    sl = round_to_tick(float(ema), tick)
    sl_dist = abs(close_safe - sl)
    if sl_dist <= 0:
        return None
    rr = rr_ratio_for_side(side)
    if side == "long":
        return sl, round_to_tick(close_safe + sl_dist * rr, tick)
    return sl, round_to_tick(close_safe - sl_dist * rr, tick)


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
