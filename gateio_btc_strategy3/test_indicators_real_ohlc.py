"""
Print SQZMOM + IBS + SuperTrend values from real Gate.io OHLC data.

Run:
    python test_indicators_real_ohlc.py
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List

import requests

import config
from indicators import add_indicators


def fetch_gate_candles(contract: str = "BTC_USDT", interval: str = "1h", limit: int = 300) -> List[Dict]:
    url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
    params = {"contract": contract, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    candles: List[Dict] = []
    for x in raw:
        if isinstance(x, dict):
            candles.append(
                {
                    "t": int(x["t"]),
                    "o": float(x["o"]),
                    "h": float(x["h"]),
                    "l": float(x["l"]),
                    "c": float(x["c"]),
                    "v": float(x.get("v", 0.0)),
                }
            )
        else:
            candles.append(
                {
                    "t": int(x[0]),
                    "v": float(x[1]),
                    "c": float(x[2]),
                    "h": float(x[3]),
                    "l": float(x[4]),
                    "o": float(x[5]),
                }
            )
    candles.sort(key=lambda r: r["t"])
    return candles


def main() -> None:
    candles = fetch_gate_candles(contract=config.CONTRACT, interval=config.CANDLE_INTERVAL, limit=config.CANDLE_LIMIT)
    out = add_indicators(
        candles,
        bb_length=config.BB_LENGTH,
        bb_mult=config.BB_MULT,
        kc_length=config.KC_LENGTH,
        kc_mult=config.KC_MULT,
        use_true_range_kc=config.USE_TRUE_RANGE_KC,
        st_atr_period=config.ST_ATR_PERIOD,
        st_multiplier=config.ST_MULTIPLIER,
        st_change_atr=config.ST_CHANGE_ATR,
        atr_len=config.ATR_LEN,
        atr_mult=config.ATR_MULT,
        rr_ratio=config.RR_RATIO,
    )

    row = out[-2] if len(out) > 1 else out[-1]
    kst = timezone(timedelta(hours=9))
    ts_str = datetime.fromtimestamp(int(row["t"]), tz=kst).strftime("%Y-%m-%d %H:%M:%S %Z")

    print(f"contract: {config.CONTRACT}")
    print(f"interval: {config.CANDLE_INTERVAL}")
    print(f"bar_time: {ts_str} (completed)")
    print(f"close: {row['c']}")
    print(f"sqz_val: {row['sqz_val']}")
    print(f"val_rising: {row['val_rising']}")
    print(f"val_falling: {row['val_falling']}")
    print(f"ibs: {row['ibs']}")
    print(f"sl_dist: {row['sl_dist']}")
    print(f"sl_pct: {row['sl_pct']}")
    print(f"supertrend_trend: {row['supertrend_trend']}")
    print(f"buy_signal: {row['buy_signal']}")
    print(f"sell_signal: {row['sell_signal']}")


if __name__ == "__main__":
    main()
