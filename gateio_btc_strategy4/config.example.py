"""Donchian BTC Gate.io bot configuration.

Copy to config.py. Values here are public placeholders, not live params.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class StrategyConfig:
    entry_len: int = 20
    exit_len: int = 10
    allow_long: bool = True
    allow_short: bool = True

    enable_ibs_filter: bool = True
    ibs_long_max: float = 0.2
    ibs_short_min: float = 0.8

    atr_len: int = 14
    atr_mult: float = 1.0

    rr_tp1: float = 2.0
    tp1_qty_pct: float = 50.0
    rr_tp2: float = 3.0
    tp2_qty_pct: float = 50.0

    enable_swing_trail: bool = False
    swing_pivot: int = 5

    max_risk_pct: float = 1.0
    max_leverage: float = 3.0


@dataclass(frozen=True)
class BotConfig:
    api_key: str
    api_secret: str
    settle: str = "usdt"
    contract: str = "BTC_USDT"
    timeframe: str = "1d"
    poll_interval_sec: int = 60
    trigger_expiration_sec: int = 30 * 24 * 3600
    trigger_price_type: int = 1
    state_path: str = "trade_state.json"
    margin_mode: str = "cross"
    host: str = "https://api.gateio.ws"
    prefix: str = "/api/v4"
    discord_webhook_url: str = ""


def load_bot_config() -> BotConfig:
    api_key = os.getenv("GATE_API_KEY", "").strip()
    api_secret = os.getenv("GATE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise SystemExit("GATE_API_KEY / GATE_API_SECRET 를 .env 에 설정하세요.")

    return BotConfig(
        api_key=api_key,
        api_secret=api_secret,
        settle=os.getenv("GATE_SETTLE", "usdt").strip().lower(),
        contract=os.getenv("GATE_CONTRACT", "BTC_USDT").strip(),
        poll_interval_sec=int(os.getenv("POLL_INTERVAL_SEC", "60")),
        trigger_expiration_sec=_trigger_expiration_sec(),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
    )


def _trigger_expiration_sec() -> int:
    day = 86400
    raw = int(os.getenv("TRIGGER_EXPIRATION_SEC", str(30 * day)))
    days = max(1, min(30, raw // day if raw >= day else raw))
    return days * day


STRATEGY = StrategyConfig()
