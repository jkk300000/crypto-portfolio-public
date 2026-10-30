"""
BTC_USDT USDT-M futures — SR + IBS + SuperTrend skeleton.
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
CONTRACT = "BTC_USDT"
CANDLE_INTERVAL = "1h"
CHART_BAR_MINUTES = 60
CANDLE_LIMIT = 1000

SR_TIMEFRAME = "4h"
SR_BAR_MINUTES = 240
SR_LEFT_BARS = 1
SR_RIGHT_BARS = 1

ORDER_SIZE_MIN = 1
CONTRACT_MULTIPLIER = float(os.environ.get("CONTRACT_MULTIPLIER", "0.0001"))
PRICE_TICK = float(os.environ.get("PRICE_TICK", "0.1"))
ENTRY_CHECK_ONLY_ON_BAR_CLOSE = True

ST_ATR_PERIOD = 10
ST_MULTIPLIER = 3.0
ST_USE_TA_ATR = True
ATR_STOP_PERIOD = 14
ATR_STOP_MULT = 1.5
RR_RATIO = float(os.environ.get("RR_RATIO", "2.0"))
RR_RATIO_SHORT = float(os.environ.get("RR_RATIO_SHORT", "2.0"))

ENABLE_BE_ADD = os.environ.get("ENABLE_BE_ADD", "0").strip().lower() in ("1", "true", "yes")
ADD_RISK_PCT = float(os.environ.get("ADD_RISK_PCT", "1.0"))
MIN_ADD_DIST_PCT = float(os.environ.get("MIN_ADD_DIST_PCT", "0.01"))
_STATE_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sr_state.json")
SR_STATE_PATH = (os.environ.get("SR_STATE_PATH") or _STATE_DEFAULT).strip() or _STATE_DEFAULT

IBS_LONG_MAX = 0.2
IBS_SHORT_MIN = 0.8

MAX_LEVERAGE = 3.0
MAX_RISK_PCT = 1.0
MAX_SL_PCT = 2.0
equity_usage_ratio = 0.95

ALLOW_LONG = True
ALLOW_SHORT = True
enable_close_on_opposite = True

DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()


@dataclass
class Config:
    gate_api_key: str = field(default_factory=lambda: GATE_API_KEY)
    gate_api_secret: str = field(default_factory=lambda: GATE_API_SECRET)

    settle: str = field(default_factory=lambda: SETTLE)
    contract: str = field(default_factory=lambda: CONTRACT)
    candle_interval: str = field(default_factory=lambda: CANDLE_INTERVAL)

    equity_usage_ratio: float = field(default_factory=lambda: equity_usage_ratio)
    max_leverage: float = field(default_factory=lambda: MAX_LEVERAGE)
    max_risk_pct: float = field(default_factory=lambda: MAX_RISK_PCT)
    max_sl_pct: float = field(default_factory=lambda: MAX_SL_PCT)
    rr_ratio: float = field(default_factory=lambda: RR_RATIO)
    rr_ratio_short: float = field(default_factory=lambda: RR_RATIO_SHORT)
    enable_close_on_opposite: bool = field(default_factory=lambda: enable_close_on_opposite)
    enable_be_add: bool = field(default_factory=lambda: ENABLE_BE_ADD)
    add_risk_pct: float = field(default_factory=lambda: ADD_RISK_PCT)
    min_add_dist_pct: float = field(default_factory=lambda: MIN_ADD_DIST_PCT)
    allow_long: bool = field(default_factory=lambda: ALLOW_LONG)
    allow_short: bool = field(default_factory=lambda: ALLOW_SHORT)

    st_atr_period: int = field(default_factory=lambda: ST_ATR_PERIOD)
    st_multiplier: float = field(default_factory=lambda: ST_MULTIPLIER)
    st_use_ta_atr: bool = field(default_factory=lambda: ST_USE_TA_ATR)
    atr_stop_period: int = field(default_factory=lambda: ATR_STOP_PERIOD)
    atr_stop_mult: float = field(default_factory=lambda: ATR_STOP_MULT)

    ibs_long_max: float = field(default_factory=lambda: IBS_LONG_MAX)
    ibs_short_min: float = field(default_factory=lambda: IBS_SHORT_MIN)
    sr_left_bars: int = field(default_factory=lambda: SR_LEFT_BARS)
    sr_right_bars: int = field(default_factory=lambda: SR_RIGHT_BARS)
    sr_bar_minutes: int = field(default_factory=lambda: SR_BAR_MINUTES)

    dry_run: bool = True
    loop_interval_sec: int = 60
    use_websocket_price: bool = field(default_factory=lambda: _use_websocket_price)


use_websocket_price = _use_websocket_price


def load_config() -> Config:
    return Config()
