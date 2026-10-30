"""
Gate.io Futures API client.
- 캔들/계정/포지션 조회 재시도 및 연결 복구
- TP/SL 가격 트리거 주문
"""
import logging
import time
from typing import Optional, TYPE_CHECKING

import config

if TYPE_CHECKING:
    from price_stream import PriceStream

try:
    import gate_api
    from gate_api import FuturesOrder, FuturesPriceTriggeredOrder, FuturesInitialOrder, FuturesPriceTrigger
except ImportError:
    gate_api = None
    FuturesOrder = FuturesPriceTriggeredOrder = FuturesInitialOrder = FuturesPriceTrigger = None

logger = logging.getLogger(__name__)


class PositionLookupError(Exception):
    """Raised when position cannot be confirmed. Must not be treated as flat."""


def _is_connection_error(exc: Exception) -> bool:
    if exc is None:
        return False
    msg = str(exc).lower()
    if "remotedisconnected" in msg or ("connection" in msg and ("closed" in msg or "reset" in msg or "refused" in msg)):
        return True
    return False


def _is_transient_api_error(exc: Exception) -> bool:
    if _is_connection_error(exc):
        return True
    status = getattr(exc, "status", None)
    if status in (429, 502, 503, 504):
        return True
    msg = str(exc).lower()
    if "gateway timeout" in msg or "request timeout" in msg:
        return True
    if gate_api is not None and isinstance(exc, gate_api.exceptions.GateApiException):
        label = (getattr(exc, "label", "") or "").upper()
        if label == "INTERNAL" and "timeout" in (getattr(exc, "message", "") or "").lower():
            return True
    return False


