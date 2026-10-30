# -*- coding: utf-8 -*-
"""Book-level target weights. Copy to config.py and fill in local values.

Live account UIDs, principals, and weights are not in this public file.
Set GATE_SUB_UID_* and PRINCIPAL_* in .env instead.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
ALLOC_PATH = ROOT / "allocation.json"

SLEEVES = ("eth", "ema", "sr", "dc")

# Placeholder equal weights. Replace in your private config.py.
WEIGHTS = {
    "eth": 0.25,
    "ema": 0.25,
    "sr": 0.25,
    "dc": 0.25,
}

NAMES = {
    "eth": "ETH SuperTrend+BB",
    "ema": "BTC EMA+RSI Trail",
    "sr": "BTC SR+IBS+ST",
    "dc": "BTC Donchian",
}

# Seed principal per sleeve (USDT). Override with PRINCIPAL_* in .env.
PRINCIPALS = {
    "eth": 0.0,
    "ema": 0.0,
    "sr": 0.0,
    "dc": 0.0,
}

BOT_ROOTS = {
    "eth": REPO / "gateio_supertrend_eth",
    "ema": REPO / "gateio_btc_ema_rsi_trail",
    "sr": REPO / "gateio_btc_sr_ibs_supertrend",
    "dc": REPO / "돈치안채널_btc",
}

BOT_COMMANDS = {
    "eth": ["python", "main.py"],
    "ema": ["python", "main.py"],
    "sr": ["python", "main.py"],
    "dc": ["python", "bot.py"],
}

GATE_HOST = "https://api.gateio.ws"
GATE_PREFIX = "/api/v4"
SETTLE = "usdt"
CURRENCY = "USDT"

# Leave empty — required at runtime via GATE_SUB_UID_* in .env
SUB_UIDS = {
    "eth": "",
    "ema": "",
    "sr": "",
    "dc": "",
}

MIN_TRANSFER = 10.0
DONOR_KEEP_PCT = 0.05
ETH_RECEIVE_FIRST = True
POLL_INTERVAL_SEC = 60
TRANSFER_STATE = ROOT / "transfer_state.json"
DASHBOARD_PORT = 8787
DASHBOARD_HOST = "127.0.0.1"
