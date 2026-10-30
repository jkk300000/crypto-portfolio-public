"""
Gate.io Futures API 클라이언트 (gateio 디렉토리 패턴 준수).
- gate_api SDK 사용, 30분봉 OHLCV 조회(재시도), 포지션/계정, 시장가 주문
- TP/SL: 가격 트리거 주문(price_orders), 트리거 가격 반올림(PRICE_TICK)
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

# RemoteDisconnected, 504 Gateway Timeout 등 일시 오류 재시도용
def _is_connection_error(e: Exception) -> bool:
    if e is None:
        return False
    msg = str(e).lower()
    if "remotedisconnected" in msg or "connection" in msg and ("closed" in msg or "reset" in msg or "refused" in msg):
        return True
    return False


def _is_sdk_schema_error(e: Exception) -> bool:
    """게이트 응답 필드를 구버전 SDK가 거절하는 경우 (요청은 이미 처리됐을 수 있음)."""
    text = str(e)
    return "Invalid value for" in text or "pos_margin_mode" in text


def _is_retriable_error(e: Exception) -> bool:
    if _is_connection_error(e):
        return True
    msg = str(e).lower()
    if "504" in msg or "gateway timeout" in msg or "request timeout" in msg:
        return True
    if gate_api is not None:
        if isinstance(e, gate_api.exceptions.ApiException) and getattr(e, "status", None) == 504:
            return True
        if isinstance(e, gate_api.exceptions.GateApiException):
            if getattr(e, "status", None) == 504:
                return True
            if getattr(e, "label", "") == "INTERNAL" and "timeout" in msg:
                return True
    return False


class GateIOClient:
    """gateio/api/gate_client.py GateFuturesClient 패턴: 캔들 재시도, TP/SL round_trigger, get_last_price(WS/REST)."""

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

    def _log_connection_once(self):
        if self._connection_logged:
            return
        try:
            logger.info(
                "Gate.io 연결: base=%s settle=%s contract=%s",
                self.config.host, self.settle, self.contract,
            )
            acc = self.api.list_futures_accounts(self.settle)
            acc_list = acc if isinstance(acc, list) else [acc] if acc else []
            a0 = acc_list[0] if acc_list else None
            total = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0) if a0 else 0.0
            logger.info("API 계정 확인 완료: 선물 계정 total=%.2f USDT", total)
            self._connection_logged = True
        except Exception as e:
            logger.warning("Gate.io 연결/계정 확인 중 오류: %s", e)

    @property
    def api(self):
        if self._api is None:
            self._api = gate_api.FuturesApi(gate_api.ApiClient(self.config))
        return self._api

    def _reset_api_on_connection_error(self):
        """연결 끊김 후 재시도 시 새 TCP 연결을 쓰도록 API 클라이언트 초기화 (테스트넷 끊김 완화)."""
        self._api = None

    @property
    def settle(self) -> str:
        return config.SETTLE

    @property
    def contract(self) -> str:
        return config.CONTRACT

    # --- 캔들: gateio와 동일하게 재시도 + [{"t","o","h","l","c","v"}] 시간순 ---
    def get_candles(
        self,
        limit: Optional[int] = None,
        interval: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> list:
        """30분봉 OHLCV. 반환: [{"t", "o", "h", "l", "c", "v"}, ...] 오래된 것부터."""
        self._log_connection_once()
        limit = limit or config.CANDLE_LIMIT
        interval = interval or config.CANDLE_INTERVAL
        for attempt in range(max_retries):
            try:
                raw = self.api.list_futures_candlesticks(
                    self.settle, self.contract, limit=limit, interval=interval
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning("캔들 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, e)
                    time.sleep(retry_delay)
                else:
                    logger.error("캔들 조회 최종 실패 (%d회 시도): %s", max_retries, e)
                    return []

        out = []
        for r in raw:
            out.append({
                "t": int(r.t) if hasattr(r, "t") else int(getattr(r, "t", 0)),
                "o": float(r.o),
                "h": float(r.h),
                "l": float(r.l),
                "c": float(r.c),
                "v": float(r.v) if hasattr(r, "v") else 0.0,
            })
        out.sort(key=lambda x: x["t"])
        logger.debug("캔들 조회: interval=%s limit=%d → %d봉", interval, limit, len(out))
        return out

    def get_last_price(self) -> Optional[float]:
        """현재가. WebSocket 스트림 있으면 우선 사용, 없으면 REST 티커."""
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
        except Exception as e:
            logger.debug("현재가 조회 실패: %s", e)
            return None

    def _round_trigger_price_str(self, price) -> str:
        """트리거 가격을 PRICE_TICK 단위로 반올림 (Gate API 요구). gateio/api/gate_client.py 참고."""
        tick = getattr(config, "PRICE_TICK", 0.01)
        p = float(price)
        rounded = round(p / tick) * tick
        if tick >= 1:
            return str(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]) if "." in f"{tick:.10f}" else 0)
        return f"{rounded:.{nd}f}".rstrip("0").rstrip(".")

    def _round_trigger_price(self, price) -> float:
        """트리거 가격 반올림 (float 반환). gateio/api/gate_client.py 참고."""
        tick = getattr(config, "PRICE_TICK", 0.01)
        p = float(price)
        rounded = round(p / tick) * tick
        if tick >= 1:
            return float(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]) if "." in f"{tick:.10f}" else 0)
        return float(f"{rounded:.{nd}f}".rstrip("0").rstrip("."))

    # --- 계정/포지션 (연결 끊김 시 재시도) ---
    def get_account(self):
        self._log_connection_once()
        max_retries = 3
        retry_delay = 2.0
        for attempt in range(max_retries):
            try:
                return self.api.list_futures_accounts(self.settle)
            except Exception as e:
                if _is_retriable_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("계정 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, e)
                    time.sleep(retry_delay)
                else:
                    raise

    def get_position(self) -> Optional[dict]:
        """현재 포지션. 연결 끊김(RemoteDisconnected) 시 재시도.

        주의: 현재 설치된 gate_api 버전에서는 dual 모드 개별 롱/숏이 아닌
        net 포지션(get_position)만 지원하는 것으로 보입니다.
        따라서 이 봇은 **one-way(단일 포지션 모드)** 기준으로 사용하는 것을 권장합니다.
        """
        max_retries = 3
        retry_delay = 2.0
        for attempt in range(max_retries):
            try:
                pos = self.api.get_position(self.settle, self.contract)
                size = float(pos.size or 0)
                if size == 0:
                    return None
                return {
                    "size": size,
                    "side": "long" if size > 0 else "short",
                    "entry_price": float(pos.entry_price or 0),
                    "contract": pos.contract,
                }
            except gate_api.exceptions.GateApiException as e:
                if getattr(e, "label", "") == "POSITION_NOT_FOUND":
                    return None
                if _is_retriable_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("포지션 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, e)
                    time.sleep(retry_delay)
                    continue
                raise
            except gate_api.exceptions.ApiException as e:
                if e.status == 404:
                    return None
                if _is_retriable_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("포지션 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, e)
                    time.sleep(retry_delay)
                else:
                    raise
            except Exception as e:
                if _is_retriable_error(e) and attempt < max_retries - 1:
                    self._reset_api_on_connection_error()
                    logger.warning("포지션 조회 실패 (재시도 %d/%d): %s", attempt + 1, max_retries, e)
                    time.sleep(retry_delay)
                else:
                    raise
        return None  # retries exhausted on connection error → 포지션 없음으로 간주

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

    def _place_market_order_once(self, side: str, size: str, reduce_only: bool = False):
        size_int = int(size)
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
                result = self.api.create_futures_order(self.settle, order)
                logger.info(
                    "시장가 청산 주문 접수(dual mode): pos_side=%s auto_size=%s order_id=%s",
                    pos["side"],
                    auto_size,
                    getattr(result, "id", "?"),
                )
                return result

        self.set_exchange_leverage()
        order_size = str(abs(size_int)) if side == "long" else str(-abs(size_int))
        order = FuturesOrder(
            contract=self.contract,
            size=order_size,
            iceberg=0,
            price="0",
            close=False,
            reduce_only=reduce_only,
            tif="ioc",
        )
        result = self.api.create_futures_order(self.settle, order)
        logger.info(
            "시장가 주문 접수: side=%s size=%s reduce_only=%s order_id=%s",
            side,
            order_size,
            reduce_only,
            getattr(result, "id", "?"),
        )
        return result

    def _order_succeeded_despite_error(self, side: str, reduce_only: bool) -> bool:
        """504 등 응답 타임아웃 후 실제 주문/청산 반영 여부 확인."""
        pos = self.get_position()
        if reduce_only:
            return pos is None
        return pos is not None and pos.get("side") == side

    # --- 시장가 주문 (진입/청산) ---
    def place_market_order(self, side: str, size: str, reduce_only: bool = False):
        """시장가 주문. 504 등 일시 오류 시 재시도, 타임아웃이어도 체결됐으면 성공 처리."""
        max_retries = 3
        retry_delay = 1.5
        last_err = None
        for attempt in range(max_retries):
            try:
                return self._place_market_order_once(side, size, reduce_only)
            except Exception as e:
                last_err = e
                if self._order_succeeded_despite_error(side, reduce_only):
                    logger.warning(
                        "시장가 주문 응답 오류지만 포지션 상태 확인으로 성공 처리: side=%s reduce_only=%s err=%s",
                        side,
                        reduce_only,
                        e,
                    )
                    return None
                if not _is_retriable_error(e) or attempt >= max_retries - 1:
                    raise
                logger.warning(
                    "시장가 주문 실패 (재시도 %d/%d): side=%s size=%s reduce_only=%s err=%s",
                    attempt + 1,
                    max_retries,
                    side,
                    size,
                    reduce_only,
                    e,
                )
                time.sleep(retry_delay * (attempt + 1))
        if last_err:
            raise last_err

    def count_open_price_orders(self) -> int:
        try:
            orders = self.list_price_orders(status="open")
            return len(orders) if orders else 0
        except Exception as e:
            logger.warning("가격 트리거 주문 조회 실패: %s", e)
            return -1

    # --- TP/SL: 가격 트리거 (gateio/api/gate_client.py 참고) ---
    # FuturesInitialOrder.price="0" → 트리거 도달 시 시장가 체결 (Gate API 규격).
    # 포지션 방향별 청산: 롱 청산 = 매도(sell), 숏 청산 = 매수(buy). API 스키마는 side: "buy" | "sell".
    # FuturesPriceTrigger.rule: 1 = >= price, 2 = <= price
    # 롱 TP: 가격 >= trigger 시 매도(sell) → rule=1
    # 롱 SL: 가격 <= trigger 시 매도(sell) → rule=2
    # 숏 TP: 가격 <= trigger 시 매수(buy) → rule=2
    # 숏 SL: 가격 >= trigger 시 매수(buy) → rule=1
    def _close_side(self, position_side: str) -> str:
        """포지션 방향에 따른 청산 주문 side. long → sell, short → buy."""
        return "sell" if position_side == "long" else "buy"

    def create_stop_loss_order(self, trigger_price: str, size: str, side: str):
        """Stop loss: 트리거 시 시장가 청산. 전체 포지션 청산(close-*-position, size=0).
        Gate 규칙(롱 SL < last_price, 숏 SL > last_price) 위반 시 트리거가를 조정해 등록.
        dual position mode에서는 close/is_close 미사용(API 오류 방지)."""
        order_side = self._close_side(side)
        tick = getattr(config, "PRICE_TICK", 0.01)
        last_price_check = self.get_last_price()

        price_float = float(trigger_price)
        if last_price_check is not None:
            # 롱 SL: trigger < last_price / 숏 SL: trigger > last_price (미충족 시 등록 실패)
            if side == "long" and price_float >= last_price_check:
                price_float = self._round_trigger_price(last_price_check - tick * 2)
                if price_float >= last_price_check:
                    price_float = last_price_check - tick
                logger.warning(
                    "SL 가격 규칙 조정: 롱 SL을 last_price(%.4f) 미만으로 조정 → trigger=%.4f",
                    last_price_check, price_float,
                )
            elif side == "short" and price_float <= last_price_check:
                price_float = self._round_trigger_price(last_price_check + tick * 2)
                if price_float <= last_price_check:
                    price_float = last_price_check + tick
                logger.warning(
                    "SL 가격 규칙 조정: 숏 SL을 last_price(%.4f) 초과로 조정 → trigger=%.4f",
                    last_price_check, price_float,
                )
        price_str = self._round_trigger_price_str(price_float)
        trigger_rule = 2 if side == "long" else 1

        # 현재 포지션 수량 검증 (reduce_only 규칙)
        pos_check = self.get_position()
        if pos_check:
            pos_size_abs = abs(pos_check["size"])
            size_int = int(size) if isinstance(size, str) else int(size)
            if size_int > pos_size_abs:
                logger.warning(
                    "SL 등록 시 수량 규칙 위반 가능성: 주문수량(%d) > 현재포지션수량(%.2f)",
                    size_int, pos_size_abs,
                )

        # dual position mode: size=0 전체 청산 시 auto_size 필요(close_long/close_short)
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
        trigger = FuturesPriceTrigger(
            strategy_type=0,
            price_type=0,
            price=price_str,
            rule=trigger_rule,
        )
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        order_type_value = "close-long-position" if side == "long" else "close-short-position"
        setattr(order, "order_type", order_type_value)
        try:
            result = self.api.create_price_triggered_order(self.settle, order)
            logger.info(
                "가격 트리거 SL 등록: trigger_price=%s side=%s order_type=%s rule=%d reduce_only=True order_id=%s",
                price_str, side, order_type_value, trigger_rule, getattr(result, "id", "?"),
            )
            return result
        except Exception as e:
            logger.error("SL 주문 등록 실패: trigger_price=%s size=%s side=%s rule=%d 오류=%s", price_str, size, side, trigger_rule, e)
            raise

    def create_take_profit_order(self, trigger_price: str, size: str, side: str, is_partial: bool = False):
        """Take profit: 트리거 시 시장가 청산. price=0 → 시장가.
        is_partial=False → 전체 포지션 익절(close-*-position, size=0).
        is_partial=True → 부분 익절(plan-close-*-position). Gate: 롱 부분 청산 size 음수, 숏 부분 청산 size 양수.
        dual position mode: close/is_close 미사용."""
        order_side = self._close_side(side)
        price_str = self._round_trigger_price_str(trigger_price)
        size_int = int(size) if isinstance(size, str) else int(size)
        if not is_partial:
            initial_size = 0
        else:
            initial_size = -size_int if side == "long" else size_int
        # dual position mode: size=0 전체 청산 시 auto_size 필요, 부분 청산(size!=0)은 auto_size 불필요
        auto_size = None
        if not is_partial:
            auto_size = "close_long" if side == "long" else "close_short"
        initial = FuturesInitialOrder(
            contract=self.contract,
            size=initial_size,
            price="0",
            reduce_only=True,
            tif="ioc",
            auto_size=auto_size,
        )
        setattr(initial, "side", order_side)
        trigger = FuturesPriceTrigger(
            strategy_type=0,
            price_type=0,
            price=price_str,
            rule=1 if side == "long" else 2,
        )
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        # 부분 청산: plan-close-long-position / plan-close-short-position, 전체: close-long-position / close-short-position
        if is_partial:
            order_type_value = "plan-close-long-position" if side == "long" else "plan-close-short-position"
        else:
            order_type_value = "close-long-position" if side == "long" else "close-short-position"
        setattr(order, "order_type", order_type_value)
        try:
            result = self.api.create_price_triggered_order(self.settle, order)
            logger.info(
                "가격 트리거 TP 등록: trigger_price=%s size=%s order_type=%s(is_partial=%s) reduce_only=True order_id=%s",
                price_str, size, order_type_value, is_partial, getattr(result, "id", "?"),
            )
            return result
        except Exception as e:
            logger.error("TP 주문 등록 실패: trigger_price=%s size=%s side=%s 오류=%s", price_str, size, side, e)
            raise

    def list_price_orders(self, status: str = "open"):
        return self.api.list_price_triggered_orders(self.settle, status=status)

    def cancel_all_price_orders(self):
        logger.info("가격 트리거 주문 전체 취소")
        try:
            return self.api.cancel_price_triggered_order_list(self.settle)
        except Exception as e:
            if _is_sdk_schema_error(e):
                logger.warning("가격 트리거 취소 응답 파싱 무시(요청은 처리됐을 수 있음): %s", e)
                return None
            logger.warning("가격 트리거 주문 취소 중 오류(무시): %s", e)
            return None
