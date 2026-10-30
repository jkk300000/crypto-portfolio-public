# -*- coding: utf-8 -*-
"""Live sleeve snapshot: position, TP/SL, SuperTrend, partial TP, PnL."""
from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd

from .config import BOT_ROOTS, NAMES, PRINCIPALS, SETTLE, SLEEVES, WEIGHTS
from .env import load_dotenv, main_keys, sleeve_keys, sub_uids
from .gate_wallet import GateWallet, GateWalletError
from .mdd import book_mark_complete, load_mdd, update_book_mdd

SLEEVE_SPEC = {
    "eth": {"contract": "ETH_USDT", "interval": "30m", "st": (10, 3.0), "bar_sec": 1800},
    "ema": {"contract": "BTC_USDT", "interval": "1h", "st": None, "bar_sec": 3600},
    "sr": {"contract": "BTC_USDT", "interval": "1h", "st": (10, 3.0), "bar_sec": 3600},
    "dc": {"contract": "BTC_USDT", "interval": "1d", "st": None, "bar_sec": 86400},
}

_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_SEC = 2.0
_SNAP_LOCK = Lock()


def _principals() -> dict[str, float]:
    load_dotenv()
    out = dict(PRINCIPALS)
    names = {
        "eth": "PRINCIPAL_ETH",
        "ema": "PRINCIPAL_EMA",
        "sr": "PRINCIPAL_SR",
        "dc": "PRINCIPAL_DC",
    }
    for sleeve, env_name in names.items():
        raw = (os.getenv(env_name) or "").strip()
        if not raw and sleeve == "ema":
            raw = (os.getenv("PRINCIPAL_SQZ") or "").strip()
        if raw:
            out[sleeve] = float(raw)
    return out


def _as_dict(v: Any) -> dict:
    if isinstance(v, list):
        v = v[0] if v else {}
    return v if isinstance(v, dict) else {}


def _pick_position(raw: Any, contract: str) -> dict:
    rows = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    best: dict = {}
    best_abs = 0.0
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("contract") or "")
        if name and name != contract:
            continue
        sz = abs(_f(item.get("size"), 0.0) or 0.0)
        if sz > best_abs:
            best_abs = sz
            best = item
    return best


def _f(v: Any, default: float | None = None) -> float | None:
    if v in (None, ""):
        return default
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series, period: int, mult: float) -> tuple[int, float, float]:
    src = (high + low) / 2.0
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    n = len(close)
    up = np.empty(n)
    dn = np.empty(n)
    trend = np.ones(n, dtype=np.int32)
    for i in range(n):
        up_raw = float(src.iloc[i]) - mult * float(atr.iloc[i])
        dn_raw = float(src.iloc[i]) + mult * float(atr.iloc[i])
        if i == 0 or not math.isfinite(up_raw):
            up[i] = up_raw
            dn[i] = dn_raw
            continue
        up1, dn1 = up[i - 1], dn[i - 1]
        prev_c = float(close.iloc[i - 1])
        up[i] = max(up_raw, up1) if prev_c > up1 else up_raw
        dn[i] = min(dn_raw, dn1) if prev_c < dn1 else dn_raw
        c = float(close.iloc[i])
        if trend[i - 1] == -1 and c > dn1:
            trend[i] = 1
        elif trend[i - 1] == 1 and c < up1:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    last = n - 1
    return int(trend[last]), float(up[last]), float(dn[last])


def _candles_df(rows: list) -> pd.DataFrame:
    recs = []
    for row in rows or []:
        if isinstance(row, dict):
            recs.append(
                {
                    "t": _f(row.get("t")),
                    "o": _f(row.get("o")),
                    "h": _f(row.get("h")),
                    "l": _f(row.get("l")),
                    "c": _f(row.get("c")),
                }
            )
        elif isinstance(row, (list, tuple)) and len(row) >= 6:
            recs.append({"t": _f(row[0]), "o": _f(row[5]), "h": _f(row[3]), "l": _f(row[4]), "c": _f(row[2])})
    df = pd.DataFrame(recs).dropna()
    return df.sort_values("t").reset_index(drop=True) if not df.empty else df


def _closed_bars(df: pd.DataFrame, bar_sec: int) -> pd.DataFrame:
    if df.empty:
        return df
    last_t = float(df.iloc[-1]["t"])
    if last_t > 1e12:
        last_t /= 1000.0
    if time.time() < last_t + bar_sec - 2:
        return df.iloc[:-1] if len(df) > 1 else df
    return df


