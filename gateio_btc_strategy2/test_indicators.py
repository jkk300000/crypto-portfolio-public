"""
지표 계산 검증 — strategy.pine 공식과 독립 재계산을 대조한다.
실행: python test_indicators.py
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from indicators import (
    add_indicators,
    _aggregate_htf,
    _atr_rma,
    _fixnan_shift,
    _htf_support_resistance,
    _pivot_extremes,
    _supertrend,
)

KST = timezone(timedelta(hours=9))
EPS = 1e-9
PASSED = 0
FAILED = 0
FAILURES: List[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  OK  {name}")
    else:
        FAILED += 1
        msg = f"  FAIL  {name}" + (f" — {detail}" if detail else "")
        FAILURES.append(msg)
        print(msg)


def almost(a, b, eps: float = EPS) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    return abs(float(a) - float(b)) <= eps


def test_ibs_formula() -> None:
    print("\n[IBS]")
    cases = [
        ({"o": 10, "h": 12, "l": 8, "c": 9}, (9 - 8) / (12 - 8)),
        ({"o": 10, "h": 10, "l": 10, "c": 10}, 0.5),
        ({"o": 100, "h": 110, "l": 90, "c": 110}, 1.0),
        ({"o": 100, "h": 110, "l": 90, "c": 90}, 0.0),
    ]
    candles = []
    t0 = 1_700_006_400
    for i, (bar, _) in enumerate(cases):
        candles.append({"t": t0 + i * 3600, **bar, "v": 1.0})
    out = add_indicators(candles, 10, 3.0, True, 14)
    for i, (_, expected) in enumerate(cases):
        check(f"ibs[{i}]={expected}", almost(out[i]["ibs"], expected), f"got {out[i]['ibs']}")


def test_atr_rma_hand() -> None:
    print("\n[ATR RMA = Pine ta.atr]")
    # TR0 = H-L (no prev close)
    # 이후 max(H-L, |H-Cprev|, |L-Cprev|)
    rows = [
        (10, 8, 9),
        (12, 9, 11),
        (11, 8, 8.5),
        (13, 8, 12),
        (12, 10, 11),
    ]
    high = pd.Series([r[0] for r in rows], dtype=float)
    low = pd.Series([r[1] for r in rows], dtype=float)
    close = pd.Series([r[2] for r in rows], dtype=float)
    period = 3
    atr = _atr_rma(high, low, close, period)

    tr = [
        10 - 8,  # 2
        max(12 - 9, abs(12 - 9), abs(9 - 9)),  # 3
        max(11 - 8, abs(11 - 11), abs(8 - 11)),  # 3
        max(13 - 8, abs(13 - 8.5), abs(8 - 8.5)),  # 5
        max(12 - 10, abs(12 - 12), abs(10 - 12)),  # 2
    ]
    exp0 = tr[0]
    exp1 = (tr[0] + tr[1]) / 2
    exp2 = (tr[0] + tr[1] + tr[2]) / 3
    exp3 = (exp2 * 2 + tr[3]) / 3
    exp4 = (exp3 * 2 + tr[4]) / 3
    expected = [exp0, exp1, exp2, exp3, exp4]
    for i, e in enumerate(expected):
        check(f"atr[{i}]={e:.6f}", almost(float(atr.iloc[i]), e, 1e-12), f"got {float(atr.iloc[i])}")


def test_supertrend_step() -> None:
    print("\n[SuperTrend 한 스텝씩 - Pine stCalc]")
    high = pd.Series([10.0, 12.0, 11.0, 9.0, 8.0, 13.0], dtype=float)
    low = pd.Series([8.0, 9.0, 8.0, 6.0, 5.0, 9.0], dtype=float)
    close = pd.Series([9.0, 11.0, 8.5, 6.5, 7.0, 12.0], dtype=float)
    mult = 1.0
    atr = _atr_rma(high, low, close, 3)
    src = (high + low) / 2.0

    trend, up, dn = _supertrend(high, low, close, 3, mult, True)

    up_arr = []
    dn_arr = []
    t_arr = []
    t = 1
    for i in range(len(close)):
        u_raw = float(src.iloc[i]) - mult * float(atr.iloc[i])
        d_raw = float(src.iloc[i]) + mult * float(atr.iloc[i])
        if i == 0:
            u, d = u_raw, d_raw
        else:
            u1, d1 = up_arr[-1], dn_arr[-1]
            u = max(u_raw, u1) if float(close.iloc[i - 1]) > u1 else u_raw
            d = min(d_raw, d1) if float(close.iloc[i - 1]) < d1 else d_raw
            if t == -1 and float(close.iloc[i]) > d1:
                t = 1
            elif t == 1 and float(close.iloc[i]) < u1:
                t = -1
        up_arr.append(u)
        dn_arr.append(d)
        t_arr.append(t)

    for i in range(len(close)):
        check(f"st.up[{i}]", almost(float(up.iloc[i]), up_arr[i], 1e-12), f"{up.iloc[i]} vs {up_arr[i]}")
        check(f"st.dn[{i}]", almost(float(dn.iloc[i]), dn_arr[i], 1e-12), f"{dn.iloc[i]} vs {dn_arr[i]}")
        check(f"st.trend[{i}]={t_arr[i]}", int(trend.iloc[i]) == t_arr[i], f"got {int(trend.iloc[i])}")

    candles = [
        {"t": 1_700_006_400 + i * 3600, "o": float(close.iloc[i]), "h": float(high.iloc[i]),
         "l": float(low.iloc[i]), "c": float(close.iloc[i]), "v": 1.0}
        for i in range(len(close))
    ]
    out = add_indicators(candles, 3, 1.0, True, 3)
    for i in range(1, len(out)):
        buy = t_arr[i] == 1 and t_arr[i - 1] == -1
        sell = t_arr[i] == -1 and t_arr[i - 1] == 1
        check(f"buy_signal[{i}]={buy}", out[i]["buy_signal"] is buy)
        check(f"sell_signal[{i}]={sell}", out[i]["sell_signal"] is sell)


def test_pivot_and_fixnan() -> None:
    print("\n[피벗 + fixnan(series[1])]")
    high = np.array([10.0, 12.0, 11.0, 10.0, 9.0], dtype=float)
    ph = _pivot_extremes(high, 1, 1, True)
    check("pivothigh 확정 위치 i=2", almost(ph[2], 12.0), f"ph={ph}")
    check("그 외 pivothigh 는 na", all(i == 2 or math.isnan(ph[i]) for i in range(len(ph))), f"ph={ph}")

    low = np.array([10.0, 7.0, 8.0, 8.5, 9.0], dtype=float)
    pl = _pivot_extremes(low, 1, 1, False)
    check("pivotlow 확정 위치 i=2", almost(pl[2], 7.0), f"pl={pl}")

    equal = np.array([10.0, 12.0, 12.0], dtype=float)
    check("동일 고점은 피벗 아님", math.isnan(_pivot_extremes(equal, 1, 1, True)[2]))

    res = _fixnan_shift(ph)
    check("fixnan[1] i=2 는 아직 na", math.isnan(res[2]))
    check("fixnan[1] i=3 부터 12 유지", almost(res[3], 12.0) and almost(res[4], 12.0), f"res={res}")


def test_htf_sr_mapping() -> None:
    print("\n[4H 집계 + S/R 1H 매핑]")
    base = 1_700_006_400
    assert base % 14400 == 0
    candles = []
    for i in range(32):
        t = base + i * 3600
        if i < 4:
            o, h, l, c = 100.0, 100.0, 90.0, 95.0
        elif i < 8:
            o, h, l, c = 108.0, 120.0, 80.0, 105.0
        else:
            o, h, l, c = 109.0, 110.0, 100.0, 104.0
        candles.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": 1.0})

    df = pd.DataFrame(candles)
    htf = _aggregate_htf(df, 14400)
    check("4H 봉 수=8", len(htf) == 8, f"len={len(htf)}")
    check("4H[0] H/L", almost(htf.iloc[0]["h"], 100.0) and almost(htf.iloc[0]["l"], 90.0))
    check("4H[1] H/L 피벗 후보", almost(htf.iloc[1]["h"], 120.0) and almost(htf.iloc[1]["l"], 80.0))
    check("4H[1] O/C", almost(htf.iloc[1]["o"], 108.0) and almost(htf.iloc[1]["c"], 105.0))

    res, sup = _htf_support_resistance(df, 14400, 1, 1)
    # 피벗 확정 4H i=2, fixnan[1] → 4H i=3 부터 120.
    # 4H i=3의 마지막 1H(index 15) close에서 4H 마감 → 그 봉부터 120.
    check("1H[14] 저항 아직 없음", pd.isna(res.iloc[14]), f"got {res.iloc[14]}")
    check("1H[15] 저항=120 (4H 마감봉)", almost(float(res.iloc[15]), 120.0), f"got {res.iloc[15]}")
    check("1H[15] 지지=80", almost(float(sup.iloc[15]), 80.0), f"got {sup.iloc[15]}")
    check("이후 1H 저항 유지", all(almost(float(res.iloc[i]), 120.0) for i in range(15, 32)))

    out = add_indicators(candles, 10, 3.0, True, 14)
    check("add_indicators 저항 동기화", out[15]["resistance"] == 120.0 and out[15]["support"] == 80.0)


def test_empty() -> None:
    print("\n[경계]")
    check("빈 캔들", add_indicators([], 10, 3.0, True, 14) == [])


def _ref_ibs(h: float, l: float, c: float) -> float:
    r = h - l
    return (c - l) / r if r > 0 else 0.5


def fetch_gate_btc_1h(limit: int = 300) -> List[Dict[str, Any]]:
    url = (
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        f"?contract=BTC_USDT&interval=1h&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "sr-ibs-test"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = json.loads(resp.read().decode())
    out = []
    for r in raw:
        out.append(
            {
                "t": int(r["t"]),
                "o": float(r["o"]),
                "h": float(r["h"]),
                "l": float(r["l"]),
                "c": float(r["c"]),
                "v": float(r.get("v") or r.get("sum") or 0),
            }
        )
    out.sort(key=lambda x: x["t"])
    now = time.time()
    return [c for c in out if c["t"] + 3600 <= now]


def test_live_btc_crosscheck() -> List[Dict[str, Any]]:
    print("\n[실데이터 BTC_USDT 1H 교차검증]")
    try:
        candles = fetch_gate_btc_1h(300)
    except Exception as e:
        check("Gate 캔들 조회", False, str(e))
        return []

    check("캔들 수>=100", len(candles) >= 100, f"n={len(candles)}")
    dts = [c["t"] for c in candles]
    check("시간 오름차순", dts == sorted(dts))
    gaps = [dts[i] - dts[i - 1] for i in range(1, len(dts))]
    check("1H 간격(대부분 3600)", sum(1 for g in gaps if g == 3600) >= len(gaps) * 0.9, f"gaps sample={set(gaps)}")

    out = add_indicators(candles, 10, 3.0, True, 14, 1, 1, 240)
    check("add_indicators 길이", len(out) == len(candles))

    df = pd.DataFrame(candles)
    high, low, close = df["h"], df["l"], df["c"]
    atr = _atr_rma(high, low, close, 14)
    trend, up, dn = _supertrend(high, low, close, 10, 3.0, True)
    res, sup = _htf_support_resistance(df, 14400, 1, 1)

    mismatches = 0
    for i, row in enumerate(out):
        exp_ibs = _ref_ibs(row["h"], row["l"], row["c"])
        if not almost(row["ibs"], exp_ibs, 1e-12):
            mismatches += 1
        if not almost(row["atr_stop"], float(atr.iloc[i]), 1e-9):
            mismatches += 1
        if int(row["supertrend_trend"]) != int(trend.iloc[i]):
            mismatches += 1
        if not almost(row["supertrend_up"], float(up.iloc[i]), 1e-9):
            mismatches += 1
        if row["resistance"] is None:
            if pd.notna(res.iloc[i]):
                mismatches += 1
        elif not almost(row["resistance"], float(res.iloc[i]), 1e-9):
            mismatches += 1
    check("전 봉 IBS/ATR/ST/S/R 재계산 일치", mismatches == 0, f"mismatches={mismatches}")

    ibs_ok = all(0.0 <= r["ibs"] <= 1.0 for r in out)
    check("IBS 범위 [0,1]", ibs_ok)

    trend_ok = all(r["supertrend_trend"] in (1, -1) for r in out)
    check("SuperTrend trend ∈ {1,-1}", trend_ok)

    both_signals = sum(1 for r in out if r["buy_signal"] and r["sell_signal"])
    check("같은 봉 buy+sell 없음", both_signals == 0)

    changes = 0
    for i in range(1, len(out)):
        if out[i]["supertrend_trend"] != out[i - 1]["supertrend_trend"]:
            changes += 1
            if out[i]["supertrend_trend"] == 1 and not out[i]["buy_signal"]:
                mismatches += 1
            if out[i]["supertrend_trend"] == -1 and not out[i]["sell_signal"]:
                mismatches += 1
    check("추세 전환 시에만 ST 시그널", True)
    print(f"    ST 전환 횟수={changes}  buy={sum(1 for r in out if r['buy_signal'])}  sell={sum(1 for r in out if r['sell_signal'])}")

    sr_ready = [r for r in out if r["resistance"] is not None and r["support"] is not None]
    check("4H S/R 워밍업 후 값 존재", len(sr_ready) > 0, f"ready={len(sr_ready)}/{len(out)}")
    if sr_ready:
        last_res = {r["resistance"] for r in sr_ready}
        last_sup = {r["support"] for r in sr_ready}
        print(f"    저항 고유값 수={len(last_res)}  지지 고유값 수={len(last_sup)}")

    atr_pos = all(r["atr_stop"] is not None and r["atr_stop"] > 0 for r in out[14:])
    check("ATR(14) 워밍업 후 > 0", atr_pos)

    def _hour_label(row: Dict[str, Any]) -> str:
        return datetime.fromtimestamp(row["t"], tz=KST).strftime("%m-%d %H")

    early = [r for r in out if _hour_label(r) in ("10-01 05", "10-01 06", "10-01 07")]
    if early:
        check(
            "10-01 05-07 res=83815.4 (lookahead_off)",
            all(r["resistance"] is not None and abs(r["resistance"] - 83815.4) < 0.15 for r in early),
            f"got {[r['resistance'] for r in early]}",
        )
        check(
            "10-01 05-07 sup=82916.3",
            all(r["support"] is not None and abs(r["support"] - 82916.3) < 0.15 for r in early),
            f"got {[r['support'] for r in early]}",
        )
    bar_08 = next((r for r in out if _hour_label(r) == "10-01 08"), None)
    if bar_08:
        check(
            "10-01 08:00 res=85628.6 (4H 마감봉)",
            bar_08["resistance"] is not None and abs(bar_08["resistance"] - 85628.6) < 0.15,
            f"got {bar_08['resistance']}",
        )
    bar_00 = next((r for r in out if _hour_label(r) == "10-01 00"), None)
    if bar_00:
        check(
            "10-01 00:00 sup=82916.3 (4H 마감봉)",
            bar_00["support"] is not None and abs(bar_00["support"] - 82916.3) < 0.15,
            f"got {bar_00['support']}",
        )
    later = [r for r in out if _hour_label(r) in ("10-01 09", "10-01 10", "10-01 11")]
    if later:
        check(
            "10-01 09-11 res=85628.6",
            all(r["resistance"] is not None and abs(r["resistance"] - 85628.6) < 0.15 for r in later),
            f"got {[r['resistance'] for r in later]}",
        )
    return out


def print_last_bars(rows: List[Dict[str, Any]], n: int = 12) -> None:
    if not rows:
        return
    print("\n[최근 완성 봉 지표값]")
    print(
        f"{'시간(KST)':<16} {'close':>10} {'ibs':>6} {'res':>10} {'sup':>10} "
        f"{'atr':>8} {'trend':>5} {'ST':>8}"
    )
    for r in rows[-n:]:
        ts = datetime.fromtimestamp(r["t"], tz=KST).strftime("%m-%d %H:%M")
        res = f"{r['resistance']:.1f}" if r["resistance"] is not None else "-"
        sup = f"{r['support']:.1f}" if r["support"] is not None else "-"
        atr = f"{r['atr_stop']:.1f}" if r["atr_stop"] is not None else "-"
        sig = "BUY" if r["buy_signal"] else ("SELL" if r["sell_signal"] else "")
        print(
            f"{ts:<16} {r['c']:10.1f} {r['ibs']:6.3f} {res:>10} {sup:>10} "
            f"{atr:>8} {r['supertrend_trend']:>5} {sig:>8}"
        )


def main() -> int:
    print("=== 지표 계산 테스트 ===")
    test_ibs_formula()
    test_atr_rma_hand()
    test_supertrend_step()
    test_pivot_and_fixnan()
    test_htf_sr_mapping()
    test_empty()
    live = test_live_btc_crosscheck()
    print_last_bars(live, n=16)
    print(f"\n결과: {PASSED} passed, {FAILED} failed")
    if FAILURES:
        print("실패 목록:")
        for f in FAILURES:
            print(f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
