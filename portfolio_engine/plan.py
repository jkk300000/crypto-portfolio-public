# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping

from .book import BookSnapshot
from .config import DONOR_KEEP_PCT, ETH_RECEIVE_FIRST, MIN_TRANSFER, SLEEVES


@dataclass
class TransferLeg:
    src: str
    dst: str
    amount: float

    def to_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "amount": self.amount}


def _floor_cents(x: float) -> float:
    return math_floor_cents(x)


def math_floor_cents(x: float) -> float:
    return float(int(x * 100.0)) / 100.0


def sendable_amounts(
    snap: BookSnapshot,
    available: Mapping[str, float],
    keep_pct: float = DONOR_KEEP_PCT,
) -> Dict[str, float]:
    """How much each overweight sleeve can actually send (unused cash only)."""
    out = {}
    for k in SLEEVES:
        keep = snap.allocated[k] * keep_pct
        free = max(0.0, float(available.get(k, 0.0)) - keep)
        out[k] = math_floor_cents(max(0.0, min(snap.surplus[k], free)))
    return out


def plan_transfers(
    snap: BookSnapshot,
    available: Mapping[str, float],
    min_amount: float = MIN_TRANSFER,
    keep_pct: float = DONOR_KEEP_PCT,
    eth_receive_first: bool = ETH_RECEIVE_FIRST,
) -> List[TransferLeg]:
    sendable = sendable_amounts(snap, available, keep_pct=keep_pct)
    receivers = [
        (k, snap.deficit[k])
        for k in SLEEVES
        if snap.deficit[k] >= min_amount
    ]
    if eth_receive_first:
        receivers.sort(key=lambda kv: (0 if kv[0] == "eth" else 1, -kv[1]))
    else:
        receivers.sort(key=lambda kv: -kv[1])

    donors = [k for k in SLEEVES if sendable[k] >= min_amount]
    donors.sort(key=lambda k: -sendable[k])
    if eth_receive_first and "eth" in donors:
        donors = [k for k in donors if k != "eth"] + ["eth"]

    legs: List[TransferLeg] = []
    pool = dict(sendable)
    for dst, need in receivers:
        remain = need
        for src in donors:
            if src == dst:
                continue
            take = math_floor_cents(min(remain, pool[src]))
            if take < min_amount:
                continue
            legs.append(TransferLeg(src=src, dst=dst, amount=take))
            pool[src] -= take
            remain -= take
            if remain < min_amount:
                break
    return legs
