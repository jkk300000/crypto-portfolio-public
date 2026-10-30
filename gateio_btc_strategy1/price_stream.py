"""
Gate.io futures price stream (WebSocket ticker).
"""
import json
import logging
import threading
import time
from typing import Optional

import websocket

import config

logger = logging.getLogger(__name__)


class PriceStream:
    def __init__(self, contract: Optional[str] = None, settle: Optional[str] = None):
        self.contract = contract or config.CONTRACT
        self.settle = settle or config.SETTLE
        self._last_price: Optional[float] = None
        self._lock = threading.Lock()
        self._ws = None
        self._running = False
        self._thread = None
        base = getattr(config, "GATE_WS_BASE", "wss://fx-ws.gateio.ws/v4/ws").rstrip("/")
        self._ws_url = f"{base}/{self.settle}"

    def get_last_price(self) -> Optional[float]:
        with self._lock:
            return self._last_price

    def _on_message(self, _ws, message):
        try:
            data = json.loads(message)
            if data.get("channel") != "futures.tickers":
                return
            result = data.get("result")
            if not isinstance(result, list):
                return
            for ticker in result:
                if not isinstance(ticker, dict):
                    continue
                if ticker.get("contract") != self.contract:
                    continue
                last_ = ticker.get("last")
                if last_ is None:
                    continue
                with self._lock:
                    self._last_price = float(last_)
        except Exception as exc:
            logger.debug("WebSocket parse error: %s", exc)

    def _on_error(self, _ws, error):
        logger.warning("WebSocket error: %s", error)

    def _on_close(self, _ws, code, msg):
        logger.info("WebSocket closed: code=%s msg=%s", code, msg)

    def _on_open(self, ws):
        payload = {
            "time": int(time.time()),
            "channel": "futures.tickers",
            "event": "subscribe",
            "payload": [self.contract],
        }
        ws.send(json.dumps(payload))
        logger.info("WebSocket subscribed: %s", self.contract)

    def _run(self):
        while self._running:
            try:
                headers = {"X-Gate-Size-Decimal": "1"}
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                    header=headers,
                )
                self._ws.run_forever()
            except Exception as exc:
                logger.warning("WebSocket run failed: %s", exc)
            if self._running:
                time.sleep(2)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread:
            self._thread.join(timeout=2)
