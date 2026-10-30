# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping

from .config import SLEEVES, WEIGHTS


@dataclass
class BookSnapshot:
    total: float
    weights: Dict[str, float]
    cash: Dict[str, float]
    allocated: Dict[str, float]
    actual_weight: Dict[str, float] = field(default_factory=dict)
    deficit: Dict[str, float] = field(default_factory=dict)
    surplus: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "weights": dict(self.weights),
            "cash": dict(self.cash),
            "allocated": dict(self.allocated),
            "actual_weight": dict(self.actual_weight),
            "deficit": dict(self.deficit),
            "surplus": dict(self.surplus),
        }


def allocate(
    cash: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> BookSnapshot:
    """T = sum of sleeve cash. Each bot should size on allocated[i] = T × w_i."""
    w = dict(weights or WEIGHTS)
    missing = [k for k in SLEEVES if k not in cash]
    if missing:
        raise KeyError(f"missing sleeves: {missing}")
    cleaned = {k: max(float(cash[k]), 0.0) for k in SLEEVES}
    total = sum(cleaned.values())
    allocated = {k: total * float(w[k]) for k in SLEEVES}
    actual = {k: (cleaned[k] / total if total > 0 else 0.0) for k in SLEEVES}
    deficit = {k: max(0.0, allocated[k] - cleaned[k]) for k in SLEEVES}
    surplus = {k: max(0.0, cleaned[k] - allocated[k]) for k in SLEEVES}
    return BookSnapshot(
        total=total,
        weights={k: float(w[k]) for k in SLEEVES},
        cash=cleaned,
        allocated=allocated,
        actual_weight=actual,
        deficit=deficit,
        surplus=surplus,
    )
