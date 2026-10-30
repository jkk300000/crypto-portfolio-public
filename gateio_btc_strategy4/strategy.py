"""Donchian 20/10 + IBS + ATR risk — entry/exit and sizing."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import STRATEGY, StrategyConfig
from indicators import build_indicators


@dataclass
class SignalSnapshot:
    bar_time: int
    close: float
    atr: float
    entry_upper: float
    entry_lower: float
    exit_upper: float
    exit_lower: float
    ibs: float
    long_signal: bool
    short_signal: bool
    long_exit: bool
    short_exit: bool
    swing_low: float | None
    swing_high: float | None


@dataclass
class EntryPlan:
    direction: int  # 1 long, -1 short
    entry_price: float
    stop: float
    sl_dist: float
    tp1: float
    tp2: float
    qty_btc: float


def latest_snapshot(df: pd.DataFrame, cfg: StrategyConfig = STRATEGY) -> SignalSnapshot | None:
    if df is None or len(df) < max(cfg.entry_len, cfg.atr_len) + 2:
        return None
    ind = build_indicators(df, cfg)
    row = ind.iloc[-1]
    if pd.isna(row["entry_upper"]) or pd.isna(row["atr"]):
        return None

    atr = float(row["atr"])
    atr_safe = max(atr, 1e-8)
    close = float(row["c"])
    ibs = float(row["ibs"])
    entry_upper = float(row["entry_upper"])
    entry_lower = float(row["entry_lower"])
    exit_upper = float(row["exit_upper"])
    exit_lower = float(row["exit_lower"])

    ibs_long_ok = (not cfg.enable_ibs_filter) or (ibs < cfg.ibs_long_max)
    ibs_short_ok = (not cfg.enable_ibs_filter) or (ibs > cfg.ibs_short_min)

    long_signal = cfg.allow_long and close > entry_upper and ibs_long_ok
    short_signal = cfg.allow_short and close < entry_lower and ibs_short_ok
    long_exit = close < exit_lower
    short_exit = close > exit_upper

    swing_low = None if pd.isna(row["swing_low"]) else float(row["swing_low"])
    swing_high = None if pd.isna(row["swing_high"]) else float(row["swing_high"])

    return SignalSnapshot(
        bar_time=int(row["t"]),
        close=close,
        atr=atr_safe,
        entry_upper=entry_upper,
        entry_lower=entry_lower,
        exit_upper=exit_upper,
        exit_lower=exit_lower,
        ibs=ibs,
        long_signal=long_signal,
        short_signal=short_signal,
        long_exit=long_exit,
        short_exit=short_exit,
        swing_low=swing_low,
        swing_high=swing_high,
    )


def calc_qty_btc(entry_px: float, sl_dist: float, equity: float, cfg: StrategyConfig = STRATEGY) -> float:
    risk_cash = equity * (cfg.max_risk_pct / 100.0)
    qty_by_risk = risk_cash / sl_dist if sl_dist > 0 else 0.0
    qty_by_lev = equity * cfg.max_leverage / entry_px if entry_px > 0 else 0.0
    return min(qty_by_risk, qty_by_lev)


def make_entry_plan(
    snap: SignalSnapshot,
    equity: float,
    cfg: StrategyConfig = STRATEGY,
) -> EntryPlan | None:
    close = snap.close if snap.close > 0 else 0.0001
    atr = snap.atr

    if snap.long_signal:
        sl = close - atr * cfg.atr_mult
        sl_dist = close - sl
        if sl_dist <= 0 or sl >= close:
            return None
        qty = calc_qty_btc(close, sl_dist, equity, cfg)
        if qty <= 0:
            return None
        return EntryPlan(
            direction=1,
            entry_price=close,
            stop=sl,
            sl_dist=sl_dist,
            tp1=close + sl_dist * cfg.rr_tp1,
            tp2=close + sl_dist * cfg.rr_tp2,
            qty_btc=qty,
        )

    if snap.short_signal:
        sl = close + atr * cfg.atr_mult
        sl_dist = sl - close
        if sl_dist <= 0 or sl <= close:
            return None
        qty = calc_qty_btc(close, sl_dist, equity, cfg)
        if qty <= 0:
            return None
        return EntryPlan(
            direction=-1,
            entry_price=close,
            stop=sl,
            sl_dist=sl_dist,
            tp1=close - sl_dist * cfg.rr_tp1,
            tp2=close - sl_dist * cfg.rr_tp2,
            qty_btc=qty,
        )

    return None


def update_hard_stop(
    *,
    direction: int,
    hard_stop: float,
    entry_price: float,
    tp1_done: bool,
    swing_low: float | None,
    swing_high: float | None,
    close: float,
    enable_swing: bool = True,
) -> float:
    hs = hard_stop
    if enable_swing and direction == 1 and swing_low is not None:
        be_ok = (not tp1_done) or (swing_low >= entry_price)
        if be_ok and swing_low > hs and swing_low < close:
            hs = swing_low
    if enable_swing and direction == -1 and swing_high is not None:
        be_ok = (not tp1_done) or (swing_high <= entry_price)
        if be_ok and swing_high < hs and swing_high > close:
            hs = swing_high

    if tp1_done:
        if direction == 1 and hs < entry_price:
            hs = entry_price
        elif direction == -1 and hs > entry_price:
            hs = entry_price
    return hs
