"""본전 추가 수량/조건 검증. 실행: python test_be_add.py"""
from __future__ import annotations

import config
from strategy import check_be_add, st_line

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  OK  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name} {detail}")


def _row(*, close: float, trend: int, up: float, dn: float) -> dict:
    return {"c": close, "supertrend_trend": trend, "supertrend_up": up, "supertrend_dn": dn}


def main() -> int:
    print("BE-add")
    check("st_line long uses up", st_line(_row(close=100, trend=1, up=101, dn=90)) == 101)
    check("st_line short uses dn", st_line(_row(close=100, trend=-1, up=110, dn=95)) == 95)

    long_ok = check_be_add(
        _row(close=110_000, trend=1, up=111_000, dn=90_000),
        side="long",
        orig_entry=100_000,
        orig_tp=200_000,
        current_contracts=100,
        equity=2_000,
        add_risk_used=0.0,
    )
    check("long scale when ST above entry", long_ok is not None)
    if long_ok:
        check("long SL is first entry", abs(long_ok.sl_price - 100_000) < 1e-9)
        check("long TP unchanged", abs(long_ok.tp_price - 200_000) < 1e-9)
        check("long adds contracts", long_ok.add_contracts >= config.ORDER_SIZE_MIN, str(long_ok.add_contracts))
        check("long add risk <= budget", long_ok.risk_used_delta <= 2_000 * config.ADD_RISK_PCT / 100.0 + 1e-6)

    long_no = check_be_add(
        _row(close=110_000, trend=1, up=99_000, dn=90_000),
        side="long",
        orig_entry=100_000,
        orig_tp=200_000,
        current_contracts=1000,
        equity=2_000,
        add_risk_used=0.0,
    )
    check("long skip when ST below entry", long_no is None)

    short_ok = check_be_add(
        _row(close=90_000, trend=-1, up=110_000, dn=89_000),
        side="short",
        orig_entry=100_000,
        orig_tp=50_000,
        current_contracts=100,
        equity=2_000,
        add_risk_used=0.0,
    )
    check("short scale when ST below entry", short_ok is not None and short_ok.add_contracts >= 1)

    spent = check_be_add(
        _row(close=110_000, trend=1, up=111_000, dn=90_000),
        side="long",
        orig_entry=100_000,
        orig_tp=200_000,
        current_contracts=100,
        equity=2_000,
        add_risk_used=2_000 * config.ADD_RISK_PCT / 100.0,
    )
    check("budget exhausted -> stop only", spent is not None and spent.add_contracts == 0 and spent.reason == "be_stop_only")

    tight = check_be_add(
        _row(close=100_005, trend=1, up=100_010, dn=90_000),
        side="long",
        orig_entry=100_000,
        orig_tp=200_000,
        current_contracts=100,
        equity=2_000,
        add_risk_used=0.0,
    )
    # dist_pct = 5/100005*100 ≈ 0.005% < 0.01%
    check("min distance blocks size", tight is not None and tight.add_contracts == 0)

    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
