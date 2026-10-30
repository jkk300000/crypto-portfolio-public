"""
EMA200 + RSI Trail — Gate.io BTC 자동매매 엔진.
인프라는 gateio_btc_smi_ibs_supertrend를 재사용하고, 전략만 ema200 +rsi trail.pine에 맞춘다.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
import requests
from config import CHART_BAR_MINUTES, CANDLE_LIMIT, CONTRACT, ENTRY_CHECK_ONLY_ON_BAR_CLOSE, ORDER_SIZE_MIN
from gate_client import GateIOClient, PositionLookupError
from indicators import add_indicators
from price_stream import PriceStream
from strategy import (
    check_entry,
    fallback_tp_sl,
    get_last_completed_bar_row,
    next_trail_stop,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
BAR_SECONDS = CHART_BAR_MINUTES * 60


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


def _format_size_for_notify(size: str | None) -> str | None:
    if size is None:
        return None
    try:
        contracts = float(size)
        btc_qty = contracts * float(getattr(config, "CONTRACT_MULTIPLIER", 1.0))
        return f"{btc_qty:.6f} BTC"
    except Exception:
        return size


def _fmt_price(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):,.4f}"
    except Exception:
        return str(v)


def notify_discord(event: str, **kwargs) -> None:
    webhook = (getattr(config, "DISCORD_WEBHOOK_URL", "") or "").strip()
    side = kwargs.get("side", "-")
    side_kr = "롱" if side == "long" else "숏" if side == "short" else str(side)

    if event == "entry":
        extra = kwargs.get("extra") or ""
        content = "\n".join(
            [
                f"📥 EMA 진입 ({side_kr}) {extra}".strip(),
                f"- 진입가: {_fmt_price(kwargs.get('entry_price'))}",
                f"- 손절가(SL): {_fmt_price(kwargs.get('sl_price'))}",
                f"- 익절가(TP): {_fmt_price(kwargs.get('tp_price'))}",
                f"- 수량: {kwargs.get('size') or '-'}",
            ]
        )
    elif event == "close":
        extra = kwargs.get("extra") or ""
        content = "\n".join(
            [
                f"📤 EMA 청산 ({side_kr}) {extra}".strip(),
                f"- 청산가: {_fmt_price(kwargs.get('close_price'))}",
                f"- 수량: {kwargs.get('size') or '-'}",
            ]
        )
    else:
        content = f"[INFO] {kwargs}"
    if webhook:
        try:
            resp = requests.post(webhook, json={"content": content}, timeout=8)
            if resp.status_code >= 400:
                logger.warning("Discord 알림 HTTP %s: %s", resp.status_code, (resp.text or "")[:200])
        except Exception as exc:
            logger.warning("Discord 알림 실패: %s", exc)
    else:
        logger.warning("DISCORD_WEBHOOK_URL 없음 — Discord 생략")
    if event in ("entry", "close"):
        try:
            from portfolio_events import emit, _path

            emit("ema", event, content)
            logger.info("카카오 이벤트 기록: %s → %s", event, _path())
        except Exception as exc:
            logger.warning("카카오 이벤트 기록 실패: %s", exc)


class RuntimeState:
    def __init__(self, path: str):
        self.path = path
        self.entry_bar_ts: Optional[int] = None
        self.side: Optional[str] = None
        self.entry_price: Optional[float] = None
        self.sl_price: Optional[float] = None
        self.tp_price: Optional[float] = None
        self.size: Optional[str] = None
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.entry_bar_ts = data.get("entry_bar_ts")
            self.side = data.get("side")
            self.entry_price = data.get("entry_price")
            self.sl_price = data.get("sl_price")
            self.tp_price = data.get("tp_price")
            self.size = data.get("size")
        except Exception as exc:
            logger.warning("상태 파일 로드 실패(%s): %s", self.path, exc)

    def save_entry(
        self,
        side: str,
        entry_bar_ts: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        size: str,
    ) -> None:
        self.side = side
        self.entry_bar_ts = int(entry_bar_ts)
        self.entry_price = float(entry_price)
        self.sl_price = float(sl_price)
        self.tp_price = float(tp_price)
        self.size = str(size)
        self._dump()

    def update_stop(self, sl_price: float) -> None:
        self.sl_price = float(sl_price)
        self._dump()

    def clear(self) -> None:
        self.entry_bar_ts = None
        self.side = None
        self.entry_price = None
        self.sl_price = None
        self.tp_price = None
        self.size = None
        if os.path.isfile(self.path):
            try:
                os.remove(self.path)
                return
            except OSError:
                pass
        self._dump()

    def _dump(self) -> None:
        payload = {
            "side": self.side,
            "entry_bar_ts": self.entry_bar_ts,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "size": self.size,
        }
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as exc:
            logger.warning("상태 파일 저장 실패(%s): %s", self.path, exc)

    def adopt_live(self, pos: dict) -> None:
        side = pos["side"]
        size = str(int(abs(float(pos["size"]))))
        same_side = self.side == side
        self.side = side
        self.size = size
        live_entry = pos.get("entry_price")
        if live_entry not in (None, "", 0, 0.0):
            try:
                self.entry_price = float(live_entry)
            except (TypeError, ValueError):
                pass
        if not same_side:
            self.entry_bar_ts = None
            self.sl_price = None
            self.tp_price = None
        self._dump()

    def after_entry(self, completed_bar_ts: Optional[int], position_side: Optional[str]) -> bool:
        if completed_bar_ts is None:
            return False
        if self.entry_bar_ts is None or self.side != position_side:
            return True
        return int(completed_bar_ts) > int(self.entry_bar_ts)


class TradingEngine:
    def __init__(self, client: Optional[GateIOClient] = None):
        self._cfg = config.load_config() if hasattr(config, "load_config") else None
        price_stream = None
        if self._cfg and getattr(self._cfg, "use_websocket_price", True):
            try:
                price_stream = PriceStream()
                price_stream.start()
            except Exception as exc:
                logger.warning("WebSocket 가격 스트림 미사용: %s", exc)
        self.client = client or GateIOClient(price_stream=price_stream)
        self._last_bar_ts_checked: Optional[int] = None
        self._last_processed_bar_end_ts: Optional[float] = None
        self._entry_check_lock = threading.Lock()
        self._stop_entry_check_thread = False
        self._entry_check_thread: Optional[threading.Thread] = None
        self._tp_sl_recovered_key: Optional[str] = None
        self._entry_blocked_until_position_ok = False
        self._last_live_pos: Optional[dict] = None
        self._position_poll_lock = threading.Lock()
        state_path = getattr(config, "STATE_FILE", "runtime_state.json")
        if not os.path.isabs(state_path):
            state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), state_path)
        self._state = RuntimeState(state_path)

    def _current_bar_start_ts(self) -> int:
        return int(time.time() // BAR_SECONDS) * BAR_SECONDS

    def _get_candles_cached(self, limit: Optional[int] = None) -> list:
        limit = limit or CANDLE_LIMIT
        raw = self.client.get_candles(limit=limit, interval=config.CANDLE_INTERVAL)
        htf = self.client.get_candles(
            limit=config.EMA_TF_CANDLE_LIMIT,
            interval=config.EMA_TF_INTERVAL,
        )
        return add_indicators(raw, htf_candles=htf or None)

    def _setup_tp_sl(self, side: str, size: str, sl_price: float, tp_price: float, replace: bool = True):
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        if dry:
            logger.info("DRY RUN: TP/SL 등록 생략 side=%s sl=%.4f tp=%.4f", side, sl_price, tp_price)
            return
        max_attempts = 4
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                if replace:
                    try:
                        self.client.cancel_all_price_orders()
                    except Exception as exc:
                        logger.warning("기존 가격 트리거 주문 취소 중 오류(무시): %s", exc)
                have = self.client.classify_open_tp_sl(side)
                if have.get("sl") is None:
                    self.client.create_stop_loss_order(str(sl_price), size, side)
                else:
                    logger.info("SL 이미 있음: trigger=%s", have["sl"])
                have = self.client.classify_open_tp_sl(side)
                if have.get("tp") is None:
                    self.client.create_take_profit_order(str(tp_price), size, side, is_partial=False)
                else:
                    logger.info("TP 이미 있음: trigger=%s", have["tp"])
                have = self.client.classify_open_tp_sl(side)
                logger.info(
                    "TP/SL 등록 결과: side=%s sl=%s tp=%s size=%s",
                    side,
                    have.get("sl"),
                    have.get("tp"),
                    size,
                )
                if have.get("sl") is not None:
                    return
                raise RuntimeError("SL 등록 후에도 트리거가 없음")
            except Exception as exc:
                last_exc = exc
                if _is_auto_user_no_position_error(exc) and attempt < max_attempts:
                    wait_sec = 0.8 * attempt
                    logger.warning(
                        "포지션 반영 지연으로 TP/SL 재시도(%d/%d): side=%s size=%s sl=%.4f tp=%.4f wait=%.1fs",
                        attempt,
                        max_attempts,
                        side,
                        size,
                        sl_price,
                        tp_price,
                        wait_sec,
                    )
                    time.sleep(wait_sec)
                    continue
                logger.error(
                    "TP/SL 등록 실패(최종): side=%s size=%s sl=%.4f tp=%.4f 오류=%s",
                    side,
                    size,
                    sl_price,
                    tp_price,
                    exc,
                )
                break
        if last_exc:
            logger.error("포지션은 유지. 메인 루프에서 빠진 TP/SL 복구 예정")

    def _wait_for_position(
        self,
        expected_side: str,
        min_size: int = 1,
        timeout_sec: float = 5.0,
        poll_interval_sec: float = 0.25,
    ) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                pos = self.client.get_position()
                if pos and pos.get("side") == expected_side and abs(float(pos.get("size", 0))) >= float(min_size):
                    return True
            except Exception:
                pass
            time.sleep(poll_interval_sec)
        return False

    def _equity(self) -> float:
        """사이징은 이 계좌 total/equity. available(묶인 마진)은 쓰지 않는다."""
        try:
            acc = self.client.get_account()
            acc_list = acc if isinstance(acc, list) else [acc] if acc else []
            a0 = acc_list[0] if acc_list else None
            total = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0) if a0 else 0.0
            return max(0.0, total)
        except Exception as exc:
            logger.warning("계정 조회 실패: %s", exc)
            return 0.0

    def _entry_check_loop(self):
        logger.info("진입 조건 체크 스레드 시작 (2초 주기, %s분 봉 완성 시점 감지)", CHART_BAR_MINUTES)
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
                            if candles:
                                completed_row, completed_idx = get_last_completed_bar_row(candles, BAR_SECONDS, now)
                                if completed_row is not None:
                                    last_ts = completed_row.get("t")
                                    if not ENTRY_CHECK_ONLY_ON_BAR_CLOSE or last_ts != self._last_bar_ts_checked:
                                        self._check_and_execute(candles, completed_idx)
                                        self._last_bar_ts_checked = last_ts
                                        self._last_processed_bar_end_ts = current_bar_end
                    finally:
                        self._entry_check_lock.release()
            except Exception as exc:
                logger.exception("진입 체크 오류: %s", exc)
            dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
            if not dry and self._last_live_pos is not None:
                try:
                    self._poll_live_position()
                except PositionLookupError:
                    pass
            time.sleep(2.0)
        logger.info("진입 조건 체크 스레드 종료")

    def _fetch_live_position(self):
        return self.client.get_position()

    def _pos_snapshot(self, pos: dict) -> dict:
        return {
            "side": pos["side"],
            "size": float(pos["size"]),
            "entry_price": float(pos.get("entry_price") or 0),
        }

    def _handle_live_position_change(self, pos: Optional[dict]) -> None:
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        with self._position_poll_lock:
            prev = self._last_live_pos
            if prev and not pos:
                if not dry:
                    try:
                        self.client.cancel_all_price_orders()
                    except Exception as exc:
                        logger.warning("청산 후 잔여 TP/SL 취소 실패: %s", exc)
                    close_price = None
                    try:
                        close_price = self.client.get_last_price()
                    except Exception:
                        pass
                    notify_discord(
                        "close",
                        side=prev["side"],
                        close_price=close_price,
                        size=_format_size_for_notify(str(int(abs(float(prev["size"]))))),
                        extra="(TP/SL·트레일 청산)",
                    )
                    logger.info(
                        "거래소 TP/SL 청산 감지: side=%s size=%s close=%s",
                        prev["side"],
                        prev["size"],
                        close_price,
                    )
                self._state.clear()
                self._tp_sl_recovered_key = None
            self._last_live_pos = self._pos_snapshot(pos) if pos else None

    def _poll_live_position(self):
        pos = self._fetch_live_position()
        self._handle_live_position_change(pos)
        return pos

    def _sync_exchange_position_on_start(self) -> None:
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        try:
            pos = self._fetch_live_position()
        except PositionLookupError as exc:
            self._entry_blocked_until_position_ok = True
            logger.error("시작 시 포지션 조회 실패 — 확인될 때까지 신규 진입하지 않음: %s", exc)
            return
        self._entry_blocked_until_position_ok = False
        if pos:
            logger.info(
                "시작 시 기존 포지션 확인: side=%s size=%s entry=%.4f — 신규 진입 차단",
                pos["side"],
                pos["size"],
                float(pos.get("entry_price") or 0),
            )
            self._state.adopt_live(pos)
            self._last_live_pos = self._pos_snapshot(pos)
            return
        logger.info("시작 시 포지션 없음 (flat)")
        self._last_live_pos = None
        if not dry and self._state.side:
            logger.info("거래소는 플랫 — 로컬 진입 상태 초기화")
            self._state.clear()

    def _open_position(self, side: str, info, dry: bool) -> bool:
        try:
            live = self._fetch_live_position()
        except PositionLookupError as exc:
            logger.error("진입 직전 포지션 조회 실패 — 진입하지 않음: %s", exc)
            return False
        if live:
            logger.warning(
                "진입 직전 기존 포지션 확인 — 신규 진입 취소: side=%s size=%s entry=%.4f",
                live["side"],
                live["size"],
                float(live.get("entry_price") or 0),
            )
            self._state.adopt_live(live)
            return False
        if dry:
            logger.info("DRY RUN: 시장가 진입 생략 side=%s size=%s", side, info.size)
            return True
        self.client.place_market_order(side, info.size, reduce_only=False)
        self._wait_for_position(side, min_size=int(info.size))
        try:
            self._setup_tp_sl(side, info.size, info.sl_price, info.tp_price)
        except Exception as exc:
            logger.error("진입 후 TP/SL 등록 실패 — 알림은 보내고 복구 대기: %s", exc)
        try:
            live = self._fetch_live_position()
            self._last_live_pos = (
                self._pos_snapshot(live) if live else {"side": side, "size": float(info.size), "entry_price": 0.0}
            )
        except PositionLookupError:
            self._last_live_pos = {"side": side, "size": float(info.size), "entry_price": 0.0}
        return True

    def _maybe_trail(self, pos: dict, row: dict, dry: bool) -> None:
        ema = row.get("ema")
        sl = self._state.sl_price
        tp = self._state.tp_price
        if ema is None or sl is None or tp is None:
            return
        if not self._state.after_entry(row.get("t"), pos["side"]):
            return
        tick = float(getattr(config, "PRICE_TICK", 0.1))
        new_sl = next_trail_stop(pos["side"], float(sl), float(ema), tick)
        if new_sl is None:
            return
        size = str(int(abs(float(pos["size"]))))
        logger.info(
            "EMA 트레일: side=%s sl %.4f → %.4f ema=%.4f tp=%.4f",
            pos["side"],
            sl,
            new_sl,
            float(ema),
            tp,
        )
        if not dry:
            self._setup_tp_sl(pos["side"], size, new_sl, float(tp))
        self._state.update_stop(new_sl)

    def _check_and_execute(self, candles: list, completed_idx: int):
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        try:
            live_pos = self._poll_live_position()
        except PositionLookupError as exc:
            logger.error("포지션 조회 실패 — 이번 봉 매매 생략: %s", exc)
            return
        self._entry_blocked_until_position_ok = False
        if live_pos:
            self._state.adopt_live(live_pos)

        pos = live_pos
        if dry and not live_pos and self._state.side:
            pos = {
                "side": self._state.side,
                "size": float(self._state.size or 1),
                "entry_price": float(self._state.entry_price or 0),
            }
        equity = self._equity()
        if equity <= 0:
            return

        long_ok, short_ok, long_info, short_info = check_entry(candles, completed_idx, equity)
        row = candles[completed_idx]
        logger.info(
            "봉 체크 %s | close=%.4f ema=%s rsi=%.1f/%.1f/%.1f ibs=%.4f | long_ok=%s short_ok=%s pos=%s",
            _ts_to_ymdhm(row.get("t")),
            float(row.get("c")),
            f"{float(row['ema']):.4f}" if row.get("ema") is not None else "None",
            float(row.get("rsi1") or 0),
            float(row.get("rsi2") or 0),
            float(row.get("rsi3") or 0),
            float(row.get("ibs") or 0),
            long_ok,
            short_ok,
            pos["side"] if pos else "flat",
        )

        if pos:
            if live_pos and long_ok:
                logger.info("기존 실포지션 보유 중 — 롱 진입 신호 무시")
            if live_pos and short_ok:
                logger.info("기존 실포지션 보유 중 — 숏 진입 신호 무시")
            self._maybe_trail(pos, row, dry)
            return

        if not long_ok and not short_ok:
            return

        if long_ok and long_info and int(long_info.size) >= ORDER_SIZE_MIN:
            opened = self._open_position("long", long_info, dry)
            if not opened:
                return
            self._state.save_entry(
                "long", int(row["t"]), long_info.entry_price, long_info.sl_price, long_info.tp_price, long_info.size
            )
            logger.info(
                "롱 진입: size=%s sl=%.4f tp=%.4f sl_pct=%.4f lev=%.2f",
                long_info.size,
                long_info.sl_price,
                long_info.tp_price,
                long_info.sl_pct,
                long_info.leverage,
            )
            notify_discord(
                "entry",
                side="long",
                entry_price=float(row["c"]) if row.get("c") is not None else None,
                sl_price=long_info.sl_price,
                tp_price=long_info.tp_price,
                size=_format_size_for_notify(long_info.size),
            )
        elif short_ok and short_info and int(short_info.size) >= ORDER_SIZE_MIN:
            opened = self._open_position("short", short_info, dry)
            if not opened:
                return
            self._state.save_entry(
                "short", int(row["t"]), short_info.entry_price, short_info.sl_price, short_info.tp_price, short_info.size
            )
            logger.info(
                "숏 진입: size=%s sl=%.4f tp=%.4f sl_pct=%.4f lev=%.2f",
                short_info.size,
                short_info.sl_price,
                short_info.tp_price,
                short_info.sl_pct,
                short_info.leverage,
            )
            notify_discord(
                "entry",
                side="short",
                entry_price=float(row["c"]) if row.get("c") is not None else None,
                sl_price=short_info.sl_price,
                tp_price=short_info.tp_price,
                size=_format_size_for_notify(short_info.size),
            )

    def _position_key(self, pos: dict) -> str:
        return f"{pos['side']}:{int(abs(float(pos['size'])))}:{pos.get('entry_price', 0)}"

    def _recover_tp_sl_if_missing(self, pos: Optional[dict] = None):
        dry = getattr(self._cfg, "dry_run", False) if self._cfg else False
        if dry:
            return
        if pos is None:
            try:
                pos = self._poll_live_position()
            except PositionLookupError as exc:
                logger.error("TP/SL 복구용 포지션 조회 실패 — 상태 유지: %s", exc)
                return
        if not pos:
            return
        pos_key = self._position_key(pos)
        side = pos["side"]
        have = self.client.classify_open_tp_sl(side)
        if have.get("sl") is not None and have.get("tp") is not None:
            self._tp_sl_recovered_key = pos_key
            return
        if self._tp_sl_recovered_key == pos_key and have.get("sl") is not None and have.get("tp") is not None:
            return

        size = str(int(abs(float(pos["size"]))))
        sl = self._state.sl_price if self._state.side == side else None
        tp = self._state.tp_price if self._state.side == side else None
        if sl is None or tp is None:
            candles = self._get_candles_cached()
            completed_row, _ = get_last_completed_bar_row(candles, BAR_SECONDS, time.time())
            if not completed_row:
                return
            fb = fallback_tp_sl(completed_row, side, self._state.entry_price)
            if not fb:
                return
            sl, tp = fb
            self._state.sl_price = sl
            self._state.tp_price = tp
            self._state._dump()
        logger.warning(
            "포지션 보유 중 TP/SL 일부 없음 — 자동 복구: side=%s size=%s have_sl=%s have_tp=%s sl=%.4f tp=%.4f",
            side,
            size,
            have.get("sl"),
            have.get("tp"),
            sl,
            tp,
        )
        self._setup_tp_sl(side, size, sl, tp, replace=False)
        have = self.client.classify_open_tp_sl(side)
        if have.get("sl") is not None and have.get("tp") is not None:
            self._tp_sl_recovered_key = pos_key

    def run_loop(self, interval_sec: float = 60.0):
        logger.info(
            "엔진 주기 시작: interval_sec=%.1f contract=%s dry_run=%s ema_tf=%s",
            interval_sec,
            CONTRACT,
            getattr(self._cfg, "dry_run", False),
            config.EMA_TF_INTERVAL,
        )
        self._sync_exchange_position_on_start()
        self._stop_entry_check_thread = False
        self._entry_check_thread = threading.Thread(target=self._entry_check_loop, daemon=True)
        self._entry_check_thread.start()
        while True:
            try:
                try:
                    pos = self._poll_live_position()
                except PositionLookupError as exc:
                    logger.error("주기 포지션 조회 실패: %s", exc)
                    time.sleep(interval_sec)
                    continue
                if pos:
                    logger.debug("포지션 보유: %s size=%s entry=%.2f", pos["side"], pos["size"], pos["entry_price"])
                    self._recover_tp_sl_if_missing(pos)
            except Exception as exc:
                logger.exception("run_once 오류: %s", exc)
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
    except Exception as exc:
        logger.warning("계좌 조회 실패(시작 시): %s", exc)
    engine.run_loop(interval_sec=cfg.loop_interval_sec)


if __name__ == "__main__":
    main()
