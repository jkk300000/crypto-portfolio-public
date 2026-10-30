# -*- coding: utf-8 -*-
"""
Publish T×w allocations and optionally start the four strategy processes.

Examples:
  python -m portfolio_engine.run_book --eth 1000 --ema 1000 --sr 1000 --dc 1000
  python -m portfolio_engine.run_book --eth 1000 --ema 1000 --sr 1000 --dc 1000 --launch
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .config import ALLOC_PATH, BOT_COMMANDS, BOT_ROOTS, SLEEVES
from .publish import publish


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish book allocations and start sleeves")
    for k in SLEEVES:
        p.add_argument(f"--{k}", type=float, required=True, help=f"{k} local equity USDT")
    p.add_argument("--alloc", type=Path, default=ALLOC_PATH)
    p.add_argument("--launch", action="store_true", help="start the four bot processes")
    p.add_argument("--live", action="store_true", help="read balances from Gate instead of --eth/--ema/--sr/--dc")
    args = p.parse_args(argv)

    cash = {k: float(getattr(args, k)) for k in SLEEVES}
    snap = publish(cash, path=args.alloc)
    print(f"T={snap.total:.2f}  wrote {args.alloc}")
    for k in SLEEVES:
        print(
            f"  {k:4s}  w={snap.weights[k]:.1%}  cash={snap.cash[k]:,.2f}  "
            f"alloc={snap.allocated[k]:,.2f}  "
            f"{'need +' + format(snap.deficit[k], ',.2f') if snap.deficit[k] else 'ok'}"
        )

    if not args.launch:
        return 0

    alloc = str(args.alloc.resolve())
    procs = []
    for k in SLEEVES:
        root = BOT_ROOTS[k]
        if not root.exists():
            print(f"skip launch {k}: missing {root}", file=sys.stderr)
            continue
        env = os.environ.copy()
        env["PORTFOLIO_ALLOC_PATH"] = alloc
        env["PORTFOLIO_SLEEVE"] = k
        cmd = BOT_COMMANDS[k]
        print(f"launch {k}: {cmd} cwd={root}")
        procs.append(subprocess.Popen(cmd, cwd=str(root), env=env))
    for proc in procs:
        proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
