"""
SuperTrend + Bollinger Bands — Gate.io ETH 30m 자동매매 엔진.
gateio/engine.py 패턴: 30봉 완성 시점 감지, get_candles → add_indicators → 진입/반대신호 청산 후 반대진입, TP/SL은 가격 트리거 주문.
"""
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
from config import ( 
    CHART_BAR_MINUTES,
    CANDLE_LIMIT,
    CONTRACT,
    ENTRY_CHECK_ONLY_ON_BAR_CLOSE,
    ORDER_SIZE_MIN,
)
from gate_client import GateIOClient, _is_retriable_error
from indicators import add_indicators
from price_stream import PriceStream
from strategy import check_entry, check_entry_relaxed, get_last_completed_bar_row, rr_ratio_for_side

try:
    from gateio.notify.discord import notify_discord
except ImportError:
    notify_discord = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
BAR_SECONDS = CHART_BAR_MINUTES * 60


def _account_snapshot(acc) -> tuple[float, float | None]:
    """Gate 선물 잔고. equity=장부(total/equity), available=주문 가능액."""
    if acc is None:
        return 0.0, None
    if isinstance(acc, dict):
        equity = float(acc.get("equity") or acc.get("total") or 0)
        raw = acc.get("available")
    else:
        equity = float(getattr(acc, "equity", None) or getattr(acc, "total", None) or 0)
        raw = getattr(acc, "available", None)
    try:
        available = float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        available = None
    return max(0.0, equity), available


def _account_sizing_usdt(acc) -> float:
    """진입 수량용: 이 계좌 total/equity. available(묶인 마진)은 쓰지 않는다."""
    equity, _available = _account_snapshot(acc)
    return equity


def _format_size_for_notify(size: str | None) -> str | None:
    """Discord 알림용 수량 포맷: 실제 ETH 수량만 표시."""
    if size is None:
        return None
    try:
        contracts = float(size)
        eth_qty = contracts * float(getattr(config, "CONTRACT_MULTIPLIER", 1.0))
        return f"{eth_qty:.4f} ETH"
    except Exception:
        return size


def _ts_to_ymdhm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=KST).strftime("%Y-%m-%d %H:%M")


def _is_auto_user_no_position_error(err: Exception) -> bool:
    """Gate AUTO_USER_NO_LONG/SHORT_POSITION(1037/1038) 식별."""
    text = str(err)
    return (
        "AUTO_USER_NO_LONG_POSITION" in text
        or "AUTO_USER_NO_SHORT_POSITION" in text
        or '"code":"1037"' in text
        or '"code":"1038"' in text
    )


def _is_tp_sl_retriable(err: Exception) -> bool:
    return _is_auto_user_no_position_error(err) or _is_retriable_error(err)


