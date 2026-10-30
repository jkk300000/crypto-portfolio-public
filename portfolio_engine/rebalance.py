# -*- coding: utf-8 -*-
"""
Query four Gate sub-accounts, compare to T×w, transfer unused surplus to deficits.

  python -m portfolio_engine.rebalance
  python -m portfolio_engine.rebalance --execute
  python -m portfolio_engine.rebalance --loop --execute
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Dict, List

from .book import allocate
from .config import POLL_INTERVAL_SEC, SLEEVES, TRANSFER_STATE
from .env import main_keys, sub_uids
from .events import append_event, drain_events
from .gate_wallet import GateWallet, GateWalletError, SleeveWallet, parse_futures_account
from .mdd import book_mark_complete, update_book_mdd
from .plan import TransferLeg, plan_transfers
from .publish import publish


def fetch_wallets(wallet: GateWallet, uids: Dict[str, str]) -> Dict[str, SleeveWallet]:
    out = {}
    for sleeve, uid in uids.items():
        row = wallet.sub_futures(uid)
        out[sleeve] = parse_futures_account(sleeve, uid, row)
    return out


def _cid(prefix: str, src: str, dst: str, seq: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"pb{prefix}{src}{dst}{stamp}{seq}"[:64]


def execute_leg(wallet: GateWallet, uids: Dict[str, str], leg: TransferLeg, seq: int) -> dict:
    src_uid = uids[leg.src]
    dst_uid = uids[leg.dst]
    out_id = _cid("o", leg.src, leg.dst, seq)
    in_id = _cid("i", leg.src, leg.dst, seq)
    state = {
        "src": leg.src,
        "dst": leg.dst,
        "amount": leg.amount,
        "out_id": out_id,
        "in_id": in_id,
        "out_ok": False,
        "in_ok": False,
        "error": None,
    }
    TRANSFER_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        wallet.transfer_sub_futures(src_uid, leg.amount, "from", out_id)
        state["out_ok"] = True
        TRANSFER_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        time.sleep(0.4)
        wallet.transfer_sub_futures(dst_uid, leg.amount, "to", in_id)
        state["in_ok"] = True
        TRANSFER_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        append_event(
            "book",
            "transfer",
            f"{leg.src} → {leg.dst}  {leg.amount:.2f} USDT",
            leg.to_dict(),
        )
    except GateWalletError as exc:
        state["error"] = str(exc)
        TRANSFER_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        raise
    return state


def inspect(execute: bool = False) -> dict:
    key, secret = main_keys()
    uids = sub_uids()
    wallet = GateWallet(key, secret)
    wallets = fetch_wallets(wallet, uids)
    cash = {k: wallets[k].equity for k in SLEEVES}
    available = {k: wallets[k].available for k in SLEEVES}
    snap = allocate(cash)
    try:
        spot = wallet.main_spot_usdt()
        sub_spots = {k: wallet.sub_spot_usdt(uids[k]) for k in SLEEVES}
        sub_spot = sum(sub_spots.values())
    except GateWalletError:
        spot = None
        sub_spot = None
        sub_spots = {}
    sleeve_marks = {k: wallets[k].equity for k in SLEEVES}
    if book_mark_complete(sleeve_marks, spot, sub_spot, sub_spots):
        update_book_mdd(snap.total + float(spot) + float(sub_spot))
    open_sleeves = [k for k in SLEEVES if wallets[k].has_position]
    blocked = bool(open_sleeves)
    legs = [] if blocked else plan_transfers(snap, available)
    report = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": snap.total,
        "main_spot_usdt": 0.0 if spot is None else float(spot),
        "sub_spot_usdt": 0.0 if sub_spot is None else float(sub_spot),
        "sub_spots": sub_spots,
        "blocked": blocked,
        "open_sleeves": open_sleeves,
        "published": False,
        "sleeves": {
            k: {
                "uid": wallets[k].uid,
                "equity": wallets[k].equity,
                "available": wallets[k].available,
                "position_margin": wallets[k].position_margin,
                "has_position": wallets[k].has_position,
                "target": snap.allocated[k],
                "weight": snap.actual_weight[k],
                "target_weight": snap.weights[k],
                "deficit": snap.deficit[k],
                "surplus": snap.surplus[k],
            }
            for k in SLEEVES
        },
        "legs": [leg.to_dict() for leg in legs],
        "executed": [],
    }
    if blocked:
        return report
    if legs and execute:
        done: List[dict] = []
        for i, leg in enumerate(legs, start=1):
            done.append(execute_leg(wallet, uids, leg, i))
        report["executed"] = done
        wallets = fetch_wallets(wallet, uids)
        still_open = [k for k in SLEEVES if wallets[k].has_position]
        if still_open:
            report["blocked_after"] = still_open
            return report
        cash = {k: wallets[k].equity for k in SLEEVES}
        snap = allocate(cash)
        report["total_after"] = snap.total
        report["sleeves_after"] = {
            k: {"equity": wallets[k].equity, "target": snap.allocated[k], "deficit": snap.deficit[k]}
            for k in SLEEVES
        }
    if legs and not execute:
        return report
    publish(cash)
    report["published"] = True
    return report


def _tick_line(report: dict) -> str:
    open_s = ",".join(report.get("open_sleeves") or []) or "-"
    if report.get("blocked"):
        return f"{report['ts']}  T={report['total']:.2f}  OPEN {open_s}  wait"
    nleg = len(report.get("legs") or [])
    pub = "alloc" if report.get("published") else "plan"
    nexe = len(report.get("executed") or [])
    return f"{report['ts']}  T={report['total']:.2f}  flat  {pub} legs={nleg} xfer={nexe}"


def _print_report(report: dict) -> None:
    print(f"T={report['total']:.2f}  main_spot={report['main_spot_usdt']:.2f}")
    if report.get("blocked"):
        print("정지: 열린 포지션", ", ".join(report.get("open_sleeves") or []), "— 이체/금액 전달 없음")
    print(f"{'id':4s}  {'w':>6s}  {'hat':>6s}  {'equity':>10s}  {'avail':>10s}  {'target':>10s}  gap")
    for k, row in report["sleeves"].items():
        gap = row["surplus"] - row["deficit"]
        tag = "OK"
        if row["deficit"] >= 10:
            tag = f"NEED +{row['deficit']:.2f}"
        elif row["surplus"] >= 10:
            tag = f"EXTRA {row['surplus']:.2f}"
        print(
            f"{k:4s}  {row['target_weight']:6.1%}  {row['weight']:6.1%}  "
            f"{row['equity']:10.2f}  {row['available']:10.2f}  {row['target']:10.2f}  {tag}"
        )
    if not report["legs"]:
        print("이체 없음 (비중이 맞거나, 미사용 현금이 부족)")
        return
    print("이체 계획 (서브선물 → 메인현물 → 서브선물)")
    for leg in report["legs"]:
        print(f"  {leg['src']} → {leg['dst']}  {leg['amount']:.2f} USDT")
    for row in report.get("executed") or []:
        print(f"  done {row['src']}→{row['dst']} {row['amount']:.2f} out={row['out_ok']} in={row['in_ok']}")


def _sleep_and_drain(interval: float) -> None:
    end = time.time() + interval
    while True:
        try:
            n = drain_events()
            if n:
                print(f"kakao sent {n}", flush=True)
        except Exception as exc:
            print(f"kakao drain error {exc}", flush=True)
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(2.0, left))


def run_loop(execute: bool, interval: float) -> int:
    print(f"poll {interval:.0f}s  execute={execute}  열린 계정이 있으면 이체 안 함")
    last_open = None
    while True:
        try:
            report = inspect(execute=execute)
        except Exception as exc:
            print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  error {exc}")
            _sleep_and_drain(interval)
            continue
        open_s = tuple(report.get("open_sleeves") or [])
        changed = open_s != last_open
        last_open = open_s
        if report.get("executed") or report.get("published") or changed:
            _print_report(report)
        else:
            print(_tick_line(report), flush=True)
        _sleep_and_drain(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Inspect sleeve weights and transfer unused surplus")
    p.add_argument("--execute", action="store_true", help="actually transfer (default is dry-run)")
    p.add_argument("--loop", action="store_true", help="poll open/flat every interval")
    p.add_argument("--interval", type=float, default=POLL_INTERVAL_SEC, help="seconds (default 60)")
    args = p.parse_args(argv)
    if args.loop:
        try:
            return run_loop(execute=args.execute, interval=max(5.0, args.interval))
        except KeyboardInterrupt:
            print("stopped")
            return 0
    report = inspect(execute=args.execute)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
