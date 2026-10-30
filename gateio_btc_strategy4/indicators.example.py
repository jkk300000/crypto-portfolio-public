"""Donchian + IBS + ATR — public API only.

Pine-matching implementation is omitted from this public snapshot.
Copy to indicators.py to satisfy `from indicators import candles_to_df, build_indicators`.
"""
from __future__ import annotations

import pandas as pd

from config import STRATEGY, StrategyConfig


def candles_to_df(candles: list[dict], closed_only: bool = True) -> pd.DataFrame:
    raise NotImplementedError("indicators.py implementation is not published")


def compute_atr(df: pd.DataFrame, length: int) -> pd.Series:
    raise NotImplementedError("indicators.py implementation is not published")


def build_indicators(df: pd.DataFrame, cfg: StrategyConfig = STRATEGY) -> pd.DataFrame:
    raise NotImplementedError("indicators.py implementation is not published")
