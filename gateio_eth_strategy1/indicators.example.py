"""SuperTrend + Bollinger Bands — public API only.

Pine-matching implementation is omitted from this public snapshot.
Copy to indicators.py to satisfy `from indicators import add_indicators`.
"""
from __future__ import annotations

import pandas as pd


def compute_all(
    df: pd.DataFrame,
    atr_period: int = 10,
    atr_mult: float = 3.0,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    bb_ma_type: str = "SMA",
) -> pd.DataFrame:
    raise NotImplementedError("indicators.py implementation is not published")


def add_indicators(
    candles: list,
    atr_period: int = 10,
    atr_mult: float = 3.0,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    bb_ma_type: str = "SMA",
) -> list:
    raise NotImplementedError("indicators.py implementation is not published")
