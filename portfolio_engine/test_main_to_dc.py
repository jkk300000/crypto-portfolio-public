# -*- coding: utf-8 -*-
"""
메인 계정 USDT 전액을 돈치안 서브 선물로 보냅니다.
기본은 조회만 하고, --execute 일 때만 이체합니다.

  python -m portfolio_engine.test_main_to_dc
  python -m portfolio_engine.test_main_to_dc --execute
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from .env import load_dotenv, main_keys, one_sub_uid
from .gate_wallet import GateWallet, parse_futures_account
from .plan import math_floor_cents

_DC_HINTS = ("돈치안", "donchian", "dc", "dch")


def _sub_label(row: dict) -> str:
    parts = [
        str(row.get("remark") or ""),
        str(row.get("login_name") or ""),
        str(row.get("name") or ""),
        str(row.get("email") or ""),
    ]
    return " ".join(p for p in parts if p).lower()


def resolve_dc_uid(wallet: GateWallet, explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    load_dotenv()
    try:
        return one_sub_uid("dc")
    except RuntimeError:
        pass
    rows = wallet.list_sub_accounts()
    if not rows:
        raise RuntimeError("서브계정이 없습니다. GATE_SUB_UID_DC 또는 --uid 를 넣으세요")
    print("서브계정 목록")
    matches = []
    for row in rows:
        uid = str(row.get("user_id") or row.get("uid") or "")
        label = _sub_label(row)
        print(f"  uid={uid}  {label}")
        if uid and any(h in label for h in _DC_HINTS):
            matches.append(uid)
    uniq = list(dict.fromkeys(matches))
    if len(uniq) == 1:
        print(f"돈치안으로 추정: {uniq[0]}")
        return uniq[0]
    raise RuntimeError(
        "돈치안 UID를 특정하지 못했습니다. "
        ".env에 GATE_SUB_UID_DC=숫자 를 넣거나 --uid 로 지정하세요"
    )


def _cid(tag: str) -> str:
    return f"pbtodc{tag}{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"[:64]


def run(execute: bool, uid: str | None = None) -> int:
    key, secret = main_keys()
    wallet = GateWallet(key, secret)
    try:
        dc_uid = resolve_dc_uid(wallet, uid)
    except RuntimeError as exc:
        print(exc)
        return 1

    fut = math_floor_cents(wallet.main_futures_usdt())
    spot = math_floor_cents(wallet.main_spot_usdt())
    dc_before = parse_futures_account("dc", dc_uid, wallet.sub_futures(dc_uid))

    print(f"main futures available  {fut:.2f} USDT")
    print(f"main spot available     {spot:.2f} USDT")
    print(f"dc uid                  {dc_uid}")
    print(f"dc futures equity       {dc_before.equity:.2f} USDT")
    print(f"dc futures available    {dc_before.available:.2f} USDT")

    total = math_floor_cents(fut + spot)
    if total <= 0:
        print("메인에 보낼 USDT가 없습니다")
        return 0

    print(f"보낼 금액               {total:.2f} USDT  (선물 {fut:.2f} + 현물 {spot:.2f})")
    if not execute:
        print("dry-run: 이체 안 함. 실행하려면 --execute")
        return 0

    if fut > 0:
        print(f"1) 메인 선물 → 메인 현물  {fut:.2f}")
        wallet.transfer_main(fut, "futures", "spot", _cid("fs"))
        time.sleep(0.4)
        spot = math_floor_cents(wallet.main_spot_usdt())
        print(f"   메인 현물 재조회       {spot:.2f}")

    send = math_floor_cents(wallet.main_spot_usdt())
    if send <= 0:
        print("메인 현물에 보낼 금액이 없습니다")
        return 1

    print(f"2) 메인 현물 → 돈치안 선물  {send:.2f}")
    wallet.transfer_sub_futures(dc_uid, send, "to", _cid("to"))
    time.sleep(0.4)

    dc_after = parse_futures_account("dc", dc_uid, wallet.sub_futures(dc_uid))
    left_spot = wallet.main_spot_usdt()
    left_fut = wallet.main_futures_usdt()
    print(f"메인 선물 잔액          {left_fut:.2f}")
    print(f"메인 현물 잔액          {left_spot:.2f}")
    print(f"dc futures equity       {dc_after.equity:.2f} USDT")
    print(f"dc futures available    {dc_after.available:.2f} USDT")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Send all main USDT to Donchian sub futures")
    p.add_argument("--execute", action="store_true", help="actually transfer")
    p.add_argument("--uid", help="Donchian sub-account user id")
    args = p.parse_args(argv)
    return run(execute=args.execute, uid=args.uid)


if __name__ == "__main__":
    raise SystemExit(main())
