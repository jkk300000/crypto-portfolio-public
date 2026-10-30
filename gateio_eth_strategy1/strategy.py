"""
SuperTrend + Bollinger Bands 진입 조건 (gateio 패턴).
- check_entry(candles_with_indicators, last_bar_index) → (long_ok, short_ok, size_info)
- 완성된 봉 기준: buy_signal/sell_signal, BB 스탑 허용, 포지션 사이즈(95% 자본).
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

import config


@dataclass
class EntryInfo:
    """진입 시 사용할 SL/TP/수량."""
    side: str  # "long" | "short"
    size: str  # 계약 수 문자열
    sl_price: float
    tp_price: float


def rr_ratio_for_side(side: str) -> float:
    if side == "short":
        return float(getattr(config, "RR_RATIO_SHORT", getattr(config, "rr_ratio_short", 2.0)))
    return float(getattr(config, "RR_RATIO", getattr(config, "rr_ratio", 2.0)))


def _safe_pct(x: float, ref: float) -> float:
    if ref <= 0:
        return 0.0
    return (x / ref) * 100.0


def check_entry(
    candles_with_indicators: List[Dict[str, Any]],
    last_bar_index: int,
    equity: float,
) -> Tuple[bool, bool, Optional[EntryInfo], Optional[EntryInfo]]:
    """
    완성된 봉(last_bar_index) 기준 진입 조건.
    equity: 선물 계좌 자산(USDT). 포지션 사이즈 = equity * equity_usage_ratio * leverage / (close * CONTRACT_MULTIPLIER).
    returns: (long_ok, short_ok, long_entry_info, short_entry_info).
    """
    if last_bar_index < 0 or last_bar_index >= len(candles_with_indicators):
        return False, False, None, None
    row = candles_with_indicators[last_bar_index]
    c = row.get("c")
    bb_lower = row.get("bb_lower")
    bb_upper = row.get("bb_upper")
    buy_signal = row.get("buy_signal")
    sell_signal = row.get("sell_signal")
    if c is None or bb_lower is None or bb_upper is None:
        return False, False, None, None
    close_safe = float(c) if float(c) > 0 else 1e-6
    long_stop = float(bb_lower)
    short_stop = float(bb_upper)
    long_sl_dist = close_safe - long_stop
    short_sl_dist = short_stop - close_safe
    long_sl_pct = _safe_pct(long_sl_dist, close_safe)
    short_sl_pct = _safe_pct(short_sl_dist, close_safe)
    # 테스트 모드: 원본 진입 조건 그대로, 최대 스탑로스 비율만 TEST_MAX_SL_PCT로 관대하게 적용
    _max_sl_pct = (
        getattr(config, "TEST_MAX_SL_PCT", 10.0)
        if getattr(config, "TEST_MODE", False)
        else getattr(config, "MAX_SL_PCT", getattr(config, "max_sl_pct", 2.3))
    )
    long_sl_allowed = long_sl_pct > 0 and long_sl_pct <= _max_sl_pct
    short_sl_allowed = short_sl_pct > 0 and short_sl_pct <= _max_sl_pct
    max_lev = getattr(config, "MAX_LEVERAGE", getattr(config, "max_leverage", 2.0))
    max_risk = getattr(config, "MAX_RISK_PCT", getattr(config, "max_risk_pct", 2.3))
    max_sl = _max_sl_pct
    long_tp = close_safe + long_sl_dist * rr_ratio_for_side("long")
    short_tp = close_safe - short_sl_dist * rr_ratio_for_side("short")
    long_lev = min(max_lev, max_sl / max(long_sl_pct, 0.01), max_risk / max(long_sl_pct, 0.01))
    short_lev = min(max_lev, max_sl / max(short_sl_pct, 0.01), max_risk / max(short_sl_pct, 0.01))
    long_ok = bool(buy_signal) and long_sl_allowed
    short_ok = bool(sell_signal) and short_sl_allowed
    mult = getattr(config, "CONTRACT_MULTIPLIER", 0.0001)
    equity_pct = getattr(config, "equity_usage_ratio", 0.95)
    order_min = getattr(config, "ORDER_SIZE_MIN", 1)
    equity_safe = max(float(equity), 1e-6) * equity_pct
    long_size = 0
    short_size = 0
    if long_ok:
        long_size = int(round((equity_safe * long_lev) / (close_safe * mult)))
        long_size = max(order_min, long_size)
    if short_ok:
        short_size = int(round((equity_safe * short_lev) / (close_safe * mult)))
        short_size = max(order_min, short_size)
    long_info = EntryInfo("long", str(long_size), long_stop, long_tp) if long_ok and long_size > 0 else None
    short_info = EntryInfo("short", str(short_size), short_stop, short_tp) if short_ok and short_size > 0 else None
    return long_ok, short_ok, long_info, short_info


def get_last_completed_bar_row(candles: List[Dict], bar_seconds: int, now_ts: float):
    """완성된 가장 최근 봉 1개. bar_seconds=30*60, now_ts=time.time()."""
    if not candles:
        return None, None
    for i in range(len(candles) - 1, -1, -1):
        row = candles[i]
        bar_t = row.get("t")
        if bar_t is None:
            continue
        bar_end = bar_t + bar_seconds
        if bar_end <= now_ts:
            return row, i
    return None, None


def check_entry_relaxed(
    candles_with_indicators: List[Dict[str, Any]],
    last_bar_index: int,
    equity: float,
) -> Tuple[bool, bool, Optional[EntryInfo], Optional[EntryInfo]]:
    """
    완화된 진입 조건: 진입은 supertrend_trend만 사용 (trend==1 롱, trend==-1 숏).
    SL/TP는 기존과 동일하게 볼린저 밴드(bb_lower, bb_upper)와 진입가(close) 차이 기반.
    """
    if last_bar_index < 0 or last_bar_index >= len(candles_with_indicators):
        return False, False, None, None
    row = candles_with_indicators[last_bar_index]
    c = row.get("c")
    trend = row.get("supertrend_trend")
    bb_lower = row.get("bb_lower")
    bb_upper = row.get("bb_upper")
    if c is None or trend is None or bb_lower is None or bb_upper is None:
        return False, False, None, None
    close_safe = float(c) if float(c) > 0 else 1e-6
    mult = getattr(config, "CONTRACT_MULTIPLIER", 0.0001)
    equity_pct = getattr(config, "equity_usage_ratio", 0.95)
    order_min = getattr(config, "ORDER_SIZE_MIN", 1)
    equity_safe = max(float(equity), 1e-6) * equity_pct

    long_ok = trend == 1
    short_ok = trend == -1

    # 기존과 동일: SL = bb_lower(롱) / bb_upper(숏), TP = close ± (진입가와 lower/upper 차이) * rr
    long_stop = float(bb_lower)
    short_stop = float(bb_upper)
    long_sl_dist = close_safe - long_stop
    short_sl_dist = short_stop - close_safe
    long_tp = close_safe + long_sl_dist * rr_ratio_for_side("long")
    short_tp = close_safe - short_sl_dist * rr_ratio_for_side("short")

    long_size = 0
    short_size = 0
    if long_ok:
        long_size = int(round((equity_safe * 1.0) / (close_safe * mult)))
        long_size = max(order_min, long_size)
    if short_ok:
        short_size = int(round((equity_safe * 1.0) / (close_safe * mult)))
        short_size = max(order_min, short_size)

    long_info = EntryInfo("long", str(long_size), long_stop, long_tp) if long_ok and long_size > 0 else None
    short_info = EntryInfo("short", str(short_size), short_stop, short_tp) if short_ok and short_size > 0 else None
    return long_ok, short_ok, long_info, short_info

