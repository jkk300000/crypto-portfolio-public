"""
Configuration: API keys from env, strategy params.
Copy to config.py. Values here are public placeholders, not live params.
"""
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GATE_API_KEY = (os.environ.get("GATE_API_KEY") or "").strip()
GATE_API_SECRET = (os.environ.get("GATE_API_SECRET") or "").strip()

_use_testnet = os.environ.get("GATE_USE_TESTNET", "").strip().lower() in ("1", "true", "yes")
if _use_testnet:
    GATE_FUTURES_BASE = os.environ.get("GATE_FUTURES_BASE", "https://api-testnet.gateapi.io/api/v4")
    GATE_WS_BASE = os.environ.get("GATE_WS_BASE", "wss://fx-ws-testnet.gateio.ws/v4/ws")
else:
    GATE_FUTURES_BASE = os.environ.get("GATE_FUTURES_BASE", "https://api.gateio.ws/api/v4")
    GATE_WS_BASE = os.environ.get("GATE_WS_BASE", "wss://fx-ws.gateio.ws/v4/ws")

_ws_env = os.environ.get("USE_WEBSOCKET_PRICE", "").strip().lower()
if _ws_env in ("1", "true", "yes"):
    _use_websocket_price = True
elif _ws_env in ("0", "false", "no"):
    _use_websocket_price = False
else:
    _use_websocket_price = False if _use_testnet else True

SETTLE = "usdt"
CONTRACT = "ETH_USDT"
CANDLE_INTERVAL = "30m"
CHART_BAR_MINUTES = 30
CANDLE_LIMIT = 800

ORDER_SIZE_MIN = 1
CONTRACT_MULTIPLIER = 0.01
PRICE_TICK = 0.01

ENTRY_CHECK_ONLY_ON_BAR_CLOSE = True

MAX_LEVERAGE = 3.0
MAX_RISK_PCT = 1.0
MAX_SL_PCT = 2.0
RR_RATIO = float(os.environ.get("RR_RATIO", "2.0"))
RR_RATIO_SHORT = float(os.environ.get("RR_RATIO_SHORT", "2.0"))
equity_usage_ratio = 0.95
max_leverage = MAX_LEVERAGE
max_risk_pct = MAX_RISK_PCT
max_sl_pct = MAX_SL_PCT
rr_ratio = RR_RATIO
rr_ratio_short = RR_RATIO_SHORT

TEST_MODE = os.environ.get("TEST_MODE", "").strip().lower() in ("1", "true", "yes")
_test_max_sl = os.environ.get("TEST_MAX_SL_PCT", "10").strip()
try:
    TEST_MAX_SL_PCT = float(_test_max_sl) if _test_max_sl else 10.0
except ValueError:
    TEST_MAX_SL_PCT = 10.0

DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()


@dataclass
class Config:
    gate_api_key: str = field(default_factory=lambda: GATE_API_KEY)
    gate_api_secret: str = field(default_factory=lambda: GATE_API_SECRET)

    settle: str = field(default_factory=lambda: SETTLE)
    contract: str = field(default_factory=lambda: CONTRACT)
    candle_interval: str = field(default_factory=lambda: CANDLE_INTERVAL)

    equity_usage_ratio: float = field(default_factory=lambda: equity_usage_ratio)

    atr_period: int = 10
    atr_multiplier: float = 3.0
    bb_length: int = 20
    bb_mult: float = 2.0
    bb_ma_type: str = "SMA"

    max_leverage: float = field(default_factory=lambda: max_leverage)
    max_risk_pct: float = field(default_factory=lambda: max_risk_pct)
    rr_ratio: float = field(default_factory=lambda: rr_ratio)
    rr_ratio_short: float = field(default_factory=lambda: rr_ratio_short)
    enable_close_on_opposite: bool = True

    dry_run: bool = True
    loop_interval_sec: int = 60

    use_websocket_price: bool = field(default_factory=lambda: _use_websocket_price)


use_websocket_price = _use_websocket_price


def load_config() -> Config:
    return Config()
