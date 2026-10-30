"""HTF pivot S/R + IBS + SuperTrend + ATR stop — public API only.

Pine-matching implementation is omitted from this public snapshot.
Copy to indicators.py to satisfy `from indicators import add_indicators`.
"""
from __future__ import annotations

from typing import Any, Dict, List


def add_indicators(
    candles: List[Dict[str, Any]],
    st_atr_period: int,
    st_multiplier: float,
    st_use_ta_atr: bool,
    atr_stop_period: int,
    sr_left_bars: int = 1,
    sr_right_bars: int = 1,
    sr_bar_minutes: int = 240,
) -> List[Dict[str, Any]]:
    raise NotImplementedError("indicators.py implementation is not published")
