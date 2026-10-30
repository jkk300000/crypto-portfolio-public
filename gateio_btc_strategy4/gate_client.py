"""Gate.io Futures REST client (USDT perpetual, price-triggered orders)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import requests

from config import BotConfig

log = logging.getLogger(__name__)


class GateApiError(RuntimeError):
    def __init__(self, status: int, label: str, message: str, body: Any = None):
        super().__init__(f"[{status}] {label}: {message}")
        self.status = status
        self.label = label
        self.message = message
        self.body = body


class GateClient:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    # ——— low level ———
    def _sign(self, method: str, path: str, query: str, body: str, timestamp: str) -> str:
        hashed = hashlib.sha512(body.encode()).hexdigest()
        payload = f"{method}\n{path}\n{query}\n{hashed}\n{timestamp}"
        return hmac.new(self.cfg.api_secret.encode(), payload.encode(), hashlib.sha512).hexdigest()

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        auth: bool = False,
    ) -> Any:
        params = params or {}
        query = urlencode(params) if params else ""
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = f"{self.cfg.host}{self.cfg.prefix}{path}"
        if query:
            url = f"{url}?{query}"

        headers: dict[str, str] = {}
        if auth:
            ts = str(int(time.time()))
            full_path = f"{self.cfg.prefix}{path}"
            sign = self._sign(method.upper(), full_path, query, body_str, ts)
            headers.update(
                {
                    "KEY": self.cfg.api_key,
                    "Timestamp": ts,
                    "SIGN": sign,
                }
            )

        for attempt in range(5):
            resp = self.session.request(
                method.upper(),
                url,
                data=body_str if body is not None else None,
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 1.5 * (attempt + 1)
                log.warning("HTTP %s, retry in %.1fs", resp.status_code, wait)
                time.sleep(wait)
                continue
            break

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"label": "HTTP_ERROR", "message": resp.text}
            raise GateApiError(
                resp.status_code,
                err.get("label", "ERROR"),
                err.get("message", resp.text),
                err,
            )

        if not resp.text:
            return None
        return resp.json()

    # ——— market ———
    def get_contract(self) -> dict:
        return self.request("GET", f"/futures/{self.cfg.settle}/contracts/{self.cfg.contract}")

    def get_candles(self, interval: str, limit: int = 200) -> list[dict]:
        raw = self.request(
            "GET",
            f"/futures/{self.cfg.settle}/candlesticks",
            params={"contract": self.cfg.contract, "interval": interval, "limit": limit},
        )
        # Gate returns list of dicts or list of lists depending on version
        rows: list[dict] = []
        for item in raw or []:
            if isinstance(item, dict):
                rows.append(
                    {
                        "t": int(item["t"]),
                        "o": float(item["o"]),
                        "h": float(item["h"]),
                        "l": float(item["l"]),
                        "c": float(item["c"]),
                        "v": float(item.get("v", 0)),
                    }
                )
            else:
                # [t, v, c, h, l, o, sum]
                rows.append(
                    {
                        "t": int(item[0]),
                        "v": float(item[1]),
                        "c": float(item[2]),
                        "h": float(item[3]),
                        "l": float(item[4]),
                        "o": float(item[5]),
                    }
                )
        rows.sort(key=lambda x: x["t"])
        return rows

    def get_tickers(self) -> dict:
        data = self.request(
            "GET",
            f"/futures/{self.cfg.settle}/tickers",
            params={"contract": self.cfg.contract},
        )
        return data[0] if data else {}

    # ——— account / position ———
    def get_account(self) -> dict:
        return self.request("GET", f"/futures/{self.cfg.settle}/accounts", auth=True)

    def get_position(self) -> dict | None:
        try:
            pos = self.request(
                "GET",
                f"/futures/{self.cfg.settle}/positions/{self.cfg.contract}",
                auth=True,
            )
        except GateApiError as e:
            if e.status == 404 or e.label in {"POSITION_NOT_FOUND", "CONTRACT_NOT_FOUND"}:
                return None
            raise
        size = int(float(pos.get("size", 0) or 0))
        if size == 0:
            return None
        return pos

    def set_leverage_cross(self, leverage: float) -> None:
        """Cross margin: leverage=0, cross_leverage_limit=<배율>."""
        path = f"/futures/{self.cfg.settle}/positions/{self.cfg.contract}/leverage"
        try:
            self.request(
                "POST",
                path,
                params={
                    "leverage": "0",
                    "cross_leverage_limit": str(leverage),
                },
                body={},
                auth=True,
            )
            log.info("cross leverage set to %sx", leverage)
        except GateApiError as e:
            log.warning("set_leverage: %s", e)

    def ensure_cross_mode(self) -> None:
        """Switch to CROSS margin. mode 값은 CROSS/ISOLATED (대문자)."""
        try:
            self.request(
                "POST",
                f"/futures/{self.cfg.settle}/positions/cross_mode",
                body={"mode": "CROSS", "contract": self.cfg.contract},
                auth=True,
            )
            log.info("margin mode -> CROSS")
        except GateApiError as e:
            # 이미 CROSS 이거나 레버리지 API로 전환되는 경우
            log.info("cross_mode note: %s", e)

    # ——— orders ———
    def place_market(self, size: int, reduce_only: bool = False, text: str = "t-dc-entry") -> dict:
        body: dict[str, Any] = {
            "contract": self.cfg.contract,
            "size": size,
            "price": "0",
            "tif": "ioc",
            "reduce_only": reduce_only,
            "text": text,
        }
        return self.request("POST", f"/futures/{self.cfg.settle}/orders", body=body, auth=True)

    def close_position_market(self, text: str = "t-dc-close") -> dict | None:
        pos = self.get_position()
        if not pos:
            return None
        size = int(float(pos["size"]))
        # opposite sign to flatten
        return self.place_market(size=-size, reduce_only=True, text=text)

    def reduce_market(self, close_contracts: int, is_long: bool, text: str) -> dict:
        """close_contracts > 0 absolute contracts to reduce."""
        size = -abs(close_contracts) if is_long else abs(close_contracts)
        return self.place_market(size=size, reduce_only=True, text=text)

    # ——— price triggered ———
    def list_price_orders(self, status: str = "open") -> list[dict]:
        data = self.request(
            "GET",
            f"/futures/{self.cfg.settle}/price_orders",
            params={"status": status, "contract": self.cfg.contract, "limit": 100},
            auth=True,
        )
        return data or []

    def create_price_order(
        self,
        *,
        size: int,
        trigger_price: float,
        rule: int,
        text: str,
        order_type: str,
        reduce_only: bool = True,
        close: bool = False,
        price_round: str = "0.1",
    ) -> dict:
        trig = f"{trigger_price:.{_decimals(price_round)}f}"
        body = {
            "initial": {
                "contract": self.cfg.contract,
                "size": size,
                "price": "0",
                "tif": "ioc",
                "reduce_only": reduce_only,
                "text": text,
                "close": close,
            },
            "trigger": {
                "strategy_type": 0,
                "price_type": self.cfg.trigger_price_type,
                "price": trig,
                "rule": rule,
                "expiration": _gate_expiration(self.cfg.trigger_expiration_sec),
            },
            "order_type": order_type,
        }
        return self.request(
            "POST",
            f"/futures/{self.cfg.settle}/price_orders",
            body=body,
            auth=True,
        )

    def cancel_price_order(self, order_id: str | int) -> None:
        try:
            self.request(
                "DELETE",
                f"/futures/{self.cfg.settle}/price_orders/{order_id}",
                auth=True,
            )
        except GateApiError as e:
            log.warning("cancel_price_order %s: %s", order_id, e)

    def cancel_all_price_orders(self) -> None:
        try:
            self.request(
                "DELETE",
                f"/futures/{self.cfg.settle}/price_orders",
                params={"contract": self.cfg.contract},
                auth=True,
            )
        except GateApiError as e:
            log.warning("cancel_all_price_orders: %s", e)


def _gate_expiration(sec: int) -> int:
    day = 86400
    days = max(1, min(30, int(sec) // day if int(sec) >= day else int(sec)))
    return days * day


def _decimals(round_str: str) -> int:
    if "." not in round_str:
        return 0
    return len(round_str.split(".")[1].rstrip("0")) or len(round_str.split(".")[1])
