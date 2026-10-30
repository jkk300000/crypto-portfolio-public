# -*- coding: utf-8 -*-
"""Start the book controller: 1-minute poll, transfer when all flat, publish T×w."""
from .config import POLL_INTERVAL_SEC
from .rebalance import run_loop


def main() -> int:
    try:
        return run_loop(execute=True, interval=POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
