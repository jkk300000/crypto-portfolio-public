# -*- coding: utf-8 -*-
"""
돈치안 서브 선물에서 SR 서브 선물로 USDT를 보냅니다.
경로: DC 선물 → 메인 현물 → SR 선물
기본은 조회만 하고, --execute 일 때만 이체합니다.

  python -m portfolio_engine.test_dc_to_sr
  python -m portfolio_engine.test_dc_to_sr --execute
  python -m portfolio_engine.test_dc_to_sr --amount 30 --execute
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from .env import one_sub_uid, main_keys
from .gate_wallet import GateWallet, parse_futures_account
from .plan import math_floor_cents


def _cid(tag: str) -> str:
    return f"pbdctosr{tag}{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"[:64]


def run(amount: float, execute: bool) -> int:
    key, secret = main_keys()
    wallet = GateWallet(key, secret)
    dc_uid = one_sub_uid("dc")
    sr_uid = one_sub_uid("sr")
    send = math_floor_cents(amount)
    if send <= 0:
        print("금액이 0 이하입니다")
        return 1

    dc = parse_futures_account("dc", dc_uid, wallet.sub_futures(dc_uid))
    sr = parse_futures_account("sr", sr_uid, wallet.sub_futures(sr_uid))
    spot = wallet.main_spot_usdt()

    print(f"dc uid                  {dc_uid}")
    print(f"sr uid                  {sr_uid}")
    print(f"dc equity / available   {dc.equity:.2f} / {dc.available:.2f}")
    print(f"sr equity / available   {sr.equity:.2f} / {sr.available:.2f}")
    print(f"main spot               {spot:.2f}")
    print(f"보낼 금액               {send:.2f} USDT")

    if dc.has_position:
        print("정지: 돈치안에 포지션이 열려 있습니다")
        return 1
    if sr.has_position:
        print("정지: SR에 포지션이 열려 있습니다")
        return 1
    if dc.available + 1e-9 < send:
        print(f"돈치안 가용액 부족: {dc.available:.2f} < {send:.2f}")
        return 1
    if not execute:
        print("dry-run: 이체 안 함. 실행하려면 --execute")
        return 0

    print(f"1) 돈치안 선물 → 메인 현물  {send:.2f}")
    wallet.transfer_sub_futures(dc_uid, send, "from", _cid("fr"))
    time.sleep(0.4)
    print(f"2) 메인 현물 → SR 선물      {send:.2f}")
    wallet.transfer_sub_futures(sr_uid, send, "to", _cid("to"))
    time.sleep(0.4)

    dc2 = parse_futures_account("dc", dc_uid, wallet.sub_futures(dc_uid))
    sr2 = parse_futures_account("sr", sr_uid, wallet.sub_futures(sr_uid))
    print(f"dc equity / available   {dc2.equity:.2f} / {dc2.available:.2f}")
    print(f"sr equity / available   {sr2.equity:.2f} / {sr2.available:.2f}")
    print(f"main spot               {wallet.main_spot_usdt():.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Transfer USDT from Donchian to SR via main spot")
    p.add_argument("--amount", type=float, default=30.0)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)
    return run(amount=args.amount, execute=args.execute)


if __name__ == "__main__":
    raise SystemExit(main())
