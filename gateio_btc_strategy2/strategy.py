from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import config


@dataclass
class EntryInfo:
    side: str
    size: str
    sl_price: float
    tp_price: float


def _position_size(close: float, atr_stop: float, equity: float) -> Tuple[bool, int, float]:
    sl_dist = float(atr_stop) * config.ATR_STOP_MULT
    sl_pct = (sl_dist / max(close, 1e-8)) * 100.0
    sl_allowed = sl_pct > 0 and sl_pct <= config.MAX_SL_PCT
    if not sl_allowed:
        return False, 0, sl_dist

    sl_pct_safe = max(sl_pct, 0.01)
    eff_lev = min(config.MAX_LEVERAGE, config.MAX_RISK_PCT / sl_pct_safe)
    equity_safe = max(float(equity), 1e-8) * config.equity_usage_ratio
    notional = equity_safe * eff_lev
    contracts = int(round(notional / (max(close, 1e-8) * config.CONTRACT_MULTIPLIER)))
    contracts = max(config.ORDER_SIZE_MIN, contracts)
    return True, contracts, sl_dist


def check_entry(
    candles_with_indicators: List[Dict[str, Any]],
    last_bar_index: int,
    equity: float,
) -> Tuple[bool, bool, Optional[EntryInfo], Optional[EntryInfo]]:
    if last_bar_index < 0 or last_bar_index >= len(candles_with_indicators):
        return False, False, None, None

    row = candles_with_indicators[last_bar_index]
    close = float(row["c"])
    ibs = row.get("ibs")
    resistance = row.get("resistance")
    support = row.get("support")
    atr_stop = row.get("atr_stop")
    if ibs is None or atr_stop is None:
        return False, False, None, None

    sl_allowed, contracts, sl_dist = _position_size(close, float(atr_stop), equity)
    if not sl_allowed or contracts <= 0:
        return False, False, None, None

    long_signal = (
        config.ALLOW_LONG
        and resistance is not None
        and close > float(resistance)
        and float(ibs) < config.IBS_LONG_MAX
    )
    short_signal = (
        config.ALLOW_SHORT
        and support is not None
        and close < float(support)
        and float(ibs) > config.IBS_SHORT_MIN
    )

    long_info = None
    short_info = None
    if long_signal:
        long_info = EntryInfo("long", str(contracts), close - sl_dist, close + config.RR_RATIO * sl_dist)
    if short_signal:
        short_info = EntryInfo("short", str(contracts), close + sl_dist, close - config.RR_RATIO_SHORT * sl_dist)
    return long_signal, short_signal, long_info, short_info


@dataclass
class BeAddPlan:
    sl_price: float
    tp_price: float
    add_contracts: int
    risk_used_delta: float
    dist: float
    dist_pct: float
    reason: str


def st_line(row: Dict[str, Any]) -> Optional[float]:
    trend = row.get("supertrend_trend")
    if trend == 1:
        raw = row.get("supertrend_up")
    elif trend == -1:
        raw = row.get("supertrend_dn")
    else:
        return None
    if raw is None:
        return None
    return float(raw)


def check_be_add(
    row: Dict[str, Any],
    *,
    side: str,
    orig_entry: float,
    orig_tp: float,
    current_contracts: int,
    equity: float,
    add_risk_used: float,
) -> Optional[BeAddPlan]:
    """봉 마감 기준 본전 스탑 + 추가분 수량. 조건 아니면 None."""
    if not config.ENABLE_BE_ADD:
        return None
    close = float(row["c"])
    line = st_line(row)
    orig_entry = float(orig_entry)
    orig_tp = float(orig_tp)
    if line is None or orig_entry <= 0 or close <= 0 or orig_tp <= 0:
        return None

    if side == "long":
        ok = line > orig_entry and close > orig_entry
        dist = close - orig_entry
    elif side == "short":
        ok = line < orig_entry and close < orig_entry
        dist = orig_entry - close
    else:
        return None
    if not ok or dist <= 0:
        return None

    add_budget = max(float(equity), 0.0) * (config.ADD_RISK_PCT / 100.0)
    remaining = max(0.0, add_budget - max(float(add_risk_used), 0.0))
    dist_pct = dist / close * 100.0
    max_notional = max(float(equity), 0.0) * config.MAX_LEVERAGE
    cur_notional = abs(int(current_contracts)) * close * config.CONTRACT_MULTIPLIER
    notional_room = max(0.0, max_notional - cur_notional)

    add_contracts = 0
    risk_delta = 0.0
    if dist_pct >= config.MIN_ADD_DIST_PCT and remaining > 0 and notional_room > 0:
        add_qty_coins = min(remaining / dist, notional_room / close)
        contracts = int(round(add_qty_coins / config.CONTRACT_MULTIPLIER))
        while contracts >= config.ORDER_SIZE_MIN:
            actual_coins = contracts * config.CONTRACT_MULTIPLIER
            actual_risk = actual_coins * dist
            actual_notional = actual_coins * close
            if actual_risk <= remaining + 1e-9 and actual_notional <= notional_room + 1e-9:
                add_contracts = contracts
                risk_delta = actual_risk
                break
            contracts -= 1

    return BeAddPlan(
        sl_price=orig_entry,
        tp_price=orig_tp,
        add_contracts=add_contracts,
        risk_used_delta=risk_delta,
        dist=dist,
        dist_pct=dist_pct,
        reason="be_add" if add_contracts > 0 else "be_stop_only",
    )


def get_last_completed_bar_row(candles: List[Dict[str, Any]], bar_seconds: int, now_ts: float):
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