class TradingEngine:
    """gateio/engine.py 패턴: 30분봉 완성 직후 진입 체크, TP/SL 가격 트리거 등록."""

    def __init__(self, client: Optional[GateIOClient] = None):
        self._cfg = config.load_config() if hasattr(config, "load_config") else None
        price_stream = None
        if self._cfg and getattr(self._cfg, "use_websocket_price", True):
            try:
                price_stream = PriceStream()
                price_stream.start()
            except Exception as e:
                logger.warning("WebSocket 가격 스트림 미사용: %s", e)
        self.client = client or GateIOClient(price_stream=price_stream)
        self._last_bar_ts_checked: Optional[int] = None
        self._last_processed_bar_end_ts: Optional[float] = None
        self._entry_check_lock = threading.Lock()
        self._stop_entry_check_thread = False
        self._entry_check_thread: Optional[threading.Thread] = None
        self._pending_tp_sl: Optional[dict] = None
        self._last_live_pos: Optional[dict] = None
        self._position_poll_lock = threading.Lock()

    def _remember_tp_sl(self, side: str, sl_price: float, tp_price: float):
        self._pending_tp_sl = {"side": side, "sl_price": sl_price, "tp_price": tp_price}

    def _compute_tp_sl_for_position(self, pos: dict) -> Optional[dict]:
        candles = self._get_candles_cached()
        row, _ = get_last_completed_bar_row(candles, BAR_SECONDS, time.time())
        if not row:
            return None
        side = pos["side"]
        entry = float(pos["entry_price"])
        rr = rr_ratio_for_side(side)
        if side == "long":
            bb_lower = row.get("bb_lower")
            if bb_lower is None:
                return None
            sl = float(bb_lower)
            dist = entry - sl
            tp = entry + dist * rr
        else:
            bb_upper = row.get("bb_upper")
            if bb_upper is None:
                return None
            sl = float(bb_upper)
            dist = sl - entry
            tp = entry - dist * rr
        return {"side": side, "sl_price": sl, "tp_price": tp}

    def _recover_tp_sl_if_needed(self):
        pos = self.client.get_position()
        if not pos:
            self._pending_tp_sl = None
            return
        open_count = self.client.count_open_price_orders()
        if open_count >= 2:
            return
        if open_count < 0:
            return
        logger.warning(
            "포지션 보유 중 TP/SL 미등록 감지: side=%s size=%s open_price_orders=%d",
            pos["side"],
            pos["size"],
            open_count,
        )
        plan = self._pending_tp_sl
        if not plan or plan.get("side") != pos["side"]:
            plan = self._compute_tp_sl_for_position(pos)
        if not plan:
            logger.error("TP/SL 복구용 가격 계산 실패: side=%s", pos["side"])
            return
        size = str(int(abs(pos["size"])))
        self._remember_tp_sl(plan["side"], plan["sl_price"], plan["tp_price"])
        self._setup_tp_sl(plan["side"], size, plan["sl_price"], plan["tp_price"])

    def _execute_entry(self, side: str, size: str, sl_price: float, tp_price: float) -> bool:
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        self._remember_tp_sl(side, sl_price, tp_price)
        if not dry:
            self.client.place_market_order(side, size, reduce_only=False)
            self._wait_for_position(side, min_size=int(size))
            if not self._setup_tp_sl(side, size, sl_price, tp_price):
                logger.error("진입 후 TP/SL 등록 실패 — 메인 루프에서 복구 재시도 예정")
            try:
                live = self.client.get_position()
                with self._position_poll_lock:
                    self._last_live_pos = self._pos_snapshot(live) if live else {
                        "side": side,
                        "size": float(size),
                        "entry_price": 0.0,
                    }
            except Exception:
                with self._position_poll_lock:
                    self._last_live_pos = {"side": side, "size": float(size), "entry_price": 0.0}
        logger.info("%s 진입: size=%s sl=%.4f tp=%.4f", "롱" if side == "long" else "숏", size, sl_price, tp_price)
        return True

    def _current_bar_start_ts(self) -> int:
        return int(time.time() // BAR_SECONDS) * BAR_SECONDS

    def _get_candles_cached(self, limit: Optional[int] = None) -> list:
        limit = limit or CANDLE_LIMIT
        raw = self.client.get_candles(limit=limit, interval=config.CANDLE_INTERVAL)
        cfg = self._cfg
        kw = {}
        if cfg:
            kw = dict(
                atr_period=cfg.atr_period,
                atr_mult=cfg.atr_multiplier,
                bb_length=cfg.bb_length,
                bb_mult=cfg.bb_mult,
                bb_ma_type=cfg.bb_ma_type,
            )
        out = add_indicators(raw, **kw) if kw else add_indicators(raw)
        return out

    def _setup_tp_sl(self, side: str, size: str, sl_price: float, tp_price: float) -> bool:
        """TP/SL 등록. 포지션 반영 지연(1037/1038) 및 504 타임아웃 시 재시도."""
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        if dry:
            logger.info("DRY RUN: TP/SL 등록 생략 side=%s sl=%.4f tp=%.4f", side, sl_price, tp_price)
            return True
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                try:
                    self.client.cancel_all_price_orders()
                except Exception as e:
                    logger.warning("기존 가격 트리거 주문 취소 중 오류(무시): %s", e)
                self.client.create_stop_loss_order(str(sl_price), size, side)
                self.client.create_take_profit_order(str(tp_price), size, side, is_partial=False)
                logger.info("TP/SL 등록 완료: side=%s sl=%.4f tp=%.4f size=%s", side, sl_price, tp_price, size)
                return True
            except Exception as e:
                if _is_tp_sl_retriable(e) and attempt < max_attempts:
                    wait_sec = 1.0 * attempt
                    logger.warning(
                        "TP/SL 재시도(%d/%d): side=%s size=%s sl=%.4f tp=%.4f wait=%.1fs err=%s",
                        attempt,
                        max_attempts,
                        side,
                        size,
                        sl_price,
                        tp_price,
                        wait_sec,
                        e,
                    )
                    time.sleep(wait_sec)
                    continue
                logger.error(
                    "TP/SL 등록 실패(최종): side=%s size=%s sl=%.4f tp=%.4f 오류=%s",
                    side,
                    size,
                    sl_price,
                    tp_price,
                    e,
                )
                return False
        return False

    def _wait_for_position(
        self,
        expected_side: str,
        min_size: int = 1,
        timeout_sec: float = 5.0,
        poll_interval_sec: float = 0.25,
    ) -> bool:
        """진입 주문 후 포지션 반영을 polling으로 확인(단일 모드 기준)."""
        deadline = time.time() + timeout_sec
        last_pos = None
        while time.time() < deadline:
            try:
                pos = self.client.get_position()
                last_pos = pos
                if pos and pos.get("side") == expected_side and abs(float(pos.get("size", 0))) >= float(min_size):
                    logger.info(
                        "포지션 반영 확인 완료: side=%s size=%s",
                        pos.get("side"),
                        pos.get("size"),
                    )
                    return True
            except Exception as e:
                logger.debug("포지션 polling 조회 오류(재시도): %s", e)
            time.sleep(poll_interval_sec)
        logger.warning(
            "포지션 반영 확인 타임아웃: expected_side=%s min_size=%s timeout=%.1fs last_pos=%s",
            expected_side,
            min_size,
            timeout_sec,
            last_pos,
        )
        return False

    def _entry_check_loop(self):
        """30분 봉 완성 시점 감지 → 지표 계산 → 진입/반대신호 처리."""
        logger.info("진입 조건 체크 스레드 시작 (2초 주기, 30분 봉 완성 시점 감지)")
        while not self._stop_entry_check_thread:
            try:
                if self._entry_check_lock.acquire(blocking=False):
                    try:
                        now = time.time()
                        current_bar_start = self._current_bar_start_ts()
                        current_bar_end = current_bar_start + BAR_SECONDS
                        if self._last_processed_bar_end_ts is None:
                            self._last_processed_bar_end_ts = current_bar_end
                        if now >= self._last_processed_bar_end_ts and now < current_bar_end:
                            logger.info("30분 봉 완성 감지: 지표 계산 시작")
                            candles = self._get_candles_cached()
                            if not candles:
                                logger.warning("캔들 조회 결과 없음")
                            else:
                                completed_row, completed_idx = get_last_completed_bar_row(candles, BAR_SECONDS, now)
                                if completed_row is None:
                                    pass
                                else:
                                    last_ts = completed_row.get("t")
                                    if ENTRY_CHECK_ONLY_ON_BAR_CLOSE and last_ts == self._last_bar_ts_checked:
                                        pass
                                    else:
                                        self._last_bar_ts_checked = last_ts
                                        self._check_and_execute_entry(candles, completed_idx)
                                self._last_processed_bar_end_ts = current_bar_end
                    finally:
                        self._entry_check_lock.release()
            except Exception as e:
                logger.exception("진입 체크 오류: %s", e)
            dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
            if not dry:
                try:
                    self._poll_live_close()
                except Exception as e:
                    logger.debug("청산 폴링 오류: %s", e)
            time.sleep(2.0)
        logger.info("진입 조건 체크 스레드 종료")

    def _pos_snapshot(self, pos: dict) -> dict:
        return {
            "side": pos["side"],
            "size": float(pos["size"]),
            "entry_price": float(pos.get("entry_price") or 0),
        }

    def _notify_live_flat(self, prev: dict, extra: str = "(TP/SL·거래소 청산)") -> None:
        try:
            self.client.cancel_all_price_orders()
        except Exception as e:
            logger.warning("청산 후 잔여 TP/SL 취소 실패: %s", e)
        close_price = None
        try:
            close_price = self.client.get_last_price()
        except Exception:
            pass
        if notify_discord:
            try:
                notify_discord(
                    "close",
                    side=prev["side"],
                    close_price=close_price,
                    size=_format_size_for_notify(str(int(abs(float(prev["size"]))))),
                    extra=extra,
                )
            except Exception as e:
                logger.debug("Discord 알림 실패: %s", e)
        logger.info(
            "청산 감지: side=%s size=%s close=%s extra=%s",
            prev["side"],
            prev["size"],
            close_price,
            extra,
        )
        self._pending_tp_sl = None

    def _poll_live_close(self) -> None:
        pos = self.client.get_position()
        with self._position_poll_lock:
            prev = self._last_live_pos
            if prev and not pos:
                self._notify_live_flat(prev)
                self._last_live_pos = None
                return
            self._last_live_pos = self._pos_snapshot(pos) if pos else None

    def _account_row(self):
        acc = self.client.get_account()
        acc_list = acc if isinstance(acc, list) else [acc] if acc else []
        return acc_list[0] if acc_list else None

    def _fetch_sizing_usdt(self) -> float:
        try:
            a0 = self._account_row()
            equity, available = _account_snapshot(a0)
            sizing = _account_sizing_usdt(a0)
            logger.info(
                "사이징 잔고 | total=%.4f available=%s sizing=%.4f",
                equity,
                f"{available:.4f}" if available is not None else "None",
                sizing,
            )
            return sizing
        except Exception as e:
            logger.warning("계정 조회 실패: %s", e)
            return 0.0

    def _flatten_for_reverse(self, side_pos: str, size_abs: str, dry: bool) -> None:
        """반대 신호 청산. 포지션 0 확인 없이 0.5초 대기 후 재진입한다."""
        if dry:
            return
        try:
            self.client.cancel_all_price_orders()
        except Exception as e:
            logger.warning("반대 신호: 트리거 취소 실패(무시하고 청산 진행): %s", e)
        close_side = "short" if side_pos == "long" else "long"
        self.client.place_market_order(close_side, size_abs, reduce_only=True)
        time.sleep(0.5)
        with self._position_poll_lock:
            self._last_live_pos = None
            self._pending_tp_sl = None

    def _check_and_execute_entry(self, candles: list, completed_idx: int, use_relaxed: bool = False):
        """진입 조건 체크 및 실행. 수량은 계좌 total 기준. 반대 신호면 청산 후 그 수량으로 진입한다."""
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        enable_opposite = getattr(self._cfg, "enable_close_on_opposite", True) if self._cfg else True
        pos = self.client.get_position()
        row = candles[completed_idx]
        buy_signal = bool(row.get("buy_signal"))
        sell_signal = bool(row.get("sell_signal"))

        equity = self._fetch_sizing_usdt()
        if equity <= 0:
            return
        if use_relaxed:
            long_ok, short_ok, long_info, short_info = check_entry_relaxed(candles, completed_idx, equity)
        else:
            long_ok, short_ok, long_info, short_info = check_entry(candles, completed_idx, equity)
        logger.info(
            "진입 계산 결과 | equity=%.4f | long_ok=%s long_size=%s | short_ok=%s short_size=%s",
            float(equity),
            long_ok,
            getattr(long_info, "size", None) if long_info else None,
            short_ok,
            getattr(short_info, "size", None) if short_info else None,
        )
        last_ts = row.get("t")
        c = row.get("c")
        st_trend = row.get("supertrend_trend")
        st_up = row.get("supertrend_up")
        st_dn = row.get("supertrend_dn")
        bb_upper = row.get("bb_upper")
        bb_lower = row.get("bb_lower")
        bb_basis = row.get("bb_basis")
        logger.info(
            "진입 조건 체크(봉 %s) | close=%s | SuperTrend=%s up=%s dn=%s | BB upper=%s lower=%s basis=%s | "
            "long_ok=%s short_ok=%s sell_signal=%s buy_signal=%s",
            _ts_to_ymdhm(last_ts),
            f"{float(c):.4f}" if c is not None else "None",
            st_trend,
            f"{float(st_up):.4f}" if st_up is not None else "None",
            f"{float(st_dn):.4f}" if st_dn is not None else "None",
            f"{float(bb_upper):.4f}" if bb_upper is not None else "None",
            f"{float(bb_lower):.4f}" if bb_lower is not None else "None",
            f"{float(bb_basis):.4f}" if bb_basis is not None else "None",
            long_ok,
            short_ok,
            sell_signal,
            buy_signal,
        )

        if pos and enable_opposite:
            side_pos = pos["side"]
            if (side_pos == "long" and sell_signal) or (side_pos == "short" and buy_signal):
                flatten_size = str(int(abs(pos["size"])))
                logger.info(
                    "반대 신호 체크 | pos_side=%s pos_size=%s sell_signal=%s buy_signal=%s long_ok=%s short_ok=%s",
                    side_pos,
                    pos["size"],
                    sell_signal,
                    buy_signal,
                    long_ok,
                    short_ok,
                )
                self._flatten_for_reverse(side_pos, flatten_size, dry)
                approx_price = float(row["c"]) if row.get("c") is not None else None
                if notify_discord:
                    try:
                        notify_discord(
                            "close",
                            side=side_pos,
                            close_price=approx_price,
                            size=_format_size_for_notify(flatten_size),
                            extra="(반대 신호)",
                        )
                    except Exception as e:
                        logger.debug("Discord 알림 실패: %s", e)
                if side_pos == "long":
                    if short_ok and short_info:
                        if not dry:
                            self._execute_entry("short", short_info.size, short_info.sl_price, short_info.tp_price)
                        logger.info("반대 신호: 롱 청산 후 숏 진입 size=%s", short_info.size)
                        if notify_discord:
                            try:
                                notify_discord(
                                    "entry",
                                    side="short",
                                    entry_price=approx_price,
                                    sl_price=short_info.sl_price,
                                    tp_price=short_info.tp_price,
                                    size=_format_size_for_notify(short_info.size),
                                )
                            except Exception as e:
                                logger.debug("Discord 알림 실패: %s", e)
                    else:
                        logger.info("반대 신호: 롱 청산만 수행 (새 숏 진입 조건 불충족)")
                    return
                if long_ok and long_info:
                    if not dry:
                        self._execute_entry("long", long_info.size, long_info.sl_price, long_info.tp_price)
                    logger.info("반대 신호: 숏 청산 후 롱 진입 size=%s", long_info.size)
                    if notify_discord:
                        try:
                            notify_discord(
                                "entry",
                                side="long",
                                entry_price=approx_price,
                                sl_price=long_info.sl_price,
                                tp_price=long_info.tp_price,
                                size=_format_size_for_notify(long_info.size),
                            )
                        except Exception as e:
                            logger.debug("Discord 알림 실패: %s", e)
                else:
                    logger.info("반대 신호: 숏 청산만 수행 (새 롱 진입 조건 불충족)")
                return

        if pos:
            return
        if not long_ok and not short_ok:
            return
        if long_ok and long_info and int(long_info.size) >= ORDER_SIZE_MIN:
            self._execute_entry("long", long_info.size, long_info.sl_price, long_info.tp_price)
            approx = float(row["c"]) if row.get("c") is not None else None
            if notify_discord:
                try:
                    notify_discord(
                        "entry",
                        side="long",
                        entry_price=approx,
                        sl_price=long_info.sl_price,
                        tp_price=long_info.tp_price,
                        size=_format_size_for_notify(long_info.size),
                    )
                except Exception as e:
                    logger.debug("Discord 알림 실패: %s", e)
        elif short_ok and short_info and int(short_info.size) >= ORDER_SIZE_MIN:
            self._execute_entry("short", short_info.size, short_info.sl_price, short_info.tp_price)
            approx = float(row["c"]) if row.get("c") is not None else None
            if notify_discord:
                try:
                    notify_discord(
                        "entry",
                        side="short",
                        entry_price=approx,
                        sl_price=short_info.sl_price,
                        tp_price=short_info.tp_price,
                        size=_format_size_for_notify(short_info.size),
                    )
                except Exception as e:
                    logger.debug("Discord 알림 실패: %s", e)

    def run_loop(self, interval_sec: float = 60.0):
        """메인 루프: 진입 체크는 별도 스레드에서 30분 봉 완성 시에만."""
        logger.info("엔진 주기 시작: interval_sec=%.1f contract=%s", interval_sec, CONTRACT)
        self._stop_entry_check_thread = False
        self._entry_check_thread = threading.Thread(target=self._entry_check_loop, daemon=True)
        self._entry_check_thread.start()
        try:
            live = self.client.get_position()
            if live:
                with self._position_poll_lock:
                    self._last_live_pos = self._pos_snapshot(live)
                logger.info("시작 시 기존 포지션 추적: %s size=%s", live["side"], live["size"])
        except Exception as e:
            logger.warning("시작 시 포지션 동기화 실패: %s", e)
        while True:
            try:
                pos = self.client.get_position()
                if pos:
                    logger.debug("포지션 보유: %s size=%s entry=%.2f", pos["side"], pos["size"], pos["entry_price"])
                    self._recover_tp_sl_if_needed()
            except Exception as e:
                logger.exception("run_once 오류: %s", e)
            time.sleep(interval_sec)


def main():
    cfg = config.load_config()
    if not cfg.gate_api_key or not cfg.gate_api_secret:
        logger.error("GATE_API_KEY, GATE_API_SECRET 환경 변수 설정 필요")
        return
    engine = TradingEngine()
    engine.client.set_exchange_leverage(getattr(cfg, "max_leverage", None))
    try:
        acc = engine.client.get_account()
        a0 = acc[0] if isinstance(acc, (list, tuple)) and acc else (acc if acc is not None else None)
        total = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0) if a0 else 0.0
        logger.info("계좌 금액: %.2f USDT", total)
    except Exception as e:
        logger.warning("계좌 조회 실패(시작 시): %s", e)
    try:
        engine._recover_tp_sl_if_needed()
    except Exception as e:
        logger.warning("시작 시 TP/SL 복구 시도 실패: %s", e)
    engine.run_loop(interval_sec=cfg.loop_interval_sec)


if __name__ == "__main__":
    main()
