"""Persistent trade state for restart recovery."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class TradeState:
    direction: int = 0  # 1 long, -1 short, 0 flat
    entry_price: float | None = None
    initial_stop: float | None = None
    hard_stop: float | None = None
    take_profit1: float | None = None
    take_profit2: float | None = None
    initial_qty_contracts: int = 0
    tp1_qty_contracts: int = 0
    tp2_qty_contracts: int = 0
    tp1_done: bool = False
    tp2_done: bool = False
    entry_bar_time: int | None = None
    last_processed_bar: int | None = None
    sl_order_id: str | None = None
    tp1_order_id: str | None = None
    tp2_order_id: str | None = None

    def clear_position(self) -> None:
        self.direction = 0
        self.entry_price = None
        self.initial_stop = None
        self.hard_stop = None
        self.take_profit1 = None
        self.take_profit2 = None
        self.initial_qty_contracts = 0
        self.tp1_qty_contracts = 0
        self.tp2_qty_contracts = 0
        self.tp1_done = False
        self.tp2_done = False
        self.entry_bar_time = None
        self.sl_order_id = None
        self.tp1_order_id = None
        self.tp2_order_id = None


class StateStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> TradeState:
        if not os.path.exists(self.path):
            return TradeState()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw: dict[str, Any] = json.load(f)
            base = TradeState()
            for k in TradeState.__dataclass_fields__:
                if k in raw and raw[k] is not None:
                    setattr(base, k, raw[k])
            return base
        except Exception as e:
            log.error("state load failed: %s — starting fresh", e)
            return TradeState()

    def save(self, state: TradeState) -> None:
        data = asdict(state)
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="state_", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
