"""
Gate.io live trading — EMA + RSI Trail (BTC).
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
EMA_TF_INTERVAL = os.environ.get("EMA_TF_INTERVAL", "8h")
EMA_TF_MINUTES = _env_int("EMA_TF_MINUTES", "480")
EMA_TF_CANDLE_LIMIT = _env_int("EMA_TF_CANDLE_LIMIT", "120")

ORDER_SIZE_MIN = _env_int("ORDER_SIZE_MIN", "1")
CONTRACT_MULTIPLIER = _env_float("CONTRACT_MULTIPLIER", "0.0001")
PRICE_TICK = _env_float("PRICE_TICK", "0.1")
POSITION_MODE = (os.environ.get("POSITION_MODE", "single") or "single").strip().lower()

ENTRY_CHECK_ONLY_ON_BAR_CLOSE = True

EMA_LEN = _env_int("EMA_LEN", "200")
RSI_LEN1 = _env_int("RSI_LEN1", "14")
RSI_LEN2 = _env_int("RSI_LEN2", "14")
RSI_LEN3 = _env_int("RSI_LEN3", "14")
RSI_LEVEL = _env_float("RSI_LEVEL", "50.0")

IBS_LONG_MAX = _env_float("IBS_LONG_MAX", "0.2")
IBS_SHORT_MIN = _env_float("IBS_SHORT_MIN", "0.8")

MAX_LEVERAGE = _env_float("MAX_LEVERAGE", "3.0")
MAX_RISK_PCT = _env_float("MAX_RISK_PCT", "1.0")
MAX_SL_PCT = _env_float("MAX_SL_PCT", "2.0")
RR_RATIO = _env_float("RR_RATIO", "2.0")
RR_RATIO_SHORT = _env_float("RR_RATIO_SHORT", "2.0")
TP_FALLBACK_MOVE_PCT = _env_float("TP_FALLBACK_MOVE_PCT", "50")
ALLOW_LONG = _env_bool("ALLOW_LONG", "1")
ALLOW_SHORT = _env_bool("ALLOW_SHORT", "1")
equity_usage_ratio = _env_float("EQUITY_USAGE_RATIO", "1.0")

USE_MIN_SL = _env_bool("USE_MIN_SL", "0")
MIN_SL_PCT = _env_float("MIN_SL_PCT", "0.05")
MIN_SL_MODE = (os.environ.get("MIN_SL_MODE") or "skip").strip().lower()

USE_FEE_SIZING = _env_bool("USE_FEE_SIZING", "0")
FEE_PCT_SIDE = _env_float("FEE_PCT_SIDE", "0.05")

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
    max_leverage: float = field(default_factory=lambda: MAX_LEVERAGE)


def load_config() -> Config:
    return Config()


max_leverage = MAX_LEVERAGE
max_risk_pct = MAX_RISK_PCT
max_sl_pct = MAX_SL_PCT
rr_ratio = RR_RATIO
rr_ratio_short = RR_RATIO_SHORT
use_websocket_price = USE_WEBSOCKET_PRICE
