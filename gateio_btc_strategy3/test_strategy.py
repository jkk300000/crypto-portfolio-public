"""Unit tests for SQZMOM + IBS + SuperTrend trail strategy helpers."""
from __future__ import annotations

import pandas as pd

import config
from indicators import linreg
from strategy import (
    check_entry,
    check_supertrend_exit,
    compute_position_size,
)


def test_linreg_perfect_line():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = linreg(s, 5, 0)
    assert abs(float(out.iloc[-1]) - 5.0) < 1e-9


def test_ibs_from_entry_row():
    row = {"c": 92.0, "h": 100.0, "l": 90.0, "o": 91.0}
    ibs = (row["c"] - row["l"]) / (row["h"] - row["l"])
    assert abs(ibs - 0.2) < 1e-12


def test_position_size_matches_pine():
    close = 100_000.0
    sl_dist = 1000.0  # 1.0%
    equity = 10_000.0
    # sl 1% <= 1.5%, lev = min(5, 2/1) = 2
    # btc = 10000 * 2 / 100000 = 0.2
    # contracts = 0.2 / 0.0001 = 2000
    size, sl_pct, lev = compute_position_size(equity, close, sl_dist)
    assert abs(sl_pct - 1.0) < 1e-9
    assert abs(lev - 2.0) < 1e-9
    assert size == 2000


def test_skip_when_sl_too_wide():
    size, sl_pct, lev = compute_position_size(10_000.0, 100_000.0, 2000.0)  # 2% > 1.5%
    assert sl_pct > config.MAX_SL_PCT
    assert size == 0
    assert lev == 0.0


def test_long_entry_requires_val_rising_and_ibs():
    row = {
        "t": 1,
        "c": 100_000.0,
        "sqz_val": 1.0,
        "val_rising": True,
        "val_falling": False,
        "ibs": 0.10,
        "sl_dist": 1000.0,
        "buy_signal": False,
        "sell_signal": False,
    }
    long_ok, short_ok, long_info, short_info = check_entry([row], 0, 10_000.0)
    assert long_ok and long_info is not None
    assert not short_ok and short_info is None
    assert abs(long_info.sl_price - (100_000.0 - 1000.0)) < 1e-9
    assert abs(long_info.tp_price - (100_000.0 + 1000.0 * config.RR_RATIO)) < 1e-9


def test_short_blocked_by_ibs():
    row = {
        "t": 1,
        "c": 100_000.0,
        "sqz_val": -1.0,
        "val_rising": False,
        "val_falling": True,
        "ibs": 0.50,
        "sl_dist": 1000.0,
    }
    long_ok, short_ok, _, _ = check_entry([row], 0, 10_000.0)
    assert not long_ok
    assert not short_ok


def test_supertrend_exit_skips_entry_bar():
    row = {"sell_signal": True, "buy_signal": False}
    assert not check_supertrend_exit(row, "long", after_entry=False)
    assert check_supertrend_exit(row, "long", after_entry=True)
    assert not check_supertrend_exit(row, "short", after_entry=True)


if __name__ == "__main__":
    test_linreg_perfect_line()
    test_ibs_from_entry_row()
    test_position_size_matches_pine()
    test_skip_when_sl_too_wide()
    test_long_entry_requires_val_rising_and_ibs()
    test_short_blocked_by_ibs()
    test_supertrend_exit_skips_entry_bar()
    print("ok")
