"""
BTC_USDT 선물 SR + IBS + SuperTrend 자동매매 엔진.
실행: python main.py
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from gate_client import GateIOClient
from indicators import add_indicators
from notify import notify_discord
from price_stream import PriceStream
from state_store import load_state, save_state
from strategy import check_be_add, check_entry, get_last_completed_bar_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
BAR_SECONDS = config.CHART_BAR_MINUTES * 60
BAR_CLOSE_POSITION_POLLS = 3
BAR_CLOSE_POSITION_POLL_SEC = 1.0


def _ts_to_ymdhm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=KST).strftime("%Y-%m-%d %H:%M")


def _is_auto_user_no_position_error(err: Exception) -> bool:
    text = str(err)
    return (
        "AUTO_USER_NO_LONG_POSITION" in text
        or "AUTO_USER_NO_SHORT_POSITION" in text
        or '"code":"1037"' in text
        or '"code":"1038"' in text
    )


class TradingEngine:
    def __init__(self, client: Optional[GateIOClient] = None):
        self._cfg = config.load_config()
        price_stream = None
        if self._cfg.use_websocket_price:
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
        self._tracked_pos: Optional[dict] = None
        self._tracked_pos_lock = threading.Lock()
        self._entry_bar_ts: Optional[int] = None

    def _persist_state(self) -> None:
        tracked = self._get_tracked_pos()
        if not tracked:
            save_state(config.SR_STATE_PATH, {})
            return
        payload = dict(tracked)
        payload["entry_bar_ts"] = self._entry_bar_ts
        try:
            save_state(config.SR_STATE_PATH, payload)
        except Exception as e:
            logger.warning("SR state save failed: %s", e)

    def _restore_state(self) -> None:
        raw = load_state(config.SR_STATE_PATH)
        side = raw.get("side")
        if side not in ("long", "short"):
            return
        bar_ts = raw.get("entry_bar_ts")
        try:
            self._entry_bar_ts = int(bar_ts) if bar_ts is not None else 0
        except (TypeError, ValueError):
            self._entry_bar_ts = 0
        self._set_tracked_pos(
            side,
            raw.get("size") or "0",
            raw.get("entry_price"),
            orig_tp=raw.get("orig_tp"),
            orig_sl=raw.get("orig_sl"),
            add_risk_used=raw.get("add_risk_used"),
            be_mode=raw.get("be_mode"),
            persist=False,
        )

    def _set_tracked_pos(
        self,
        side: str,
        size,
        entry_price: Optional[float] = None,
        *,
        orig_tp: Optional[float] = None,
        orig_sl: Optional[float] = None,
        add_risk_used=None,
        be_mode=None,
        persist: bool = True,
    ) -> None:
        with self._tracked_pos_lock:
            prev = dict(self._tracked_pos) if self._tracked_pos else {}
            self._tracked_pos = {
                "side": side,
                "size": str(size),
                "entry_price": prev.get("entry_price") if entry_price is None else entry_price,
                "orig_tp": prev.get("orig_tp") if orig_tp is None else orig_tp,
                "orig_sl": prev.get("orig_sl") if orig_sl is None else orig_sl,
                "add_risk_used": float(prev.get("add_risk_used") or 0.0)
                if add_risk_used is None
                else float(add_risk_used),
                "be_mode": bool(prev.get("be_mode")) if be_mode is None else bool(be_mode),
            }
        if persist:
            self._persist_state()

    def _get_tracked_pos(self) -> Optional[dict]:
        with self._tracked_pos_lock:
            return dict(self._tracked_pos) if self._tracked_pos else None

    def _clear_tracked_pos(self) -> Optional[dict]:
        with self._tracked_pos_lock:
            prev = self._tracked_pos
            self._tracked_pos = None
            self._entry_bar_ts = None
        self._persist_state()
        return prev

    def _safe_last_price(self) -> Optional[float]:
        try:
            return self.client.get_last_price()
        except Exception:
            return None

    def _current_bar_start_ts(self) -> int:
        return int(time.time() // BAR_SECONDS) * BAR_SECONDS

    def _get_candles_cached(self, limit: Optional[int] = None) -> list:
        limit = limit or config.CANDLE_LIMIT
        raw = self.client.get_candles(limit=limit, interval=config.CANDLE_INTERVAL)
        now = time.time()
        raw = [c for c in raw if c.get("t") is not None and c["t"] + BAR_SECONDS <= now]
        return add_indicators(
            raw,
            st_atr_period=self._cfg.st_atr_period,
            st_multiplier=self._cfg.st_multiplier,
            st_use_ta_atr=self._cfg.st_use_ta_atr,
            atr_stop_period=self._cfg.atr_stop_period,
            sr_left_bars=self._cfg.sr_left_bars,
            sr_right_bars=self._cfg.sr_right_bars,
            sr_bar_minutes=self._cfg.sr_bar_minutes,
        )

    def _setup_tp_sl(self, side: str, size: str, sl_price: float, tp_price: float):
        if self._cfg.dry_run:
            logger.info("DRY RUN: TP/SL 등록 생략 side=%s sl=%.2f tp=%.2f", side, sl_price, tp_price)
            return
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                try:
                    self.client.cancel_all_price_orders()
                except Exception as e:
                    logger.warning("기존 가격 트리거 주문 취소 오류(무시): %s", e)
                self.client.create_stop_loss_order(str(sl_price), size, side)
                self.client.create_take_profit_order(str(tp_price), size, side, is_partial=False)
                logger.info("TP/SL 등록 완료: side=%s sl=%.2f tp=%.2f size=%s", side, sl_price, tp_price, size)
                return
            except Exception as e:
                if _is_auto_user_no_position_error(e) and attempt < max_attempts:
                    time.sleep(0.8 * attempt)
                    continue
                raise

    def _get_position_for_side(self, side: str, min_size: int = 1) -> Optional[dict]:
        try:
            return self.client.get_position_for_side(side, min_size=min_size)
        except Exception:
            return None

    def _exchange_has_position(self, positions: dict, side: str, min_size: int = 1) -> bool:
        pos = positions.get(side)
        if pos and abs(float(pos.get("size", 0))) >= float(min_size):
            return True
        return self._get_position_for_side(side, min_size=min_size) is not None

    def _fetch_positions_at_bar_close(self) -> dict:
        return self.client.get_contract_positions()

    def _confirm_exchange_flat(self, side: str, min_size: int = 1) -> bool:
        for attempt in range(BAR_CLOSE_POSITION_POLLS):
            if self._get_position_for_side(side, min_size=min_size):
                return False
            if attempt < BAR_CLOSE_POSITION_POLLS - 1:
                time.sleep(BAR_CLOSE_POSITION_POLL_SEC)
        return True

    def _notify_tracked_flat(self, tracked: dict, extra: str, close_price: Optional[float] = None) -> None:
        """거래소에서 포지션이 사라진 직후 Discord/카카오 청산 알림."""
        if close_price is None:
            close_price = self._safe_last_price()
        if not self._cfg.dry_run:
            try:
                self.client.cancel_all_price_orders()
            except Exception as exc:
                logger.warning("청산 후 잔여 TP/SL 취소 실패: %s", exc)
        self._clear_tracked_pos()
        notify_discord(
            "close",
            side=tracked["side"],
            close_price=close_price,
            size=tracked["size"],
            contract=config.CONTRACT,
            extra=extra,
        )
        logger.info(
            "청산 감지: %s size=%s close=%s extra=%s",
            tracked["side"],
            tracked["size"],
            f"{close_price:.2f}" if close_price is not None else "-",
            extra,
        )

    def _poll_live_close(self) -> None:
        """1시간봉을 기다리지 않고, 추적 포지션이 사라지면 바로 알림."""
        if self._cfg.dry_run:
            return
        tracked = self._get_tracked_pos()
        if not tracked:
            return
        side = tracked["side"]
        min_size = max(1, int(float(tracked["size"]) * 0.5))
        if self._get_position_for_side(side, min_size=min_size):
            return
        time.sleep(0.3)
        if self._get_position_for_side(side, min_size=min_size):
            return
        self._notify_tracked_flat(tracked, extra="TP/SL·거래소 청산")

    def _adopt_open_position(self, positions: Optional[dict] = None) -> None:
        if self._get_tracked_pos():
            return
        positions = positions if positions is not None else self._fetch_positions_at_bar_close()
        open_pos = positions.get("long") or positions.get("short")
        if not open_pos:
            return
        self._restore_state()
        tracked = self._get_tracked_pos()
        side = open_pos["side"]
        size = int(abs(float(open_pos["size"])))
        if tracked and tracked.get("side") == side:
            self._set_tracked_pos(side, size, persist=True)
            logger.info(
                "상태 복원: side=%s size=%s orig_entry=%s be_mode=%s add_risk_used=%.4f",
                side,
                size,
                tracked.get("entry_price"),
                tracked.get("be_mode"),
                float(tracked.get("add_risk_used") or 0),
            )
            return
        if tracked and tracked.get("side") != side:
            logger.warning("저장 상태 side=%s 거래소=%s — 상태 폐기", tracked.get("side"), side)
            self._clear_tracked_pos()
        self._set_tracked_pos(side, size, open_pos.get("entry_price"), persist=True)
        if self._entry_bar_ts is None:
            self._entry_bar_ts = 0
        logger.warning("첫 진입가/TP 없음 — 본전 추가 생략, 거래소 평균가 추적만")

    def _sync_position_on_bar_close(self, row: dict) -> dict:
        positions = self._fetch_positions_at_bar_close()
        tracked = self._get_tracked_pos()

        if tracked:
            side = tracked["side"]
            min_size = max(1, int(float(tracked["size"]) * 0.5))
            if not self._exchange_has_position(positions, side, min_size=min_size):
                if self._confirm_exchange_flat(side, min_size=min_size):
                    close_price = self._safe_last_price() or float(row["c"])
                    self._notify_tracked_flat(tracked, extra="TP/SL 도달 추정", close_price=close_price)
                    positions = self._fetch_positions_at_bar_close()
                else:
                    logger.warning(
                        "봉마감 [%s] 포지션 API 일시 미조회 — 보유 중으로 간주, 진입 검사 생략",
                        side,
                    )
                    pos = self._get_position_for_side(side, min_size=min_size)
                    if pos:
                        positions[pos["side"]] = pos
        else:
            self._adopt_open_position(positions)

        return positions

    def _wait_for_position(
        self,
        expected_side: str,
        min_size: int = 1,
        timeout_sec: float = 5.0,
        poll_interval_sec: float = 0.25,
    ) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._get_position_for_side(expected_side, min_size=min_size):
                return True
            time.sleep(poll_interval_sec)
        return False

    def _get_equity(self) -> float:
        try:
            acc = self.client.get_account()
            acc_list = acc if isinstance(acc, list) else [acc] if acc else []
            a0 = acc_list[0] if acc_list else None
            local = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0) if a0 else 0.0
            raw = getattr(a0, "available", None) if a0 else None
            try:
                available = float(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                available = None
            if available is not None and available > 0:
                return max(0.0, min(local, available) if local > 0 else available)
            return local
        except Exception as e:
            logger.warning("계정 조회 실패: %s", e)
            return 0.0

    def _format_position_status(self, positions: dict) -> str:
        parts = []
        for side, label in (("long", "롱"), ("short", "숏")):
            pos = positions.get(side)
            if not pos:
                continue
            size = int(abs(float(pos.get("size", 0))))
            entry = pos.get("entry_price")
            if entry is not None:
                parts.append(f"{label} size={size} entry={float(entry):.2f}")
            else:
                parts.append(f"{label} size={size}")
        if not parts:
            return "보유=없음"
        return "보유=" + ", ".join(parts)

    def _log_position_status(self, positions: dict, *, context: str = "") -> None:
        msg = self._format_position_status(positions)
        tracked = self._get_tracked_pos()
        if tracked:
            msg += f" | 내부추적={tracked['side']} size={tracked['size']}"
        if context:
            logger.info("포지션 [%s] %s", context, msg)
        else:
            logger.info("포지션 %s", msg)

    def _log_bar_row(self, row: dict, *, pos_label: str, long_ok=None, short_ok=None) -> None:
        def _ind(v: Optional[float]) -> str:
            return f"{float(v):.2f}" if v is not None else "-"

        ibs = row.get("ibs")
        ibs_s = f"{float(ibs):.3f}" if ibs is not None else "-"
        if long_ok is not None:
            logger.info(
                "봉 %s close=%.2f trend=%s pos=%s res=%s sup=%s ibs=%s atr=%s "
                "long_ok=%s short_ok=%s buy=%s sell=%s",
                _ts_to_ymdhm(row["t"]),
                float(row["c"]),
                row.get("supertrend_trend"),
                pos_label,
                _ind(row.get("resistance")),
                _ind(row.get("support")),
                ibs_s,
                _ind(row.get("atr_stop")),
                long_ok,
                short_ok,
                bool(row.get("buy_signal")),
                bool(row.get("sell_signal")),
            )
        else:
            logger.info(
                "봉 %s close=%.2f trend=%s pos=%s res=%s sup=%s ibs=%s — 진입 검사 생략 buy=%s sell=%s",
                _ts_to_ymdhm(row["t"]),
                float(row["c"]),
                row.get("supertrend_trend"),
                pos_label,
                _ind(row.get("resistance")),
                _ind(row.get("support")),
                ibs_s,
                bool(row.get("buy_signal")),
                bool(row.get("sell_signal")),
            )

    def _after_entry(self, row: dict) -> bool:
        return self._entry_bar_ts is not None and int(row["t"]) > int(self._entry_bar_ts)

    def _maybe_apply_be_add(self, row: dict, held: dict) -> None:
        if not self._cfg.enable_be_add:
            return
        if not self._after_entry(row):
            return
        tracked = self._get_tracked_pos()
        orig_entry = None if not tracked else tracked.get("entry_price")
        orig_tp = None if not tracked else tracked.get("orig_tp")
        if orig_entry is None or orig_tp is None:
            logger.info("본전 추가 생략: 첫 진입가 또는 TP 없음")
            return
        side = held["side"]
        current_size = int(abs(float(held.get("size") or 0)))
        if current_size < config.ORDER_SIZE_MIN:
            return
        equity = self._get_equity()
        if equity <= 0:
            return
        plan = check_be_add(
            row,
            side=side,
            orig_entry=float(orig_entry),
            orig_tp=float(orig_tp),
            current_contracts=current_size,
            equity=equity,
            add_risk_used=float((tracked or {}).get("add_risk_used") or 0.0),
        )
        if plan is None:
            return
        already_be = bool((tracked or {}).get("be_mode"))
        if already_be and plan.add_contracts <= 0:
            return

        new_size = current_size
        filled_add = 0
        risk_delta = 0.0
        if plan.add_contracts >= config.ORDER_SIZE_MIN:
            if self._cfg.dry_run:
                filled_add = plan.add_contracts
                new_size = current_size + filled_add
                risk_delta = plan.risk_used_delta
                logger.info(
                    "DRY RUN 본전 추가: side=%s add=%s new_size=%s sl=%.2f tp=%.2f",
                    side, filled_add, new_size, plan.sl_price, plan.tp_price,
                )
            else:
                try:
                    self.client.place_market_order(side, str(plan.add_contracts), reduce_only=False)
                except Exception as e:
                    logger.exception("본전 추가 주문 실패: %s", e)
                    filled_add = 0
                else:
                    self._wait_for_position(side, min_size=current_size + plan.add_contracts)
                    pos = self._get_position_for_side(side, min_size=current_size)
                    if pos:
                        new_size = int(abs(float(pos["size"])))
                        filled_add = max(0, new_size - current_size)
                    if filled_add > 0 and plan.add_contracts > 0:
                        risk_delta = plan.risk_used_delta * (filled_add / plan.add_contracts)
                    logger.info(
                        "본전 추가 체결: side=%s planned=%s filled=%s new_size=%s",
                        side, plan.add_contracts, filled_add, new_size,
                    )

        if filled_add <= 0 and already_be:
            return

        used = float((tracked or {}).get("add_risk_used") or 0.0) + risk_delta
        extra = f"거리 {plan.dist_pct:.3f}% | 추가리스크 누적 {used:.2f} USDT"
        if filled_add > 0:
            notify_discord(
                "be_add",
                side=side,
                entry_price=float(orig_entry),
                sl_price=plan.sl_price,
                tp_price=plan.tp_price,
                size=str(filled_add),
                contract=config.CONTRACT,
                extra=extra,
            )

        try:
            self._setup_tp_sl(side, str(new_size), plan.sl_price, plan.tp_price)
        except Exception as e:
            logger.exception("본전 SL/TP 재등록 실패: %s", e)
            self._set_tracked_pos(
                side,
                new_size,
                float(orig_entry),
                orig_tp=float(orig_tp),
                orig_sl=plan.sl_price,
                add_risk_used=used,
                be_mode=True,
            )
            return

        self._set_tracked_pos(
            side,
            new_size,
            float(orig_entry),
            orig_tp=float(orig_tp),
            orig_sl=plan.sl_price,
            add_risk_used=used,
            be_mode=True,
        )
        if filled_add <= 0:
            notify_discord(
                "be_stop",
                side=side,
                sl_price=plan.sl_price,
                tp_price=plan.tp_price,
                size=str(new_size),
                contract=config.CONTRACT,
                extra=extra,
            )
        logger.info(
            "본전 모드: side=%s sl=%.2f tp=%.2f add=%s size=%s %s",
            side, plan.sl_price, plan.tp_price, filled_add, new_size, extra,
        )

    def _entry_check_loop(self):
        logger.info("진입 조건 체크 스레드 시작 (2초 주기, 1시간봉 마감)")
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
                            candles = self._get_candles_cached()
                            completed_row, completed_idx = get_last_completed_bar_row(candles, BAR_SECONDS, now)
                            if completed_row is not None:
                                last_ts = completed_row.get("t")
                                if (not config.ENTRY_CHECK_ONLY_ON_BAR_CLOSE) or last_ts != self._last_bar_ts_checked:
                                    self._last_bar_ts_checked = last_ts
                                    self._check_and_execute_entry(candles, completed_idx)
                            self._last_processed_bar_end_ts = current_bar_end
                    finally:
                        self._entry_check_lock.release()
            except Exception as e:
                logger.exception("진입 체크 오류: %s", e)
            try:
                self._poll_live_close()
            except Exception as e:
                logger.debug("청산 폴링 오류: %s", e)
            time.sleep(2.0)

    def _check_and_execute_entry(self, candles: list, completed_idx: int):
        row = candles[completed_idx]
        positions = self._sync_position_on_bar_close(row)
        self._log_position_status(positions, context="봉마감")
        long_pos = positions.get("long")
        short_pos = positions.get("short")

        if long_pos and short_pos:
            self._log_bar_row(row, pos_label="long+short")
            logger.warning("롱·숏 동시 보유 — 주문 없음")
            return

        held = long_pos or short_pos
        if held:
            side_pos = held["side"]
            size_abs = str(int(abs(float(held["size"]))))
            self._log_bar_row(row, pos_label=side_pos)

            sell_signal = bool(row.get("sell_signal"))
            buy_signal = bool(row.get("buy_signal"))
            opposite = (
                self._cfg.enable_close_on_opposite
                and self._after_entry(row)
                and ((side_pos == "long" and sell_signal) or (side_pos == "short" and buy_signal))
            )
            if not opposite:
                self._maybe_apply_be_add(row, held)
                return

            close_price = self._safe_last_price() or float(row["c"])
            if not self._cfg.dry_run:
                try:
                    self.client.cancel_all_price_orders()
                except Exception as exc:
                    logger.warning("청산 전 트리거 취소 실패(무시하고 청산 진행): %s", exc)
                self.client.close_position(side_pos)
                self._clear_tracked_pos()
                time.sleep(0.5)
                positions = self._fetch_positions_at_bar_close()
                if self._exchange_has_position(positions, side_pos):
                    logger.warning("SuperTrend 청산 후에도 포지션 잔존")
                    return

            notify_discord(
                "supertrend_close",
                side=side_pos,
                close_price=close_price,
                size=size_abs,
                contract=config.CONTRACT,
                extra="반대 신호(매도)" if side_pos == "long" else "반대 신호(매수)",
            )
            logger.info("반대 신호: %s SuperTrend 전량 청산 (재진입 없음)", side_pos)
            return

        logger.info("무포지션 확인 — 진입 조건 검사 (%s)", _ts_to_ymdhm(row["t"]))
        equity = self._get_equity()
        if equity <= 0:
            return

        long_ok, short_ok, long_info, short_info = check_entry(candles, completed_idx, equity)
        self._log_bar_row(row, pos_label="flat", long_ok=long_ok, short_ok=short_ok)

        if long_ok and long_info and int(long_info.size) >= config.ORDER_SIZE_MIN:
            if not self._cfg.dry_run:
                self.client.place_market_order("long", long_info.size, reduce_only=False)
                if not self._wait_for_position("long", min_size=int(long_info.size)):
                    logger.error("롱 진입 후 포지션 미확인 — TP/SL 등록 생략")
                    return
                self._setup_tp_sl("long", long_info.size, long_info.sl_price, long_info.tp_price)
                self._entry_bar_ts = int(row["t"])
                self._set_tracked_pos(
                    "long",
                    long_info.size,
                    float(row["c"]),
                    orig_tp=long_info.tp_price,
                    orig_sl=long_info.sl_price,
                    add_risk_used=0.0,
                    be_mode=False,
                )
            else:
                self._entry_bar_ts = int(row["t"])
            notify_discord(
                "entry", side="long", entry_price=float(row["c"]),
                sl_price=long_info.sl_price, tp_price=long_info.tp_price,
                size=long_info.size, contract=config.CONTRACT,
            )
            logger.info("롱 진입: size=%s sl=%.2f tp=%.2f", long_info.size, long_info.sl_price, long_info.tp_price)
            return
        if short_ok and short_info and int(short_info.size) >= config.ORDER_SIZE_MIN:
            if not self._cfg.dry_run:
                self.client.place_market_order("short", short_info.size, reduce_only=False)
                if not self._wait_for_position("short", min_size=int(short_info.size)):
                    logger.error("숏 진입 후 포지션 미확인 — TP/SL 등록 생략")
                    return
                self._setup_tp_sl("short", short_info.size, short_info.sl_price, short_info.tp_price)
                self._entry_bar_ts = int(row["t"])
                self._set_tracked_pos(
                    "short",
                    short_info.size,
                    float(row["c"]),
                    orig_tp=short_info.tp_price,
                    orig_sl=short_info.sl_price,
                    add_risk_used=0.0,
                    be_mode=False,
                )
            else:
                self._entry_bar_ts = int(row["t"])
            notify_discord(
                "entry", side="short", entry_price=float(row["c"]),
                sl_price=short_info.sl_price, tp_price=short_info.tp_price,
                size=short_info.size, contract=config.CONTRACT,
            )
            logger.info("숏 진입: size=%s sl=%.2f tp=%.2f", short_info.size, short_info.sl_price, short_info.tp_price)

    def run_loop(self, interval_sec: float = 60.0):
        logger.info(
            "엔진 시작: contract=%s interval=%ss tf=%s sr_tf=%s",
            config.CONTRACT,
            interval_sec,
            config.CANDLE_INTERVAL,
            config.SR_TIMEFRAME,
        )
        notify_discord(
            "start", contract=config.CONTRACT,
            extra=f"interval={interval_sec}s, dry_run={self._cfg.dry_run}, tf=1h sr=4h",
        )
        try:
            self._adopt_open_position()
            if self._get_tracked_pos():
                logger.info("시작 시 기존 포지션 추적 시작: %s", self._get_tracked_pos())
        except Exception as e:
            logger.warning("시작 시 포지션 동기화 실패: %s", e)
        t = threading.Thread(target=self._entry_check_loop, daemon=True)
        t.start()
        while True:
            try:
                positions = self.client.get_contract_positions()
                self._log_position_status(positions, context="주기")
                if not self._get_tracked_pos():
                    self._adopt_open_position(positions)
            except Exception as e:
                logger.exception("run_loop 오류: %s", e)
            time.sleep(interval_sec)


def main():
    cfg = config.load_config()
    if not cfg.gate_api_key or not cfg.gate_api_secret:
        logger.error("GATE_API_KEY, GATE_API_SECRET 환경 변수 설정 필요")
        return
    engine = TradingEngine()
    engine.client.set_exchange_leverage(getattr(cfg, "max_leverage", None))
    logger.info("독립 프로젝트 실행: gateio_btc_sr_ibs_supertrend")
    engine.run_loop(interval_sec=cfg.loop_interval_sec)


if __name__ == "__main__":
    main()
