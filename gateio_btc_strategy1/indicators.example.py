"""EMA + RSI + IBS — public API only.

Pine-matching implementation is omitted from this public snapshot.
Copy to indicators.py to satisfy `from indicators import add_indicators`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


def compute_all(
    chart_candles: List[Dict[str, Any]],
    htf_candles: Optional[List[Dict[str, Any]]] = None,
    **params,
) -> pd.DataFrame:
    raise NotImplementedError("indicators.py implementation is not published")


def add_indicators(
    chart_candles: List[Dict[str, Any]],
    htf_candles: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raise NotImplementedError("indicators.py implementation is not published")