class GateIOClient:
    def __init__(
        self,
        key: Optional[str] = None,
        secret: Optional[str] = None,
        base: Optional[str] = None,
        price_stream: Optional["PriceStream"] = None,
    ):
        if gate_api is None:
            raise RuntimeError("Missing dependency: gate_api. Install with `pip install gate-api`.")
        self.key = key or config.GATE_API_KEY
        self.secret = secret or config.GATE_API_SECRET
        base = base or config.GATE_FUTURES_BASE
        self.config = gate_api.Configuration(host=base, key=self.key, secret=self.secret)
        if hasattr(self.config, "client_side_validation"):
            self.config.client_side_validation = False
        self._api = None
        self._connection_logged = False
        self._price_stream = price_stream

    @property
    def api(self):
        if self._api is None:
            self._api = gate_api.FuturesApi(gate_api.ApiClient(self.config))
        return self._api

    @property
    def settle(self) -> str:
        return config.SETTLE

    @property
    def contract(self) -> str:
        return config.CONTRACT

    def _log_connection_once(self):
        if self._connection_logged:
            return
        try:
            logger.info("Gate.io 연결: base=%s settle=%s contract=%s", self.config.host, self.settle, self.contract)
            acc = self.api.list_futures_accounts(self.settle)
            acc_list = acc if isinstance(acc, list) else [acc] if acc else []
            a0 = acc_list[0] if acc_list else None
            total = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0) if a0 else 0.0
            logger.info("API 계정 확인 완료: 선물 계정 total=%.2f USDT", total)
            self._connection_logged = True
        except Exception as exc:
            logger.warning("Gate.io 연결/계정 확인 중 오류: %s", exc)

    def _reset_api_on_connection_error(self):
        self._api = None

    def get_candles(
        self,
        limit: Optional[int] = None,
        interval: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> list:
        self._log_connection_once()
        limit = limit or config.CANDLE_LIMIT
        interval = interval or config.CANDLE_INTERVAL
        for attempt in range(max_retries):
            try:
                raw = self.api.list_futures_candlesticks(self.settle, self.contract, limit=limit, interval=interval)
                out = []
                for r in raw:
                    out.append(
                        {
                            "t": int(getattr(r, "t", 0)),
                            "o": float(r.o),
                            "h": float(r.h),
                            "l": float(r.l),
                            "c": float(r.c),
                            "v": float(getattr(r, "v", 0) or 0),
                        }
                    )
                out.sort(key=lambda x: x["t"])
                return out
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning("캔들 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, exc)
                    time.sleep(retry_delay)
                else:
                    logger.error("캔들 조회 최종 실패 (%d회 시도): %s", max_retries, exc)
                    return []

    def get_last_price(self) -> Optional[float]:
        if self._price_stream:
            ws_price = self._price_stream.get_last_price()
            if ws_price is not None:
                return ws_price
        try:
            tickers = self.api.list_futures_tickers(self.settle)
            for t in (tickers or []):
                if getattr(t, "contract", None) == self.contract:
                    last_ = getattr(t, "last", None)
                    if last_ is not None:
                        return float(last_)
            return None
        except Exception as exc:
            logger.debug("현재가 조회 실패: %s", exc)
            return None

    def _round_trigger_price_str(self, price) -> str:
        tick = getattr(config, "PRICE_TICK", 1)
        p = float(price)
        rounded = round(p / tick) * tick
        if tick >= 1:
            return str(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]) if "." in f"{tick:.10f}" else 0)
        return f"{rounded:.{nd}f}".rstrip("0").rstrip(".")

    def _round_trigger_price(self, price) -> float:
        tick = getattr(config, "PRICE_TICK", 1)
        p = float(price)
        rounded = round(p / tick) * tick
        if tick >= 1:
            return float(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]) if "." in f"{tick:.10f}" else 0)
        return float(f"{rounded:.{nd}f}".rstrip("0").rstrip("."))

    def get_account(self):
        self._log_connection_once()
        max_retries = 3
        retry_delay = 2.0
        for attempt in range(max_retries):
            try:
                return self.api.list_futures_accounts(self.settle)
            except Exception as exc:
                if _is_connection_error(exc) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("계정 조회 연결 끊김 (재시도 %d/%d): %s", attempt + 1, max_retries, exc)
                    time.sleep(retry_delay)
                else:
                    raise

    def _position_dict(self, pos, size: float) -> dict:
        if isinstance(pos, dict):
            entry = pos.get("entry_price") or 0
            contract = pos.get("contract", self.contract)
        else:
            entry = getattr(pos, "entry_price", 0) or 0
            contract = getattr(pos, "contract", self.contract)
        return {
            "size": size,
            "side": "long" if size > 0 else "short",
            "entry_price": float(entry),
            "contract": contract,
        }

    def _list_open_positions_for_contract(self) -> Optional[list]:
        list_fn = getattr(self.api, "list_positions", None) or getattr(self.api, "list_futures_positions", None)
        if list_fn is None:
            return None
        try:
            raw = list_fn(self.settle, holding="true")
        except TypeError:
            raw = list_fn(self.settle)
        out = []
        for pos in raw or []:
            contract = getattr(pos, "contract", None) if not isinstance(pos, dict) else pos.get("contract")
            if contract != self.contract:
                continue
            raw_size = getattr(pos, "size", None) if not isinstance(pos, dict) else pos.get("size")
            size = float(raw_size or 0)
            if size == 0:
                continue
            out.append(self._position_dict(pos, size))
        return out

    def _read_open_position(self) -> Optional[dict]:
        listed = None
        try:
            listed = self._list_open_positions_for_contract()
        except Exception as exc:
            logger.debug("list_positions 실패, get_position으로 대체: %s", exc)
        if listed is not None:
            if len(listed) > 1:
                logger.warning(
                    "계약 %s 오픈 포지션 %d건 확인 — 신규 진입 차단",
                    self.contract,
                    len(listed),
                )
            return listed[0] if listed else None
        pos = self.api.get_position(self.settle, self.contract)
        size = float(pos.size or 0)
        if size == 0:
            return None
        return self._position_dict(pos, size)

    def get_position(self) -> Optional[dict]:
        """Return open position dict, or None if confirmed flat. Raises PositionLookupError if unknown."""
        max_retries = 3
        retry_delay = 2.0
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                return self._read_open_position()
            except gate_api.exceptions.GateApiException as exc:
                if getattr(exc, "label", "") == "POSITION_NOT_FOUND":
                    return None
                last_exc = exc
                if _is_connection_error(exc) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("포지션 조회 연결 끊김 (재시도 %d/%d): %s", attempt + 1, max_retries, exc)
                    time.sleep(retry_delay)
                    continue
                raise
            except gate_api.exceptions.ApiException as exc:
                if exc.status == 404:
                    return None
                last_exc = exc
                if _is_connection_error(exc) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("포지션 조회 연결 끊김 (재시도 %d/%d): %s", attempt + 1, max_retries, exc)
                    time.sleep(retry_delay)
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                if _is_connection_error(exc) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("포지션 조회 연결 끊김 (재시도 %d/%d): %s", attempt + 1, max_retries, exc)
                    time.sleep(retry_delay)
                    continue
                raise
        raise PositionLookupError(str(last_exc) if last_exc else "포지션 조회 실패")

    def _position_matches_entry(self, side: str, min_size: int) -> Optional[dict]:
        try:
            pos = self.get_position()
        except PositionLookupError:
            return None
        if pos and pos["side"] == side and abs(float(pos["size"])) >= float(min_size):
            return pos
        return None

    def _submit_market_order(
        self,
        order: "FuturesOrder",
        log_msg: str,
        log_args: tuple,
        *,
        verify_entry_side: Optional[str] = None,
        verify_entry_min_size: int = 1,
        verify_close: bool = False,
    ):
        max_retries = 4
        for attempt in range(max_retries):
            try:
                result = self.api.create_futures_order(self.settle, order)
                logger.info(log_msg, *log_args, getattr(result, "id", "?"))
                return result
            except Exception as exc:
                if verify_close:
                    try:
                        if not self.get_position():
                            logger.warning("청산 주문 타임아웃 후 포지션 없음 — 청산 완료로 간주")
                            return None
                    except PositionLookupError:
                        pass
                if verify_entry_side:
                    pos = self._position_matches_entry(verify_entry_side, verify_entry_min_size)
                    if pos:
                        logger.warning(
                            "시장가 주문 타임아웃 후 포지션 확인 — 체결로 간주: side=%s size=%s",
                            verify_entry_side,
                            pos["size"],
                        )
                        return pos
                if not _is_transient_api_error(exc) or attempt >= max_retries - 1:
                    raise
                wait_sec = 1.5 * (attempt + 1)
                logger.warning(
                    "시장가 주문 API 일시 오류 (재시도 %d/%d): %s wait=%.1fs",
                    attempt + 1,
                    max_retries,
                    exc,
                    wait_sec,
                )
                if _is_connection_error(exc):
                    self._reset_api_on_connection_error()
                time.sleep(wait_sec)

    def set_exchange_leverage(self, leverage=None) -> None:
        lev = float(leverage if leverage is not None else getattr(config, "MAX_LEVERAGE", 1))
        if lev < 1:
            lev = 1.0
        lev_s = str(int(round(lev))) if abs(lev - round(lev)) < 1e-9 else f"{lev:g}"
        cross = False
        try:
            pos = self.api.get_position(self.settle, self.contract)
            cross = float(getattr(pos, "leverage", 0) or 0) == 0
        except Exception:
            cross = False
        try:
            if cross:
                self.api.update_position_leverage(
                    self.settle, self.contract, "0", cross_leverage_limit=lev_s
                )
                logger.info("거래소 레버리지 CROSS %sx", lev_s)
            else:
                self.api.update_position_leverage(self.settle, self.contract, lev_s)
                logger.info("거래소 레버리지 ISOLATE %sx", lev_s)
        except Exception as exc:
            logger.warning("거래소 레버리지 설정 실패(%sx): %s", lev_s, exc)

    def place_market_order(self, side: str, size: str, reduce_only: bool = False):
        size_int = int(float(size))
        if reduce_only:
            pos = self.get_position()
            if pos:
                auto_size = "close_long" if pos["side"] == "long" else "close_short"
                order = FuturesOrder(
                    contract=self.contract,
                    size="0",
                    price="0",
                    reduce_only=True,
                    tif="ioc",
                    auto_size=auto_size,
                )
                return self._submit_market_order(
                    order,
                    "시장가 청산 주문 접수: pos_side=%s auto_size=%s order_id=%s",
                    (pos["side"], auto_size),
                    verify_close=True,
                )

        self.set_exchange_leverage()
        order_size = str(abs(size_int)) if side == "long" else str(-abs(size_int))
        order = FuturesOrder(
            contract=self.contract,
            size=order_size,
            price="0",
            reduce_only=reduce_only,
            tif="ioc",
        )
        return self._submit_market_order(
            order,
            "시장가 주문 접수: side=%s size=%s reduce_only=%s order_id=%s",
            (side, order_size, reduce_only),
            verify_entry_side=side,
            verify_entry_min_size=abs(size_int),
        )

    def _close_side(self, position_side: str) -> str:
        return "sell" if position_side == "long" else "buy"

    def create_stop_loss_order(self, trigger_price: str, size: str, side: str):
        order_side = self._close_side(side)
        tick = getattr(config, "PRICE_TICK", 1)
        last_price_check = self.get_last_price()

        price_float = float(trigger_price)
        if last_price_check is not None:
            if side == "long" and price_float >= last_price_check:
                price_float = self._round_trigger_price(last_price_check - tick * 2)
                if price_float >= last_price_check:
                    price_float = last_price_check - tick
            elif side == "short" and price_float <= last_price_check:
                price_float = self._round_trigger_price(last_price_check + tick * 2)
                if price_float <= last_price_check:
                    price_float = last_price_check + tick

        price_str = self._round_trigger_price_str(price_float)
        trigger_rule = 2 if side == "long" else 1
        auto_size = "close_long" if side == "long" else "close_short"
        initial = FuturesInitialOrder(
            contract=self.contract,
            size=0,
            price="0",
            reduce_only=True,
            tif="ioc",
            auto_size=auto_size,
        )
        setattr(initial, "side", order_side)
        trigger = FuturesPriceTrigger(strategy_type=0, price_type=0, price=price_str, rule=trigger_rule)
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        setattr(order, "order_type", "close-long-position" if side == "long" else "close-short-position")
        result = self.api.create_price_triggered_order(self.settle, order)
        logger.info("가격 트리거 SL 등록: trigger=%s side=%s order_id=%s", price_str, side, getattr(result, "id", "?"))
        return result

    def create_take_profit_order(self, trigger_price: str, size: str, side: str, is_partial: bool = False):
        order_side = self._close_side(side)
        price_str = self._round_trigger_price_str(trigger_price)
        size_int = int(size) if isinstance(size, str) else int(size)
        initial_size = 0 if not is_partial else (-size_int if side == "long" else size_int)
        auto_size = ("close_long" if side == "long" else "close_short") if not is_partial else None
        initial = FuturesInitialOrder(
            contract=self.contract,
            size=initial_size,
            price="0",
            reduce_only=True,
            tif="ioc",
            auto_size=auto_size,
        )
        setattr(initial, "side", order_side)
        trigger = FuturesPriceTrigger(strategy_type=0, price_type=0, price=price_str, rule=1 if side == "long" else 2)
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        if is_partial:
            setattr(order, "order_type", "plan-close-long-position" if side == "long" else "plan-close-short-position")
        else:
            setattr(order, "order_type", "close-long-position" if side == "long" else "close-short-position")
        result = self.api.create_price_triggered_order(self.settle, order)
        logger.info("가격 트리거 TP 등록: trigger=%s side=%s partial=%s order_id=%s", price_str, side, is_partial, getattr(result, "id", "?"))
        return result

    def list_open_price_orders(self) -> list:
        try:
            return self.api.list_price_triggered_orders(self.settle, status="open") or []
        except Exception as exc:
            logger.warning("가격 트리거 주문 조회 실패: %s", exc)
            return []

    def cancel_all_price_orders(self):
        logger.info("가격 트리거 주문 전체 취소")
        try:
            return self.api.cancel_price_triggered_order_list(self.settle)
        except Exception as e:
            text = str(e)
            if "Invalid value for" in text or "pos_margin_mode" in text:
                logger.warning("가격 트리거 취소 응답 파싱 무시(요청은 처리됐을 수 있음): %s", e)
                return None
            logger.warning("가격 트리거 주문 취소 중 오류(무시): %s", e)
            return None
