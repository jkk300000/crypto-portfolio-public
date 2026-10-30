"""Squeeze Momentum + IBS + SuperTrend — public API only.

Pine-matching implementation is omitted from this public snapshot.
Copy to indicators.py to satisfy `from indicators import add_indicators`.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def compute_all(df: pd.DataFrame, **params) -> pd.DataFrame:
    raise NotImplementedError("indicators.py implementation is not published")


def add_indicators(candles: List[Dict[str, Any]], **params) -> List[Dict[str, Any]]:
    raise NotImplementedError("indicators.py implementation is not published")
