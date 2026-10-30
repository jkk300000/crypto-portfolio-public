"""
Gate.io live trading configuration for Squeeze Momentum + IBS + SuperTrend trail.
Copy to config.py. Values here are public placeholders, not live params.
"""
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env_bool(name: str, default: str) -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def _env_int(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


GATE_API_KEY = (os.environ.get("GATE_API_KEY") or "").strip()
GATE_API_SECRET = (os.environ.get("GATE_API_SECRET") or "").strip()

_use_testnet = _env_bool("GATE_USE_TESTNET", "0")
if _use_testnet:
    GATE_FUTURES_BASE = os.environ.get("GATE_FUTURES_BASE", "https://api-testnet.gateapi.io/api/v4")
    GATE_WS_BASE = os.environ.get("GATE_WS_BASE", "wss://fx-ws-testnet.gateio.ws/v4/ws")
else:
    GATE_FUTURES_BASE = os.environ.get("GATE_FUTURES_BASE", "https://api.gateio.ws/api/v4")
    GATE_WS_BASE = os.environ.get("GATE_WS_BASE", "wss://fx-ws.gateio.ws/v4/ws")

SETTLE = "usdt"
CONTRACT = os.environ.get("GATE_CONTRACT", "BTC_USDT")
CANDLE_INTERVAL = os.environ.get("CANDLE_INTERVAL", "1h")
CHART_BAR_MINUTES = _env_int("CHART_BAR_MINUTES", "60")
CANDLE_LIMIT = _env_int("CANDLE_LIMIT", "800")

ORDER_SIZE_MIN = _env_int("ORDER_SIZE_MIN", "1")
CONTRACT_MULTIPLIER = _env_float("CONTRACT_MULTIPLIER", "0.0001")
PRICE_TICK = _env_float("PRICE_TICK", "1")
POSITION_MODE = (os.environ.get("POSITION_MODE", "single") or "single").strip().lower()

ENTRY_CHECK_ONLY_ON_BAR_CLOSE = True

BB_LENGTH = _env_int("BB_LENGTH", "20")
BB_MULT = _env_float("BB_MULT", "2.0")
KC_LENGTH = _env_int("KC_LENGTH", "20")
KC_MULT = _env_float("KC_MULT", "1.5")
USE_TRUE_RANGE_KC = _env_bool("USE_TRUE_RANGE_KC", "1")

IBS_LONG_MAX = _env_float("IBS_LONG_MAX", "0.2")
IBS_SHORT_MIN = _env_float("IBS_SHORT_MIN", "0.8")

ST_ATR_PERIOD = _env_int("ST_ATR_PERIOD", "10")
ST_MULTIPLIER = _env_float("ST_MULTIPLIER", "3.0")
ST_CHANGE_ATR = _env_bool("ST_CHANGE_ATR", "1")

ATR_LEN = _env_int("ATR_LEN", "14")
ATR_MULT = _env_float("ATR_MULT", "2.0")
RR_RATIO = _env_float("RR_RATIO", "2.0")

MAX_LEVERAGE = _env_float("MAX_LEVERAGE", "3.0")
MAX_RISK_PCT = _env_float("MAX_RISK_PCT", "1.0")
MAX_SL_PCT = _env_float("MAX_SL_PCT", "2.0")
ALLOW_LONG = _env_bool("ALLOW_LONG", "1")
ALLOW_SHORT = _env_bool("ALLOW_SHORT", "1")
equity_usage_ratio = _env_float("EQUITY_USAGE_RATIO", "1.0")
ENABLE_CLOSE_ON_OPPOSITE = _env_bool("ENABLE_CLOSE_ON_OPPOSITE", "1")

DRY_RUN = _env_bool("DRY_RUN", "1")
LOOP_INTERVAL_SEC = _env_int("LOOP_INTERVAL_SEC", "60")
DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "runtime_state.json")

_ws_env = (os.environ.get("USE_WEBSOCKET_PRICE") or "").strip().lower()
if _ws_env in ("1", "true", "yes"):
    USE_WEBSOCKET_PRICE = True
elif _ws_env in ("0", "false", "no"):
    USE_WEBSOCKET_PRICE = False
else:
    USE_WEBSOCKET_PRICE = False if _use_testnet else True


@dataclass
class Config:
    gate_api_key: str = field(default_factory=lambda: GATE_API_KEY)
    gate_api_secret: str = field(default_factory=lambda: GATE_API_SECRET)
    settle: str = field(default_factory=lambda: SETTLE)
    contract: str = field(default_factory=lambda: CONTRACT)
    candle_interval: str = field(default_factory=lambda: CANDLE_INTERVAL)
    chart_bar_minutes: int = field(default_factory=lambda: CHART_BAR_MINUTES)
    candle_limit: int = field(default_factory=lambda: CANDLE_LIMIT)
    dry_run: bool = field(default_factory=lambda: DRY_RUN)
    loop_interval_sec: int = field(default_factory=lambda: LOOP_INTERVAL_SEC)
    use_websocket_price: bool = field(default_factory=lambda: USE_WEBSOCKET_PRICE)


def load_config() -> Config:
    return Config()


max_leverage = MAX_LEVERAGE
max_risk_pct = MAX_RISK_PCT
max_sl_pct = MAX_SL_PCT
rr_ratio = RR_RATIO
use_websocket_price = USE_WEBSOCKET_PRICE
