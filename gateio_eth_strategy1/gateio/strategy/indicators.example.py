"""Legacy nested indicators — public API only.

Implementation is omitted from this public snapshot.
Copy to indicators.py only if you still import this path.
"""
from typing import Any, Dict, List


def add_indicators(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raise NotImplementedError("indicators.py implementation is not published")
