"""
Print EMA + RSI + IBS from real Gate.io OHLC.

Run:
    python test_indicators_real_ohlc.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests

import config
from indicators import add_indicators
from strategy import check_entry


def fetch_gate_candles(contract: str, interval: str, limit: int) -> List[Dict]:
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
    chart = fetch_gate_candles(config.CONTRACT, config.CANDLE_INTERVAL, config.CANDLE_LIMIT)
    htf = fetch_gate_candles(config.CONTRACT, config.EMA_TF_INTERVAL, config.EMA_TF_CANDLE_LIMIT)
    out = add_indicators(chart, htf_candles=htf)
    row = out[-2] if len(out) > 1 else out[-1]
    kst = timezone(timedelta(hours=9))
    ts_str = datetime.fromtimestamp(int(row["t"]), tz=kst).strftime("%Y-%m-%d %H:%M:%S %Z")
    long_ok, short_ok, long_info, short_info = check_entry(out, len(out) - 2 if len(out) > 1 else 0, 10_000.0)

    print(f"contract: {config.CONTRACT}")
    print(f"interval: {config.CANDLE_INTERVAL}  ema_tf: {config.EMA_TF_INTERVAL} len={config.EMA_LEN}")
    print(f"bar_time: {ts_str} (completed)")
    print(f"close: {row['c']}")
    print(f"ema: {row['ema']}")
    print(f"rsi7/14/21: {row['rsi1']} / {row['rsi2']} / {row['rsi3']}")
    print(f"ibs: {row['ibs']}")
    print(f"long_ok={long_ok} short_ok={short_ok}")
    if long_info:
        print(f"long sl={long_info.sl_price} tp={long_info.tp_price} lev={long_info.leverage:.2f} size={long_info.size}")
    if short_info:
        print(f"short sl={short_info.sl_price} tp={short_info.tp_price} lev={short_info.leverage:.2f} size={short_info.size}")


if __name__ == "__main__":
    main()
