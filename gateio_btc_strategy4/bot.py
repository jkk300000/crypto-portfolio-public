"""Donchian Gate.io live bot — BTC_USDT daily, cross margin, price-trigger TP/SL."""

from __future__ import annotations

import logging
import math
import time

from config import STRATEGY, BotConfig, load_bot_config
from gate_client import GateApiError, GateClient
from indicators import candles_to_df
from notify import DiscordNotifier
from state_store import StateStore, TradeState
from strategy import (
    EntryPlan,
    latest_snapshot,
    make_entry_plan,
    update_hard_stop,
)

log = logging.getLogger(__name__)

TEXT_SL = "t-dc-sl"
TEXT_TP1 = "t-dc-tp1"
TEXT_TP2 = "t-dc-tp2"
TEXT_ENTRY = "t-dc-entry"
TEXT_EXIT_DC = "t-dc-dc10"


class DonchianBot:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.client = GateClient(cfg)
        self.store = StateStore(cfg.state_path)
        self.state = self.store.load()
        self.notify = DiscordNotifier(cfg.discord_webhook_url)
        self.quanto = 0.0001
        self.order_price_round = "0.1"
        self.order_size_min = 1

    # ——— setup ———
    def bootstrap(self) -> None:
        contract = self.client.get_contract()
        self.quanto = float(contract.get("quanto_multiplier") or 0.0001)
        self.order_price_round = str(contract.get("order_price_round") or "0.1")
        self.order_size_min = int(float(contract.get("order_size_min") or 1))
        log.info(
            "contract=%s quanto=%s price_round=%s min_size=%s",
            self.cfg.contract,
            self.quanto,
            self.order_price_round,
            self.order_size_min,
        )
        self.client.ensure_cross_mode()
        self.client.set_leverage_cross(STRATEGY.max_leverage)
        self.reconcile_on_start()
        self._seed_last_bar_if_needed()

    def _seed_last_bar_if_needed(self) -> None:
        """첫 실행 시 이미 마감된 봉 신호로 즉시 진입하지 않도록 시드."""
        if self.state.last_processed_bar is not None:
            return
        candles = self.client.get_candles(self.cfg.timeframe, limit=50)
        df = candles_to_df(candles, closed_only=True)
        snap = latest_snapshot(df)
        if snap:
            self.state.last_processed_bar = snap.bar_time
            self.store.save(self.state)
            log.info("seed last_processed_bar=%s (no catch-up entry)", snap.bar_time)

    def equity_usdt(self) -> float:
        acc = self.client.get_account()
        total = float(acc.get("equity") or acc.get("total") or 0)
        raw = acc.get("available")
        try:
            available = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            available = None
        if available is not None and available > 0:
            total = min(total, available) if total > 0 else available
        return max(total, 0.0001)

    def btc_to_contracts(self, qty_btc: float) -> int:
        n = int(math.floor(qty_btc / self.quanto))
        return max(n, 0)

    def round_price(self, px: float) -> float:
        step = float(self.order_price_round)
        if step <= 0:
            return px
        return round(round(px / step) * step, 10)

    # ——— restart reconcile ———
    def reconcile_on_start(self) -> None:
        log.info("=== restart reconcile ===")
        pos = self.client.get_position()
        open_triggers = self.client.list_price_orders("open")

        if not pos:
            log.info("포지션 없음 — 상태/고아 트리거 정리")
            if open_triggers:
                self.client.cancel_all_price_orders()
            self.state.clear_position()
            self.store.save(self.state)
            return

        size = int(float(pos["size"]))
        entry = float(pos.get("entry_price") or 0)
        direction = 1 if size > 0 else -1
        abs_size = abs(size)
        log.info("포지션 보유: size=%s entry=%s", size, entry)

        # 상태가 비었거나 방향 불일치면 재구성
        if self.state.direction == 0 or self.state.direction != direction:
            log.warning("로컬 상태 없음/불일치 → 포지션 기준 재구성")
            self._rebuild_state_from_position(pos, open_triggers)
        else:
            self.state.direction = direction
            if not self.state.entry_price:
                self.state.entry_price = entry
            if self.state.initial_qty_contracts <= 0:
                self.state.initial_qty_contracts = abs_size
                self._recalc_tp_qtys()

        self._sync_tp_flags_from_size(abs_size)
        self._ensure_protective_orders(open_triggers)
        self.store.save(self.state)
        log.info(
            "reconcile done: dir=%s SL=%s TP1=%s/%s TP2=%s/%s",
            self.state.direction,
            self.state.hard_stop,
            self.state.take_profit1,
            self.state.tp1_done,
            self.state.take_profit2,
            self.state.tp2_done,
        )

    def _rebuild_state_from_position(self, pos: dict, open_triggers: list[dict]) -> None:
        size = int(float(pos["size"]))
        entry = float(pos.get("entry_price") or 0)
        direction = 1 if size > 0 else -1
        abs_size = abs(size)

        # ATR로 SL/TP 재추정 (재시작 시 근사)
        candles = self.client.get_candles(self.cfg.timeframe, limit=200)
        df = candles_to_df(candles, closed_only=True)
        snap = latest_snapshot(df)
        atr = snap.atr if snap else entry * 0.01
        sl_dist = atr * STRATEGY.atr_mult
        if direction == 1:
            stop = entry - sl_dist
            tp1 = entry + sl_dist * STRATEGY.rr_tp1
            tp2 = entry + sl_dist * STRATEGY.rr_tp2
        else:
            stop = entry + sl_dist
            tp1 = entry - sl_dist * STRATEGY.rr_tp1
            tp2 = entry - sl_dist * STRATEGY.rr_tp2

        # 기존 트리거에서 가격 힌트 추출
        by_text = self._index_triggers(open_triggers)
        if TEXT_SL in by_text:
            stop = float(by_text[TEXT_SL]["trigger"]["price"])
        if TEXT_TP1 in by_text:
            tp1 = float(by_text[TEXT_TP1]["trigger"]["price"])
        if TEXT_TP2 in by_text:
            tp2 = float(by_text[TEXT_TP2]["trigger"]["price"])

        self.state = TradeState(
            direction=direction,
            entry_price=entry,
            initial_stop=stop,
            hard_stop=stop,
            take_profit1=tp1,
            take_profit2=tp2,
            initial_qty_contracts=max(abs_size, self.state.initial_qty_contracts or abs_size),
            tp1_done=TEXT_TP1 not in by_text and abs_size < (self.state.initial_qty_contracts or abs_size),
            tp2_done=TEXT_TP2 not in by_text,
            sl_order_id=str(by_text[TEXT_SL]["id"]) if TEXT_SL in by_text else None,
            tp1_order_id=str(by_text[TEXT_TP1]["id"]) if TEXT_TP1 in by_text else None,
            tp2_order_id=str(by_text[TEXT_TP2]["id"]) if TEXT_TP2 in by_text else None,
            last_processed_bar=self.state.last_processed_bar,
        )
        # initial_qty를 더 크게 추정: 현재 잔량 + 이미 익절된 양 모를 때
        if self.state.initial_qty_contracts < abs_size:
            self.state.initial_qty_contracts = abs_size
        self._recalc_tp_qtys()
        # tp flags refine
        self._sync_tp_flags_from_size(abs_size)

    def _recalc_tp_qtys(self) -> None:
        init_q = self.state.initial_qty_contracts
        self.state.tp1_qty_contracts = max(1, int(round(init_q * STRATEGY.tp1_qty_pct / 100.0)))
        remaining = max(init_q - self.state.tp1_qty_contracts, 0)
        self.state.tp2_qty_contracts = max(1, int(round(remaining * STRATEGY.tp2_qty_pct / 100.0)))

    def _sync_tp_flags_from_size(self, abs_size: int) -> None:
        init_q = max(self.state.initial_qty_contracts, abs_size)
        expected_after_tp1 = init_q - self.state.tp1_qty_contracts
        if abs_size <= expected_after_tp1:
            self.state.tp1_done = True
        expected_after_tp2 = expected_after_tp1 - self.state.tp2_qty_contracts
        if abs_size <= expected_after_tp2:
            self.state.tp2_done = True
        if self.state.tp1_done and self.state.entry_price and self.state.hard_stop is not None:
            if self.state.direction == 1 and self.state.hard_stop < self.state.entry_price:
                self.state.hard_stop = self.state.entry_price
            elif self.state.direction == -1 and self.state.hard_stop > self.state.entry_price:
                self.state.hard_stop = self.state.entry_price

    def _index_triggers(self, orders: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for o in orders:
            initial = o.get("initial") or {}
            text = (initial.get("text") or "").strip()
            if text in {TEXT_SL, TEXT_TP1, TEXT_TP2}:
                out[text] = o
        return out

    def _ensure_protective_orders(self, open_triggers: list[dict] | None = None) -> None:
        """포지션 있을 때 SL/TP1/TP2 없으면 재주문."""
        if self.state.direction == 0:
            return
        pos = self.client.get_position()
        if not pos:
            return

        abs_size = abs(int(float(pos["size"])))
        triggers = open_triggers if open_triggers is not None else self.client.list_price_orders("open")
        by_text = self._index_triggers(triggers)
        last = float((self.client.get_tickers() or {}).get("last") or pos.get("mark_price") or 0)

        # SL
        if TEXT_SL not in by_text:
            log.warning("SL 트리거 없음 → 재주문 @ %s", self.state.hard_stop)
            self._place_sl(abs_size, last)
        else:
            self.state.sl_order_id = str(by_text[TEXT_SL]["id"])
            # 가격이 크게 다르면(트레일 후) 교체는 on_bar에서 처리

        # TP1
        if not self.state.tp1_done:
            if TEXT_TP1 not in by_text:
                log.warning("TP1 트리거 없음 → 재주문 @ %s", self.state.take_profit1)
                self._place_tp1(last)
            else:
                self.state.tp1_order_id = str(by_text[TEXT_TP1]["id"])
        elif TEXT_TP1 in by_text:
            self.client.cancel_price_order(by_text[TEXT_TP1]["id"])
            self.state.tp1_order_id = None

        # TP2
        if not self.state.tp2_done:
            if TEXT_TP2 not in by_text:
                log.warning("TP2 트리거 없음 → 재주문 @ %s", self.state.take_profit2)
                self._place_tp2(last)
            else:
                self.state.tp2_order_id = str(by_text[TEXT_TP2]["id"])
        elif TEXT_TP2 in by_text:
            self.client.cancel_price_order(by_text[TEXT_TP2]["id"])
            self.state.tp2_order_id = None

    # ——— place protective ———
    def _order_type(self) -> str:
        return "plan-close-long-position" if self.state.direction == 1 else "plan-close-short-position"

    def _close_size_sign(self, contracts: int) -> int:
        # plan-close-long: size < 0 / plan-close-short: size > 0
        if self.state.direction == 1:
            return -abs(contracts)
        return abs(contracts)

    def _rule_for_long_below(self) -> int:
        return 2  # price <= trigger

    def _rule_for_long_above(self) -> int:
        return 1  # price >= trigger

    def _place_sl(self, abs_size: int, last_price: float) -> None:
        if self.state.hard_stop is None:
            return
        stop = self.round_price(self.state.hard_stop)
        is_long = self.state.direction == 1
        rule = self._rule_for_long_below() if is_long else self._rule_for_long_above()

        # 이미 돌파된 손절이면 즉시 청산
        if is_long and last_price and last_price <= stop:
            log.warning("SL already breached → market close")
            self.client.close_position_market(text="t-dc-sl-mkt")
            self._flatten_local(reason="SL", exit_price=last_price)
            return
        if (not is_long) and last_price and last_price >= stop:
            log.warning("SL already breached → market close")
            self.client.close_position_market(text="t-dc-sl-mkt")
            self._flatten_local(reason="SL", exit_price=last_price)
            return

        size = self._close_size_sign(abs_size)
        try:
            resp = self.client.create_price_order(
                size=size,
                trigger_price=stop,
                rule=rule,
                text=TEXT_SL,
                order_type=self._order_type(),
                price_round=self.order_price_round,
            )
            self.state.sl_order_id = str(resp.get("id") or resp.get("order_id"))
            self.state.hard_stop = stop
            log.info("SL placed id=%s px=%s size=%s", self.state.sl_order_id, stop, size)
        except GateApiError as e:
            log.error("SL place failed: %s", e)

    def _place_tp1(self, last_price: float) -> None:
        if self.state.take_profit1 is None or self.state.tp1_done:
            return
        tp = self.round_price(self.state.take_profit1)
        qty = max(self.state.tp1_qty_contracts, self.order_size_min)
        is_long = self.state.direction == 1
        rule = self._rule_for_long_above() if is_long else self._rule_for_long_below()

        if is_long and last_price and last_price >= tp:
            log.warning("TP1 already hit → market reduce")
            self.client.reduce_market(qty, True, TEXT_TP1)
            self.state.tp1_done = True
            self._on_tp1_filled()
            return
        if (not is_long) and last_price and last_price <= tp:
            log.warning("TP1 already hit → market reduce")
            self.client.reduce_market(qty, False, TEXT_TP1)
            self.state.tp1_done = True
            self._on_tp1_filled()
            return

        try:
            resp = self.client.create_price_order(
                size=self._close_size_sign(qty),
                trigger_price=tp,
                rule=rule,
                text=TEXT_TP1,
                order_type=self._order_type(),
                price_round=self.order_price_round,
            )
            self.state.tp1_order_id = str(resp.get("id") or resp.get("order_id"))
            log.info("TP1 placed id=%s px=%s qty=%s", self.state.tp1_order_id, tp, qty)
        except GateApiError as e:
            log.error("TP1 place failed: %s", e)

    def _place_tp2(self, last_price: float) -> None:
        if self.state.take_profit2 is None or self.state.tp2_done:
            return
        tp = self.round_price(self.state.take_profit2)
        qty = max(self.state.tp2_qty_contracts, self.order_size_min)
        is_long = self.state.direction == 1
        rule = self._rule_for_long_above() if is_long else self._rule_for_long_below()

        if is_long and last_price and last_price >= tp:
            log.warning("TP2 already hit → market reduce")
            self.client.reduce_market(qty, True, TEXT_TP2)
            self.state.tp2_done = True
            self.state.tp2_order_id = None
            return
        if (not is_long) and last_price and last_price <= tp:
            log.warning("TP2 already hit → market reduce")
            self.client.reduce_market(qty, False, TEXT_TP2)
            self.state.tp2_done = True
            self.state.tp2_order_id = None
            return

        try:
            resp = self.client.create_price_order(
                size=self._close_size_sign(qty),
                trigger_price=tp,
                rule=rule,
                text=TEXT_TP2,
                order_type=self._order_type(),
                price_round=self.order_price_round,
            )
            self.state.tp2_order_id = str(resp.get("id") or resp.get("order_id"))
            log.info("TP2 placed id=%s px=%s qty=%s", self.state.tp2_order_id, tp, qty)
        except GateApiError as e:
            log.error("TP2 place failed: %s", e)

    def _on_tp1_filled(self) -> None:
        self.state.tp1_done = True
        self.state.tp1_order_id = None
        if self.state.entry_price is not None:
            if self.state.direction == 1:
                self.state.hard_stop = max(self.state.hard_stop or self.state.entry_price, self.state.entry_price)
            else:
                self.state.hard_stop = min(self.state.hard_stop or self.state.entry_price, self.state.entry_price)
        self._replace_sl_to_hard_stop()

    def _replace_sl_to_hard_stop(self) -> None:
        pos = self.client.get_position()
        if not pos:
            return
        # cancel old SL
        triggers = self._index_triggers(self.client.list_price_orders("open"))
        if TEXT_SL in triggers:
            self.client.cancel_price_order(triggers[TEXT_SL]["id"])
        last = float((self.client.get_tickers() or {}).get("last") or 0)
        self._place_sl(abs(int(float(pos["size"]))), last)

    def _flatten_local(
        self,
        reason: str | None = None,
        exit_price: float | None = None,
    ) -> None:
        if reason and self.state.direction != 0:
            self.notify.exit(
                direction=self.state.direction,
                contract=self.cfg.contract,
                reason=reason,
                entry=self.state.entry_price,
                exit_price=exit_price,
            )
        self.client.cancel_all_price_orders()
        self.state.clear_position()
        self.store.save(self.state)

    # ——— live sync (fills) ———
    def sync_fills(self) -> None:
        pos = self.client.get_position()
        if not pos:
            if self.state.direction != 0:
                log.info("포지션 청산 감지 — 상태 초기화")
                last = float((self.client.get_tickers() or {}).get("last") or 0) or None
                self._flatten_local(reason="SL/TP 체결 감지", exit_price=last)
            return

        abs_size = abs(int(float(pos["size"])))
        triggers = self._index_triggers(self.client.list_price_orders("open"))

        # TP1 filled?
        if not self.state.tp1_done and TEXT_TP1 not in triggers:
            expected = self.state.initial_qty_contracts - self.state.tp1_qty_contracts
            if abs_size <= expected + 1:  # tolerance 1 contract
                log.info("TP1 fill inferred")
                self._on_tp1_filled()

        # TP2 filled?
        if self.state.tp1_done and not self.state.tp2_done and TEXT_TP2 not in triggers:
            log.info("TP2 fill inferred (order gone)")
            self.state.tp2_done = True
            self.state.tp2_order_id = None

        # SL gone but still in position → re-place
        if TEXT_SL not in triggers:
            log.warning("sync: SL missing → re-place")
            last = float((self.client.get_tickers() or {}).get("last") or 0)
            self._place_sl(abs_size, last)

        if not self.state.tp1_done and TEXT_TP1 not in triggers:
            last = float((self.client.get_tickers() or {}).get("last") or 0)
            self._place_tp1(last)

        if not self.state.tp2_done and TEXT_TP2 not in triggers:
            last = float((self.client.get_tickers() or {}).get("last") or 0)
            self._place_tp2(last)

        self.store.save(self.state)

    # ——— entry / exit on daily close ———
    def on_new_bar(self, snap) -> None:
        log.info(
            "new daily bar t=%s close=%.2f IBS=%.3f DC20=[%.2f,%.2f] L=%s S=%s",
            snap.bar_time,
            snap.close,
            snap.ibs,
            snap.entry_lower,
            snap.entry_upper,
            snap.long_signal,
            snap.short_signal,
        )

        pos = self.client.get_position()

        # flat → entry
        if not pos and self.state.direction == 0:
            equity = self.equity_usdt()
            plan = make_entry_plan(snap, equity)
            if plan:
                self._enter(plan, snap.bar_time)
            self.state.last_processed_bar = snap.bar_time
            self.store.save(self.state)
            return

        # in position → trail + DC10 exit
        if pos and self.state.direction != 0:
            # swing trail
            new_hs = update_hard_stop(
                direction=self.state.direction,
                hard_stop=float(self.state.hard_stop or 0),
                entry_price=float(self.state.entry_price or 0),
                tp1_done=self.state.tp1_done,
                swing_low=snap.swing_low,
                swing_high=snap.swing_high,
                close=snap.close,
                enable_swing=STRATEGY.enable_swing_trail,
            )
            if self.state.hard_stop is None or abs(new_hs - self.state.hard_stop) > float(self.order_price_round) * 0.5:
                log.info("hard stop update: %s → %s", self.state.hard_stop, new_hs)
                self.state.hard_stop = self.round_price(new_hs)
                self._replace_sl_to_hard_stop()

            # Donchian 10 exit
            if self.state.direction == 1 and snap.long_exit:
                log.info("DC10 long exit")
                self.client.cancel_all_price_orders()
                self.client.close_position_market(text=TEXT_EXIT_DC)
                self._flatten_local(reason="DC10 Exit", exit_price=snap.close)
            elif self.state.direction == -1 and snap.short_exit:
                log.info("DC10 short exit")
                self.client.cancel_all_price_orders()
                self.client.close_position_market(text=TEXT_EXIT_DC)
                self._flatten_local(reason="DC10 Exit", exit_price=snap.close)

        self.state.last_processed_bar = snap.bar_time
        self.store.save(self.state)

    def _enter(self, plan: EntryPlan, bar_time: int) -> None:
        contracts = self.btc_to_contracts(plan.qty_btc)
        if contracts < self.order_size_min:
            log.warning("qty too small: btc=%.6f contracts=%s", plan.qty_btc, contracts)
            return

        size = contracts if plan.direction == 1 else -contracts
        log.info(
            "ENTRY %s size=%s entry≈%.2f SL=%.2f TP1=%.2f TP2=%.2f",
            "LONG" if plan.direction == 1 else "SHORT",
            size,
            plan.entry_price,
            plan.stop,
            plan.tp1,
            plan.tp2,
        )
        try:
            self.client.place_market(size=size, text=TEXT_ENTRY)
        except GateApiError as e:
            log.error("entry failed: %s", e)
            return

        time.sleep(0.5)
        pos = self.client.get_position()
        if not pos:
            log.error("entry filled but no position — abort protectives")
            return

        fill_entry = float(pos.get("entry_price") or plan.entry_price)
        abs_size = abs(int(float(pos["size"])))
        # 실제 체결가 기준으로 SL/TP 재계산 (R 유지)
        sl_dist = plan.sl_dist
        if plan.direction == 1:
            stop = fill_entry - sl_dist
            tp1 = fill_entry + sl_dist * STRATEGY.rr_tp1
            tp2 = fill_entry + sl_dist * STRATEGY.rr_tp2
        else:
            stop = fill_entry + sl_dist
            tp1 = fill_entry - sl_dist * STRATEGY.rr_tp1
            tp2 = fill_entry - sl_dist * STRATEGY.rr_tp2

        self.state = TradeState(
            direction=plan.direction,
            entry_price=fill_entry,
            initial_stop=self.round_price(stop),
            hard_stop=self.round_price(stop),
            take_profit1=self.round_price(tp1),
            take_profit2=self.round_price(tp2),
            initial_qty_contracts=abs_size,
            tp1_done=False,
            tp2_done=False,
            entry_bar_time=bar_time,
            last_processed_bar=bar_time,
        )
        self._recalc_tp_qtys()
        # tp qty가 포지션보다 크지 않게
        if self.state.tp1_qty_contracts >= abs_size:
            self.state.tp1_qty_contracts = max(1, abs_size // 10)
        remaining = abs_size - self.state.tp1_qty_contracts
        if self.state.tp2_qty_contracts >= remaining:
            self.state.tp2_qty_contracts = max(1, remaining // 10) if remaining > 1 else 0
            if self.state.tp2_qty_contracts == 0:
                self.state.tp2_done = True

        last = float((self.client.get_tickers() or {}).get("last") or fill_entry)
        self._place_sl(abs_size, last)
        self._place_tp1(last)
        if not self.state.tp2_done:
            self._place_tp2(last)
        self.store.save(self.state)

        self.notify.entry(
            direction=self.state.direction,
            contract=self.cfg.contract,
            size=abs_size,
            entry=fill_entry,
            stop=float(self.state.hard_stop or stop),
            tp1=float(self.state.take_profit1 or tp1),
            tp2=float(self.state.take_profit2 or tp2),
        )

    # ——— main loop ———
    def run(self) -> None:
        self.bootstrap()
        log.info("bot started — timeframe=%s poll=%ss", self.cfg.timeframe, self.cfg.poll_interval_sec)
        if not self.notify.enabled:
            log.warning("DISCORD_WEBHOOK_URL 미설정 — 디스코드 알림 비활성")
        else:
            try:
                equity = self.equity_usdt()
            except Exception:
                equity = None
            self.notify.bot_started(
                contract=self.cfg.contract,
                timeframe=self.cfg.timeframe,
                leverage=STRATEGY.max_leverage,
                equity=equity,
                has_position=self.state.direction != 0,
            )

        fill_poll_sec = 2.0
        bar_poll_sec = float(self.cfg.poll_interval_sec or 60)
        last_bar_check = 0.0
        log.info("청산 감지 주기=%.0fs, 일봉 체크 주기=%.0fs", fill_poll_sec, bar_poll_sec)
        while True:
            try:
                self.sync_fills()

                now = time.time()
                if now - last_bar_check >= bar_poll_sec:
                    last_bar_check = now
                    candles = self.client.get_candles(self.cfg.timeframe, limit=200)
                    df = candles_to_df(candles, closed_only=True)
                    snap = latest_snapshot(df)
                    if snap is None:
                        log.warning("indicators not ready")
                    elif self.state.last_processed_bar != snap.bar_time:
                        self.on_new_bar(snap)
                    else:
                        log.debug("waiting next daily close (last=%s)", snap.bar_time)

            except GateApiError as e:
                log.error("API error: %s", e)
            except Exception:
                log.exception("loop error")

            time.sleep(fill_poll_sec)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = load_bot_config()
    DonchianBot(cfg).run()


if __name__ == "__main__":
    main()
