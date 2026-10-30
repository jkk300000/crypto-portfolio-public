"""Unit tests for EMA + RSI + IBS trail strategy helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from indicators import map_htf_ema_lagged, pine_ema, pine_rsi
from strategy import (
    check_entry,
    compute_position_size,
    next_trail_stop,
    round_to_tick,
)


def test_pine_ema_sma_seed():
    s = pd.Series([float(i) for i in range(1, 11)])
    out = pine_ema(s, 5)
    assert abs(float(out.iloc[4]) - 3.0) < 1e-9  # SMA(1..5)
    alpha = 2.0 / 6.0
    expected = alpha * 6.0 + (1.0 - alpha) * 3.0
    assert abs(float(out.iloc[5]) - expected) < 1e-9


def test_pine_rsi_flat_is_not_nan_after_warmup():
    s = pd.Series([100.0] * 40)
    s.iloc[20] = 110.0
    out = pine_rsi(s, 14)
    assert pd.notna(out.iloc[-1])
    assert 0.0 <= float(out.iloc[-1]) <= 100.0


def test_htf_ema_uses_previous_completed_bar():
    # 1h bars inside 8h bucket starting at 0; previous HTF bar start = -28800
    chart_ts = np.array([0, 3600, 7200], dtype=np.int64)
    htf_ts = np.array([-57600, -28800, 0], dtype=np.int64)
    htf_ema = np.array([10.0, 20.0, 30.0])
    mapped = map_htf_ema_lagged(chart_ts, htf_ts, htf_ema, 28800)
    assert np.allclose(mapped, [20.0, 20.0, 20.0])


def test_position_size_matches_pine():
    close = 100_000.0
    sl_dist = 1000.0  # 1.0%
    equity = 10_000.0
    # sl 1% <= 2%, lev = min(30, 1.5/1) = 1.5
    # btc = 10000 * 1.5 / 100000 = 0.15
    # contracts = 0.15 / 0.0001 = 1500
    size, sl_pct, lev = compute_position_size(equity, close, sl_dist)
    assert abs(sl_pct - 1.0) < 1e-9
    assert abs(lev - 1.5) < 1e-9
    assert size == 1500


def test_skip_when_sl_too_wide():
    size, sl_pct, lev = compute_position_size(10_000.0, 100_000.0, 3000.0)  # 3% > 2%
    assert sl_pct > config.MAX_SL_PCT
    assert size == 0
    assert lev == 0.0


def _row(**kwargs):
    base = {
        "t": 1,
        "c": 100_000.0,
        "ema": 99_000.0,
        "rsi1": 55.0,
        "rsi2": 55.0,
        "rsi3": 55.0,
        "ibs": 0.10,
    }
    base.update(kwargs)
    return base


def test_long_entry_requires_ema_rsi_ibs():
    long_ok, short_ok, long_info, short_info = check_entry([_row()], 0, 10_000.0)
    assert long_ok and long_info is not None
    assert not short_ok and short_info is None
    assert long_info.sl_price == round_to_tick(99_000.0, config.PRICE_TICK)


def test_long_blocked_by_ibs():
    long_ok, short_ok, _, _ = check_entry([_row(ibs=0.50)], 0, 10_000.0)
    assert not long_ok
    assert not short_ok


def test_short_entry():
    row = _row(c=99_000.0, ema=100_000.0, rsi1=40.0, rsi2=40.0, rsi3=40.0, ibs=0.95)
    long_ok, short_ok, long_info, short_info = check_entry([row], 0, 10_000.0)
    assert not long_ok and long_info is None
    assert short_ok and short_info is not None
    assert short_info.sl_price == round_to_tick(100_000.0, config.PRICE_TICK)


def test_trail_only_favorable():
    assert next_trail_stop("long", 100.0, 101.0, 0.1) == 101.0
    assert next_trail_stop("long", 100.0, 99.0, 0.1) is None
    assert next_trail_stop("short", 100.0, 99.0, 0.1) == 99.0
    assert next_trail_stop("short", 100.0, 101.0, 0.1) is None
