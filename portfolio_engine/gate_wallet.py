# -*- coding: utf-8 -*-
"""Main-key Gate wallet: sub futures balances and main-spot hops."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from .config import CURRENCY, GATE_HOST, GATE_PREFIX, SETTLE


class GateWalletError(RuntimeError):
    pass


@dataclass
class SleeveWallet:
    sleeve: str
    uid: str
    equity: float
    available: float
    total: float
    unrealised_pnl: float
    position_margin: float
    bonus: float
    has_position: bool
    raw: dict


class GateWallet:
    def __init__(self, key: str, secret: str, host: str = GATE_HOST, prefix: str = GATE_PREFIX):
        self.key = key
        self.secret = secret
        self.host = host.rstrip("/")
        self.prefix = prefix
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _sign(self, method: str, path: str, query: str, body: str, timestamp: str) -> str:
        hashed = hashlib.sha512(body.encode()).hexdigest()
        payload = f"{method}\n{path}\n{query}\n{hashed}\n{timestamp}"
        return hmac.new(self.secret.encode(), payload.encode(), hashlib.sha512).hexdigest()

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        params = params or {}
        query = urlencode(params) if params else ""
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = f"{self.host}{self.prefix}{path}"
        if query:
            url = f"{url}?{query}"
        ts = str(int(time.time()))
        sign = self._sign(method.upper(), f"{self.prefix}{path}", query, body_str, ts)
        headers = {"KEY": self.key, "Timestamp": ts, "SIGN": sign}
        resp = self.session.request(
            method.upper(),
            url,
            data=body_str if body is not None else None,
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise GateWalletError(f"HTTP {resp.status_code} {path}: {resp.text[:400]}")
        if not resp.content:
            return None
        return resp.json()

    def list_sub_accounts(self) -> list[dict]:
        try:
            rows = self.request("GET", "/sub_accounts")
        except GateWalletError:
            rows = self.request(
                "GET",
                "/wallet/sub_account_futures_balances",
                params={"settle": SETTLE},
            )
        if rows is None:
            return []
        return rows if isinstance(rows, list) else [rows]

    def main_futures_usdt(self) -> float:
        row = self.request("GET", f"/futures/{SETTLE}/accounts")
        if isinstance(row, list):
            row = row[0] if row else {}
        return max(0.0, float((row or {}).get("available") or 0))

    def transfer_main(self, amount: float, src: str, dst: str, client_order_id: str) -> Any:
        return self.request(
            "POST",
            "/wallet/transfers",
            body={
                "currency": CURRENCY,
                "from": src,
                "to": dst,
                "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
                "settle": SETTLE,
                "client_order_id": client_order_id[:64],
            },
        )

    def main_spot_usdt(self) -> float:
        rows = self.request("GET", "/spot/accounts", params={"currency": CURRENCY})
        if not rows:
            return 0.0
        if isinstance(rows, list):
            row = next(
                (r for r in rows if str((r or {}).get("currency") or "").upper() == CURRENCY),
                rows[0],
            )
        else:
            row = rows
        avail = float((row or {}).get("available") or 0)
        locked = float((row or {}).get("locked") or 0)
        return max(0.0, avail + locked)

    def sub_spot_usdt(self, uid: str) -> float:
        rows = self.request(
            "GET",
            "/wallet/sub_account_balances",
            params={"sub_uid": str(uid)},
        )
        if rows is None:
            return 0.0
        if isinstance(rows, list):
            row = next(
                (r for r in rows if str((r or {}).get("uid") or "") == str(uid)),
                rows[0] if rows else {},
            )
        else:
            row = rows
        return _spot_usdt(row)

    def sub_futures(self, uid: str) -> dict:
        rows = self.request(
            "GET",
            "/wallet/sub_account_futures_balances",
            params={"sub_uid": str(uid), "settle": SETTLE},
        )
        if isinstance(rows, list):
            for row in rows:
                if str(row.get("uid") or "") == str(uid) or len(rows) == 1:
                    return row
            raise GateWalletError(f"sub futures balance missing uid={uid}")
        return rows

    def transfer_sub_futures(self, uid: str, amount: float, direction: str, client_order_id: str) -> Any:
        if direction not in ("from", "to"):
            raise ValueError(direction)
        if amount <= 0:
            raise ValueError("amount must be > 0")
        return self.request(
            "POST",
            "/wallet/sub_account_transfers",
            body={
                "sub_account": str(uid),
                "sub_account_type": "futures",
                "currency": CURRENCY,
                "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
                "direction": direction,
                "client_order_id": client_order_id[:64],
            },
        )


def _spot_usdt(row: Any) -> float:
    if not isinstance(row, dict):
        return 0.0
    total = 0.0
    for key in ("available", "locked", "freeze", "frozen"):
        block = row.get(key)
        if block is None:
            continue
        if isinstance(block, dict):
            raw = block.get("USDT")
            if raw is None:
                raw = block.get("usdt")
            if isinstance(raw, dict):
                total += float(raw.get("available") or raw.get("amount") or 0)
                total += float(raw.get("locked") or 0)
            elif raw not in (None, ""):
                total += float(raw)
        elif block not in (None, ""):
            try:
                total += float(block)
            except (TypeError, ValueError):
                pass
    if total <= 0:
        for key in ("USDT", "usdt"):
            raw = row.get(key)
            if isinstance(raw, dict):
                total += float(raw.get("available") or 0) + float(raw.get("locked") or 0)
            elif raw not in (None, ""):
                try:
                    total += float(raw)
                except (TypeError, ValueError):
                    pass
    return max(0.0, total)


def _f(row: dict, *keys: str) -> float:
    for k in keys:
        if row.get(k) not in (None, ""):
            return float(row[k])
    return 0.0


def parse_futures_account(sleeve: str, uid: str, row: dict) -> SleeveWallet:
    block = row.get("available") or row
    acct = None
    if isinstance(block, dict):
        acct = block.get("USDT") or block.get("usdt") or block
        if isinstance(acct, dict) and "total" not in acct and "available" in acct and isinstance(acct.get("available"), dict):
            acct = acct.get("USDT") or acct.get("usdt")
    if not isinstance(acct, dict):
        acct = row
    total = _f(acct, "total")
    upl = _f(acct, "unrealised_pnl", "unrealized_pnl")
    available = _f(acct, "available", "cross_available")
    bonus = _f(acct, "bonus")
    pos_m = _f(
        acct,
        "position_margin",
        "isolated_position_margin",
        "cross_initial_margin",
    )
    isolated = _f(acct, "isolated_position_margin")
    cross_im = _f(acct, "cross_initial_margin")
    has_position = max(pos_m, isolated, cross_im) > 1.0
    return SleeveWallet(
        sleeve=sleeve,
        uid=str(uid),
        equity=total + upl,
        available=max(0.0, available - bonus),
        total=total,
        unrealised_pnl=upl,
        position_margin=pos_m,
        bonus=bonus,
        has_position=has_position,
        raw=acct if isinstance(acct, dict) else {},
    )