def _pine_ema(series: pd.Series, length: int) -> pd.Series:
    x = series.to_numpy(dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    if length <= 0 or n == 0:
        return pd.Series(out, index=series.index)
    if length == 1:
        return pd.Series(x.copy(), index=series.index)
    alpha = 2.0 / (float(length) + 1.0)
    start = None
    for i in range(length - 1, n):
        window = x[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            out[i] = float(window.mean())
            start = i
            break
    if start is None:
        return pd.Series(out, index=series.index)
    for i in range(start + 1, n):
        prev, cur = out[i - 1], x[i]
        if np.isfinite(prev) and np.isfinite(cur):
            out[i] = alpha * cur + (1.0 - alpha) * prev
    return pd.Series(out, index=series.index)


def _load_closed(api: GateWallet, contract: str, interval: str, bar_sec: int, limit: int = 300) -> pd.DataFrame:
    raw = api.request(
        "GET",
        f"/futures/{SETTLE}/candlesticks",
        params={"contract": contract, "interval": interval, "limit": limit},
    )
    return _closed_bars(_candles_df(raw if isinstance(raw, list) else []), bar_sec)


def _attach_guide(api: GateWallet, sleeve: str, spec: dict, out: dict) -> None:
    """Fill strategy-specific trail/channel values for the dashboard."""
    contract = spec["contract"]
    if sleeve in ("eth", "sr"):
        out["guide"] = {"kind": "supertrend", "label": "슈퍼트렌드", "value": None, "side": None}
        st_cfg = spec.get("st")
        if not (st_cfg and out.get("has_position")):
            return
        try:
            df = _load_closed(api, contract, spec["interval"], spec["bar_sec"])
            if len(df) < st_cfg[0] + 5:
                return
            trend, up, dn = _supertrend(df["h"], df["l"], df["c"], st_cfg[0], st_cfg[1])
            side = "long" if trend == 1 else "short"
            value = up if out["side"] == "long" else dn
            out["supertrend_side"] = side
            out["supertrend"] = value
            out["guide"] = {"kind": "supertrend", "label": "슈퍼트렌드", "value": value, "side": side}
        except (GateWalletError, ValueError, KeyError):
            return
        return

    if sleeve == "ema":
        out["guide"] = {"kind": "ema", "label": "EMA 8h", "value": None, "side": None}
        try:
            df = _load_closed(api, contract, "8h", 8 * 3600, limit=120)
            if len(df) < 20:
                return
            val = float(_pine_ema(df["c"], 20).iloc[-1])
            if math.isfinite(val):
                out["guide"]["value"] = val
                out["ema"] = val
        except (GateWalletError, ValueError, KeyError, IndexError):
            pass
        return

    if sleeve == "dc":
        rows = []
        out["guide"] = {"kind": "donchian", "label": "돈치안", "value": None, "side": None, "rows": rows}
        try:
            df = _load_closed(api, contract, spec["interval"], spec["bar_sec"], limit=80)
            if len(df) < 22:
                return
            h, l = df["h"], df["l"]
            entry_upper = _f(h.rolling(20).max().shift(1).iloc[-1])
            entry_lower = _f(l.rolling(20).min().shift(1).iloc[-1])
            exit_upper = _f(h.rolling(10).max().shift(1).iloc[-1])
            exit_lower = _f(l.rolling(10).min().shift(1).iloc[-1])
            side = out.get("side")
            rows.extend(
                [
                    {"label": "돈치안20 상단", "value": entry_upper, "note": "롱 진입", "emphasis": False},
                    {"label": "돈치안20 하단", "value": entry_lower, "note": "숏 진입", "emphasis": False},
                    {
                        "label": "돈치안10 상단",
                        "value": exit_upper,
                        "note": "숏 청산",
                        "emphasis": side == "short",
                    },
                    {
                        "label": "돈치안10 하단",
                        "value": exit_lower,
                        "note": "롱 청산",
                        "emphasis": side == "long",
                    },
                ]
            )
            if side == "long":
                out["guide"]["value"] = exit_lower
            elif side == "short":
                out["guide"]["value"] = exit_upper
        except (GateWalletError, ValueError, KeyError, IndexError):
            return


def _classify_orders(orders: list, side: str | None, entry: float | None) -> dict:
    sls: list[float] = []
    tps: list[float] = []
    partials: list[dict] = []
    for raw in orders or []:
        raw = _as_dict(raw)
        if not raw:
            continue
        ot = str(raw.get("order_type") or "").lower()
        trigger = _as_dict(raw.get("trigger"))
        initial = _as_dict(raw.get("initial"))
        price = _f(trigger.get("price"))
        if price is None:
            continue
        size = abs(int(float(initial.get("size") or 0)))
        rule = int(trigger.get("rule") or 0)
        if "plan-close" in ot:
            partials.append({"price": price, "size": size or None, "status": "open"})
            continue
        is_long_exit = "long" in ot or (side == "long")
        is_short_exit = "short" in ot or (side == "short")
        if side == "long" or is_long_exit:
            if rule == 2 or (entry is not None and price < entry):
                sls.append(price)
            else:
                tps.append(price)
        elif side == "short" or is_short_exit:
            if rule == 1 or (entry is not None and price > entry):
                sls.append(price)
            else:
                tps.append(price)
        elif rule == 2:
            sls.append(price)
        else:
            tps.append(price)
    return {
        "sl": min(sls) if side == "long" and sls else (max(sls) if sls else None),
        "tp": max(tps) if side == "long" and tps else (min(tps) if tps else None),
        "partials": partials,
    }


def _donchian_state() -> dict:
    path = BOT_ROOTS["dc"] / "trade_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _ema_state() -> dict:
    path = BOT_ROOTS["ema"] / "runtime_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _one(sleeve: str) -> dict:
    spec = SLEEVE_SPEC[sleeve]
    contract = spec["contract"]
    out = {
        "id": sleeve,
        "name": NAMES[sleeve],
        "weight": WEIGHTS[sleeve],
        "contract": contract,
        "has_position": False,
        "side": None,
        "size": 0.0,
        "entry": None,
        "mark": None,
        "pnl": None,
        "pnl_pct": None,
        "leverage": None,
        "sl": None,
        "tp": None,
        "supertrend": None,
        "supertrend_side": None,
        "ema": None,
        "guide": None,
        "partials": [],
        "equity": None,
        "principal": _principals()[sleeve],
        "book_pnl": None,
        "book_pnl_pct": None,
        "error": None,
    }
    try:
        key, secret = sleeve_keys(sleeve)
    except RuntimeError as exc:
        out["error"] = str(exc)
        return out
    api = GateWallet(key, secret)
    try:
        acct = _as_dict(api.request("GET", f"/futures/{SETTLE}/accounts"))
        # Gate total excludes UPL. Same mark as controller parse_futures_account.
        total = _f(acct.get("total"), 0.0) or 0.0
        upl = _f(acct.get("unrealised_pnl") or acct.get("unrealized_pnl"), 0.0) or 0.0
        equity = total + upl
        principal = out["principal"]
        out["equity"] = equity
        out["book_pnl"] = equity - principal
        out["book_pnl_pct"] = ((equity - principal) / principal * 100.0) if principal else None
    except GateWalletError as exc:
        out["error"] = str(exc)
    try:
        pos = _pick_position(api.request("GET", f"/futures/{SETTLE}/positions/{contract}"), contract)
    except GateWalletError as exc:
        if "POSITION_NOT_FOUND" in str(exc) or "404" in str(exc):
            pos = {}
        else:
            out["error"] = str(exc)
            return out
    if abs(_f(pos.get("size"), 0.0) or 0.0) <= 0:
        try:
            listed = api.request("GET", f"/futures/{SETTLE}/positions", params={"holding": "true"})
            pos = _pick_position(listed, contract)
        except GateWalletError:
            pass
    size = _f(pos.get("size"), 0.0) or 0.0
    if abs(size) > 0:
        side = "long" if size > 0 else "short"
        entry = _f(pos.get("entry_price"))
        mark = _f(pos.get("mark_price") or pos.get("last_price"))
        pnl = _f(pos.get("unrealised_pnl") or pos.get("unrealized_pnl"), 0.0)
        out.update(
            {
                "has_position": True,
                "side": side,
                "size": abs(size),
                "entry": entry,
                "mark": mark,
                "pnl": pnl,
                "leverage": _f(pos.get("cross_leverage_limit") or pos.get("leverage")),
            }
        )
        if entry and mark and entry > 0:
            raw = (mark - entry) / entry
            out["pnl_pct"] = raw * 100.0 if side == "long" else -raw * 100.0
        lev = _f(pos.get("leverage"))
        if lev == 0:
            out["margin_mode"] = "cross"
        elif lev:
            out["margin_mode"] = "isolate"
            out["leverage"] = lev

    try:
        orders = api.request(
            "GET",
            f"/futures/{SETTLE}/price_orders",
            params={"status": "open", "contract": contract, "limit": 100},
        ) or []
    except GateWalletError:
        orders = []
    classified = _classify_orders(orders if isinstance(orders, list) else [], out["side"], out["entry"])
    out["sl"] = classified["sl"]
    out["tp"] = classified["tp"]
    out["partials"] = classified["partials"]

    if sleeve == "dc":
        st = _as_dict(_donchian_state())
        out["sl"] = out["sl"] or _f(st.get("hard_stop") or st.get("initial_stop"))
        out["tp"] = out["tp"] or _f(st.get("take_profit2") or st.get("take_profit1"))
        if st.get("take_profit1") is not None:
            out["partials"] = out["partials"] or []
            out["partials"].append(
                {
                    "price": _f(st.get("take_profit1")),
                    "label": "TP1",
                    "status": "done" if st.get("tp1_done") else "open",
                }
            )
        if st.get("take_profit2") is not None:
            out["partials"].append(
                {
                    "price": _f(st.get("take_profit2")),
                    "label": "TP2",
                    "status": "done" if st.get("tp2_done") else "open",
                }
            )
        if not out["tp"]:
            out["tp"] = _f(st.get("take_profit2"))
    elif sleeve == "ema" and out["has_position"]:
        st = _as_dict(_ema_state())
        out["sl"] = out["sl"] or _f(st.get("sl_price"))
        out["tp"] = out["tp"] or _f(st.get("tp_price"))

    _attach_guide(api, sleeve, spec, out)
    return out


def _idle_usdt() -> tuple[float | None, float | None, dict[str, float]]:
    """Main spot + all sub-account spot USDT. None if a query failed."""
    try:
        key, secret = main_keys()
        api = GateWallet(key, secret)
        main_spot = api.main_spot_usdt()
        spots: dict[str, float] = {}
        for sleeve, uid in sub_uids().items():
            spots[sleeve] = api.sub_spot_usdt(uid)
        return main_spot, sum(spots.values()), spots
    except (RuntimeError, GateWalletError, TypeError, ValueError):
        return None, None, {}


def snapshot() -> dict:
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < _CACHE_SEC:
        return _CACHE["data"]
    with _SNAP_LOCK:
        now = time.time()
        if _CACHE["data"] is not None and now - _CACHE["ts"] < _CACHE_SEC:
            return _CACHE["data"]
        with ThreadPoolExecutor(max_workers=len(SLEEVES)) as pool:
            sleeves = list(pool.map(_one, SLEEVES))
        equities = [s.get("equity") for s in sleeves]
        main_spot, sub_spot, sub_spots = _idle_usdt()
        sleeve_marks = {s["id"]: s.get("equity") for s in sleeves}
        complete = book_mark_complete(sleeve_marks, main_spot, sub_spot, sub_spots)
        sleeve_total = sum(float(e) for e in equities) if all(e is not None for e in equities) else None
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if complete:
            equity_total = float(sleeve_total) + float(main_spot) + float(sub_spot)
            mdd = update_book_mdd(equity_total, ts)
        else:
            # Do not feed a 0-filled partial sum into MDD or the headline 현재 자산.
            mdd = load_mdd()
            equity_total = float(mdd["equity"]) if mdd.get("equity") else None
        principal_total = sum(float(s["principal"] or 0) for s in sleeves)
        book_pnl = (equity_total - principal_total) if equity_total is not None else None
        data = {
            "ts": ts,
            "sleeves": sleeves,
            "open_count": sum(1 for s in sleeves if s.get("has_position")),
            "pnl_total": sum(float(s["pnl"] or 0) for s in sleeves),
            "equity_total": equity_total,
            "sleeve_total": sleeve_total,
            "main_spot_usdt": main_spot,
            "sub_spot_usdt": sub_spot,
            "sub_spots": sub_spots,
            "principal_total": principal_total,
            "book_pnl": book_pnl,
            "book_pnl_pct": (
                book_pnl / principal_total * 100.0
                if book_pnl is not None and principal_total
                else None
            ),
            "mdd": mdd,
            "mdd_complete": complete,
        }
        _CACHE["ts"] = now
        _CACHE["data"] = data
        return data
