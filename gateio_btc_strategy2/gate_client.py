"""
Gate.io Futures API 클라이언트.
"""
import logging
import time
from typing import Optional, TYPE_CHECKING

import config

if TYPE_CHECKING:
    from price_stream import PriceStream

try:
    import gate_api
    from gate_api import (
        FuturesOrder,
        FuturesPriceTriggeredOrder,
        FuturesInitialOrder,
        FuturesPriceTrigger,
    )
except ImportError:
    gate_api = None
    FuturesOrder = FuturesPriceTriggeredOrder = FuturesInitialOrder = FuturesPriceTrigger = None

logger = logging.getLogger(__name__)


def _is_connection_error(e: Exception) -> bool:
    if e is None:
        return False
    msg = str(e).lower()
    return "remotedisconnected" in msg or ("connection" in msg and ("closed" in msg or "reset" in msg or "refused" in msg))


class GateIOClient:
    def __init__(
        self,
        key: Optional[str] = None,
        secret: Optional[str] = None,
        base: Optional[str] = None,
        price_stream: Optional["PriceStream"] = None,
    ):
        if gate_api is None:
            raise RuntimeError("pip install gate-api 필요")
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

    def _reset_api_on_connection_error(self):
        self._api = None

    def _log_connection_once(self):
        if self._connection_logged:
            return
        try:
            logger.info("Gate.io 연결: base=%s settle=%s contract=%s", self.config.host, self.settle, self.contract)
            self._connection_logged = True
        except Exception as e:
            logger.warning("Gate.io 연결 로그 실패: %s", e)

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
                            "t": int(r.t),
                            "o": float(r.o),
                            "h": float(r.h),
                            "l": float(r.l),
                            "c": float(r.c),
                            "v": float(getattr(r, "v", 0.0)),
                        }
                    )
                out.sort(key=lambda x: x["t"])
                return out
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning("캔들 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, e)
                    time.sleep(retry_delay)
                else:
                    logger.error("캔들 조회 최종 실패: %s", e)
                    return []

    def get_last_price(self) -> Optional[float]:
        if self._price_stream:
            ws_price = self._price_stream.get_last_price()
            if ws_price is not None:
                return ws_price
        try:
            tickers = self.api.list_futures_tickers(self.settle)
            for t in tickers or []:
                if getattr(t, "contract", None) == self.contract:
                    last_ = getattr(t, "last", None)
                    if last_ is not None:
                        return float(last_)
        except Exception as e:
            logger.debug("현재가 조회 실패: %s", e)
        return None

    def get_account(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self.api.list_futures_accounts(self.settle)
            except Exception as e:
                if _is_connection_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    time.sleep(1.5)
                    continue
                raise

    def _row_from_api_position(self, pos) -> dict:
        size = float(pos.size or 0)
        return {
            "size": size,
            "side": "long" if size > 0 else "short",
            "entry_price": float(pos.entry_price or 0),
            "contract": getattr(pos, "contract", self.contract),
        }

    def _classify_position_row(self, pos) -> Optional[str]:
        size = float(pos.size or 0)
        if abs(size) < 1e-12:
            return None
        mode = str(getattr(pos, "mode", "") or "").lower()
        if "dual_short" in mode or mode == "short":
            return "short"
        if "dual_long" in mode or mode == "long":
            return "long"
        return "long" if size > 0 else "short"

    def get_contract_positions(self) -> dict:
        out: dict = {"long": None, "short": None}
        try:
            for p in self.api.list_positions(self.settle) or []:
                if getattr(p, "contract", None) != self.contract:
                    continue
                side = self._classify_position_row(p)
                if side is None:
                    continue
                row = self._row_from_api_position(p)
                row["side"] = side
                row["size"] = abs(float(row["size"])) if side == "long" else -abs(float(row["size"]))
                out[side] = row
        except Exception as e:
            logger.warning("포지션 목록 조회 실패: %s", e)
        if out["long"] is None and out["short"] is None:
            single = self._get_position_single()
            if single:
                out[single["side"]] = single
        return out

    def _get_position_single(self) -> Optional[dict]:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                pos = self.api.get_position(self.settle, self.contract)
                size = float(pos.size or 0)
                if size == 0:
                    return None
                return self._row_from_api_position(pos)
            except gate_api.exceptions.GateApiException as e:
                if getattr(e, "label", "") == "POSITION_NOT_FOUND":
                    return None
                raise
            except gate_api.exceptions.ApiException as e:
                if e.status == 404:
                    return None
                if _is_connection_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    time.sleep(1.5)
                    continue
                raise
            except Exception as e:
                if _is_connection_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    time.sleep(1.5)
                    continue
                raise
        return None

    def get_position_for_side(self, side: str, min_size: int = 1) -> Optional[dict]:
        positions = self.get_contract_positions()
        pos = positions.get(side)
        if pos and abs(float(pos.get("size", 0))) >= float(min_size):
            return pos
        single = self._get_position_single()
        if single and single.get("side") == side and abs(float(single.get("size", 0))) >= float(min_size):
            return single
        return None

    def get_position(self) -> Optional[dict]:
        positions = self.get_contract_positions()
        return positions["short"] or positions["long"]

    def close_position(self, position_side: str):
        auto_size = "close_long" if position_side == "long" else "close_short"
        order = FuturesOrder(
            contract=self.contract,
            size="0",
            price="0",
            reduce_only=True,
            tif="ioc",
            auto_size=auto_size,
        )
        return self.api.create_futures_order(self.settle, order)

    def _round_trigger_price_str(self, price) -> str:
        tick = getattr(config, "PRICE_TICK", 0.1)
        rounded = round(float(price) / tick) * tick
        if tick >= 1:
            return str(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]))
        return f"{rounded:.{nd}f}".rstrip("0").rstrip(".")

    def _close_side(self, position_side: str) -> str:
        return "sell" if position_side == "long" else "buy"

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
        except Exception as e:
            logger.warning("거래소 레버리지 설정 실패(%sx): %s", lev_s, e)

    def place_market_order(self, side: str, size: str, reduce_only: bool = False, close_position_side: Optional[str] = None):
        if reduce_only:
            pos_side = close_position_side
            if not pos_side:
                pos = self.get_position()
                pos_side = pos["side"] if pos else None
            if pos_side:
                return self.close_position(pos_side)
            logger.warning("reduce_only 주문 생략: 청산할 포지션 없음")
            return None
        self.set_exchange_leverage()
        size_int = int(size)
        order_size = str(abs(size_int)) if side == "long" else str(-abs(size_int))
        order = FuturesOrder(contract=self.contract, size=order_size, iceberg=0, price="0", close=False, reduce_only=False, tif="ioc")
        return self.api.create_futures_order(self.settle, order)

    def create_stop_loss_order(self, trigger_price: str, size: str, side: str):
        order_side = self._close_side(side)
        trigger_rule = 2 if side == "long" else 1
        auto_size = "close_long" if side == "long" else "close_short"
        initial = FuturesInitialOrder(contract=self.contract, size=0, price="0", reduce_only=True, tif="ioc", auto_size=auto_size)
        setattr(initial, "side", order_side)
        trigger = FuturesPriceTrigger(strategy_type=0, price_type=0, price=self._round_trigger_price_str(trigger_price), rule=trigger_rule)
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        setattr(order, "order_type", "close-long-position" if side == "long" else "close-short-position")
        return self.api.create_price_triggered_order(self.settle, order)

    def create_take_profit_order(self, trigger_price: str, size: str, side: str, is_partial: bool = False):
        order_side = self._close_side(side)
        size_int = int(size)
        initial_size = 0 if not is_partial else (-size_int if side == "long" else size_int)
        auto_size = None if is_partial else ("close_long" if side == "long" else "close_short")
        initial = FuturesInitialOrder(contract=self.contract, size=initial_size, price="0", reduce_only=True, tif="ioc", auto_size=auto_size)
        setattr(initial, "side", order_side)
        trigger = FuturesPriceTrigger(strategy_type=0, price_type=0, price=self._round_trigger_price_str(trigger_price), rule=1 if side == "long" else 2)
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        if is_partial:
            setattr(order, "order_type", "plan-close-long-position" if side == "long" else "plan-close-short-position")
        else:
            setattr(order, "order_type", "close-long-position" if side == "long" else "close-short-position")
        return self.api.create_price_triggered_order(self.settle, order)

    def cancel_all_price_orders(self):
        try:
            return self.api.cancel_price_triggered_order_list(self.settle, contract=self.contract)
        except Exception as e:
            text = str(e)
            if "Invalid value for" in text or "pos_margin_mode" in text:
                logger.warning("가격 트리거 취소 응답 파싱 무시(요청은 처리됐을 수 있음): %s", e)
                return None
            logger.warning("가격 트리거 주문 취소 중 오류(무시): %s", e)
            return None
